"""Tests for the ITEM_CATEGORY_MAP hints."""
import pandas as pd
import pytest

import config
from rag.category_map import FRESH_VEG, ITEM_CATEGORY_MAP, OILS, SPICES, lookup_subcategories


def test_every_hinted_subcategory_exists_in_its_domain():
    df = pd.read_parquet(config.PRODUCTS_PARQUET, columns=["domain", "sub_category"])
    valid = df.groupby("domain")["sub_category"].agg(set).to_dict()
    bad = [
        (term, dom, sc)
        for term, by_dom in ITEM_CATEGORY_MAP.items()
        for dom, subs in by_dom.items()
        for sc in subs
        if sc not in valid.get(dom, set())
    ]
    assert not bad, f"sub-categories not in catalogue: {bad[:5]}"


@pytest.mark.parametrize(
    "item, domain, expected",
    [
        ("tomato", "food", FRESH_VEG),
        ("2 tomatoes - finely chopped", "food", FRESH_VEG),
        ("coconut oil", "food", OILS),
        ("cumin seeds (jeera)", "food", SPICES),
        ("red chilli powder", "food", SPICES),
        ("sambhar powder", "food", SPICES),          # generic '... powder' rule
    ],
)
def test_food_hints(item, domain, expected):
    assert lookup_subcategories(item, domain) == expected


def test_same_word_differs_by_domain():
    assert lookup_subcategories("coconut oil", "food") == OILS
    assert lookup_subcategories("coconut oil", "personal_care") == ["Hair Care", "Skin Care"]
    assert lookup_subcategories("floor cleaner", "household") == ["All Purpose Cleaners"]
    assert lookup_subcategories("cleaner", "personal_care") == ["Skin Care", "Men's Grooming"]


@pytest.mark.parametrize("item", ["tomato puree", "ginger garlic paste", "lemon juice", "potato chips"])
def test_processed_produce_is_not_sent_to_fresh(item):
    assert lookup_subcategories(item, "food") != FRESH_VEG


def test_no_hint_cases():
    assert lookup_subcategories("tomato", None) is None
    assert lookup_subcategories("gujiya", "food") is None
    assert lookup_subcategories("toilet brush", "household") is None
