"""Parse pack size / weight out of a BigBasket product name.

Only about 3% of names carry a size (see docs/DATA_LOG.md), so the parser
returns ``None`` far more often than not, and callers must handle that.
"""
from __future__ import annotations

import re
from typing import NamedTuple

_UNIT_ALIASES: dict[str, str] = {
    "kg": "kg", "kgs": "kg", "kilo": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g", "gr": "g",
    "mg": "mg",
    "l": "l", "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "ml": "ml",
    "pc": "pcs", "pcs": "pcs", "piece": "pcs", "pieces": "pcs",
}
# grams / millilitres per unit, for comparing pack sizes with recipe quantities
_TO_BASE: dict[str, tuple[float, str]] = {
    "kg": (1000.0, "g"), "g": (1.0, "g"), "mg": (0.001, "g"),
    "l": (1000.0, "ml"), "ml": (1.0, "ml"), "pcs": (1.0, "pcs"),
}

_NUM = r"\d+(?:\.\d+)?"
_UNITS = "|".join(sorted(_UNIT_ALIASES, key=len, reverse=True))
# optional "2 x" multiplier, a number, then a unit that is not part of a longer word
_SIZE_RE = re.compile(
    rf"(?:(?P<mult>\d+)\s*[x×]\s*)?(?P<qty>{_NUM})\s*(?P<unit>{_UNITS})(?![a-z])",
    re.IGNORECASE,
)
# "pouch" is a pack format only as a trailing marker ("Noodles - Pouch", "Refill Pouch");
# "Pencil Pouch - Purple" is a product that happens to be a pouch.
_PACK_OF_RE = re.compile(r"\bpack\s+of\s+(\d+)\b", re.IGNORECASE)  # "(Pack of 10)" → 10 pcs
_POUCH_RE = re.compile(r"(?:-|\(|refill)\s*pouch\)?\s*$", re.IGNORECASE)


class PackSize(NamedTuple):
    qty: float | None        # numeric quantity in `unit` (multiplier applied)
    unit: str                # normalised unit (g, kg, mg, l, ml, pcs) or "pouch"
    base_qty: float | None   # quantity in g / ml / pcs, for comparisons
    base_unit: str | None


def parse_pack_size(name: str | None) -> PackSize | None:
    """Return the pack size found in ``name``, or None.

    Combo products ("A 500 g + B 250 g") hold several items with different
    sizes, so they return None instead of a misleading single size.
    """
    if not name:
        return None
    matches = list(_SIZE_RE.finditer(name))
    if len(matches) > 1 and "+" in name:
        return None  # combo pack: no single size describes it
    if not matches and (pack_of := _PACK_OF_RE.search(name)):
        n = float(pack_of.group(1))
        return PackSize(n, "pcs", n, "pcs")
    if matches:
        m = matches[-1]  # the size usually comes last in the name
        qty = float(m.group("qty")) * (int(m.group("mult")) if m.group("mult") else 1)
        unit = _UNIT_ALIASES[m.group("unit").lower()]
        factor, base_unit = _TO_BASE[unit]
        return PackSize(qty, unit, round(qty * factor, 3), base_unit)
    if _POUCH_RE.search(name):
        return PackSize(None, "pouch", None, None)
    return None
