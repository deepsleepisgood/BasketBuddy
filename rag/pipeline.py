"""End to end: message → plan → retrieval (cart or catalogue) → grounded answer → guard.

`answer()` is the single entry point used by the Streamlit app and by eval/run_eval.py.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import config
from rag.cart import Cart, CatalogueResult, build_cart, retrieve_catalogue
from rag.decompose import Mode, Plan, decompose
from rag.fallback import CAPABILITIES, fallback_text, is_sentinel, weak_retrieval
from rag.generator import (
    CART_INSTRUCTIONS, CATALOGUE_INSTRUCTIONS, Context, GuardResult, cart_context, catalogue_context,
    generate, normalise_citations, verify_answer,
)
from rag.llm import LLMError

Kind = Literal["cart", "catalogue", "suggestion", "fallback", "help"]


@dataclass
class BotResponse:
    kind: Kind
    text: str
    plan: Plan
    cart: Cart | None = None
    catalogue: list[CatalogueResult] | None = None
    context: str = ""
    guard: GuardResult | None = None
    regenerated: bool = False
    fallback_trigger: str | None = None   # out_of_scope | weak_retrieval | no_match | llm_sentinel | guard | llm_error
    fallback_reason: str | None = None
    llm_model: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def llm_calls(self) -> int:
        return self.plan.llm_calls

    @property
    def needs_confirmation(self) -> bool:
        return self.kind == "suggestion"


def _fallback(plan: Plan, trigger: str, reason: str | None, **kw: Any) -> BotResponse:
    plan.trace.append(f"Fallback ({trigger}): {reason}")
    return BotResponse("fallback", fallback_text(reason), plan, fallback_trigger=trigger,
                       fallback_reason=reason, **kw)


def _grounded(plan: Plan, ctx: Context, instructions: str, resp_kw: dict[str, Any]) -> BotResponse:
    """Generate, guard, regenerate once on failure, else fall back."""
    kind: Kind = resp_kw.pop("kind")
    try:
        out = generate(ctx, instructions)
        plan.llm_calls += 1
        if is_sentinel(out.text):
            return _fallback(plan, "llm_sentinel", "the retrieved products don't contain the answer",
                             context=ctx.text, llm_model=out.model, **resp_kw)
        guard = verify_answer(out.text, ctx)
        regenerated = False
        if not guard.ok:
            plan.trace.append("Guard failed: " + "; ".join(guard.problems) + ". Regenerating once.")
            out = generate(ctx, instructions, correction="\n".join(f"- {p}" for p in guard.problems))
            plan.llm_calls += 1
            regenerated = True
            if is_sentinel(out.text):
                return _fallback(plan, "llm_sentinel", "the retrieved products don't contain the answer",
                                 context=ctx.text, llm_model=out.model, **resp_kw)
            guard = verify_answer(out.text, ctx)
            if not guard.ok:
                return _fallback(plan, "guard", "the generated answer couldn't be verified against the data ("
                                 + "; ".join(guard.problems) + ")", context=ctx.text, guard=guard,
                                 regenerated=True, llm_model=out.model, **resp_kw)
        plan.trace.append(f"Answer by {out.model}{' (cached)' if out.cached else ''}; guard passed, "
                          f"{len(guard.citations)} citation(s).")
        return BotResponse(kind, normalise_citations(out.text).strip(), plan, context=ctx.text, guard=guard,
                           regenerated=regenerated, llm_model=out.model, **resp_kw)
    except LLMError as e:
        # Degrade gracefully: carts are still fully grounded without the LLM summary.
        if kind == "cart":
            plan.trace.append(f"LLM unavailable ({e}); showing the cart without a written summary.")
            return BotResponse(kind, "Here's your cart (the written summary is unavailable right now).",
                               plan, context=ctx.text, **resp_kw)
        return _fallback(plan, "llm_error", f"the language model is unavailable ({e})", context=ctx.text, **resp_kw)


def answer(
    message: str,
    mode: Mode | None = None,
    extra_pantry: list[str] | None = None,
    default_servings: int | None = None,
    confirmed_items: list[str] | None = None,
) -> BotResponse:
    """Answer one message.

    mode: sidebar mode (overrides a mode stated in the message only when given).
    default_servings: sidebar servings, used when the message doesn't state any.
    confirmed_items: for an AI-generated need list, the items the user kept. When the
      plan's list is AI-generated and this is None, a "suggestion" is returned first
      so the user can tick/untick items before retrieval (as the brief requires).
    """
    t0 = time.perf_counter()
    plan = decompose(message, extra_pantry, default_servings)
    timings = {"decompose_ms": round((time.perf_counter() - t0) * 1000, 1)}

    if plan.intent == "smalltalk":
        timings["total_ms"] = timings["decompose_ms"]
        plan.trace.append("Small talk: replied with what BasketBuddy can do (no retrieval, no LLM).")
        return BotResponse("help", "Hi! I'm BasketBuddy. " + CAPABILITIES, plan, timings=timings)

    if plan.intent == "out_of_scope":
        timings["total_ms"] = timings["decompose_ms"]
        return _fallback(plan, "out_of_scope", plan.reason, timings=timings)

    if plan.intent in ("dish", "recipe_text", "need"):
        if not plan.sub_queries:
            timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return _fallback(plan, "no_match", plan.reason or "nothing to search for", timings=timings)
        if plan.ai_generated and confirmed_items is None:
            items = ", ".join(s.label for s in plan.active)
            timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return BotResponse("suggestion", f"**Suggested list (AI-generated), edit before adding:** {items}",
                               plan, timings=timings)
        if plan.ai_generated and confirmed_items is not None:
            # unticked items are dropped (not "excluded": that means the user already has them)
            keep = {c.lower() for c in confirmed_items}
            removed = [s.label for s in plan.sub_queries if s.label.lower() not in keep]
            plan.sub_queries = [s for s in plan.sub_queries if s.label.lower() in keep]
            if removed:
                plan.trace.append(f"User removed from the suggested list: {', '.join(removed)}")
            if not plan.sub_queries:
                return _fallback(plan, "no_match", "every suggested item was removed", timings=timings)
        t1 = time.perf_counter()
        cart = build_cart(plan, mode)
        timings["retrieve_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        if not cart.lines:
            timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return _fallback(plan, "no_match", "none of the items matched a BigBasket product", cart=cart,
                             timings=timings)
        t2 = time.perf_counter()
        resp = _grounded(plan, cart_context(plan, cart), CART_INSTRUCTIONS,
                         {"kind": "cart", "cart": cart, "timings": timings})
    else:
        t1 = time.perf_counter()
        results = retrieve_catalogue(plan)
        timings["retrieve_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        best = max((cr.result.best_vector_sim for cr in results), default=0.0)
        reason = weak_retrieval(best, config.SIM_THRESHOLD)
        if reason or not any(cr.products for cr in results):
            timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return _fallback(plan, "weak_retrieval", reason or "no product passed the relevance and filter checks",
                             catalogue=results, timings=timings)
        t2 = time.perf_counter()
        resp = _grounded(plan, catalogue_context(plan, results), CATALOGUE_INSTRUCTIONS,
                         {"kind": "catalogue", "catalogue": results, "timings": timings})
    timings["generate_ms"] = round((time.perf_counter() - t2) * 1000, 1)
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return resp
