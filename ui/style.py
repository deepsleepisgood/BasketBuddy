"""BigBasket-style look for BasketBuddy: header bar, product cards, red CTA buttons.

Colours and fonts come from .streamlit/config.toml. The CSS here only covers what the theme
can't: the brand header, product-card anatomy and the crimson Add / quantity controls. Widgets
are targeted through their `.st-key-<key>` classes, never Streamlit's private class names.
"""
from __future__ import annotations

from html import escape
from typing import Any

GREEN = "#689F38"
LEAF = "#84C225"
RED = "#CC0000"

# Icon per BigBasket category (the dataset has no product photos).
CATEGORY_ICON = {
    "Fruits & Vegetables": "🥦",
    "Foodgrains, Oil & Masala": "🌾",
    "Bakery, Cakes & Dairy": "🧈",
    "Beverages": "🧃",
    "Snacks & Branded Foods": "🍪",
    "Eggs, Meat & Fish": "🍗",
    "Gourmet & World Food": "🧀",
    "Beauty & Hygiene": "🧴",
    "Cleaning & Household": "🧽",
    "Kitchen, Garden & Pets": "🪴",
    "Baby Care": "🍼",
}
VEG_CATEGORIES = {"Fruits & Vegetables"}          # certainly vegetarian
NON_VEG_CATEGORIES = {"Eggs, Meat & Fish"}        # certainly not

CSS = f"""
<style>
/* ---------- sticky top bar (header + category strip) ---------- */
/* Sticky goes on the layout wrapper around the keyed container: the wrapper is the child of the
   page-high block, so it can stick; the container itself is only as tall as its wrapper. */
:has(> .st-key-bb_top) {{
  position: sticky; top: 3.75rem; z-index: 99;    /* just below Streamlit's toolbar */
}}
.st-key-bb_top {{ background: #fff; padding: 6px 0 10px; border-bottom: 1px solid #E5E7EB; }}
/* ---------- header ---------- */
.bb-header {{
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 12px 18px; margin-bottom: 4px;
  background: #fff; border: 1px solid #E5E7EB; border-radius: 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,.06);
}}
.bb-logo {{ display: flex; align-items: center; gap: 10px; text-decoration: none; }}
.bb-logo-mark {{
  width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center;
  background: {LEAF}; font-size: 24px; box-shadow: inset 0 -3px 0 {GREEN};
}}
.bb-logo-name {{ font-weight: 800; font-size: 22px; color: #222; letter-spacing: -.02em; line-height: 1; }}
.bb-logo-name b {{ color: {GREEN}; }}
.bb-logo-tag {{ font-size: 11.5px; color: #666; margin-top: 3px; }}
.bb-sla {{
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px;
  background: #F1F8E9; color: {GREEN}; font-weight: 700; font-size: 13px; white-space: nowrap;
}}
.bb-sla .bolt {{ color: #F59E0B; }}
.bb-spacer {{ flex: 1; }}
.bb-account {{
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius: 999px;
  border: 1px solid #E5E7EB; font-size: 13px; font-weight: 600; color: #222; white-space: nowrap;
}}
.bb-basket-pill {{
  display: inline-flex; align-items: center; gap: 10px; padding: 7px 16px 7px 10px;
  border-radius: 999px; background: {RED}; color: #fff; font-weight: 700; font-size: 14px;
  box-shadow: 0 2px 8px rgba(204,0,0,.22); white-space: nowrap;
}}
.bb-basket-pill .count {{
  min-width: 26px; height: 26px; padding: 0 6px; border-radius: 999px; display: grid; place-items: center;
  background: #fff; color: {RED}; font-size: 13px;
}}
.bb-basket-pill.bump {{ animation: bb-bump .6s ease-out; }}
@keyframes bb-bump {{ 30% {{ transform: scale(1.08); }} 60% {{ transform: scale(.97); }} }}

/* ---------- category strip ---------- */
.bb-shopby {{
  display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 10px;
  background: {GREEN}; color: #fff; font-weight: 700; font-size: 13.5px; white-space: nowrap;
}}

/* ---------- hero ---------- */
.bb-hero {{
  position: relative; overflow: hidden; border-radius: 18px; padding: 26px 28px;
  background: linear-gradient(115deg, {GREEN} 0%, {LEAF} 70%, #AED581 100%); color: #fff;
}}
.bb-hero h2 {{ margin: 0 0 6px; font-size: 28px; font-weight: 800; color: #fff; letter-spacing: -.02em; }}
.bb-hero p {{ margin: 0; font-size: 15px; max-width: 560px; opacity: .95; }}
.bb-hero .chips {{ display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }}
.bb-hero .chip {{ background: rgba(255,255,255,.2); border: 1px solid rgba(255,255,255,.35);
                  padding: 4px 10px; border-radius: 999px; font-size: 12.5px; font-weight: 600; }}
.bb-hero .float {{ position: absolute; font-size: 34px; animation: bb-float 4s ease-in-out infinite; opacity: .9; }}
.bb-hero .f1 {{ right: 40px; top: 18px; }}
.bb-hero .f2 {{ right: 110px; top: 70px; animation-delay: .8s; font-size: 28px; }}
.bb-hero .f3 {{ right: 30px; top: 96px; animation-delay: 1.6s; font-size: 26px; }}
.bb-hero .f4 {{ right: 170px; top: 22px; animation-delay: 2.2s; font-size: 24px; }}
@keyframes bb-float {{ 50% {{ transform: translateY(-8px) rotate(-6deg); }} }}
@media (max-width: 640px) {{ .bb-hero .float {{ display: none; }} .bb-hero h2 {{ font-size: 22px; }} }}
@media (prefers-reduced-motion: reduce) {{
  .bb-hero .float, .bb-basket-pill.bump {{ animation: none; }}
}}

/* ---------- product card ---------- */
.bb-card {{ position: relative; font-size: 13px; color: #222; }}
.bb-ribbon {{
  position: absolute; top: -2px; left: -2px; z-index: 1; padding: 3px 8px;
  border-radius: 8px 0 8px 0; background: {GREEN}; color: #fff; font-weight: 800; font-size: 11px;
}}
.bb-img {{
  position: relative; aspect-ratio: 1.6 / 1; border-radius: 10px; background: #F8FAF5;
  display: grid; place-items: center; font-size: 46px; margin-bottom: 8px;
}}
.bb-for {{
  position: absolute; left: 6px; bottom: 6px; max-width: calc(100% - 12px);
  padding: 2px 8px; border-radius: 999px; background: #fff; border: 1px solid #E5E7EB;
  font-size: 10.5px; font-weight: 700; color: {GREEN}; text-transform: uppercase; letter-spacing: .03em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.bb-dot {{
  position: absolute; right: 6px; bottom: 6px; width: 14px; height: 14px; border: 1.5px solid; border-radius: 3px;
  display: grid; place-items: center; background: #fff;
}}
.bb-dot::after {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
.bb-dot.veg {{ color: #2E7D32; }} .bb-dot.nonveg {{ color: #8D3B12; }}
.bb-brand {{ color: #666; font-size: 12px; font-weight: 500; }}
.bb-title {{
  font-weight: 700; font-size: 14px; line-height: 1.3; margin: 2px 0 6px; min-height: 2.6em;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}}
.bb-pack {{
  display: flex; justify-content: space-between; align-items: center; gap: 6px;
  border: 1px solid #E5E7EB; border-radius: 8px; padding: 5px 8px; color: #444; font-size: 12px;
}}
.bb-pack .star {{ color: {GREEN}; font-weight: 700; white-space: nowrap; }}
.bb-price {{ margin-top: 8px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.bb-price b {{ font-size: 17px; font-weight: 800; }}
.bb-price s {{ color: #999; font-size: 12.5px; }}
.bb-price .line {{ margin-left: auto; color: #666; font-size: 12px; }}

/* bordered card containers */
[class*="st-key-card_"] {{ box-shadow: 0 2px 8px rgba(0,0,0,.06); background: #fff; }}
[class*="st-key-card_"]:hover {{ box-shadow: 0 6px 18px rgba(0,0,0,.10); }}

/* red outlined Add button */
[class*="st-key-add_"] button {{
  border: 1.5px solid {RED} !important; color: {RED} !important; background: #fff !important; font-weight: 700;
}}
[class*="st-key-add_"] button:hover {{ background: #FFF1F1 !important; }}
/* solid red quantity stepper [ − | n | + ] */
[class*="st-key-qty_"] {{
  background: {RED}; border-radius: 999px; padding: 2px; justify-content: space-between;
  box-shadow: 0 2px 8px rgba(204,0,0,.22);
}}
[class*="st-key-qty_"] button {{
  background: transparent !important; border: none !important; color: #fff !important;
  min-height: 34px; padding: 0 12px; box-shadow: none !important;
}}
[class*="st-key-qty_"] button:hover {{ background: rgba(0,0,0,.15) !important; }}
[class*="st-key-qty_"] button:focus-visible {{ outline: 2px solid #fff !important; outline-offset: -3px; }}
[class*="st-key-qty_"] button:disabled {{ opacity: .45; }}
.bb-qty {{ color: #fff; font-weight: 800; font-size: 15px; text-align: center; min-width: 24px; }}
[class*="st-key-qty_"] [data-testid="stHtml"] {{ flex: 1; }}
/* bookmark */
[class*="st-key-save_"] button {{ border-radius: 10px !important; }}

/* savings banner */
.bb-save {{
  display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 12px;
  background: #F1F8E9; border: 1px dashed {LEAF}; color: #33691E; font-weight: 600; font-size: 14px;
}}
.bb-save .tag {{ background: {RED}; color: #fff; padding: 2px 8px; border-radius: 6px; font-weight: 800; font-size: 12px; }}
.bb-total {{ font-size: 26px; font-weight: 800; color: #222; letter-spacing: -.01em; }}
.bb-total small {{ font-size: 13px; color: #666; font-weight: 500; margin-left: 6px; }}

/* phones (last, so it wins): the sticky bar would cover too much of the screen */
@media (max-width: 640px) {{
  :has(> .st-key-bb_top) {{ position: static; }}
  .bb-logo-tag, .bb-account, .bb-sla {{ display: none; }}
  .bb-header {{ padding: 10px 12px; }}
}}
</style>
"""


def header_html(items: int, total: float, bump: bool) -> str:
    basket = (f'<span class="bb-basket-pill{" bump" if bump else ""}" aria-label="Basket: {items} items">'
              f'🧺 <span class="count">{items}</span>{rupees(total) if items else "My basket"}</span>')
    return f"""
<div class="bb-header">
  <div class="bb-logo">
    <div class="bb-logo-mark" aria-hidden="true">🧺</div>
    <div><div class="bb-logo-name">basket<b>buddy</b></div>
         <div class="bb-logo-tag">Your BigBasket cart, built from a sentence</div></div>
  </div>
  <span class="bb-sla"><span class="bolt">⚡</span> Cart in seconds</span>
  <span class="bb-spacer"></span>
  <span class="bb-account">👤 Guest</span>
  {basket}
</div>"""


HERO = """
<div class="bb-hero">
  <span class="float f1">🥕</span><span class="float f2">🧅</span><span class="float f3">🍅</span><span class="float f4">🧄</span>
  <h2>What's cooking today?</h2>
  <p>Name a dish, paste a recipe or describe a task. BasketBuddy picks real BigBasket products,
     sizes them for your servings and hands you the basket.</p>
  <div class="chips"><span class="chip">🍛 Recipes</span><span class="chip">🧹 Tasks & kits</span>
    <span class="chip">🗣️ Hinglish OK</span><span class="chip">💸 Budget mode</span></div>
</div>"""


def rupees(x: float) -> str:
    return f"₹{x:,.2f}".replace(".00", "")


def card_html(meta: dict[str, Any], *, for_label: str | None = None, qty: int | None = None) -> str:
    """Product card body: discount ribbon, icon tile, brand, 2-line title, pack + rating, price vs MRP."""
    price = float(meta.get("sale_price") or 0)
    mrp = float(meta.get("market_price") or 0)
    off = round((1 - price / mrp) * 100) if mrp > price > 0 else 0
    cat = str(meta.get("category") or "")
    icon = CATEGORY_ICON.get(cat, "🛒")
    dot = ('<span class="bb-dot veg" title="Vegetarian"></span>' if cat in VEG_CATEGORIES else
           '<span class="bb-dot nonveg" title="Non-vegetarian"></span>' if cat in NON_VEG_CATEGORIES else "")
    rating = meta.get("rating")
    star = f'<span class="star">★ {float(rating):.1f}</span>' if rating not in (None, "") and rating == rating else ""
    pack = escape(str(meta.get("pack") or _pack(meta)))
    line = f'<span class="line">× {qty} = {rupees(price * qty)}</span>' if qty and qty > 1 else ""
    return f"""
<div class="bb-card">
  {f'<span class="bb-ribbon">{off}% OFF</span>' if off >= 1 else ''}
  <div class="bb-img" aria-hidden="true">{icon}
    {f'<span class="bb-for">for {escape(for_label)}</span>' if for_label else ''}{dot}</div>
  <div class="bb-brand">{escape(str(meta.get("brand") or ""))}</div>
  <div class="bb-title" title="{escape(str(meta.get("product") or ""))}">{escape(str(meta.get("product") or ""))}</div>
  <div class="bb-pack"><span>{pack}</span>{star}</div>
  <div class="bb-price"><b>{rupees(price)}</b>{f'<s>{rupees(mrp)}</s>' if off >= 1 else ''}{line}</div>
</div>"""


def _pack(meta: dict[str, Any]) -> str:
    q, u = meta.get("pack_qty"), meta.get("pack_unit")
    if u is None or u != u:
        return "Pack size not listed"
    return f"{q:g} {u}" if isinstance(q, (int, float)) and q == q else str(u)


def savings_html(saved: float, pct: int) -> str:
    return (f'<div class="bb-save"><span class="tag">{pct}% OFF</span> '
            f'You save {rupees(saved)} on this basket compared with MRP 🎉</div>')


def total_html(total: float, items: int, products: int) -> str:
    return (f'<div class="bb-total">{rupees(total)}<small>{items} item{"s" if items != 1 else ""} · '
            f'{products} product{"s" if products != 1 else ""}</small></div>')
