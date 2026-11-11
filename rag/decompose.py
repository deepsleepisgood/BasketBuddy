"""Query decomposition: user message → intent → Plan of sub-queries.

Intents (rules first, LLM only where rules can't do the job):
  dish          "rajma chawal for 3"        → best recipe → one sub-query per ingredient   (0 LLM calls)
  recipe_text   a pasted recipe             → LLM extracts [{ingredient, qty, unit}]        (1 call)
  need          "deep-clean my bathroom"    → best kit → its items; else LLM-suggested list (0–1 call)
  catalogue     "compare Dove vs Pears"     → LLM splits into sub-queries + constraints     (1 call)
  out_of_scope  stock, delivery, other apps, medical advice                                 (0 calls)

The final grounded answer (Phase 6) adds one more call, so no path exceeds 2.
Every decision is appended to Plan.trace for the UI's Evidence panel.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import pandas as pd

import config
from rag.ingredients import ParsedIngredient, parse_ingredient_list
from rag.synonyms import expand_query

Intent = Literal["dish", "recipe_text", "need", "catalogue", "out_of_scope"]
Mode = Literal["Best match", "Budget", "Top rated"]
DOMAINS = ("food", "personal_care", "household", "baby", "kitchen", "pets", "other")


# --- data types -------------------------------------------------------------------

@dataclass
class SubQuery:
    """One thing to find on BigBasket."""

    text: str                          # retrieval query
    label: str                         # shown in the cart ("Paneer", "toilet cleaner")
    domain: str | None = None
    sub_categories: tuple[str, ...] | None = None   # explicit shelf (kit items)
    brand: str | None = None           # catalogue comparisons
    quantity: float | None = None      # already scaled to the user's servings
    unit: str | None = None
    note: str | None = None
    count: int = 1                     # packs to buy (kits: "2 packs of diapers")
    excluded: bool = False             # in the user's pantry → not searched

    @property
    def needed(self) -> str:
        """'200 g', '1.5 tsp', 'to taste', ''."""
        if self.quantity is None:
            return self.note or ""
        q = f"{self.quantity:.2f}".rstrip("0").rstrip(".")
        return f"{q} {self.unit}" if self.unit else q


@dataclass
class Plan:
    intent: Intent
    message: str
    sub_queries: list[SubQuery] = field(default_factory=list)
    servings: int | None = None
    budget: float | None = None
    mode: Mode | None = None                 # stated in the message ("budget mode")
    pantry: list[str] = field(default_factory=list)
    recipe: dict[str, Any] | None = None     # matched recipe (dish path)
    kit: dict[str, Any] | None = None        # matched kit (need path)
    ai_generated: bool = False               # need-path list proposed by the LLM
    domain: str | None = None                # catalogue: inferred domain
    constraints: dict[str, Any] = field(default_factory=dict)  # max_price, min_rating, sort
    reason: str | None = None                # why out of scope / why nothing matched
    llm_calls: int = 0
    trace: list[str] = field(default_factory=list)

    @property
    def active(self) -> list[SubQuery]:
        return [s for s in self.sub_queries if not s.excluded]

    @property
    def excluded(self) -> list[SubQuery]:
        return [s for s in self.sub_queries if s.excluded]


# --- entity extraction (rules) --------------------------------------------------------

_PEOPLE_WORDS = (r"people|persons?|ppl|peeps|pax|guests|servings|members|adults|folks|friends|kids|"
                 r"family members|of us")
_SERVINGS_RE = re.compile(
    rf"\b(?:for|serves?|serving)\s+(\d{{1,2}})\s*({_PEOPLE_WORDS})?\b", re.I)
# "5 ppl", "4 of us", "family of 4"
_PEOPLE_RE = re.compile(rf"\b(?:family of\s+(\d{{1,2}})|(\d{{1,2}})\s+({_PEOPLE_WORDS}))\b", re.I)
_BUDGET_RE = re.compile(
    r"(?:under|below|less than|within|upto|up to|max(?:imum)?|budget(?: of| is)?|not more than)\s*"
    r"(?:rs\.?|inr|₹)?\s*(\d[\d,]*)", re.I)
_RUPEE_RE = re.compile(r"(?:₹|rs\.?\s*|inr\s*)(\d[\d,]*)\s*(?:budget)?", re.I)
_MODE_BUDGET_RE = re.compile(r"\b(budget mode|cheapest|cheap|low[- ]cost|economical|affordable|save money)\b", re.I)
_MODE_RATED_RE = re.compile(r"\b(top[- ]rated|best[- ]rated|highest[- ]rated|best reviewed|top rated mode)\b", re.I)
_PANTRY_RE = re.compile(
    r"\b(?:i\s+)?(?:already\s+have|have\s+already\s+got|i\s+have(?!\s+to\b)|except|excluding|skip|minus|no need for)\s+"
    r"(?P<items>[^.;!?]+)", re.I)


def extract_servings(msg: str, require_people_word: bool = False) -> int | None:
    m = _PEOPLE_RE.search(msg)
    if m:
        n = int(m.group(1) or m.group(2))
    else:
        m = _SERVINGS_RE.search(msg)
        if not m or (require_people_word and not m.group(2)):
            return None
        n = int(m.group(1))
    return n if 1 <= n <= 50 else None


def extract_budget(msg: str) -> float | None:
    m = _BUDGET_RE.search(msg) or _RUPEE_RE.search(msg)
    return float(m.group(1).replace(",", "")) if m else None


def extract_mode(msg: str) -> Mode | None:
    if _MODE_RATED_RE.search(msg):
        return "Top rated"
    if _MODE_BUDGET_RE.search(msg):
        return "Budget"
    return None


def extract_pantry(msg: str) -> list[str]:
    """'I already have salt and ghee' → ['salt', 'ghee']."""
    out: list[str] = []
    for m in _PANTRY_RE.finditer(msg):
        for part in re.split(r",|\band\b|\baur\b|&|\+", m.group("items"), flags=re.I):
            part = re.sub(r"\b(some|the|a|my|at home|with me|already)\b", " ", part, flags=re.I).strip(" .")
            if part and len(part) < 30:
                out.append(part.lower())
    return out


def _tokens(text: str) -> set[str]:
    """Lower-case content tokens of text and its synonym expansion, crude singulars."""
    toks = set()
    for t in re.findall(r"[a-z]+", expand_query(text).lower()):
        if len(t) > 3 and t.endswith("es") and not t.endswith("ses"):
            t = t[:-2] if t[:-2].endswith(("o", "ch", "sh")) else t[:-1]
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        toks.add(t)
    return toks - {"fresh", "chopped", "powder", "whole", "dried", "split"}


def in_pantry(ingredient: str, pantry: list[str]) -> bool:
    """True if every word of some pantry item (or of its English synonym) is in the ingredient.

    pantry 'haldi' excludes 'turmeric powder' (haldi ↔ turmeric); 'oil' excludes 'sunflower oil';
    'salt' does not exclude 'salted butter'.
    """
    ing = _tokens(ingredient)
    for item in pantry:
        p = _tokens(item)
        if p and (p <= ing or any(w in ing for w in p if w in _SYNONYM_HEADS)):
            return True
    return False


# single-word pantry items that identify an ingredient by themselves after expansion
_SYNONYM_HEADS = {"turmeric", "haldi", "cumin", "jeera", "ghee", "salt", "sugar", "oil", "curd", "dahi"}


# --- classification rules ------------------------------------------------------------

_OOS_RULES: list[tuple[str, str]] = [
    (r"\b(in stock|out of stock|stock|is \w+( \w+)? available|available (in|at|near|now|today)|availability"
     r"|deliver(y|ed|s)?|delivery time|how (soon|fast|long)|when will|eta|order status|track (my )?order"
     r"|refund|cancel (my )?order|store timings?|open now)\b",
     "stock, delivery and availability aren't in the dataset (it's a static price snapshot)"),
    (r"\b(blinkit|zepto|instamart|swiggy|amazon|flipkart|jiomart|dunzo|dmart|d-mart|reliance fresh"
     r"|nature'?s basket|more supermarket|spencer'?s)\b",
     "the dataset only covers BigBasket, so it can't compare other shopping apps"),
    (r"\b(cure|cures|treat|treats|treatment|disease|diabetes|blood pressure|cholesterol|cancer|pregnan\w*"
     r"|dosage|side effects?|doctor|prescription|is it safe|safe for|good for (my )?(health|heart|kidney|liver)"
     r"|lose weight|weight loss|allerg\w*|medicine for|medical advice)\b",
     "medical, health and skin advice beyond the product description isn't something the data can support"),
    (r"\b(weather|cricket|ipl|news|stock market|share price|bitcoin|politics|election|write (me )?(a |an )?(code|poem|essay|story)"
     r"|tell me a joke|who (is|was) the|capital of)\b",
     "the question isn't about BigBasket products"),
]
_RECIPE_TEXT_QTY = re.compile(
    r"\b\d+(?:[./-]\d+)?\s*(?:cups?|tbsp|tsp|tablespoons?|teaspoons?|g|gm|grams?|kg|ml|l|litres?|inch|pinch|cloves?)\b", re.I)
_NEED_RE = re.compile(
    r"\b(clean(ing)?|deep[- ]clean|routine|skin ?care|grooming|party|birthday|festival|diwali|deepavali|holi|"
    r"puja|pooja|newborn|new ?born|new baby|baby shower|puppy|kitten|new (dog|cat|pet)|move[- ]in|moving (in|into)|"
    r"hostel|dorm|first apartment|new (home|flat|house)|set ?up (a|my) kitchen|travel|trip|toiletries|guests|"
    r"essentials|kit|starter|stock up|monthly (grocery|ration)|hosting|monsoon|rainy season|period|menstrual|"
    r"first[- ]aid|movie night|binge|office snacks?|snack drawer|breakfast for a week|get[- ]together|"
    r"getting a (pet|dog|cat|puppy|kitten|\w+)|what (should|do) i (need to )?buy|what to buy)\b", re.I)
# "cooking" is deliberately absent: "coconut oil for cooking" is a product question, not a dish.
_DISH_RE = re.compile(
    r"\b(make|cook|prepare|recipe (for|of)|ingredients (for|of|needed for|to make)|"
    r"everything (i need |needed )?(for|to make)|what (do|would) i need (for|to make)|shopping list for|"
    r"cart for|i want to make|planning to make|dinner of|lunch of|making|(?<!for )cooking)\b", re.I)
_CATALOGUE_RE = re.compile(
    r"\b(price|prices|cost|costs|cheap|cheapest|expensive|compare|comparison|vs\.?|versus|rating|rated|reviews?|"
    r"brands?|which|best|how much|kitne|kitna|kya|discount|mrp|offer|difference|options|show me|list|"
    r"recommend|suggest|under|below|above)\b|₹", re.I)


def _oos_reason(msg: str) -> str | None:
    for pattern, reason in _OOS_RULES:
        if re.search(pattern, msg, re.I):
            return reason
    return None


def _looks_like_recipe_text(msg: str) -> bool:
    qty_hits = len(_RECIPE_TEXT_QTY.findall(msg))
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    return qty_hits >= 3 or (len(lines) >= 4 and qty_hits >= 2) or (
        bool(re.search(r"\bingredients?\s*:", msg, re.I)) and len(msg.split()) >= 25)


# --- dish path -----------------------------------------------------------------------

_DISH_FILLER = re.compile(
    r"\b(give me|show me|get me|please|can you|could you|i want to|i'd like to|i would like to|"
    r"everything (i need |needed )?(for|to make)|what (do|would) i need (for|to make)|ingredients (for|of|needed for|to make)|"
    r"recipe (for|of)|shopping list for|cart for|make|cook|prepare|planning to|tonight|today|tomorrow|"
    r"for dinner|for lunch|a cart|the cart|budget mode|top rated|best match|mode|"
    # conversational lead-ins: "i need to cook butter chicken for 5 ppl" → "butter chicken"
    r"i need to|need to|i have to|have to|got to|gotta|gonna|going to|want to|wanna|would like to|"
    r"help me|let'?s|we are|we're|i am|i'm|making|cooking|cooked|this weekend)\b", re.I)
# Words that change the dish (penalised unless the user asked for them) …
_VARIANT_WORDS = {"jain", "without", "no", "onion", "garlic", "masoor", "oats", "millet", "ragi", "quinoa",
                  "vegan", "eggless", "sugar", "free", "low", "fat", "protein", "diabetic", "sattvic", "biryani",
                  "pulao", "salad", "wrap", "cutlet", "cutlets", "soup", "sandwich", "paratha", "noodles"}
# … and words that only describe it (ignored when counting extra words).
_DESCRIPTOR_WORDS = {"creamy", "delicious", "easy", "quick", "homemade", "home", "restaurant", "style",
                     "classic", "authentic", "simple", "special", "rich", "spicy", "tasty", "punjabi",
                     "dhaba", "traditional", "best", "perfect", "recipe"}


def dish_name_from(msg: str) -> str:
    """Strip servings, budget, pantry and filler phrases: 'Give me everything I need for
    dal makhani for 4 people, budget mode, I already have salt' → 'dal makhani'."""
    text = _PANTRY_RE.sub(" ", msg)
    text = _PEOPLE_RE.sub(" ", text)
    text = _SERVINGS_RE.sub(" ", text)
    text = _BUDGET_RE.sub(" ", text)
    text = _DISH_FILLER.sub(" ", text)
    text = re.sub(r"[^\w\s&'-]", " ", text)
    text = re.sub(r"\b(for|of|the|a|an|i|me|my|we|us|our|some|and|with|to|need|want|people|persons|ppl|"
                  r"peeps|folks|friends|kids|family|guests|members|adults|everyone|dinner|lunch|breakfast)\b",
                  " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _literal_words(text: str) -> set[str]:
    """Words as written, no synonym expansion ("rajma" ≠ "kidney beans" here)."""
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


def choose_recipe(dish: str, hits: list[Any]) -> tuple[Any | None, list[Any]]:
    """Pick the plainest recipe whose name contains the dish words.

    Retrieval ranks variants ("Jain Style Dal Makhani") above plain names, so among
    candidates above RECIPE_MATCH_SIM we prefer, in order: more dish words literally in
    the name → more dish words after synonym expansion → fewer variant words the user
    didn't ask for ("jain", "masoor", "without onion") → fewer extra words → RRF rank.
    """
    want = _tokens(dish)
    want_literal = _literal_words(dish)
    asked_variants = want & _VARIANT_WORDS
    scored = []
    for rank, h in enumerate(hits):
        if (h.vector_sim or 0) < config.RECIPE_MATCH_SIM:
            continue
        name = _tokens(h.metadata["name"])
        literal = len(want_literal & _literal_words(h.metadata["name"])) / max(len(want_literal), 1)
        coverage = len(want & name) / max(len(want), 1)
        extra = len(name - want - _DESCRIPTOR_WORDS)
        variants = len((name & _VARIANT_WORDS) - asked_variants)
        scored.append(((-literal, -coverage, variants, extra, rank), h))
    scored.sort(key=lambda x: x[0])
    ranked = [h for _, h in scored]
    if not ranked or -scored[0][0][1] < 0.5:  # less than half the dish words matched
        return None, ranked
    return ranked[0], ranked[1:]


def exact_recipe(dish: str) -> int | None:
    """recipe_id whose name equals the dish name (case-insensitive), if any."""
    names = _recipes()["name"].str.lower().str.strip()
    hit = names[names == dish.lower().strip()]
    return int(hit.index[0]) if len(hit) else None


@lru_cache(maxsize=1)
def _recipes() -> pd.DataFrame:
    return pd.read_parquet(config.RECIPES_PARQUET).set_index("recipe_id")


def recipe_sub_queries(ingredients: list[ParsedIngredient], scale: float, pantry: list[str]) -> list[SubQuery]:
    """One food sub-query per ingredient. The shelf hint is looked up on the name without
    its bracketed alias: "Coriander (Dhania) Leaves" → "Coriander Leaves" → fresh herbs."""
    from rag.category_map import lookup_subcategories

    out = []
    for p in ingredients:
        qty = round(p.quantity * scale, 2) if p.quantity is not None else None
        hint = lookup_subcategories(p.base_name, "food") or lookup_subcategories(p.query, "food")
        out.append(SubQuery(
            text=p.query, label=p.base_name, domain="food", quantity=qty, unit=p.unit, note=p.note,
            sub_categories=tuple(hint) if hint else None, excluded=in_pantry(p.query, pantry),
        ))
    return out


def plan_dish(plan: Plan, dish: str) -> Plan:
    from rag.retriever import hybrid_search

    res = hybrid_search(dish, k=8, collection="recipes")
    plan.trace.append(f"Recipe search for '{dish}': top hits " + ", ".join(
        f"{h.metadata['name']} ({h.vector_sim:.2f})" for h in res.hits[:5]))
    exact_id = exact_recipe(dish)
    best, others = choose_recipe(dish, res.hits)
    if exact_id is not None:  # an exact title beats any semantic neighbour
        recipe_id, similarity, how = exact_id, 1.0, "exact title match"
        others = [h for h in res.hits if h.metadata["recipe_id"] != exact_id]
    elif best is not None:
        recipe_id, similarity, how = int(best.metadata["recipe_id"]), best.vector_sim, f"similarity {best.vector_sim:.2f}"
    else:
        plan.reason = f"no recipe in the dataset matches '{dish}' (best similarity {res.best_vector_sim:.2f})"
        plan.trace.append("No recipe above the match threshold, so nothing will be invented.")
        return plan
    row = _recipes().loc[recipe_id]
    rec_servings = int(row.servings) if pd.notna(row.servings) else None
    scale = (plan.servings / rec_servings) if (plan.servings and rec_servings) else 1.0
    plan.recipe = {
        "recipe_id": recipe_id, "name": row["name"], "display_name": row.display_name,
        "servings": rec_servings, "scale": round(scale, 2), "similarity": similarity, "match": how,
        "cuisine": row.cuisine, "diet": row.diet, "url": row.url, "ingredients_raw": row.ingredients,
        "clean_ingredients": row.clean_ingredients if pd.notna(row.clean_ingredients) else None,
        "alternatives": [h.metadata["name"] for h in others[:4]],
    }
    plan.sub_queries = recipe_sub_queries(parse_ingredient_list(row.ingredients), scale, plan.pantry)
    plan.trace.append(
        f"Matched recipe: {row['name']} ({how}); "
        f"{len(plan.sub_queries)} ingredients; scale ×{scale:.2f}"
        + (f" ({rec_servings} → {plan.servings} servings)" if scale != 1.0 else ""))
    return plan


# --- recipe_text path ----------------------------------------------------------------

_EXTRACT_SYSTEM = """You extract shopping ingredients from a recipe the user pasted.
Return JSON: {"items": [{"ingredient": string, "quantity": number or null, "unit": string or null}]}
Rules:
- Only ingredients that literally appear in the text. Never add, infer or substitute any.
- One entry per ingredient; use the name as written (keep Hindi names, e.g. "haldi").
- Skip water and cooking steps. quantity null when not stated ("salt to taste")."""


def _as_quantity(value: Any) -> float | None:
    """LLM quantities arrive as 2, 0.5, "1/2" or "8-10"; normalise to a float (ranges → upper end)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        from rag.ingredients import parse_item

        parsed = parse_item(f"{value.strip()} x")
        return parsed.quantity if parsed else None
    return None


def _appears_in(ingredient: str, text: str) -> bool:
    """Guard against invented ingredients: some content word must occur in the source."""
    src = _tokens(text) | set(re.findall(r"[a-z]+", text.lower()))
    return bool(_tokens(ingredient) & src)


def plan_recipe_text(plan: Plan) -> Plan:
    from rag.llm import LLMError, complete_json

    items: list[SubQuery] = []
    try:
        data, resp = complete_json(_EXTRACT_SYSTEM, plan.message, fast=True)
        plan.llm_calls += 1
        dropped = []
        for it in data.get("items", []):
            name = str(it.get("ingredient", "")).strip()
            if not name:
                continue
            if not _appears_in(name, plan.message):
                dropped.append(name)
                continue
            items.append(SubQuery(
                text=name.lower(), label=name, domain="food", quantity=_as_quantity(it.get("quantity")),
                unit=it.get("unit") or None, excluded=in_pantry(name, plan.pantry)))
        plan.trace.append(f"LLM extracted {len(items)} ingredients ({resp.model}{', cached' if resp.cached else ''}).")
        if dropped:
            plan.trace.append(f"Dropped {len(dropped)} item(s) not found in the pasted text: {', '.join(dropped)}")
    except LLMError as e:
        plan.trace.append(f"LLM extraction failed ({e}); falling back to the regex parser.")
        body = re.sub(r"^.*?ingredients?\s*:", "", plan.message, flags=re.I | re.S)
        lines = [ln for ln in re.split(r"[\n;]", body) if ln.strip()]
        parsed = [p for ln in lines for p in parse_ingredient_list(ln)] or parse_ingredient_list(body)
        items = recipe_sub_queries(parsed, 1.0, plan.pantry)
    plan.sub_queries = items
    if not items:
        plan.reason = "couldn't find any ingredients in the pasted text"
    return plan


# --- need path -----------------------------------------------------------------------

@lru_cache(maxsize=1)
def _kits() -> dict[str, dict[str, Any]]:
    data = json.loads(config.KITS_PATH.read_text(encoding="utf-8"))
    return {k["id"]: k for k in data["kits"]}


_SUGGEST_SYSTEM = f"""A shopper describes a task or occasion. Propose a short shopping list of grocery and
household products that a supermarket like BigBasket sells.
Return JSON: {{"items": [{{"item": string, "qty": number, "unit": string, "domain": one of {list(DOMAINS)}}}]}}
Rules: 4 to 10 items; generic product names (no brands); no services, no fresh meat; no medical claims."""


def plan_need(plan: Plan, core: str, kit_hit: Any | None = None) -> Plan:
    from rag.llm import LLMError, complete_json
    from rag.retriever import hybrid_search

    if kit_hit is None:
        kit_hit = hybrid_search(core, k=3, collection="kits").best
    sim = kit_hit.vector_sim if kit_hit else 0.0
    if kit_hit and sim >= config.KIT_MATCH_SIM:
        kit = _kits()[kit_hit.metadata["kit_id"]]
        plan.kit = {"id": kit["id"], "name": kit["name"], "similarity": sim, "synthetic": True,
                    "description": kit["description"]}
        plan.sub_queries = [
            SubQuery(text=it["item"], label=it["item"], domain=it["domain"],
                     sub_categories=(it["sub_category"],) if it.get("sub_category") else None,
                     quantity=it.get("qty"), unit=it.get("unit"),
                     count=int(it["qty"]) if str(it.get("unit", "")) not in {"kg", "g", "l", "ml"} and it.get("qty") else 1,
                     note=it.get("note"), excluded=in_pantry(it["item"], plan.pantry))
            for it in kit["items"]
        ]
        plan.trace.append(f"Matched kit: {kit['name']} (similarity {sim:.2f} ≥ {config.KIT_MATCH_SIM}); "
                          f"{len(plan.sub_queries)} items. Kit lists are synthetic, written by the team.")
        return plan

    plan.trace.append(f"No kit above {config.KIT_MATCH_SIM} (best: "
                      f"{kit_hit.metadata['name'] if kit_hit else 'none'} {sim:.2f}); asking the LLM for a list.")
    try:
        data, resp = complete_json(_SUGGEST_SYSTEM, core or plan.message, fast=True)
        plan.llm_calls += 1
        for it in data.get("items", [])[:10]:
            name = str(it.get("item", "")).strip()
            dom = it.get("domain") if it.get("domain") in DOMAINS else None
            if name:
                plan.sub_queries.append(SubQuery(
                    text=name, label=name, domain=dom, quantity=it.get("qty"), unit=it.get("unit"),
                    excluded=in_pantry(name, plan.pantry)))
        plan.ai_generated = True
        plan.trace.append(f"Suggested list (AI-generated, {resp.model}): {len(plan.sub_queries)} items. "
                          "The user should edit it before adding.")
    except LLMError as e:
        plan.reason = f"no matching kit and the LLM is unavailable ({e})"
    return plan


# --- catalogue path ------------------------------------------------------------------

_CATALOGUE_SYSTEM = """You turn a shopper's question about the BigBasket product catalogue into search sub-queries.
Return JSON: {"in_scope": bool, "reason": string or null, "sub_queries": [string],
              "max_price": number or null, "min_rating": number or null,
              "sort": "price_asc" | "price_desc" | "rating_desc" | null}
Rules:
- One sub-query per distinct product asked about. A comparison of A and B gives two sub-queries,
  each a complete product description (e.g. "Dove bathing soap", "Pears bathing soap").
- Keep brand, product type and pack-size words. Translate Hindi/Hinglish product words to English
  and keep the original too (e.g. "jeera cumin").
- Never add products the user did not mention.
- "cheapest" → sort price_asc; "best rated"/"highest rated" → rating_desc; "under ₹300" → max_price 300;
  "rated above 4" → min_rating 4.
- in_scope is TRUE for any request to find, price, compare or recommend products, including
  recommendations for a skin type, hair type, need or use: "my skin is oily, what cleanser should I use"
  -> in scope, sub_queries ["face cleanser for oily skin"]; "something for dandruff" -> ["anti-dandruff
  shampoo"]. Recommending products is not medical advice.
- in_scope is false only for: stock, delivery or availability; other shopping apps; diagnosing or treating
  a medical condition (dosages, symptoms, "is this rash serious"); and things BigBasket doesn't sell
  (electronics, furniture, clothes, vehicles)."""


def _rule_catalogue_split(msg: str) -> list[str]:
    """Fallback splitter when the LLM is unavailable: 'Dove and Pears bathing soaps' →
    ['Dove bathing soaps', 'Pears bathing soaps']."""
    core = re.sub(r"\b(compare|comparison of|price of|what is the|what's the|how much (is|are|does)|"
                  r"kitne ka hai|kitna hai|kya hai|on price and rating|price and rating|in terms of \w+)\b",
                  " ", msg, flags=re.I)
    core = re.sub(r"[?!.]", " ", core)
    parts = [p.strip() for p in re.split(r"\b(?:and|vs\.?|versus|or|aur)\b|,", core, flags=re.I) if p.strip()]
    if len(parts) < 2:
        return [re.sub(r"\s+", " ", core).strip()]
    longest = max(parts, key=lambda p: len(p.split()))
    tail = " ".join(longest.split()[1:])
    return [f"{p} {tail}".strip() if len(p.split()) <= 2 and p != longest else p for p in parts]


@lru_cache(maxsize=1)
def _brand_index() -> dict[str, set[str]]:
    """lower-case brand → domains it sells in."""
    df = pd.read_parquet(config.PRODUCTS_PARQUET, columns=["brand", "domain"])
    out: dict[str, set[str]] = {}
    for b, d in zip(df["brand"], df["domain"]):
        out.setdefault(b, set()).add(d)
    return out


_GENERIC_BRANDS = {"real", "care", "premium", "best", "pure", "ultra", "indian", "go", "joy", "neo", "sol",
                   "fem", "aer", "exo", "hit", "id", "km", "in2", "unbranded", "fresh", "organic", "natural"}


def detect_brand(text: str, domain: str | None) -> str | None:
    """Longest brand name in ``text`` (word boundaries) that sells in ``domain``.

    'Pears bathing soap' (personal_care) → 'Pears'; 'fresh pears' (food) → None.
    """
    low = text.lower()
    best = None
    for brand, domains in _brand_index().items():
        b = brand.lower()
        if b in _GENERIC_BRANDS or len(b) < 2 or (domain and domain not in domains):
            continue
        if re.search(rf"(?<![a-z]){re.escape(b)}(?![a-z])", low) and (best is None or len(b) > len(best)):
            best = brand
    return best


def plan_catalogue(plan: Plan) -> Plan:
    from rag.llm import LLMError, complete_json
    from rag.retriever import infer_domain

    dom, conf, how = infer_domain(plan.message)
    plan.domain = dom
    plan.trace.append(f"Domain guess: {dom or 'all categories'} (confidence {conf}, {how}).")
    try:
        data, resp = complete_json(_CATALOGUE_SYSTEM, plan.message, fast=True)
        plan.llm_calls += 1
        if data.get("in_scope") is False:
            plan.intent = "out_of_scope"
            plan.reason = data.get("reason") or "the question isn't about BigBasket products"
            plan.trace.append(f"LLM judged the question out of scope: {plan.reason}")
            return plan
        queries = [str(q).strip() for q in data.get("sub_queries", []) if str(q).strip()]
        plan.constraints = {k: data.get(k) for k in ("max_price", "min_rating", "sort") if data.get(k) is not None}
        plan.trace.append(f"LLM decomposition ({resp.model}{', cached' if resp.cached else ''}): {queries}; "
                          f"constraints {plan.constraints or 'none'}")
    except LLMError as e:
        queries = _rule_catalogue_split(plan.message)
        plan.trace.append(f"LLM unavailable ({e}); rule-based split: {queries}")
    if not queries:
        queries = [plan.message]
    # rules backfill constraints the LLM missed
    if plan.budget and "max_price" not in plan.constraints:
        plan.constraints["max_price"] = plan.budget
    if plan.mode == "Budget" and "sort" not in plan.constraints:
        plan.constraints["sort"] = "price_asc"
    for q in queries[:6]:
        q_dom = dom
        if q_dom is None:  # a per-sub-query guess helps "jeera aur haldi" style splits
            q_dom = infer_domain(q)[0]
        brand = detect_brand(q, q_dom)
        plan.sub_queries.append(SubQuery(text=q, label=q, domain=q_dom, brand=brand))
    if any(s.brand for s in plan.sub_queries):
        plan.trace.append("Brand filters: " + ", ".join(f"{s.label} → {s.brand}" for s in plan.sub_queries if s.brand))
    return plan


# --- entry point ----------------------------------------------------------------------

def classify(msg: str) -> tuple[Intent, str]:
    """Rule-based intent with the rule that fired (shown in the Evidence panel)."""
    if reason := _oos_reason(msg):
        return "out_of_scope", reason
    if _looks_like_recipe_text(msg):
        return "recipe_text", "message contains a list of quantities (pasted recipe)"
    if _NEED_RE.search(msg) and not _DISH_RE.search(msg):
        return "need", f"task/occasion word: '{_NEED_RE.search(msg).group(0)}'"
    if _DISH_RE.search(msg):
        return "dish", f"cooking phrase: '{_DISH_RE.search(msg).group(0)}'"
    if extract_servings(msg) and not _CATALOGUE_RE.search(msg):
        return "dish", "a name followed by a number of servings ('… for 4')"
    if _CATALOGUE_RE.search(msg):
        return "catalogue", f"product-question word: '{_CATALOGUE_RE.search(msg).group(0)}'"
    if _NEED_RE.search(msg):
        return "need", f"task/occasion word: '{_NEED_RE.search(msg).group(0)}'"
    return "catalogue", "no rule fired (default)"


def decompose(message: str, extra_pantry: list[str] | None = None, default_servings: int | None = None) -> Plan:
    """Build the Plan for one user message.

    ``extra_pantry`` and ``default_servings`` come from the sidebar; servings stated in
    the message win over the sidebar value.
    """
    from rag.retriever import hybrid_search

    msg = message.strip()
    intent, why = classify(msg)
    plan = Plan(intent=intent, message=msg)
    plan.trace.append(f"Intent: {intent} ({why}).")
    plan.budget = extract_budget(msg)
    plan.mode = extract_mode(msg)
    plan.pantry = sorted(set(extract_pantry(msg)) | {p.lower() for p in (extra_pantry or [])})
    if plan.pantry:
        plan.trace.append(f"Pantry (excluded from the cart): {', '.join(plan.pantry)}")

    if intent == "out_of_scope":
        plan.reason = why
        return plan

    if intent == "catalogue" and why.endswith("(default)"):
        # Short bare names ("paneer butter masala") may be dishes; check the recipe index.
        name = dish_name_from(msg)
        if 0 < len(name.split()) <= 6:
            if exact_recipe(name) is not None:
                plan.intent = "dish"
                plan.trace.append(f"Re-classified as dish: '{name}' is an exact recipe title.")
            else:
                rec = choose_recipe(name, hybrid_search(name, k=8, collection="recipes").hits)[0]
                if rec is not None and rec.vector_sim >= config.RECIPE_MATCH_SIM + 0.1:
                    plan.intent = "dish"
                    plan.trace.append(f"Re-classified as dish: '{name}' closely matches recipe "
                                      f"'{rec.metadata['name']}' ({rec.vector_sim:.2f}).")

    if plan.intent == "dish":
        plan.servings = extract_servings(msg) or default_servings
        return plan_dish(plan, dish_name_from(msg))
    if plan.intent == "recipe_text":
        return plan_recipe_text(plan)
    if plan.intent == "need":
        core = _PANTRY_RE.sub(" ", _BUDGET_RE.sub(" ", msg))
        return plan_need(plan, core)
    return plan_catalogue(plan)
