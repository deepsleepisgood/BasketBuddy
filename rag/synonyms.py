"""Hinglish ↔ English synonym expansion for grocery queries.

BigBasket names mix both ("Cumin/Jeera/Jeerige - Whole", "Jeera Powder",
"Powder - Cumin"), and so do users ("jeera aur haldi kitne ka hai?"). We expand
in both directions: a Hinglish word adds its English equivalent and vice versa,
so BM25 can match either spelling and the vector sees the English term.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Hinglish → English. Multi-word keys are matched before single words.
HINGLISH_TO_ENGLISH: dict[str, str] = {
    # spices
    "jeera": "cumin", "haldi": "turmeric", "dhania": "coriander", "dhaniya": "coriander",
    "elaichi": "cardamom", "laung": "cloves", "dalchini": "cinnamon", "kali mirch": "black pepper",
    "hing": "asafoetida", "rai": "mustard seeds", "sarson": "mustard", "saunf": "fennel",
    "ajwain": "carom seeds", "methi": "fenugreek", "kasuri methi": "dried fenugreek leaves",
    "kesar": "saffron", "imli": "tamarind", "lal mirch": "red chilli", "mirch": "chilli",
    "hari mirch": "green chilli", "tej patta": "bay leaf", "amchur": "dry mango powder",
    # staples
    "atta": "wheat flour", "maida": "refined flour", "besan": "gram flour", "sooji": "semolina",
    "suji": "semolina", "rava": "semolina", "chawal": "rice", "poha": "flattened rice",
    "gur": "jaggery", "cheeni": "sugar", "namak": "salt", "tel": "oil",
    # dals and pulses
    "rajma": "kidney beans", "chana": "chickpeas", "kabuli chana": "chickpeas", "chole": "chickpeas",
    "toor dal": "pigeon pea lentils", "arhar dal": "pigeon pea lentils", "moong": "green gram",
    "urad": "black gram", "masoor": "red lentils",
    # dairy
    "dahi": "curd", "paneer": "cottage cheese", "ghee": "clarified butter", "makhan": "butter",
    "malai": "cream", "doodh": "milk",
    # vegetables, fruit, herbs
    "aloo": "potato", "pyaz": "onion", "pyaaz": "onion", "kanda": "onion", "tamatar": "tomato",
    "adrak": "ginger", "lehsun": "garlic", "lahsun": "garlic", "nimbu": "lemon", "gobi": "cauliflower",
    "palak": "spinach", "matar": "peas", "bhindi": "okra", "baingan": "brinjal",
    "shimla mirch": "capsicum", "pudina": "mint", "kadi patta": "curry leaves",
    "curry patta": "curry leaves", "nariyal": "coconut",
    # "patta" = leaves: without these, "dhaniya patta" matched coriander *seeds* (Phase 8 eval)
    "dhaniya patta": "coriander leaves", "dhania patta": "coriander leaves", "hara dhaniya": "coriander leaves",
    "hara dhania": "coriander leaves", "pudina patta": "mint leaves", "methi patta": "fenugreek leaves",
    # dry fruits
    "kaju": "cashew", "badam": "almonds", "kishmish": "raisins",
}

# English → Hinglish only for words BigBasket itself uses in product names
# ("Jeera Powder", "Besan", "Rajma Chitra", "Toor Dal"). Reversing generic words
# ("oil" → "tel", "salt" → "namak") would only add noise.
REVERSE_KEYS: frozenset[str] = frozenset({
    "jeera", "haldi", "dhania", "elaichi", "hing", "ajwain", "methi", "kasuri methi", "saunf",
    "atta", "maida", "besan", "sooji", "rava", "poha", "rajma", "chana", "kabuli chana",
    "toor dal", "moong", "urad", "masoor", "paneer", "ghee", "dahi", "kaju", "badam", "amchur",
})

_WORD = r"(?<![a-z]){}(?![a-z])"


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[re.Pattern[str], tuple[str, ...]]]:
    """(pattern, expansions) pairs, longest phrase first."""
    pairs: dict[str, set[str]] = {}
    for hi, en in HINGLISH_TO_ENGLISH.items():
        pairs.setdefault(hi, set()).add(en)
        if hi in REVERSE_KEYS:
            pairs.setdefault(en, set()).add(hi)  # "cumin" also searches "jeera"
    ordered = sorted(pairs, key=len, reverse=True)
    return [(re.compile(_WORD.format(re.escape(k))), tuple(sorted(pairs[k]))) for k in ordered]


def expand_query(query: str) -> str:
    """Append synonyms for every Hinglish/English grocery term found in ``query``.

    'jeera aur haldi' → 'jeera aur haldi cumin turmeric'. Each term is expanded
    once; a phrase ("kasuri methi") consumes its words so "methi" isn't expanded again.
    """
    text = query.lower()
    consumed = text
    additions: list[str] = []
    for pattern, expansions in _patterns():
        if pattern.search(consumed):
            consumed = pattern.sub(" ", consumed)
            for word in expansions:
                if not re.search(_WORD.format(re.escape(word)), text) and word not in additions:
                    additions.append(word)
    return query if not additions else f"{query} {' '.join(additions)}"
