"""Phase 8 evaluation: end-to-end questions, retrieval ablation and a threshold re-check.

    python -m eval.run_eval                 # everything, LLM cache allowed
    python -m eval.run_eval --fresh         # no cache reads (real LLM latency), paced for Groq's 8k TPM
    python -m eval.run_eval --only Q3,Q8    # a subset of questions (ablation/probe still run unless skipped)
    python -m eval.run_eval --skip-questions --skip-probe   # ablation only (no LLM calls)

Writes eval/results.md (the report) and eval/results.json (raw rows for failure analysis).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

import config
from rag.cart import retrieve_catalogue
from rag.decompose import decompose
from rag.fallback import weak_retrieval
from rag.llm import describe
from rag.pipeline import BotResponse, answer
from rag.retriever import (
    NO_FILTER, get_index, hybrid_search, infer_domain, search_item, vector_search, warm_up,
)
from rag.synonyms import expand_query

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS = EVAL_DIR / "test_questions.yaml"
RESULTS_MD = EVAL_DIR / "results.md"
RESULTS_JSON = EVAL_DIR / "results.json"
_CITE = re.compile(r"\[P(\d+)\]")
_RUPEE = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")


# --- end-to-end questions -------------------------------------------------------------

def _retrieved(resp: BotResponse) -> list[dict[str, Any]]:
    """Products the answer could draw on: cart lines, or catalogue products per sub-query."""
    if resp.cart is not None and resp.cart.lines:
        by_id = {h.metadata["product_id"]: h.metadata for _, r in resp.cart.results for h in r.hits}
        return [by_id[ln.product_id] for ln in resp.cart.lines]
    out: list[dict[str, Any]] = []
    lists = [[h.metadata for h in cr.products] for cr in (resp.catalogue or [])]
    for i in range(max((len(x) for x in lists), default=0)):   # interleave so top-3 covers each sub-query
        out += [x[i] for x in lists if i < len(x)]
    return out


def _faithful(resp: BotResponse, products: list[dict[str, Any]]) -> tuple[str, str]:
    """Independent of the in-pipeline guard: citations ⊆ retrieved, cited prices match metadata."""
    if resp.kind == "fallback":
        return "n/a", ""
    ids = {int(p["product_id"]): p for p in products}
    cites = [int(c) for c in _CITE.findall(resp.text)]
    problems = [f"P{c} not retrieved" for c in cites if c not in ids]
    for sent in re.split(r"(?<=[.;\n])\s+", resp.text):
        sc = {int(c) for c in _CITE.findall(sent)}
        if len(sc) != 1 or next(iter(sc)) not in ids:
            continue
        p = ids[next(iter(sc))]
        allowed = {round(float(p["sale_price"]), 2), round(float(p.get("market_price") or 0), 2)}
        if resp.cart is not None:
            allowed |= {round(ln.line_total, 2) for ln in resp.cart.lines} | {round(resp.cart.total, 2)}
        for amt in _RUPEE.findall(sent):
            if round(float(amt.replace(",", "")), 2) not in allowed:
                problems.append(f"₹{amt} ≠ P{p['product_id']} price")
    if not cites and resp.kind != "suggestion":
        problems.append("no citations")
    return ("Y" if not problems else "N"), "; ".join(problems)


def _category_check(q: dict[str, Any], products: list[dict[str, Any]]) -> str:
    subs = [p.get("sub_category", "") for p in products]
    notes = []
    if q.get("expect_sub_categories"):
        ok = sum(s in q["expect_sub_categories"] for s in subs)
        notes.append(f"{ok}/{len(subs)} in expected")
    if q.get("forbid_sub_categories"):
        bad = [s for s in subs if s in q["forbid_sub_categories"]]
        notes.append(f"{len(bad)} forbidden")
    return ", ".join(notes)


def run_question(q: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    resp = answer(q["question"], mode=q.get("mode"), default_servings=q.get("servings"))
    if resp.kind == "suggestion" and q.get("confirmed_items_first"):
        keep = [s.label for s in resp.plan.active][: q["confirmed_items_first"]]
        resp = answer(q["question"], mode=q.get("mode"), default_servings=q.get("servings"), confirmed_items=keep)
    latency = (time.perf_counter() - t0) * 1000
    products = _retrieved(resp)
    names = " | ".join(p["product"] for p in products).lower()
    kws = q.get("evidence_keywords") or []
    evidence = ("n/a" if q.get("expect_fallback") else
                "Y" if any(k.lower() in names for k in kws) else "N")
    faithful, faith_notes = _faithful(resp, products)
    fell_back = resp.kind == "fallback"
    row = {
        "id": q["id"], "type": q["type"], "question": q["question"],
        "top3": [p["product"] for p in products[:3]],
        "top3_sub": [p.get("sub_category") for p in products[:3]],
        "evidence": evidence, "answer": resp.text, "faithful": faithful, "faith_notes": faith_notes,
        "guard_ok": None if resp.guard is None else resp.guard.ok, "regenerated": resp.regenerated,
        "fallback_expected": bool(q.get("expect_fallback")), "fallback_actual": fell_back,
        "fallback_trigger": resp.fallback_trigger, "fallback_reason": resp.fallback_reason,
        "category_check": _category_check(q, products), "latency_ms": round(latency),
        "llm_calls": resp.llm_calls, "intent": resp.plan.intent, "trace": resp.plan.trace,
        "kind": resp.kind,
    }
    if resp.cart is not None:
        row["cart_total"] = resp.cart.total
        row["cart_lines"] = [(ln.label, ln.product, ln.price, ln.sub_category) for ln in resp.cart.lines]
        row["unmatched"] = [(u.label, u.reason) for u in resp.cart.unmatched]
        row["excluded"] = resp.cart.excluded
        row["fits_budget"] = resp.cart.fits_budget
    if resp.plan.recipe:
        row["recipe"] = resp.plan.recipe["name"]
    return row


# --- ablation --------------------------------------------------------------------------

def _relevant(meta: dict[str, Any], label: dict[str, Any]) -> bool:
    if meta.get("sub_category") not in label["sub_categories"]:
        return False
    names = label.get("name_any")
    return not names or any(n.lower() in meta.get("product", "").lower() for n in names)


def _metas_vector(q: str) -> list[dict[str, Any]]:
    idx = get_index("products")
    return [idx.metadatas[idx.row_of[cid]] for cid, _ in vector_search(expand_query(q), 5, NO_FILTER)]


def _metas_hybrid(q: str) -> list[dict[str, Any]]:
    return [h.metadata for h in hybrid_search(q, 5, NO_FILTER).hits]


def _metas_filtered(q: str, domain: str | None) -> list[dict[str, Any]]:
    return [h.metadata for h in search_item(q, domain, k=5, audit=False).hits]


def run_ablation(labels: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = get_index("products")
    support = {lab["query"]: sum(_relevant(m, lab) for m in idx.metadatas) for lab in labels}
    missing = [q for q, n in support.items() if n == 0]
    if missing:
        print("WARNING: labels with no relevant product in the catalogue:", missing)
    rows = []
    for lab in labels:
        q, dom = lab["query"], lab["domain"]
        inferred = infer_domain(q)[0]
        variants = {
            "a. vector only": _metas_vector(q),
            "b. hybrid (vector + BM25)": _metas_hybrid(q),
            "c. hybrid + category filter": _metas_filtered(q, dom),
            "d. hybrid + inferred domain": _metas_filtered(q, inferred),
        }
        for name, metas in variants.items():
            rows.append({
                "query": q, "domain": dom, "variant": name, "inferred_domain": inferred,
                "hit@5": int(any(_relevant(m, lab) for m in metas)),
                "rr": next((1 / i for i, m in enumerate(metas, 1) if _relevant(m, lab)), 0.0),
                "wrong_top1": int(bool(metas) and metas[0].get("domain") != dom),
                "wrong_share@5": (sum(m.get("domain") != dom for m in metas) / len(metas)) if metas else 0.0,
                "top1": metas[0]["product"] if metas else "",
                "top1_sub": metas[0].get("sub_category", "") if metas else "",
            })
    df = pd.DataFrame(rows)
    summary = df.groupby("variant", sort=False).agg(
        **{"hit@5": ("hit@5", "mean"), "MRR@5": ("rr", "mean"),
           "wrong-domain top-1": ("wrong_top1", "mean"), "wrong-domain share of top-5": ("wrong_share@5", "mean")})
    return df, summary


# --- threshold probe ---------------------------------------------------------------------

def run_probe(probe: dict[str, list[str]]) -> pd.DataFrame:
    """Best vector similarity the catalogue path sees for answerable vs unanswerable questions."""
    rows = []
    for group, qs in (("on", probe["answerable"]), ("off", probe["unanswerable"])):
        for q in qs:
            plan = decompose(q)
            if plan.intent == "out_of_scope":
                rows.append({"group": group, "query": q, "stopped_by": "scope rule", "best_sim": None})
                continue
            if plan.intent != "catalogue":
                rows.append({"group": group, "query": q, "stopped_by": f"routed to {plan.intent}", "best_sim": None})
                continue
            if not plan.active:
                rows.append({"group": group, "query": q, "stopped_by": "LLM scope check", "best_sim": None})
                continue
            res = retrieve_catalogue(plan)
            best = max((cr.result.best_vector_sim for cr in res), default=0.0)
            has_products = any(cr.products for cr in res)
            stop = ("threshold" if weak_retrieval(best, config.SIM_THRESHOLD)
                    else "relevance checks" if not has_products else "answered")
            rows.append({"group": group, "query": q, "stopped_by": stop, "best_sim": round(best, 3)})
    return pd.DataFrame(rows)


def _threshold_table(df: pd.DataFrame) -> pd.DataFrame:
    scored = df.dropna(subset=["best_sim"])
    out = []
    for t in (0.64, 0.66, 0.68, 0.70, 0.72, 0.74):
        on = scored[scored.group == "on"]
        off = scored[scored.group == "off"]
        out.append({"threshold": t, "answerable kept": f"{(on.best_sim >= t).sum()}/{len(on)}",
                    "unanswerable blocked": f"{(off.best_sim < t).sum()}/{len(off)}"})
    return pd.DataFrame(out)


# --- report --------------------------------------------------------------------------------

def _cell(text: Any, n: int = 220) -> str:
    s = " ".join(str(text).split())
    s = s if len(s) <= n else s[: n - 1] + "…"
    return s.replace("|", "\\|")


def _md_table(df: pd.DataFrame, floatfmt: str = ".2f") -> str:
    """Markdown table without the optional `tabulate` dependency."""
    def fmt(v: Any) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, float):
            return format(v, floatfmt)
        return _cell(v, 120)
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "---|" * len(df.columns)
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def write_report(rows: list[dict[str, Any]], ablation: tuple[pd.DataFrame, pd.DataFrame] | None,
                 probe: pd.DataFrame | None, args: argparse.Namespace) -> None:
    lines = ["# BasketBuddy evaluation results", "",
             f"Run: {time.strftime('%d %b %Y %H:%M')} · LLM: {describe()} (answers; decomposition uses the "
             f"fast model) · cache reads: {'off (--fresh)' if args.fresh else 'on'} · "
             f"SIM_THRESHOLD {config.SIM_THRESHOLD}", ""]
    if rows:
        n = len(rows)
        fb_ok = sum(r["fallback_expected"] == r["fallback_actual"] for r in rows)
        ans = [r for r in rows if not r["fallback_actual"]]
        ev = [r for r in rows if r["evidence"] != "n/a"]
        lat = [r["latency_ms"] for r in rows if not r["fallback_actual"]]
        lines += [
            "## 1. End-to-end questions", "",
            f"- Fallback behaviour as expected: **{fb_ok}/{n}**",
            f"- Evidence present (keyword check): **{sum(r['evidence'] == 'Y' for r in ev)}/{len(ev)}**",
            f"- Faithful (citations ⊆ retrieved, cited prices match metadata): "
            f"**{sum(r['faithful'] == 'Y' for r in ans)}/{len(ans)}** answered questions",
            f"- Latency of answered questions: median **{statistics.median(lat) / 1000:.1f} s**, "
            f"max {max(lat) / 1000:.1f} s" if lat else "- Latency: n/a",
            "", "Manual score: 1 = wrong or irrelevant, 2 = partly right, 3 = correct and relevant. "
            "**Fill in by hand.**", "",
            "| ID | Type | Question | Top-3 retrieved | Evidence? | Answer | Faithful? | Fallback exp → act | "
            "Category check | Latency | LLM calls | Manual score (1–3) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            fb = f"{'Y' if r['fallback_expected'] else 'N'} → {'Y' if r['fallback_actual'] else 'N'}"
            if r["fallback_actual"] and r["fallback_trigger"]:
                fb += f" ({r['fallback_trigger']})"
            faith = r["faithful"] + (f" ({_cell(r['faith_notes'], 80)})" if r["faith_notes"] else "")
            lines.append(
                f"| {r['id']} | {r['type']} | {_cell(r['question'], 90)} | {_cell('; '.join(r['top3']), 160)} | "
                f"{r['evidence']} | {_cell(r['answer'], 260)} | {faith} | {fb} | {r['category_check']} | "
                f"{r['latency_ms'] / 1000:.1f} s | {r['llm_calls']} |  |")
        lines.append("")
    if ablation is not None:
        detail, summary = ablation
        nq = detail["query"].nunique()
        lines += [
            "## 2. Retrieval ablation", "",
            f"{nq} labelled sub-queries across all domains. A top-5 result is relevant when its sub-category "
            "is in the labelled set and (if given) its name contains a labelled word. "
            "Wrong-domain = the product's domain differs from the query's labelled domain.", "",
            _md_table(summary.reset_index(), ".3f"), "",
            "- (c) uses the labelled (gold) domain, as the cart path does: recipe ingredients are food and kit "
            "items carry their domain. Its wrong-domain rate is 0 by construction; the measured gain is in hit@5.",
            "- (d) is the catalogue path: the domain is *inferred* from the question (purpose phrase, keyword "
            "rules, then a retrieval vote) and no filter is used when the vote isn't confident.", "",
            "<details><summary>Per-query top-1 (b: hybrid, no filter vs c: hybrid + filter)</summary>", "",
        ]
        piv = detail[detail.variant.str[0].isin(["b", "c"])].pivot_table(
            index="query", columns="variant", values=["hit@5", "top1"], aggfunc="first", sort=False)
        piv.columns = [f"{v[:1]} {m}" for m, v in piv.columns]
        lines += [_md_table(piv.reset_index()[["query", "b hit@5", "b top1", "c hit@5", "c top1"]], ".0f"),
                  "", "</details>", ""]
    if probe is not None:
        on = probe[(probe.group == "on")]
        off = probe[(probe.group == "off")]
        lines += [
            "## 3. Re-checking SIM_THRESHOLD", "",
            f"Catalogue-style questions with an answer in the catalogue (`on`, {len(on)}) and without (`off`, "
            f"{len(off)}), run through the real catalogue path (decompose → retrieve_catalogue). Best vector "
            f"similarity per question; `stopped_by` is the first check that stopped it.", "",
            f"- `on` best similarity: min **{on.best_sim.min():.3f}**, mean {on.best_sim.mean():.3f}",
            f"- `off` best similarity: max **{off.best_sim.max():.3f}**, mean {off.best_sim.mean():.3f}"
            f" ({off.best_sim.isna().sum()} stopped before retrieval)", "",
            _md_table(_threshold_table(probe)), "",
            "<details><summary>Per-question similarities</summary>", "",
            _md_table(probe, ".3f"), "", "</details>", "",
        ]
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh", action="store_true", help="don't read the LLM cache (answers are still written)")
    ap.add_argument("--pace", type=float, default=None, help="seconds between questions (default 20 with --fresh)")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--skip-questions", action="store_true")
    ap.add_argument("--skip-ablation", action="store_true")
    ap.add_argument("--skip-probe", action="store_true")
    args = ap.parse_args()
    if args.fresh:
        config.LLM_CACHE_READ = False
    pace = args.pace if args.pace is not None else (20.0 if args.fresh else 0.0)
    spec = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))

    print("warm-up:", warm_up())
    rows: list[dict[str, Any]] = []
    if not args.skip_questions:
        only = {s.strip() for s in args.only.split(",") if s.strip()}
        qs = [q for q in spec["questions"] if not only or q["id"] in only]
        for i, q in enumerate(qs):
            if i and pace:
                time.sleep(pace)   # Groq free tier: 8k tokens/min; bursts trigger silent SDK retries
            row = run_question(q)
            rows.append(row)
            print(f"{row['id']:4} {row['kind']:10} ev={row['evidence']:3} faithful={row['faithful']:3} "
                  f"fb {row['fallback_expected']}->{row['fallback_actual']} {row['latency_ms'] / 1000:5.1f}s "
                  f"{row['category_check']}")
    ablation = None if args.skip_ablation else run_ablation(spec["ablation"])
    if ablation is not None:
        print(ablation[1].to_string())
    config.LLM_CACHE_READ = True   # the probe measures retrieval similarity, not LLM latency
    probe = None if args.skip_probe else run_probe(spec["threshold_probe"])
    if probe is not None:
        print(_threshold_table(probe).to_string(index=False))
    write_report(rows, ablation, probe, args)
    RESULTS_JSON.write_text(json.dumps({
        "questions": rows,
        "ablation": None if ablation is None else ablation[0].to_dict("records"),
        "probe": None if probe is None else probe.to_dict("records"),
    }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {RESULTS_MD.relative_to(config.ROOT)} and {RESULTS_JSON.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
