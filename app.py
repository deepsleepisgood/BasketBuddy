"""BasketBuddy: Streamlit chat UI over the BigBasket catalogue.

Run:  .venv\\Scripts\\streamlit run app.py
Every answer goes through rag.pipeline.answer(); this file only renders.
The look (BigBasket colours, product cards, basket download) lives in ui/.
"""
from __future__ import annotations

import random
import re
import time
from typing import Any

import pandas as pd
import streamlit as st

import config
from ingest.fetch_index import fetch_index, index_missing
from rag.llm import describe
from rag.pipeline import BotResponse, answer
from rag.retriever import SearchResult, warm_up
from rag.safety import MAX_MESSAGE_CHARS
from ui.basket_download import basket_download
from ui.style import CSS, HERO, card_html, header_html, rupees, savings_html, total_html

st.set_page_config(page_title="BasketBuddy", page_icon="🧺", layout="wide")
st.html(CSS)

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
# "Shop by need" strip: one tap sends a ready-made question.
NEEDS = {
    "🍛 Dinner tonight": "Paneer butter masala for 4",
    "🎉 Party": "my kid's birthday party is this saturday",
    "🧽 Deep clean": "I want to deep-clean my bathroom this weekend",
    "🧴 Self-care": "Oily skin starter kit under ₹1000",
    "☕ Chai time": "having guests over for chai",
    "🐶 Pets": "food for my puppy",
}
TIPS = [
    "Type in Hinglish: “rajma chawal banana hai 4 logo ke liye”.",
    "Add “budget mode” to pick the cheapest of the most relevant products.",
    "Tell me what's already in your pantry and I'll leave it out of the basket.",
    "Paste a whole recipe: I'll read the ingredient list and size the packs.",
    "Say “under ₹500” and I'll check the basket against your budget.",
    "Every price in an answer cites its product ID, like `P123`.",
]
SYNTHETIC_KIT_NOTE = ("Synthetic kit list, authored by the BasketBuddy team. The products and prices are real "
                      "BigBasket data; the item list is not.")
CARDS_PER_ROW = 4
RATE_LIMIT = (8, 60)   # at most 8 questions per 60 s per browser session (Groq free tier: 8k tokens/min)


# --- startup -------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _warm() -> dict[str, float]:
    """Fetch the index on a fresh deploy (index/ is not in git), then load the model and all indexes once."""
    if index_missing():
        fetch_index()
    return warm_up()


with st.spinner("First start on this server: downloading the search index and model (about 1–2 min)…"
                if index_missing() else "Loading the embedding model and indexes (about 30 s on the first start)…"):
    WARM = _warm()

ss = st.session_state
ss.setdefault("messages", [])     # {"role", "content"} or {"role", "resp", "request", "done"}
ss.setdefault("saved", {})        # product_id -> card metadata ("save for later")
ss.setdefault("basket_seen", 0)   # item count shown last run, to bump the header pill on change
ss.setdefault("tip", random.choice(TIPS))
ss.setdefault("ask_times", [])   # when this session last asked, for the rate limit

if flash := ss.pop("flash", None):
    st.toast(flash, icon="🧺")


# --- state helpers -------------------------------------------------------------------------

def _qty_key(idx: int, j: int) -> str:
    return f"q_{idx}_{j}"


def _qty(idx: int, j: int, default: int) -> int:
    return int(ss.setdefault(_qty_key(idx, j), default))


def _set_qty(key: str, value: int) -> None:
    ss[key] = value


def _toggle_saved(pid: int, meta: dict[str, Any]) -> None:
    if pid in ss.saved:
        del ss.saved[pid]
    else:
        ss.saved[pid] = meta


def _pick_need() -> None:
    choice = ss.get("need_pick")
    if choice:
        ss.pending = NEEDS[choice]
    ss.need_pick = None


def _downloaded() -> None:
    ss.flash = "Basket packed! Your CSV is in Downloads."


def _metas(resp: BotResponse) -> dict[int, dict[str, Any]]:
    """Catalogue metadata (MRP, category, rating…) for every product the search saw."""
    out: dict[int, dict[str, Any]] = {}
    pairs = resp.cart.results if resp.cart is not None else []
    for _, res in pairs:
        for h in res.hits:
            pid = h.metadata.get("product_id")
            if pid is not None:
                out.setdefault(int(pid), dict(h.metadata))
    return out


def _edited_lines(resp: BotResponse, idx: int) -> list[tuple[int, Any, int]]:
    """(line index, cart line, quantity the user now wants) for every line."""
    assert resp.cart is not None
    return [(j, ln, _qty(idx, j, ln.count)) for j, ln in enumerate(resp.cart.lines)]


def _is_edited(resp: BotResponse, idx: int) -> bool:
    return any(q != ln.count for _, ln, q in _edited_lines(resp, idx))


def _rate_limited() -> int:
    """Seconds to wait if this session has hit RATE_LIMIT, else 0 (and the ask is recorded)."""
    limit, window = RATE_LIMIT
    now = time.time()
    ss.ask_times = [t for t in ss.ask_times if now - t < window]
    if len(ss.ask_times) >= limit:
        return int(window - (now - ss.ask_times[0])) + 1
    ss.ask_times.append(now)
    return 0


def _basket_summary() -> tuple[int, float]:
    """Item count and total of the newest cart in the chat, with the user's quantity edits."""
    for idx in range(len(ss.messages) - 1, -1, -1):
        resp = ss.messages[idx].get("resp")
        if isinstance(resp, BotResponse) and resp.kind == "cart" and resp.cart and resp.cart.lines:
            lines = _edited_lines(resp, idx)
            return sum(q for _, _, q in lines), round(sum(ln.price * q for _, ln, q in lines), 2)
    return 0, 0.0


# --- header + sidebar ------------------------------------------------------------------------

items_now, total_now = _basket_summary()
with st.container(key="bb_top", gap="small"):     # sticky: stays on screen while the chat scrolls
    st.html(header_html(items_now, total_now, bump=items_now != ss.basket_seen and items_now > 0))
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.html('<span class="bb-shopby">☰ Shop by need</span>', width="content")
        st.pills("Shop by need", list(NEEDS), key="need_pick", on_change=_pick_need,
                 label_visibility="collapsed")
ss.basket_seen = items_now

with st.sidebar:
    st.subheader(":material/tune: Basket settings")
    mode = st.segmented_control(
        "Product choice", MODES, default="Auto", key="mode", required=True,
        help="Auto = follow the message (\"budget mode\", \"top rated\"), else Best match. Best match = most "
             "relevant; Budget = cheapest of the top 5 relevant; Top rated = highest rated of the top 5 relevant.",
    ) or "Auto"
    servings = st.number_input("Servings (used when the message doesn't say)", 1, 20, 4)
    pantry = st.multiselect("Already in my pantry", PANTRY_STAPLES)
    pantry_extra = st.text_input("Other pantry items (comma-separated)")
    show_details = st.toggle("Show retrieval details", value=False)

    st.subheader(f":material/bookmark: Saved for later ({len(ss.saved)})")
    if ss.saved:
        for pid, meta in list(ss.saved.items()):
            st.markdown(f"**{meta.get('product')}**  \n:gray[{meta.get('brand', '')} · "
                        f"{rupees(float(meta.get('sale_price') or 0))} · `P{pid}`]")
        if st.button("Clear saved", icon=":material/delete:", width="stretch"):
            ss.saved = {}
            st.rerun()
    else:
        st.caption("Tap the bookmark on any product card to keep it here.")

    st.divider()
    st.caption(f"LLM: **{describe()}**")
    st.caption(f"Retrieval: `{config.EMBED_MODEL}` + BM25, RRF (k={config.RRF_K})")
    st.caption("Warm-up: " + ", ".join(f"{k.removesuffix('_s')} {v:.1f}s" for k, v in WARM.items()))
    if st.button("Clear chat", icon=":material/refresh:", width="stretch"):
        for k in [k for k in ss if isinstance(k, str) and k.startswith(("q_", "qty_"))]:
            del ss[k]
        ss.messages = []
        st.rerun()

PANTRY = pantry + [p.strip() for p in pantry_extra.split(",") if p.strip()]


# --- rendering helpers ------------------------------------------------------------------

_CITE = re.compile(r"\[P(\d+)\]")


def _md(text: str) -> str:
    """Show citations as code chips; escape '$' so Streamlit doesn't read it as LaTeX."""
    return _CITE.sub(r"`P\1`", text.replace("$", r"\$"))


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
    with st.expander("Evidence", icon=":material/search:", expanded=False):
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
            st.dataframe(_hits_table(res, chosen), hide_index=True)
        if resp.context:
            st.markdown("**Context sent to the LLM**")
            st.code(resp.context, language=None, wrap_lines=True)


def _save_button(pid: int, meta: dict[str, Any], key: str) -> None:
    saved = pid in ss.saved
    st.button(":material/bookmark:" if saved else ":material/bookmark_border:", key=key,
              help="Remove from saved" if saved else "Save for later",
              on_click=_toggle_saved, args=(pid, meta))


MAX_QTY = 50


def _stepper(idx: int, j: int, q: int) -> None:
    """BigBasket-style control: an outlined red "Add" at 0, a solid red [ − | n | + ] otherwise."""
    key = _qty_key(idx, j)
    if q == 0:
        st.button("Add", key=f"add_{idx}_{j}", icon=":material/add:", width="stretch",
                  on_click=_set_qty, args=(key, 1))
        return
    with st.container(horizontal=True, vertical_alignment="center", gap=None, wrap=False,
                      key=f"qty_{idx}_{j}", width="stretch"):
        st.button(":material/remove:", key=f"dec_{idx}_{j}", help="One less",
                  on_click=_set_qty, args=(key, q - 1))
        st.html(f'<div class="bb-qty" aria-live="polite">{q}</div>')
        st.button(":material/add:", key=f"inc_{idx}_{j}", help="One more", disabled=q >= MAX_QTY,
                  on_click=_set_qty, args=(key, q + 1))


def _card_grid(n: int) -> list[Any]:
    """Columns for n cards, CARDS_PER_ROW per row (Streamlit stacks them on phones)."""
    cells: list[Any] = []
    for start in range(0, n, CARDS_PER_ROW):
        cells += st.columns(CARDS_PER_ROW, gap="small")[: min(CARDS_PER_ROW, n - start)]
    return cells


def _unmatched_list(cart: Any, title: str) -> None:
    st.warning("\n".join(f"- **{u.label}**: {u.reason}" + (f" (closest: {u.closest})" if u.closest else "")
                         for u in cart.unmatched), title=title, icon=":material/search_off:")


def render_cart(resp: BotResponse, idx: int) -> None:
    cart = resp.cart
    plan = resp.plan
    assert cart is not None
    if plan.kit:
        st.info(f"**{plan.kit['name']}**: {SYNTHETIC_KIT_NOTE}", icon=":material/edit_note:")
    if plan.ai_generated:
        st.warning("This item list was AI-generated and confirmed by you; products and prices are BigBasket data.",
                   icon=":material/smart_toy:")
    if plan.recipe:
        r = plan.recipe
        st.caption(f"Recipe: **{r['name']}** ({r['match']}) · serves {r['servings'] or '?'} · "
                   f"quantities ×{r['scale']}" + (f" for {plan.servings}" if plan.servings else ""))
    if not cart.lines:
        return

    metas = _metas(resp)
    lines = _edited_lines(resp, idx)
    kept = [(j, ln, q) for j, ln, q in lines if q > 0]
    total = round(sum(ln.price * q for _, ln, q in kept), 2)
    mrp_total = round(sum(float(metas.get(ln.product_id, {}).get("market_price") or ln.price) * q
                          for _, ln, q in kept), 2)
    saved = round(mrp_total - total, 2)
    items = sum(q for _, _, q in kept)
    edited = any(q != ln.count for _, ln, q in lines)

    if saved >= 1 and mrp_total > 0:
        st.html(savings_html(saved, round(saved / mrp_total * 100)))

    for (j, ln, q), cell in zip(lines, _card_grid(len(lines)), strict=True):
        meta = {**metas.get(ln.product_id, {}), "product": ln.product, "brand": ln.brand,
                "sale_price": ln.price, "pack": ln.pack, "product_id": ln.product_id}
        with cell, st.container(border=True, key=f"card_{idx}_{j}"):
            st.html(card_html(meta, for_label=ln.label, qty=q))
            with st.container(horizontal=True, vertical_alignment="center", gap="small", wrap=False):
                _stepper(idx, j, q)
                _save_button(ln.product_id, meta, key=f"save_{idx}_{j}")

    with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
        st.html(total_html(total, items, len(kept)), width="content")
        if edited:
            st.badge("Quantities edited", icon=":material/edit:", color="orange")
            st.button("Reset", key=f"reset_{idx}", icon=":material/undo:", type="tertiary",
                      on_click=lambda i=idx: [ss.pop(k) for k in list(ss)
                                              if isinstance(k, str) and k.startswith((f"q_{i}_", f"qty_{i}_"))])
    st.caption(f"Mode: {cart.mode}"
               + (" (switched from your mode because the cart was over budget)" if cart.budget_switched else "")
               + (" · quantities changed by you" if edited else ""))
    if cart.budget is not None:
        fits = total <= cart.budget
        st.progress(min(total / cart.budget, 1.0) if cart.budget else 1.0,
                    text=f"Budget {rupees(cart.budget)}: " + (f"{rupees(cart.budget - total)} to spare"
                                                              if fits else f"over by {rupees(total - cart.budget)}"))
    if cart.unmatched:
        _unmatched_list(cart, "Couldn't find on BigBasket")
    if cart.excluded:
        st.caption("Left out (already in your pantry): " + ", ".join(cart.excluded))

    rows = [{**row, "Qty": q, "Line total (₹)": round(ln.price * q, 2)}
            for (_, ln, q), row in zip(lines, cart.to_rows(), strict=True) if q > 0]
    df = pd.DataFrame(rows)
    with st.expander("Table view", icon=":material/table:"):
        st.dataframe(df, hide_index=True,
                     column_config={"Price (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                                    "Line total (₹)": st.column_config.NumberColumn(format="₹%.2f")})
    if rows:
        name = plan.recipe["name"] if plan.recipe else plan.kit["name"] if plan.kit else "cart"
        slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:40] or "cart"
        basket_download(df.to_csv(index=False), f"basketbuddy_{slug}.csv",
                        f"{items} item{'s' if items != 1 else ''} · {rupees(total)}",
                        key=f"dl_{idx}", on_downloaded=_downloaded)


def render_catalogue(resp: BotResponse, idx: int) -> None:
    for c, cr in enumerate(resp.catalogue or []):
        if not cr.products:
            continue
        st.markdown(f"**Products for “{cr.sub_query.text}”**" + (f" ({cr.sub_query.brand})" if cr.sub_query.brand else ""))
        shown = cr.products[:CARDS_PER_ROW]
        for k, (h, cell) in enumerate(zip(shown, _card_grid(len(shown)), strict=True)):
            meta = dict(h.metadata)
            pid = int(meta["product_id"])
            with cell, st.container(border=True, key=f"card_{idx}_{c}_{k}"):
                st.html(card_html(meta))
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.caption(f"`P{pid}`")
                    _save_button(pid, meta, key=f"save_{idx}_{c}_{k}")
        if len(cr.products) > len(shown):
            rows = [{"Product": h.metadata["product"], "Brand": h.metadata["brand"],
                     "Price (₹)": h.metadata["sale_price"], "MRP (₹)": h.metadata.get("market_price"),
                     "Rating": h.metadata.get("rating"), "Sub-category": h.metadata.get("sub_category"),
                     "ID": f"P{h.metadata['product_id']}"} for h in cr.products]
            with st.expander(f"All {len(rows)} products", icon=":material/table:"):
                st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_suggestion(msg: dict[str, Any], idx: int) -> None:
    resp: BotResponse = msg["resp"]
    st.warning("AI-generated suggestion: no team-written kit matched this task. Untick anything you don't want, "
               "then build the cart.", icon=":material/smart_toy:")
    if msg.get("done"):
        st.caption("Confirmed below.")
        return
    with st.form(f"suggest_{idx}"):
        keep = [s.label for j, s in enumerate(resp.plan.active)
                if st.checkbox(f"{s.label}" + (f" ({s.needed})" if s.needed else ""), True, key=f"s{idx}_{j}")]
        if st.form_submit_button("Build cart from ticked items", type="primary", icon=":material/shopping_basket:"):
            req = msg["request"]
            with st.spinner("Searching BigBasket…"):
                new = _safe_answer(req["message"], req, confirmed_items=keep)
            msg["done"] = True
            ss.messages.append({"role": "assistant", "resp": new, "request": req, "done": True})
            st.rerun()


def render_assistant(msg: dict[str, Any], idx: int) -> None:
    resp: BotResponse | None = msg.get("resp")
    if resp is None:
        st.markdown(msg["content"])
        return
    if resp.kind == "suggestion":
        render_suggestion(msg, idx)
        st.markdown(_md(resp.text))
    elif resp.kind == "cart" and resp.cart and resp.cart.lines and _is_edited(resp, idx):
        # The LLM's text was checked against the cart as first built; after edits, only code speaks.
        lines = [(ln, q) for _, ln, q in _edited_lines(resp, idx) if q > 0]
        st.markdown(f"**Updated basket:** {sum(q for _, q in lines)} items across {len(lines)} products, "
                    f"total **{rupees(sum(ln.price * q for ln, q in lines))}** "
                    f"(was {rupees(resp.cart.total)} as first built).".replace("$", r"\$"))
        with st.expander("Original answer (for the cart as first built)", icon=":material/history:"):
            st.markdown(_md(resp.text))
        render_cart(resp, idx)
    else:
        st.markdown(_md(resp.text))
        if resp.kind == "cart":
            render_cart(resp, idx)
        elif resp.kind == "catalogue":
            render_catalogue(resp, idx)
        elif resp.cart is not None and resp.cart.unmatched:   # no_match fallback: say what was searched
            _unmatched_list(resp.cart, "Searched but not found on BigBasket")
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

chat_tab, how_tab = st.tabs([":material/chat: Chat", ":material/menu_book: How it works"])

with how_tab:
    st.markdown((config.DOCS_DIR / "PIPELINE.md").read_text(encoding="utf-8"))

prompt = st.chat_input("Search for a dish, paste a recipe, or describe a task (\"deep-clean my bathroom\")",
                       max_chars=MAX_MESSAGE_CHARS)
prompt = prompt or ss.pop("pending", None)

with chat_tab:
    if not ss.messages:
        st.html(HERO)
        st.markdown("##### Try one of these")
        cols = st.columns(2)
        for i, q in enumerate(EXAMPLES):
            if cols[i % 2].button(q, key=f"ex_{i}", width="stretch", icon=":material/arrow_outward:"):
                prompt = q
        st.caption(f"💡 Tip: {ss.tip} Products and prices come from the BigBasket catalogue dataset.")

    for i, m in enumerate(ss.messages):
        with st.chat_message(m["role"], avatar="🧑" if m["role"] == "user" else "🧺"):
            if m["role"] == "user":
                st.markdown(m["content"].replace("$", r"\$"))
            else:
                render_assistant(m, i)

    if prompt and (wait := _rate_limited()):
        st.warning(f"You're asking faster than the free model allows. Try again in {wait} s.",
                   icon=":material/hourglass_top:")
        prompt = None
    if prompt:
        req = {"message": prompt, "mode": None if mode == "Auto" else mode, "pantry": PANTRY, "servings": int(servings)}
        ss.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar="🧑"):
            st.markdown(prompt.replace("$", r"\$"))
        with st.chat_message("assistant", avatar="🧺"):
            with st.spinner("Filling your basket: planning, searching and checking prices…"):
                result = _safe_answer(prompt, req)
            if isinstance(result, str):
                msg = {"role": "assistant", "content": result}
            else:
                msg = {"role": "assistant", "resp": result, "request": req, "done": False}
            ss.messages.append(msg)
            st.rerun()
