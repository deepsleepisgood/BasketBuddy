"""ITEM_CATEGORY_MAP: hand-made, context-aware sub-category hints for retrieval.

A sub-query carries a target domain from its source (recipe ingredient → food,
bathroom kit item → household). Within that domain, some words still collide:
"tomato" (food) matches ketchup, puree and tomato chips far more often than
fresh tomatoes, and "coconut oil" means edible oil in a recipe but hair oil in a
hair routine. These hints narrow the search to the right sub-categories.

Every sub-category named here is checked against the cleaned catalogue in
tests/test_category_map.py, so a typo can't silently filter everything out.
"""
from __future__ import annotations

import re

# --- sub-category groups (all names exist in data/clean/products.parquet) ------
FRESH_VEG = ["Fresh Vegetables", "Organic Fruits & Vegetables", "Exotic Fruits & Veggies",
             "Herbs & Seasonings", "Cuts & Sprouts"]
FRESH_FRUIT = ["Fresh Fruits", "Organic Fruits & Vegetables", "Exotic Fruits & Veggies"]
SPICES = ["Masalas & Spices", "Organic Staples"]
DALS = ["Dals & Pulses", "Organic Staples"]
FLOURS = ["Atta, Flours & Sooji", "Organic Staples"]
RICE = ["Rice & Rice Products", "Organic Staples"]
OILS = ["Edible Oils & Ghee", "Oils & Vinegar", "Organic Staples"]
DAIRY = ["Dairy", "Dairy & Cheese"]
SWEETENERS = ["Salt, Sugar & Jaggery", "Organic Staples"]
DRY_FRUITS = ["Dry Fruits", "Snacks, Dry Fruits, Nuts"]

FOOD, PERSONAL, HOUSEHOLD = "food", "personal_care", "household"

# term → {domain: [sub_categories]}. Longest matching term wins.
ITEM_CATEGORY_MAP: dict[str, dict[str, list[str]]] = {
    # fresh produce (switched off when the item is processed, see _PROCESSED_RE)
    **{t: {FOOD: FRESH_VEG} for t in [
        "tomato", "tomatoes", "onion", "onions", "potato", "potatoes", "ginger", "garlic",
        "green chilli", "green chillies", "coriander leaves", "mint leaves", "curry leaves",
        "spinach", "cauliflower", "capsicum", "carrot", "carrots", "cucumber", "brinjal", "okra",
        "cabbage", "french beans", "beetroot", "bottle gourd", "bitter gourd", "karela",
        "radish", "pumpkin", "green peas", "mushroom", "mushrooms", "spring onion", "lemon",
        "fenugreek leaves", "methi leaves", "sweet potato", "drumstick", "raw banana", "corn",
    ]},
    **{t: {FOOD: FRESH_FRUIT} for t in [
        "banana", "apple", "mango", "orange", "pomegranate", "papaya", "grapes", "pineapple",
        "watermelon", "guava", "strawberry",
    ]},
    # spices and masalas
    **{t: {FOOD: SPICES} for t in [
        "cumin", "cumin seeds", "jeera", "turmeric", "turmeric powder", "haldi",
        "coriander powder", "coriander seeds", "red chilli powder", "chilli powder",
        "red chilli", "dry red chilli", "kashmiri red chilli", "garam masala", "cardamom",
        "cloves", "cinnamon", "black pepper", "pepper", "asafoetida", "hing", "mustard seeds",
        "fennel seeds", "carom seeds", "ajwain", "fenugreek seeds", "bay leaf", "kasuri methi",
        "dried fenugreek leaves", "saffron", "nutmeg", "star anise", "amchur", "dry mango powder",
        "chaat masala", "sambar powder", "rasam powder", "tamarind", "poppy seeds", "sesame seeds",
    ]},
    # dals, pulses, beans
    **{t: {FOOD: DALS} for t in [
        "toor dal", "arhar dal", "moong dal", "urad dal", "whole urad dal", "black urad dal",
        "chana dal", "masoor dal", "rajma", "kidney beans", "chickpeas", "kabuli chana",
        "black chana", "green gram", "black gram", "lobia", "soya chunks",
    ]},
    # flours and grains
    **{t: {FOOD: FLOURS} for t in [
        "wheat flour", "atta", "whole wheat flour", "besan", "gram flour", "maida",
        "refined flour", "sooji", "semolina", "rava", "rice flour", "ragi flour", "corn flour",
    ]},
    **{t: {FOOD: RICE} for t in ["rice", "basmati rice", "basmati", "sona masoori", "poha", "flattened rice"]},
    # oils and fats
    **{t: {FOOD: OILS} for t in [
        "oil", "cooking oil", "sunflower oil", "mustard oil", "groundnut oil", "olive oil",
        "ghee", "refined oil", "vegetable oil",
    ]},
    # dairy
    **{t: {FOOD: DAIRY} for t in [
        "milk", "curd", "yogurt", "dahi", "paneer", "butter", "fresh cream", "cream", "cheese",
        "khoya", "buttermilk",
    ]},
    **{t: {FOOD: SWEETENERS} for t in ["salt", "sugar", "jaggery", "rock salt", "black salt", "honey"]},
    **{t: {FOOD: DRY_FRUITS} for t in ["cashew", "cashews", "almonds", "raisins", "walnuts", "pistachios", "dates"]},
    # raw peanuts are sold under Dals & Pulses / Organic Staples; without a hint "peanuts" matched
    # the namkeen snack "Peanuts - Hing Jeera" (Phase 8 eval, pasted poha recipe)
    **{t: {FOOD: ["Dals & Pulses", "Organic Staples"]} for t in ["peanut", "peanuts", "groundnut", "groundnuts"]},
    "eggs": {FOOD: ["Eggs"]},
    "egg": {FOOD: ["Eggs"]},
    "bread": {FOOD: ["Breads & Buns", "Gourmet Breads"]},
    "tea": {FOOD: ["Tea"]},
    "coffee": {FOOD: ["Coffee"]},

    # context-dependent words: the same term, different shelves per domain
    "coconut oil": {FOOD: OILS, PERSONAL: ["Hair Care", "Skin Care"]},
    "coconut hair oil": {PERSONAL: ["Hair Care"]},
    "almond oil": {FOOD: OILS, PERSONAL: ["Hair Care", "Skin Care"]},
    "castor oil": {PERSONAL: ["Hair Care", "Skin Care", "Health & Medicine"]},
    "coconut": {FOOD: FRESH_VEG + ["Cooking & Baking Needs"]},
    "cleaner": {HOUSEHOLD: ["All Purpose Cleaners"], PERSONAL: ["Skin Care", "Men's Grooming"]},
    "floor cleaner": {HOUSEHOLD: ["All Purpose Cleaners"]},
    "toilet cleaner": {HOUSEHOLD: ["All Purpose Cleaners"]},
    "face wash": {PERSONAL: ["Skin Care", "Men's Grooming"]},
    "wipes": {HOUSEHOLD: ["Disposables, Garbage Bag", "Mops, Brushes & Scrubs"], "baby": ["Diapers & Wipes"]},
    "oats": {FOOD: ["Breakfast Cereals", "Cereals & Breakfast", "Organic Staples"], PERSONAL: ["Skin Care"]},
    "shampoo": {PERSONAL: ["Hair Care"], "pets": ["Pet Food & Accessories"]},
    "soap": {PERSONAL: ["Bath & Hand Wash"], HOUSEHOLD: ["Detergents & Dishwash"]},
}

# Produce words inside a processed product name should not be sent to fresh produce:
# "tomato puree", "ginger garlic paste", "lemon juice", "potato chips", "green chilli sauce".
_PROCESSED_RE = re.compile(
    r"\b(puree|paste|ketchup|sauce|powder|pickle|achaa?r|chips|juice|dried|dry|frozen|flakes|"
    r"masala|essence|extract|jam|chutney|soup|crush|squash|candy|bar)\b",
    re.IGNORECASE,
)
_FRESH_GROUPS = (FRESH_VEG, FRESH_FRUIT)
_TERMS_LONGEST_FIRST = sorted(ITEM_CATEGORY_MAP, key=len, reverse=True)


def lookup_subcategories(item: str, domain: str | None) -> list[str] | None:
    """Sub-category hint for ``item`` in ``domain``, or None when there is no hint.

    The longest term contained in ``item`` (on word boundaries) decides. Rules:
    - fresh-produce hints are skipped when the item names a processed form;
    - any '... powder' or '... masala' in food goes to spices.
    """
    if domain is None:
        return None
    text = item.lower()
    for term in _TERMS_LONGEST_FIRST:
        if not re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text):
            continue
        hint = ITEM_CATEGORY_MAP[term].get(domain)
        if hint is None:
            continue
        if any(hint is g for g in _FRESH_GROUPS) and _PROCESSED_RE.search(text):
            break  # e.g. "tomato puree": fall through to the generic rules
        return list(hint)
    if domain == FOOD and re.search(r"\b(powder|masala)\b", text):
        return list(SPICES)
    return None
