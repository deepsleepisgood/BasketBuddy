"""Edge-case battery: python -m eval.run_edge_cases [case numbers]. Prints OK/BAD per message with the cart or products."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.edge_cases import CASES  # noqa: E402

from rag.pipeline import answer  # noqa: E402

only = set(sys.argv[1:])
bad = []
for i, (msg, exp) in enumerate(CASES):
    if only and str(i) not in only:
        continue
    t = time.perf_counter()
    try:
        r = answer(msg)
        if r.kind == "suggestion":   # accept the AI list as a user would
            r = answer(msg, confirmed_items=[s.label for s in r.plan.active])
    except Exception as e:  # noqa: BLE001
        print(f"[{i}] EXCEPTION {msg!r}: {type(e).__name__}: {e}")
        bad.append(i)
        continue
    kind = r.kind
    ok = (exp == "any-answer" and kind in ("cart", "catalogue")) or kind == exp or (exp == "fallback" and kind == "help")
    detail = ""
    if r.cart is not None and r.cart.lines:
        detail = (f"recipe={r.plan.recipe['name'] if r.plan.recipe else None} kit={r.plan.kit['name'] if r.plan.kit else None} "
                  f"ai={r.plan.ai_generated} ₹{r.cart.total} lines={[ (l.label, l.product) for l in r.cart.lines][:8]} "
                  f"unmatched={[u.label for u in r.cart.unmatched]}")
    elif r.catalogue:
        detail = "subs=" + str([(cr.sub_query.text, cr.sub_query.domain, [h.metadata['product'] for h in cr.products[:2]])
                                for cr in r.catalogue])
    print(f"[{i}] {'OK ' if ok else 'BAD'} exp={exp:10} got={kind:9} {r.fallback_trigger or ''} | {msg}")
    print(f"      reason={r.fallback_reason!r} {detail}")
    print(f"      text={r.text[:200]!r}  ({(time.perf_counter() - t):.1f}s, {r.llm_calls} LLM)")
    if not ok:
        bad.append(i)
    time.sleep(7)
print("\nBAD:", bad)
