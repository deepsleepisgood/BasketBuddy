"""BasketBuddy: Streamlit chat UI over the BigBasket catalogue.

Run:  .venv\\Scripts\\streamlit run app.py
Every answer goes through rag.pipeline.answer(); this file only renders.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st

import config
from rag.llm import describe
from rag.pipeline import BotResponse, answer
from rag.retriever import SearchResult, warm_up

st.set_page_config(page_title="BasketBuddy", page_icon="🧺", layout="wide")

MODES = ("Auto", "Best match", "Budget", "Top rated")   # Auto = mode stated in the message, else Best match
PANTRY_STAPLES = ["salt", "sugar", "cooking oil", "ghee", "turmeric powder", "red chilli powder", "cumin seeds",
                  "mustard seeds", "garam masala", "onion", "tomato", "ginger", "garlic", "rice", "atta"]
EXAMPLES = [
    "Dal makhani for 4, budget mode, I already have salt and ghee",
    "I want to deep-clean my bathroom this weekend",
    "Oily skin starter kit under ₹1000",
    "Compare Dove and Pears soaps",
    "jeera aur haldi kitne ka hai?",
    "Cheapest floor cleaner",
    "Coconut oil for hair",
    "Is paneer in stock right now?",
]
SYNTHETIC_KIT_NOTE = ("Synthetic kit list, authored by the BasketBuddy team. The products and prices are real "
                      "BigBasket data; the item list is not.")


# --- startup -------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _warm() -> dict[str, float]:
    """Load the embedding model and all three indexes once per server process."""
    return warm_up()


with st.spinner("Loading the embedding model and indexes (about 30 s on the first start)…"):
    WARM = _warm()

if "messages" not in st.session_state:
    st.session_state.messages = []   # {"role", "content"} or {"role", "resp", "request", "done"}


# --- sidebar -------------------------------------------------------------------------

with st.sidebar:
    st.title("🧺 BasketBuddy")
    st.caption("Turns a dish, a pasted recipe or a task into a BigBasket cart.")
    mode = st.radio("Product choice", MODES, help="Auto = follow the message (\"budget mode\", \"top rated\"), "
                    "else Best match. Best match = most relevant; Budget = cheapest of the top 5 relevant; "
                    "Top rated = highest rated of the top 5 relevant.")
    servings = st.number_input("Servings (used when the message doesn't say)", 1, 20, 4)
    pantry = st.multiselect("Already in my pantry", PANTRY_STAPLES)
    pantry_extra = st.text_input("Other pantry items (comma-separated)")
    show_details = st.toggle("Show retrieval details", value=False)
    st.divider()
    st.caption(f"LLM: **{describe()}**")
    st.caption(f"Retrieval: `{config.EMBED_MODEL}` + BM25, RRF (k={config.RRF_K})")
    st.caption("Warm-up: " + ", ".join(f"{k.removesuffix('_s')} {v:.1f}s" for k, v in WARM.items()))
    if st.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

PANTRY = pantry + [p.strip() for p in pantry_extra.split(",") if p.strip()]


# --- rendering helpers ------------------------------------------------------------------

_CITE = re.compile(r"\[P(\d+)\]")


def _md(text: str) -> str:
    """Show citations as code chips; escape '$' so Streamlit doesn't read it as LaTeX."""
    return _CITE.sub(r"`P\1`", text.replace("$", r"\$"))


def _rupees(x: float) -> str:
    return f"₹{x:,.2f}".replace(".00", "")


def _hits_table(res: SearchResult, chosen: set[int] | None = None) -> pd.DataFrame:
    rows = []
    for i, h in enumerate(res.hits, 1):
        m = h.metadata
        pid = m.get("product_id")
        rows.append({
            "#": i, "✓": "✓" if chosen and pid in chosen else "",
            "Doc": h.doc_key, "Name": m.get("product") or m.get("name"),
            "Sub-category": m.get("sub_category", ""), "Price": m.get("sale_price"),
            "Rating": m.get("rating"), "Vector rank": h.vector_rank,
            "Cosine": None if h.vector_sim is None else round(h.vector_sim, 3),
            "BM25 rank": h.bm25_rank, "RRF": round(h.rrf_score, 4),
        })
    return pd.DataFrame(rows)


def _filter_text(res: SearchResult) -> str:
    f = res.filter
    parts = [f"domain={f.domain}" if f.domain else "no domain filter"]
    if f.sub_categories:
        parts.append("sub-categories=" + ", ".join(f.sub_categories))
    if f.brand:
        parts.append(f"brand={f.brand}")
    text = "; ".join(parts)
    if res.relaxed_from is not None:
        text += " (sub-category hint dropped: best similarity was below " f"{config.SUBCAT_RELAX_SIM})"
    if res.wrong_domain and res.unfiltered_top is not None:
        text += f" ⚠ unfiltered top hit was in another domain: {res.unfiltered_top.metadata.get('product')}"
    return text


def render_evidence(resp: BotResponse) -> None:
    plan = resp.plan
    with st.expander("🔍 Evidence", expanded=False):
        st.markdown(f"**Intent:** `{plan.intent}` · **LLM calls:** {resp.llm_calls}"
                    + (f" · **Model:** `{resp.llm_model}`" if resp.llm_model else "")
                    + (f" · **Fallback trigger:** `{resp.fallback_trigger}`" if resp.fallback_trigger else "")
                    + (" · regenerated once after the guard" if resp.regenerated else ""))
        if resp.timings:
            st.caption(" · ".join(f"{k.removesuffix('_ms')} {v:,.0f} ms" for k, v in resp.timings.items()))
        st.markdown("**Trace**")
        st.markdown("\n".join(f"{i}. {_md(t)}" for i, t in enumerate(plan.trace, 1)) or "_(empty)_")
        if plan.recipe:
            r = plan.recipe
            st.markdown(f"**Matched recipe:** {r['name']} ({r['match']}), serves {r['servings'] or '?'}, "
                        f"scale ×{r['scale']} · [source]({r['url']})")
            if r["alternatives"]:
                st.caption("Other close recipes: " + "; ".join(r["alternatives"]))
            st.text(r["ingredients_raw"])
        if plan.constraints:
            st.markdown(f"**Filters:** `{plan.constraints}`")
        if resp.guard is not None:
            st.markdown(f"**Faithfulness guard:** {'passed' if resp.guard.ok else 'failed'}; "
                        f"citations {', '.join(f'P{c}' for c in resp.guard.citations) or 'none'}"
                        + (f"; problems: {'; '.join(resp.guard.problems)}" if resp.guard.problems else ""))
        pairs: list[tuple[str, SearchResult, set[int]]] = []
        if resp.cart is not None:
            chosen = {ln.product_id for ln in resp.cart.lines}
            pairs = [(sq.label, res, chosen) for sq, res in resp.cart.results]
        elif resp.catalogue:
            pairs = [(cr.sub_query.text, cr.result, {int(h.metadata["product_id"]) for h in cr.products})
                     for cr in resp.catalogue]
        for label, res, chosen in pairs:
            st.markdown(f"**{label}** → query `{res.expanded_query}` · {_filter_text(res)} · "
                        f"{res.latency_ms:.0f} ms")
            st.dataframe(_hits_table(res, chosen), hide_index=True, use_container_width=True)
        if resp.context:
            st.markdown("**Context sent to the LLM**")
            st.code(resp.context, language=None, wrap_lines=True)


def render_cart(resp: BotResponse, idx: int) -> None:
    cart = resp.cart
    plan = resp.plan
    if plan.kit:
        st.info(f"**{plan.kit['name']}**: {SYNTHETIC_KIT_NOTE}", icon="📝")
    if plan.ai_generated:
        st.warning("This item list was AI-generated and confirmed by you; products and prices are BigBasket data.",
                   icon="🤖")
    if plan.recipe:
        r = plan.recipe
        st.caption(f"Recipe: **{r['name']}** ({r['match']}) · serves {r['servings'] or '?'} · "
                   f"quantities ×{r['scale']}" + (f" for {plan.servings}" if plan.servings else ""))
    if not cart.lines:
        return
    df = pd.DataFrame(cart.to_rows())
    st.dataframe(df, hide_index=True, use_container_width=True,
                 column_config={"Price (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                                "Line total (₹)": st.column_config.NumberColumn(format="₹%.2f")})
    st.markdown(f"### Total: {_rupees(cart.total)}")
    st.caption(f"{len(cart.lines)} product{'' if len(cart.lines) == 1 else 's'} · mode: {cart.mode}"
               + (" (switched from your mode because the cart was over budget)" if cart.budget_switched else ""))
    if cart.budget is not None:
        verdict = "fits within" if cart.fits_budget else "is over"
        (st.success if cart.fits_budget else st.error)(f"Budget {_rupees(cart.budget)}: the cart {verdict} it.")
    if cart.unmatched:
        st.markdown("**Couldn't find on BigBasket**")
        st.markdown("\n".join(f"- {u.label}: {u.reason}" + (f" (closest: {u.closest})" if u.closest else "")
                              for u in cart.unmatched))
    if cart.excluded:
        st.caption("Excluded (already in your pantry): " + ", ".join(cart.excluded))
    st.download_button("⬇ Download cart (CSV)", df.to_csv(index=False).encode("utf-8"),
                       file_name="basketbuddy_cart.csv", mime="text/csv", key=f"csv_{idx}")


def render_catalogue(resp: BotResponse) -> None:
    for cr in resp.catalogue or []:
        if not cr.products:
            continue
        rows = [{"Product": h.metadata["product"], "Brand": h.metadata["brand"],
                 "Price (₹)": h.metadata["sale_price"], "MRP (₹)": h.metadata.get("market_price"),
                 "Rating": h.metadata.get("rating"), "Sub-category": h.metadata.get("sub_category"),
                 "ID": f"P{h.metadata['product_id']}"} for h in cr.products]
        with st.expander(f"Products for “{cr.sub_query.text}”" + (f" ({cr.sub_query.brand})" if cr.sub_query.brand else ""),
                         expanded=False):
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_suggestion(msg: dict[str, Any], idx: int) -> None:
    resp: BotResponse = msg["resp"]
    st.warning("AI-generated suggestion: no team-written kit matched this task. Untick anything you don't want, "
               "then build the cart.", icon="🤖")
    if msg.get("done"):
        st.caption("Confirmed below.")
        return
    with st.form(f"suggest_{idx}"):
        keep = [s.label for j, s in enumerate(resp.plan.active)
                if st.checkbox(f"{s.label}" + (f" ({s.needed})" if s.needed else ""), True, key=f"s{idx}_{j}")]
        if st.form_submit_button("Build cart from ticked items", type="primary"):
            req = msg["request"]
            with st.spinner("Searching BigBasket…"):
                new = _safe_answer(req["message"], req, confirmed_items=keep)
            msg["done"] = True
            st.session_state.messages.append({"role": "assistant", "resp": new, "request": req, "done": True})
            st.rerun()


def render_assistant(msg: dict[str, Any], idx: int) -> None:
    resp: BotResponse | None = msg.get("resp")
    if resp is None:
        st.markdown(msg["content"])
        return
    if resp.kind == "suggestion":
        render_suggestion(msg, idx)
        st.markdown(_md(resp.text))
    else:
        st.markdown(_md(resp.text))
        if resp.kind == "cart":
            render_cart(resp, idx)
        elif resp.kind == "catalogue":
            render_catalogue(resp)
        elif resp.cart is not None and resp.cart.unmatched:   # no_match fallback: say what was searched
            st.markdown("**Searched but not found on BigBasket**")
            st.markdown("\n".join(f"- {u.label}: {u.reason}" + (f" (closest: {u.closest})" if u.closest else "")
                                  for u in resp.cart.unmatched))
    total = resp.timings.get("total_ms")
    if total is not None:
        st.caption(f"{total / 1000:.1f} s · {resp.llm_calls} LLM call(s)"
                   + (f" · fallback: {resp.fallback_trigger}" if resp.fallback_trigger else ""))
    if show_details:
        render_evidence(resp)


def _safe_answer(message: str, req: dict[str, Any], confirmed_items: list[str] | None = None) -> BotResponse | str:
    try:
        return answer(message, mode=req["mode"], extra_pantry=req["pantry"] or None,
                      default_servings=req["servings"], confirmed_items=confirmed_items)
    except Exception as e:  # noqa: BLE001 - never crash the chat; show the error instead
        return f"Something went wrong while answering: `{type(e).__name__}: {e}`"


# --- page ---------------------------------------------------------------------------------

chat_tab, how_tab = st.tabs(["💬 Chat", "📖 How it works"])

with how_tab:
    st.markdown((config.DOCS_DIR / "PIPELINE.md").read_text(encoding="utf-8"))

prompt = st.chat_input("A dish, a pasted recipe, a task (\"deep-clean my bathroom\") or a product question")

with chat_tab:
    if not st.session_state.messages:
        st.markdown("#### What do you need?")
        st.caption("Try one of these, or type your own below. Prices and products come from the BigBasket "
                   "catalogue dataset; answers cite product IDs like `P123`.")
        cols = st.columns(2)
        for i, q in enumerate(EXAMPLES):
            if cols[i % 2].button(q, key=f"ex_{i}", use_container_width=True):
                prompt = q

    for i, m in enumerate(st.session_state.messages):
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.markdown(m["content"].replace("$", r"\$"))
            else:
                render_assistant(m, i)

    if prompt:
        req = {"message": prompt, "mode": None if mode == "Auto" else mode, "pantry": PANTRY, "servings": int(servings)}
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt.replace("$", r"\$"))
        with st.chat_message("assistant"):
            with st.spinner("Planning, searching and checking the answer…"):
                result = _safe_answer(prompt, req)
            if isinstance(result, str):
                msg = {"role": "assistant", "content": result}
            else:
                msg = {"role": "assistant", "resp": result, "request": req, "done": False}
            st.session_state.messages.append(msg)
            st.rerun()
