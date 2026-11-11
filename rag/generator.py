"""Grounded answer generation: CONTEXT + question → LLM → faithfulness guard.

- Context lists every product the answer may mention, each tagged [P<id>], with
  its price, MRP, rating, category and a description snippet. Cart totals are
  computed in Python (rag.cart) and given to the LLM as facts to quote.
- The system prompt restricts the model to the context and requires citations.
- verify_answer() checks every [P<id>] is in the context and every ₹ amount
  matches the data (a cited product's price/MRP, or a cart/line total or budget).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import config
from rag.cart import Cart, CatalogueResult
from rag.decompose import Plan
from rag.fallback import SENTINEL
from rag.llm import LLMResponse, complete

SYSTEM_PROMPT = f"""You are BasketBuddy, a shopping assistant for BigBasket (India).
Answer ONLY from the CONTEXT below. It is a static snapshot of BigBasket's catalogue.

Rules:
1. Every product, brand, price and rating you mention must appear in the CONTEXT. Cite each product
   right after you mention it as [P<id>], using the id shown in the CONTEXT (e.g. [P1234]).
2. Quote prices exactly as written in the CONTEXT (₹ and the number). Never calculate a new total or
   discount; if a total is given in the CONTEXT, quote it as is.
3. Never state a pack size, weight or volume unless it is written in that product's CONTEXT line;
   the user's question is not a source for sizes.
4. Never say anything about stock, availability, delivery or current prices; the data is a snapshot.
5. No medical, health or skin claims beyond what a product description in the CONTEXT says.
6. If the CONTEXT answers part of the question, answer that part and say plainly which part is not in
   the data. Only if NOTHING in the CONTEXT answers the question, reply with exactly {SENTINEL}
   and nothing else.
7. Be brief: under 120 words. Plain sentences or a short bullet list; no tables (the app shows them)."""

CART_INSTRUCTIONS = """Write 2-4 sentences introducing this cart. Describe where the item list came from using
the wording of the "List source" line exactly (do not call it AI-generated unless that line says so).
Highlight 2-3 products with their citation and price, state the cart total exactly as given, and
mention any items under "Couldn't find" or "Excluded" (skip this if there are none). Do not list every
item; the app shows the full table."""

CATALOGUE_INSTRUCTIONS = """Answer the question directly, showing at most 3 products per thing asked about.
For a comparison, compare on what was asked (price, rating, ...) and cite each product. If the same
product name appears at several prices, say that the dataset does not record pack sizes and list those
variants with their prices. If something asked about found no product, say it isn't in the data."""


# --- context ------------------------------------------------------------------------

def _fmt_price(x: float) -> str:
    return f"₹{x:,.2f}".rstrip("0").rstrip(".") if x % 1 else f"₹{x:,.0f}"


def product_line(meta: dict[str, Any], extra: str = "") -> str:
    """'[P123] Name | Brand: X | Cat > Sub | Price ₹50 (MRP ₹60) | Rating 4.2/5 | Pack ... | desc'."""
    rating = f"{meta['rating']:g}/5" if "rating" in meta else "not rated"
    pack = f" | Pack: {meta['pack_qty']:g} {meta['pack_unit']}" if "pack_qty" in meta and "pack_unit" in meta else ""
    line = (f"[P{meta['product_id']}] {meta['product']} | Brand: {meta['brand']} | "
            f"{meta['category']} > {meta['sub_category']} | Price {_fmt_price(meta['sale_price'])} "
            f"(MRP {_fmt_price(meta['market_price'])}) | Rating {rating}{pack}")
    if extra:
        line += f" | {extra}"
    return line


def _snippet(text: str) -> str:
    """Description part of a chunk (after the header fields), trimmed."""
    desc = text.split(" | ")[-1]
    return desc[: config.CONTEXT_DESC_CHARS].rsplit(" ", 1)[0] + ("…" if len(desc) > config.CONTEXT_DESC_CHARS else "")


@dataclass
class Context:
    """The text given to the LLM plus everything the guard needs to check the answer."""

    text: str
    products: dict[int, dict[str, Any]] = field(default_factory=dict)   # id → metadata
    extra_amounts: set[float] = field(default_factory=set)              # totals, line totals, budget
    size_evidence: list[str] = field(default_factory=list)              # recipe quantities ("200 g")


def cart_context(plan: Plan, cart: Cart) -> Context:
    ctx = Context("")
    lines = [f"Request: {plan.message}"]
    if plan.recipe:
        r = plan.recipe
        serv = f"serves {r['servings']}" if r["servings"] else "servings not stated"
        scaled = f"; quantities scaled ×{r['scale']} for {plan.servings} people" if r["scale"] != 1 else ""
        lines.append(f"List source: ingredients of the recipe '{r['name']}' from the Archana's Kitchen "
                     f"recipe dataset ({serv}{scaled})")
    elif plan.kit:
        lines.append(f"List source: the '{plan.kit['name']}' kit, a synthetic item list written by the "
                     "BasketBuddy team (products and prices are real BigBasket data)")
    elif plan.ai_generated:
        lines.append("List source: an AI-generated suggestion that the user reviewed and confirmed")
    elif plan.intent == "recipe_text":
        lines.append("List source: ingredients extracted from the recipe the user pasted")
    lines.append(f"Selection mode: {cart.mode}"
                 + (" (switched to Budget because the cart was over budget)" if cart.budget_switched else ""))
    lines.append("Cart (chosen by the app; prices and totals are exact):")
    for ln in cart.lines:
        meta = next(h.metadata for sq, res in cart.results for h in res.hits
                    if h.metadata.get("product_id") == ln.product_id)
        ctx.products[ln.product_id] = meta
        qty = f" × {ln.count} = {_fmt_price(ln.line_total)}" if ln.count > 1 else ""
        need = f" (needed: {ln.needed})" if ln.needed else ""
        ctx.size_evidence.append(ln.needed)
        lines.append("- for " + ln.label + need + ": " + product_line(meta) + qty)
        ctx.extra_amounts.add(round(ln.line_total, 2))
    lines.append(f"CART TOTAL: {_fmt_price(cart.total)} for {len(cart.lines)} products")
    ctx.extra_amounts.add(round(cart.total, 2))
    if cart.budget is not None:
        verdict = "fits within" if cart.fits_budget else "is OVER"
        lines.append(f"Budget: {_fmt_price(cart.budget)}; the cart {verdict} the budget")
        ctx.extra_amounts.add(round(cart.budget, 2))
    if cart.unmatched:
        lines.append("Couldn't find on BigBasket: " + "; ".join(f"{u.label} ({u.reason})" for u in cart.unmatched))
    if cart.excluded:
        lines.append("Excluded (user already has): " + ", ".join(cart.excluded))
    lines.append("Note: pack sizes are mostly not recorded in this dataset.")
    ctx.text = "\n".join(lines)
    return ctx


def catalogue_context(plan: Plan, results: list[CatalogueResult]) -> Context:
    ctx = Context("")
    lines = [f"Question: {plan.message}"]
    if plan.constraints:
        lines.append(f"Filters applied by the app: {plan.constraints}")
    # the user's own price limit ("under ₹300") may be quoted back
    for amt in (plan.constraints.get("max_price"), plan.budget):
        if amt is not None:
            ctx.extra_amounts.add(round(float(amt), 2))
    for cr in results:
        sq = cr.sub_query
        head = f"Products for '{sq.text}'" + (f" (brand {sq.brand})" if sq.brand else "")
        if not cr.products:
            lines.append(f"{head}: none found in the data"
                         + (f" ({cr.dropped_by_constraints} matched but failed the filters)" if cr.dropped_by_constraints else ""))
            continue
        lines.append(f"{head}:")
        for h in cr.products:
            meta = h.metadata
            ctx.products[int(meta["product_id"])] = meta
            lines.append("- " + product_line(meta) + f" | {_snippet(h.text)}")
    # The question names a size ("atta 5 kg") that no retrieved product records: say so explicitly,
    # otherwise the model copies the size onto a product and the guard rejects the answer (Phase 8, Q2).
    known = {_size_key(q, u) for m in ctx.products.values()
             for q, u in _SIZE_RE.findall(f"{m['product']} {m.get('pack_qty', '')} {m.get('pack_unit', '')}")}
    asked = [f"{q} {u}" for q, u in _SIZE_RE.findall(plan.message) if _size_key(q, u) not in known]
    if asked and ctx.products:
        lines.append(f"Note: the question asks about {', '.join(dict.fromkeys(asked))}, but none of these products "
                     "records a pack size. Do not attach that size to any product; say in a separate sentence "
                     "(without citations or prices) that the dataset doesn't record pack sizes.")
    names = [m["product"] for m in ctx.products.values()]
    if len(names) != len(set(names)):
        lines.append("Note: some products appear at several prices with the same name; the dataset does not "
                     "record pack sizes, so the variants can't be told apart by size.")
    ctx.text = "\n".join(lines)
    return ctx


# --- generation ---------------------------------------------------------------------

def generate(ctx: Context, instructions: str, correction: str | None = None) -> LLMResponse:
    """One answer-model call. ``correction`` is added on the single regeneration attempt."""
    user = f"CONTEXT\n{ctx.text}\n\nTASK\n{instructions}"
    if correction:
        user += f"\n\nYOUR PREVIOUS ANSWER HAD PROBLEMS, FIX THEM:\n{correction}"
    return complete(SYSTEM_PROMPT, user, fast=False, max_tokens=config.ANSWER_MAX_TOKENS)


# --- faithfulness guard ------------------------------------------------------------------

_CITE_RE = re.compile(r"\[P(\d+)\]")
_RUPEE_RE = re.compile(r"(?:₹|Rs\.?\s?|INR\s?)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
_SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(kg|g|gm|grams?|ml|l|ltr|litres?|liters?)\b", re.I)
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
# Models write citations as [P1], [ P1 ], 【P1】, [​P1] (zero-width space) or [p1]; normalise to [P1].
_LOOSE_CITE_RE = re.compile(r"[\[【]\s*[Pp]\s*(\d+)\s*[\]】]")


def normalise_citations(text: str) -> str:
    """Strip invisible characters and rewrite every citation variant as [P<id>]."""
    return _LOOSE_CITE_RE.sub(lambda m: f"[P{m.group(1)}]", text.translate(_INVISIBLE))


def _size_key(qty: str, unit: str) -> str:
    unit = unit.lower()
    unit = {"gm": "g", "gram": "g", "grams": "g", "ltr": "l", "litre": "l", "litres": "l",
            "liter": "l", "liters": "l"}.get(unit, unit)
    return f"{float(qty):g}{unit}"


@dataclass
class GuardResult:
    ok: bool
    problems: list[str]
    citations: list[int]


def _amounts(text: str) -> list[float]:
    return [round(float(m.replace(",", "")), 2) for m in _RUPEE_RE.findall(text)]


def verify_answer(answer: str, ctx: Context, require_citation: bool = True) -> GuardResult:
    """Check citations and prices against the context.

    1. Every [P<id>] must be a product in the context.
    2. Every ₹ amount must equal a sale price / MRP of a product in the context, or a
       total / line total / budget computed by the app.
    3. In a sentence citing exactly one product, any ₹ amount must be that product's
       sale price or MRP (or an app-computed total).
    4. If the context has products, the answer must cite at least one.
    5. Pack sizes ("5 kg", "1 L") may only appear if some product in the context has them.
    6. The insufficient-context sentinel may only be the whole reply, never embedded.
    """
    problems: list[str] = []
    answer = normalise_citations(answer)
    cited = [int(x) for x in _CITE_RE.findall(answer)]
    if re.search(r"INSUFFICIENT[\s_]*CONTEXT", answer, re.I):
        problems.append(f"don't embed {SENTINEL} in an answer; say which part isn't in the data instead")
    # Sizes may come from product names / pack fields or recipe quantities, never from the
    # user's question or the sub-query text: "atta 5 kg" in the question is not evidence.
    evidence = " ".join(
        [f"{m['product']} {m.get('pack_qty', '')} {m.get('pack_unit', '')}" for m in ctx.products.values()]
        + ctx.size_evidence
    )
    known_sizes = {_size_key(q, u) for q, u in _SIZE_RE.findall(evidence)}
    # An unknown size is a problem when it is attached to a product (the sentence cites one or
    # quotes a price). "The data doesn't record whether these are 5 kg packs" is allowed.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        if not (_CITE_RE.search(sentence) or _RUPEE_RE.search(sentence)):
            continue
        for q, u in _SIZE_RE.findall(sentence):
            if _size_key(q, u) not in known_sizes:
                problems.append(f"pack size '{q} {u}' is not in the product data")
    for pid in dict.fromkeys(cited):
        if pid not in ctx.products:
            problems.append(f"[P{pid}] is not one of the retrieved products")

    known = set(ctx.extra_amounts)
    for m in ctx.products.values():
        known |= {round(float(m["sale_price"]), 2), round(float(m["market_price"]), 2)}
    for amt in _amounts(answer):
        if amt not in known:
            problems.append(f"₹{amt:g} does not match any price or total in the data")

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        ids = {int(x) for x in _CITE_RE.findall(sentence)}
        amts = _amounts(sentence)
        if len(ids) == 1 and amts:
            pid = next(iter(ids))
            if pid in ctx.products:
                m = ctx.products[pid]
                ok_prices = {round(float(m["sale_price"]), 2), round(float(m["market_price"]), 2)} | ctx.extra_amounts
                if not any(a in ok_prices for a in amts):
                    problems.append(f"price quoted for [P{pid}] doesn't match its data "
                                    f"(₹{m['sale_price']:g}); quoted {', '.join(f'₹{a:g}' for a in amts)}")

    if require_citation and ctx.products and not cited:
        problems.append("the answer mentions no product citation [P<id>]")
    return GuardResult(not problems, list(dict.fromkeys(problems)), list(dict.fromkeys(cited)))
