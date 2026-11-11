"""Turn a Plan's sub-queries into a priced cart (dish / recipe_text / need paths),
or into filtered, sorted product lists (catalogue path).

All prices, totals and "fits the budget?" are computed here from metadata;
the LLM never does arithmetic.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import config
from rag.decompose import Mode, Plan, SubQuery
from rag.retriever import Hit, ItemSpec, SearchResult, search_item, search_items

# Words that mark a processed product. A raw ingredient ("chicken", "tomato", "cashew")
# must not be matched to "Chicken Fries", "Tomato Ketchup" or "Cashew Cookies" unless
# the ingredient itself names that form.
_PROCESSED = re.compile(
    r"\b(fries|nuggets?|sausages?|salami|ketchup|sauce|puree|paste|pickle|achaa?r|syrup|sharbat|chips|crisps|namkeen|bhujia|"
    r"masala|mix|instant|ready to (cook|eat)|noodles|soup|biscuits?|cookies|cake|candy|chocolates?|"
    r"juice|drink|squash|jam|spread|flavou?red|marinade|seasoning|popcorn|tea|coffee|cereal|"
    r"flakes|powder|chutney|gravy|curry|snack|wafers?|bites|drops?|extract|essence|"
    r"fried|strips|tikka|kebabs?|seekh|cutlets?|patt(?:y|ies)|burgers?|ham|smoked|pepperoni|meatballs?)\b", re.I)


# For a spice named on its own, "powder"/"masala" is the product itself ("haldi" = turmeric
# powder). Not when the recipe asks for a whole form ("cardamom pods", "cinnamon stick"),
# names another form ("red chilli sauce"), or isn't a spice at all ("chicken", "coriander leaves").
_SPICE_SHELVES = {"Masalas & Spices", "Organic Staples"}
_SPICE_FORMS = {"powder", "masala"}
_WHOLE_FORM = re.compile(r"\b(whole|seeds?|pods?|sticks?|leaves|peppercorns?|dry red|dried red|flakes)\b", re.I)


def _spice_form_ok(ingredient: str, asked: set[str]) -> bool:
    from rag.category_map import SPICES, lookup_subcategories

    return (not asked and not _WHOLE_FORM.search(ingredient)
            and lookup_subcategories(ingredient, "food") == SPICES)


def processed_mismatch(ingredient: str, product: str, sub_category: str = "") -> str | None:
    """The processed-form word in ``product`` that ``ingredient`` didn't ask for, if any."""
    asked = {m.group(0).lower() for m in _PROCESSED.finditer(ingredient)}
    spice_ok = sub_category in _SPICE_SHELVES and _spice_form_ok(ingredient, asked)
    for m in _PROCESSED.finditer(product):
        word = m.group(0).lower()
        if spice_ok and word in _SPICE_FORMS:
            continue
        if word not in asked and not any(word.startswith(a[:4]) for a in asked):
            return word
    return None


# Head-noun check for non-food items: "oil control moisturiser" should be a moisturiser,
# not an "Oil Control Face Wash". A few product types are named differently on BigBasket.
_HEAD_ALIASES = {
    "moisturiser": ("moistur", "cream", "lotion", "gel"),
    "moisturizer": ("moistur", "cream", "lotion", "gel"),
    "sunscreen": ("sunscreen", "sun screen", "sunblock", "sun block", "spf"),
    "freshener": ("freshener", "fragrance"),
    "vaporiser": ("vapori", "machine", "refill"),
    "detergent": ("detergent", "wash"),
}
_HEAD_NOISE = {"spf", "for", "with", "and", "pack", "of", "set"}


def head_noun(item: str) -> str | None:
    """'face wash for oily skin' → 'wash'; 'sunscreen SPF 50' → 'sunscreen'."""
    core = re.split(r"\b(?:for|with)\b", item.lower())[0]
    words = [w for w in re.findall(r"[a-z]+", core) if w not in _HEAD_NOISE]
    return words[-1] if words else None


_GENERIC_WORDS = {"food", "mix", "pack", "set", "kit", "item", "items", "product", "products", "supplies",
                  "bag", "new", "basic", "small", "large", "big", "good", "best", "for", "and", "with", "the"}


def shares_content_word(item: str, meta: dict[str, Any]) -> bool:
    """At least one specific word of the item (stemmed) appears in the product name/type."""
    words = {w for w in re.findall(r"[a-z]+", item.lower()) if w not in _GENERIC_WORDS and len(w) > 2}
    if not words:
        return True
    text = f"{meta.get('product', '')} {meta.get('type', '')}".lower()
    return any(w[: max(3, min(5, len(w) - 1))] in text for w in words)


def has_head_noun(item: str, meta: dict[str, Any]) -> bool:
    head = head_noun(item)
    if not head:
        return True
    text = f"{meta.get('product', '')} {meta.get('type', '')}".lower()
    stem = head[: max(3, min(5, len(head) - 1))]
    return any(a in text for a in _HEAD_ALIASES.get(head, (stem,)))


# --- data types ------------------------------------------------------------------------

@dataclass
class CartLine:
    label: str
    needed: str
    count: int
    product_id: int
    product: str
    brand: str
    pack: str
    price: float
    line_total: float
    rating: float | None
    match: float               # vector similarity of the chosen product
    rrf: float
    sub_category: str
    why: str                   # how it was chosen (mode, pack coverage, budget switch)
    alternatives: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Unmatched:
    label: str
    reason: str
    closest: str | None = None


@dataclass
class Cart:
    mode: Mode
    lines: list[CartLine]
    unmatched: list[Unmatched]
    excluded: list[str]
    results: list[tuple[SubQuery, SearchResult]]   # evidence for every searched item
    budget: float | None = None
    budget_switched: bool = False   # Best match / Top rated went over budget → re-picked cheapest
    latency_ms: float = 0.0

    @property
    def total(self) -> float:
        return round(sum(line.line_total for line in self.lines), 2)

    @property
    def fits_budget(self) -> bool | None:
        return None if self.budget is None else self.total <= self.budget

    def to_rows(self) -> list[dict[str, Any]]:
        """Table rows for the UI / CSV download."""
        return [{
            "Item": ln.label, "Needed": ln.needed, "Product": ln.product, "Brand": ln.brand,
            "Pack": ln.pack, "Qty": ln.count, "Price (₹)": ln.price, "Line total (₹)": ln.line_total,
            "Rating": ln.rating, "Match": round(ln.match, 2), "Product ID": f"P{ln.product_id}",
        } for ln in self.lines]


# --- product choice ---------------------------------------------------------------------

def _pack(meta: dict[str, Any]) -> str:
    if "pack_unit" not in meta:
        return "size not listed"
    if "pack_qty" not in meta:
        return str(meta["pack_unit"])
    return f"{meta['pack_qty']:g} {meta['pack_unit']}"


def relevant_hits(sq: SubQuery, result: SearchResult) -> tuple[list[Hit], str | None]:
    """Hits that clear the similarity bar and, for food, aren't a processed form of a raw
    ingredient (the guard is food-only: "bathing soap" → "Bathing Bar" is fine).

    Returns (hits, reason-if-empty).
    """
    above = [h for h in result.hits if (h.vector_sim or 0) >= config.ITEM_MATCH_SIM]
    if not above:
        return [], f"no product above similarity {config.ITEM_MATCH_SIM} (best {result.best_vector_sim:.2f})"
    if sq.domain != "food":
        # must share at least one specific word ("tortoise food mix" ≠ "Adult Dry Cat Food"),
        # then prefer products of the right type; keep all overlapping ones if none name it
        above = [h for h in above if shares_content_word(sq.text, h.metadata)]
        if not above:
            return [], "no product mentions the item (closest matches were a different thing)"
        typed = [h for h in above if has_head_noun(sq.text, h.metadata)]
        return (typed or above), None
    ok = [h for h in above
          if not processed_mismatch(sq.text, h.metadata["product"], h.metadata.get("sub_category", ""))]
    if not ok:
        word = processed_mismatch(sq.text, above[0].metadata["product"], above[0].metadata.get("sub_category", ""))
        return [], f"only processed products found ('{word}', e.g. {above[0].metadata['product']})"
    return ok, None


_BASE = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000)}


def pick(sq: SubQuery, hits: list[Hit], mode: Mode) -> tuple[Hit, str]:
    """Choose one product among relevant hits according to the mode.

    Best match = highest RRF; Budget = cheapest of the top MODE_POOL; Top rated = highest
    rating of the top MODE_POOL (unrated last). If pack sizes are known and the recipe
    quantity is in g/kg/ml/l, packs that cover the quantity are preferred.
    """
    pool = hits[: config.MODE_POOL]
    why = ""
    if sq.quantity and sq.unit in _BASE:
        base_unit, factor = _BASE[sq.unit]
        need = sq.quantity * factor
        covering = [h for h in pool if h.metadata.get("pack_base_unit") == base_unit
                    and h.metadata.get("pack_base_qty", 0) >= need]
        if covering:
            pool = sorted(covering, key=lambda h: h.metadata["pack_base_qty"])
            why = f"pack covers {sq.needed}; "
    if mode == "Budget":
        chosen = min(pool, key=lambda h: h.metadata["sale_price"])
        return chosen, why + f"cheapest of top {len(pool)} relevant"
    if mode == "Top rated":
        chosen = max(pool, key=lambda h: (h.metadata.get("rating", -1), -h.metadata["sale_price"]))
        rated = "rating" in chosen.metadata
        return chosen, why + (f"highest rated of top {len(pool)}" if rated else "no rated option; best match")
    return pool[0], why + "best match (highest fused rank)"


def _line(sq: SubQuery, hit: Hit, why: str, others: list[Hit]) -> CartLine:
    m = hit.metadata
    return CartLine(
        label=sq.label, needed=sq.needed, count=max(sq.count, 1), product_id=int(m["product_id"]),
        product=m["product"], brand=m["brand"], pack=_pack(m), price=float(m["sale_price"]),
        line_total=round(float(m["sale_price"]) * max(sq.count, 1), 2), rating=m.get("rating"),
        match=hit.vector_sim or 0.0, rrf=hit.rrf_score, sub_category=m["sub_category"], why=why,
        alternatives=[{"product": o.metadata["product"], "brand": o.metadata["brand"],
                       "price": o.metadata["sale_price"], "rating": o.metadata.get("rating"),
                       "match": o.vector_sim} for o in others if o is not hit][:3],
    )


def _assemble(plan_items: list[tuple[SubQuery, SearchResult]], mode: Mode) -> tuple[list[CartLine], list[Unmatched]]:
    lines, unmatched = [], []
    for sq, res in plan_items:
        hits, reason = relevant_hits(sq, res)
        if not hits:
            closest = res.best.metadata["product"] if res.best else None
            unmatched.append(Unmatched(sq.label, reason or "no match", closest))
            continue
        chosen, why = pick(sq, hits, mode)
        lines.append(_line(sq, chosen, why, hits))
    return lines, unmatched


def build_cart(plan: Plan, mode: Mode | None = None) -> Cart:
    """Search every active sub-query (one batched embedding call) and pick products.

    Mode precedence: explicit argument (sidebar) → mode stated in the message → Best match.
    If a budget is set and the cart exceeds it, products are re-picked in Budget mode
    (no new retrieval) and the switch is reported.
    """
    t0 = time.perf_counter()
    mode = mode or plan.mode or "Best match"
    active = plan.active
    results = search_items([ItemSpec(s.text, s.domain, s.sub_categories, s.brand) for s in active])
    pairs = list(zip(active, results))
    lines, unmatched = _assemble(pairs, mode)
    cart = Cart(mode=mode, lines=lines, unmatched=unmatched, excluded=[s.label for s in plan.excluded],
                results=pairs, budget=plan.budget)
    if plan.budget is not None and cart.total > plan.budget and mode != "Budget":
        cheap_lines, cheap_unmatched = _assemble(pairs, "Budget")
        cart.lines, cart.unmatched, cart.mode, cart.budget_switched = cheap_lines, cheap_unmatched, "Budget", True
    cart.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return cart


# --- catalogue path ---------------------------------------------------------------------

@dataclass
class CatalogueResult:
    sub_query: SubQuery
    result: SearchResult
    products: list[Hit]           # relevant, constraint-filtered, sorted
    dropped_by_constraints: int


def retrieve_catalogue(plan: Plan, k: int = config.TOP_K) -> list[CatalogueResult]:
    """Per sub-query: hybrid search over a wider pool, keep relevant hits, then apply
    max_price / min_rating filters and the requested sort."""
    out = []
    c = plan.constraints
    for sq in plan.active:
        res = search_item(sq.text, sq.domain, k=20, brand=sq.brand)
        if sq.brand and not res.hits:  # brand filter found nothing: say so, search without it
            res = search_item(sq.text, sq.domain, k=20)
        hits = [h for h in res.hits if (h.vector_sim or 0) >= config.ITEM_MATCH_SIM]
        before = len(hits)
        if c.get("max_price") is not None:
            hits = [h for h in hits if h.metadata["sale_price"] <= float(c["max_price"])]
        if c.get("min_rating") is not None:
            hits = [h for h in hits if h.metadata.get("rating", 0) >= float(c["min_rating"])]
        if c.get("sort") == "price_asc":
            hits.sort(key=lambda h: h.metadata["sale_price"])
        elif c.get("sort") == "price_desc":
            hits.sort(key=lambda h: -h.metadata["sale_price"])
        elif c.get("sort") == "rating_desc":
            hits.sort(key=lambda h: -h.metadata.get("rating", -1))
        res.hits = res.hits[:k]  # keep the evidence list readable
        out.append(CatalogueResult(sq, res, hits[:k], before - len(hits)))
    return out
