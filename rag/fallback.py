"""Insufficient-context fallback: when to refuse, and what to say.

Triggers (any one):
  1. intent is out_of_scope (stock, delivery, other apps, medical advice, unrelated);
  2. retrieval is too weak: the best hybrid hit's vector similarity < SIM_THRESHOLD,
     or nothing at all could be matched (no recipe, empty cart);
  3. the LLM itself replies with the insufficient-context sentinel;
  4. (guard) the answer still cites unknown products or wrong prices after one regeneration.
"""
from __future__ import annotations

FALLBACK_MESSAGE = "I can't answer that from the BigBasket catalogue data I have."
CAPABILITIES = (
    "I can build a cart for a dish or a need (e.g. *paneer butter masala for 4*, *deep-clean my bathroom*), "
    "or look up and compare BigBasket products by price and rating."
)
SENTINEL = "INSUFFICIENT_CONTEXT"


def fallback_text(reason: str | None = None) -> str:
    """The fallback message, an optional one-line reason, and what the bot can do."""
    lines = [FALLBACK_MESSAGE]
    if reason:
        why = reason.strip().rstrip(".")   # LLM-written reasons often end with a full stop already
        lines.append(f"_Why: {why[0].lower() + why[1:]}._")
    lines.append(CAPABILITIES)
    return "\n\n".join(lines)


def is_sentinel(text: str) -> bool:
    """True if the LLM said the context is insufficient (alone or as the first word)."""
    return text.strip().strip("*`\"' ").upper().startswith(SENTINEL)


def weak_retrieval(best_similarity: float, threshold: float) -> str | None:
    """Reason string if retrieval is too weak to answer from, else None."""
    if best_similarity < threshold:
        return (f"the closest catalogue match has similarity {best_similarity:.2f}, "
                f"below the {threshold:.2f} needed to answer from the data")
    return None
