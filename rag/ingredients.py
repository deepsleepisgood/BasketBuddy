"""Parse recipe ingredient strings into (quantity, unit, name, note).

Archana's Kitchen lists look like:
  "200 gram Paneer (Homemade Cottage Cheese) - cut into cubes,3 Tomatoes - pureed,
   1 teaspoon Cumin powder (Jeera),Salt - to taste,2-1/2 cups Rice"
Each item becomes a ParsedIngredient; `query` is what retrieval searches for
(name + the alias in brackets, e.g. "cumin powder jeera").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

_UNITS = {
    "cup": "cup", "cups": "cup", "tablespoon": "tbsp", "tablespoons": "tbsp", "tbsp": "tbsp",
    "teaspoon": "tsp", "teaspoons": "tsp", "tsp": "tsp", "gram": "g", "grams": "g", "gm": "g",
    "gms": "g", "g": "g", "kg": "kg", "kilogram": "kg", "kilograms": "kg", "ml": "ml",
    "millilitre": "ml", "milliliter": "ml", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "inch": "inch", "inches": "inch", "sprig": "sprig", "sprigs": "sprig", "pinch": "pinch",
    "pinches": "pinch", "clove": "clove", "cloves": "clove", "piece": "piece", "pieces": "piece",
    "handful": "handful", "bunch": "bunch", "bunches": "bunch", "stick": "stick", "sticks": "stick",
    "stalk": "stalk", "stalks": "stalk", "slice": "slice", "slices": "slice", "can": "can",
    "packet": "packet", "packets": "packet", "drop": "drop", "drops": "drop", "dash": "dash",
}
# Things a shopper never buys for a recipe
_NOT_PURCHASABLE = re.compile(r"^(water|hot water|cold water|warm water|lukewarm water|ice|ice cubes)$", re.I)

_NUM = r"\d+(?:\.\d+)?"
_QTY_RE = re.compile(
    rf"^\s*\+?\s*(?P<qty>{_NUM}\s*-\s*\d+\s*/\s*\d+|{_NUM}\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+"
    rf"|{_NUM}\s*(?:-|to)\s*{_NUM}|{_NUM})\s*"
)
# A unit word counts only when a name follows it: "6 cloves Garlic" (unit) vs
# "4 Cloves (Laung)" (the ingredient itself, with its Hindi alias in brackets).
_UNIT_RE = re.compile(r"^(?P<unit>[A-Za-z]+)\.?\s+(?=[A-Za-z])")


@dataclass(frozen=True)
class ParsedIngredient:
    raw: str
    name: str                 # "Paneer (Homemade Cottage Cheese)"
    quantity: float | None    # 200.0
    unit: str | None          # "g"
    note: str | None          # "cut into cubes"

    @property
    def query(self) -> str:
        """Retrieval text: name with the bracketed alias kept as words, lower-case."""
        return re.sub(r"\s+", " ", re.sub(r"[()/]", " ", self.name)).strip().lower()

    @property
    def base_name(self) -> str:
        """Name without the bracketed alias: 'Paneer'."""
        return re.sub(r"\s*\(.*?\)\s*", " ", self.name).strip()


def _to_number(q: str) -> float:
    """'2-1/2' → 2.5, '1 1/2' → 1.5, '1/4' → 0.25, '2-3' → 3 (upper end), '7/8' → 8 (a range typo)."""
    q = re.sub(r"\s+", " ", q.strip())
    if m := re.fullmatch(r"(\d+)\s*-\s*(\d+)\s*/\s*(\d+)", q):
        return int(m[1]) + int(m[2]) / int(m[3])
    if m := re.fullmatch(r"(\d+) (\d+)\s*/\s*(\d+)", q):
        return int(m[1]) + int(m[2]) / int(m[3])
    if m := re.fullmatch(r"(\d+)\s*/\s*(\d+)", q):
        a, b = int(m[1]), int(m[2])
        return float(max(a, b)) if a >= b else float(Fraction(a, b))
    if m := re.fullmatch(rf"({_NUM})\s*(?:-|to)\s*({_NUM})", q):
        return float(m[2])  # a range ("2-3", "10 to 12"): plan for the upper end
    return float(q)


def split_items(text: str) -> list[str]:
    """Split on commas that are not inside brackets."""
    items, depth, cur = [], 0, []
    for ch in text:
        depth += ch == "("
        depth -= ch == ")" and depth > 0
        if ch == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return [s.strip() for s in items if s.strip()]


def parse_item(raw: str) -> ParsedIngredient | None:
    """Parse one ingredient string; None for empty or non-purchasable items (water)."""
    text = raw.strip().strip(".")
    qty = unit = None
    if m := _QTY_RE.match(text):
        try:
            qty = _to_number(m["qty"])
            text = text[m.end():]
        except (ValueError, ZeroDivisionError):
            pass
        if (u := _UNIT_RE.match(text)) and u["unit"].lower() in _UNITS:
            unit = _UNITS[u["unit"].lower()]
            text = text[u.end():]
        # "7/8 Green Chillies": a fraction with no unit, numerator ≥ 2 and a plural
        # name is a typed range (7–8 chillies). "3/4 Onion" (singular) stays 0.75.
        if (unit is None and m and (f := re.fullmatch(r"(\d+)\s*/\s*(\d+)", m["qty"].strip()))
                and int(f[1]) >= 2 and re.match(r"^[A-Za-z ]*?[a-z]{2,}s\b", text.partition(" - ")[0])):
            qty = float(f[2])
    name, _, note = text.partition(" - ")
    name = name.strip(" -,")
    if not name or _NOT_PURCHASABLE.match(name):
        return None
    return ParsedIngredient(raw.strip(), name, qty, unit, note.strip() or None)


def parse_ingredient_list(text: str) -> list[ParsedIngredient]:
    """Parse a whole comma-separated ingredient list, dropping duplicates by name."""
    out, seen = [], set()
    for item in split_items(text or ""):
        p = parse_item(item)
        if p and p.query not in seen:
            seen.add(p.query)
            out.append(p)
    return out
