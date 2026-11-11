"""Tests for product choice rules in rag.cart (no index needed)."""
from types import SimpleNamespace

import pytest

from rag.cart import has_head_noun, pick, processed_mismatch, shares_content_word
from rag.decompose import SubQuery


@pytest.mark.parametrize(
    "ingredient, product, sub_category, bad",
    [
        ("chicken", "Chicken Fries", "Sausages, Bacon & Salami", "fries"),
        ("chicken", "Masala - Chicken", "Masalas & Spices", "masala"),
        ("chicken", "Fried Strips - Chicken", "Sausages, Bacon & Salami", "fried"),
        ("tomato", "Tomato Ketchup", "Spreads, Sauces, Ketchup", "ketchup"),
        ("cinnamon stick", "Dalchini Drop", "Masalas & Spices", "drop"),
        ("red chilli sauce", "Red Chilli Powder", "Masalas & Spices", "powder"),
        ("cardamom pods", "Powder - Cardamom Seed", "Masalas & Spices", "powder"),
        ("haldi", "Turmeric Powder/Haldi", "Masalas & Spices", None),        # spice: powder is the product
        ("garam masala powder", "Powder - Garam Masala", "Masalas & Spices", None),
        ("tomato puree", "Tomato Puree", "Sauces, Spreads & Dips", None),    # asked for the processed form
        ("tomato", "Tomato - Hybrid", "Fresh Vegetables", None),
    ],
)
def test_processed_mismatch(ingredient, product, sub_category, bad):
    assert processed_mismatch(ingredient, product, sub_category) == bad


def test_head_noun_prefers_the_right_product_type():
    assert has_head_noun("oil control moisturiser", {"product": "Oil Control Moisturiser", "type": "Face Care"})
    assert not has_head_noun("oil control moisturiser", {"product": "Oil Control Face Wash", "type": "Face Care"})
    assert has_head_noun("sunscreen SPF 50", {"product": "Ultra Sheer SPF 50 Sun Block", "type": "Face Care"})
    assert has_head_noun("face wash for oily skin", {"product": "Neem Face Wash", "type": "Face Care"})


def test_shares_content_word():
    assert not shares_content_word("tortoise food mix", {"product": "Adult Dry Cat Food", "type": "Cat Food"})
    assert shares_content_word("cleaning gloves", {"product": "Kitchen Gloves Small", "type": "Gloves"})


def _h(price, rating=None, rrf=0.03, **meta):
    m = {"sale_price": price, "product": "x", **meta}
    if rating is not None:
        m["rating"] = rating
    return SimpleNamespace(metadata=m, rrf_score=rrf, vector_sim=0.8)


def test_pick_modes():
    sq = SubQuery(text="rice", label="rice")
    hits = [_h(120, 4.0, 0.033), _h(60, None, 0.032), _h(90, 4.6, 0.031)]
    assert pick(sq, hits, "Best match")[0] is hits[0]
    assert pick(sq, hits, "Budget")[0] is hits[1]
    assert pick(sq, hits, "Top rated")[0] is hits[2]      # unrated never wins on rating


def test_pick_prefers_a_pack_that_covers_the_quantity():
    sq = SubQuery(text="rice", label="rice", quantity=1, unit="kg")
    small = _h(50, pack_base_qty=500.0, pack_base_unit="g")
    big = _h(90, pack_base_qty=1000.0, pack_base_unit="g")
    chosen, why = pick(sq, [small, big], "Budget")
    assert chosen is big and "pack covers" in why


def test_achar_is_a_processed_form():
    # Phase 8 eval: "nimbu" (lemon) matched "Nimbu Achar"
    assert processed_mismatch("nimbu", "Nimbu Achar") == "achar"
    assert processed_mismatch("lemon pickle", "Lime Pickle") is None
