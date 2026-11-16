"""Download button that packs a basket before saving the CSV.

A Streamlit custom component (CCv2, inline). On click, grocery items drop into a basket,
the basket bounces, then the browser saves the CSV. With "reduce motion" turned on in the
OS, it skips the animation and downloads straight away. After the download it fires a
`downloaded` trigger, so the app can show a toast.
"""
from __future__ import annotations

from collections.abc import Callable

import streamlit as st

_HTML = """
<div class="bb-dl">
  <button type="button" class="bb-btn" aria-label="Download basket as CSV">
    <span class="bb-stage" aria-hidden="true">
      <span class="bb-drop d1"></span><span class="bb-drop d2"></span><span class="bb-drop d3"></span>
      <svg class="bb-basket" viewBox="0 0 48 40" width="34" height="28">
        <path d="M14 14 L20 3 M34 14 L28 3" stroke="currentColor" stroke-width="3" stroke-linecap="round" fill="none"/>
        <path d="M3 14 H45 L40 36 Q39.5 38 37.5 38 H10.5 Q8.5 38 8 36 Z" fill="currentColor"/>
        <path d="M15 20 V32 M24 20 V32 M33 20 V32" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".55"/>
      </svg>
      <svg class="bb-tick" viewBox="0 0 24 24" width="22" height="22">
        <path d="M5 12.5 L10 17.5 L19 7" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </span>
    <span class="bb-text">
      <span class="bb-label">Download basket (CSV)</span>
      <span class="bb-sub"></span>
    </span>
  </button>
</div>
"""

_CSS = """
.bb-dl { font-family: var(--st-font, sans-serif); }
.bb-btn {
  display: inline-flex; align-items: center; gap: 12px;
  padding: 8px 20px 8px 12px; min-height: 52px;
  border: 1.5px solid #CC0000; border-radius: 999px;
  background: #CC0000; color: #fff; cursor: pointer;
  font: inherit; box-shadow: 0 2px 8px rgba(204, 0, 0, .18);
  transition: background .2s, transform .1s;
}
.bb-btn:hover { background: #B00000; }
.bb-btn:active { transform: scale(.98); }
.bb-btn:focus-visible { outline: 3px solid #84C225; outline-offset: 2px; }
.bb-btn[disabled] { cursor: progress; }
.bb-btn.done { background: #689F38; border-color: #689F38; box-shadow: 0 2px 8px rgba(104, 159, 56, .25); }
.bb-text { display: flex; flex-direction: column; align-items: flex-start; line-height: 1.15; }
.bb-label { font-weight: 700; font-size: 15px; }
.bb-sub { font-size: 12px; opacity: .85; }
.bb-stage { position: relative; width: 36px; height: 36px; display: inline-flex;
            align-items: flex-end; justify-content: center; overflow: visible; }
.bb-basket { color: #fff; transform-origin: 50% 100%; }
.bb-tick { position: absolute; bottom: 7px; color: #fff; opacity: 0; transform: scale(.4); }
.bb-drop { position: absolute; top: -18px; width: 9px; height: 9px; border-radius: 50%; opacity: 0; }
.d1 { left: 7px;  background: #84C225; }
.d2 { left: 14px; background: #FFB300; width: 11px; height: 7px; border-radius: 4px; }
.d3 { left: 22px; background: #FF7043; }
.packing .d1 { animation: drop .55s ease-in .00s forwards; }
.packing .d2 { animation: drop .55s ease-in .18s forwards; }
.packing .d3 { animation: drop .55s ease-in .36s forwards; }
.packing .bb-basket { animation: bounce .35s ease-out .55s 2; }
.done .bb-basket { opacity: .25; }
.done .bb-tick { animation: pop .3s ease-out forwards; }
@keyframes drop {
  0%   { opacity: 0; transform: translateY(0) rotate(0); }
  15%  { opacity: 1; }
  80%  { opacity: 1; transform: translateY(30px) rotate(160deg); }
  100% { opacity: 0; transform: translateY(34px) rotate(200deg); }
}
@keyframes bounce {
  0%, 100% { transform: scale(1, 1); }
  40% { transform: scale(1.12, .86); }
  70% { transform: scale(.95, 1.06); }
}
@keyframes pop { to { opacity: 1; transform: scale(1); } }
"""

_JS = """
const state = new WeakMap()

export default function (component) {
  const { data, parentElement, setTriggerValue } = component
  const btn = parentElement.querySelector(".bb-btn")
  if (!btn) return
  const sub = parentElement.querySelector(".bb-sub")
  const label = parentElement.querySelector(".bb-label")
  const s = state.get(parentElement) || { timers: [] }
  state.set(parentElement, s)
  s.data = data || {}
  if (!btn.classList.contains("packing") && !btn.classList.contains("done")) {
    sub.textContent = s.data.summary || ""
  }

  const save = () => {
    const blob = new Blob(["\\ufeff" + (s.data.csv || "")], { type: "text/csv;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = s.data.filename || "basket.csv"
    a.style.display = "none"
    parentElement.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const later = (fn, ms) => s.timers.push(setTimeout(fn, ms))

  btn.onclick = () => {
    if (btn.disabled) return
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    btn.disabled = true
    if (reduce) {
      save()
      btn.disabled = false
      setTriggerValue("downloaded", Date.now())
      return
    }
    btn.classList.add("packing")
    label.textContent = "Packing your basket…"
    later(() => {
      save()
      btn.classList.remove("packing")
      btn.classList.add("done")
      label.textContent = "Basket packed!"
      sub.textContent = s.data.filename || ""
    }, 1300)
    later(() => {
      btn.classList.remove("done")
      label.textContent = "Download basket (CSV)"
      sub.textContent = s.data.summary || ""
      btn.disabled = false
      setTriggerValue("downloaded", Date.now())
    }, 2800)
  }

  return () => { s.timers.forEach(clearTimeout); s.timers = [] }
}
"""

_COMPONENT = st.components.v2.component("basketbuddy_basket_download", html=_HTML, css=_CSS, js=_JS)


def basket_download(csv: str, filename: str, summary: str, *, key: str,
                    on_downloaded: Callable[[], None] | None = None) -> None:
    """Show the animated download button. `summary` is the small second line (e.g. "7 items · ₹1,240")."""
    _COMPONENT(
        data={"csv": csv, "filename": filename, "summary": summary},
        key=key,
        on_downloaded_change=on_downloaded or (lambda: None),
        height="content",
    )
