"""Tests for rule-based decomposition (no LLM, no index needed)."""
from types import SimpleNamespace

import pytest

from rag.decompose import (
    _rule_catalogue_split, choose_recipe, classify, dish_name_from, extract_budget, extract_mode,
    extract_pantry, extract_servings, in_pantry,
)


@pytest.mark.parametrize(
    "msg, intent",
    [
        ("Give me everything I need for dal makhani for 4 people, budget mode, I already have salt and ghee.", "dish"),
        ("paneer butter masala for 4", "dish"),
        ("rajma chawal for 3", "dish"),
        ("I need to deep-clean my bathroom this weekend.", "need"),
        ("Skincare routine for oily skin under ₹1000.", "need"),
        ("house party for 10", "need"),
        ("jeera aur haldi kitne ka hai?", "catalogue"),
        ("Compare Dove and Pears bathing soaps on price and rating.", "catalogue"),
        ("cheapest floor cleaner", "catalogue"),
        ("coconut oil for cooking", "catalogue"),
        ("Is paneer in stock in Koramangala right now?", "out_of_scope"),
        ("Is Blinkit cheaper than BigBasket?", "out_of_scope"),
        ("Will this shampoo cure my dandruff disease?", "out_of_scope"),
        ("2 cups poha, 1 onion, 1/2 tsp mustard seeds, 1/4 tsp haldi, 1 tbsp oil", "recipe_text"),
    ],
)
def test_classify(msg, intent):
    assert classify(msg)[0] == intent


def test_entities_from_the_brief_dish_question():
    msg = "Give me everything I need for dal makhani for 4 people, budget mode, I already have salt and ghee."
    assert extract_servings(msg) == 4
    assert extract_mode(msg) == "Budget"
    assert extract_pantry(msg) == ["salt", "ghee"]
    assert dish_name_from(msg) == "dal makhani"


@pytest.mark.parametrize(
    "msg, budget",
    [("routine under ₹1000", 1000), ("within rs 1,500", 1500), ("budget of 800 for snacks", 800), ("dal makhani", None)],
)
def test_extract_budget(msg, budget):
    assert extract_budget(msg) == budget


def test_extract_mode():
    assert extract_mode("top rated shampoo") == "Top rated"
    assert extract_mode("cheapest basmati rice") == "Budget"
    assert extract_mode("basmati rice") is None


@pytest.mark.parametrize(
    "ingredient, pantry, excluded",
    [
        ("salt", ["salt"], True),
        ("turmeric powder", ["haldi"], True),          # Hinglish synonym
        ("sunflower oil", ["oil"], True),
        ("salted butter", ["salt"], False),            # not the same thing
        ("tomatoes", ["tomato"], True),                # plural
        ("ghee", ["salt", "oil"], False),
    ],
)
def test_in_pantry(ingredient, pantry, excluded):
    assert in_pantry(ingredient, pantry) is excluded


def _hit(name, sim, rid):
    return SimpleNamespace(metadata={"name": name, "recipe_id": rid}, vector_sim=sim)


def test_choose_recipe_prefers_plain_dish_over_variants():
    hits = [_hit("Jain Style Dal Makhani", 0.78, 1), _hit("Masoor Dal Makhani - Masoor Dal Makhani", 0.72, 2),
            _hit("Creamy Dal Makhani", 0.73, 3)]
    best, _ = choose_recipe("dal makhani", hits)
    assert best.metadata["name"] == "Creamy Dal Makhani"


def test_choose_recipe_prefers_literal_words_over_synonyms():
    hits = [_hit("Cajun Kidney beans Rice With Soya Chunks", 0.79, 1),
            _hit("Spiced Rajma Pulao - Spiced Kidney Bean Rice", 0.84, 2)]
    assert choose_recipe("rajma chawal", hits)[0].metadata["recipe_id"] == 2


def test_choose_recipe_refuses_weak_matches():
    assert choose_recipe("gujiya", [_hit("Guava Jelly", 0.58, 1)])[0] is None


def test_rule_split_shares_the_product_words():
    assert _rule_catalogue_split("Compare Dove and Pears bathing soaps") == ["Dove bathing soaps", "Pears bathing soaps"]
    assert _rule_catalogue_split("jeera aur haldi kitne ka hai?") == ["jeera", "haldi"]


@pytest.mark.parametrize("msg, dish, servings", [
    # conversational phrasings seen in manual testing after Phase 9
    ("i need to cook butter chicken for 5 ppl", "butter chicken", 5),
    ("gotta cook biryani for 6 guests", "biryani", 6),
    ("want to make rajma chawal tonight for 2 of us", "rajma chawal", 2),
    ("making pasta for my family of 4", "pasta", 4),
    ("I have to cook dal tadka for 3", "dal tadka", 3),
    ("cooking chole for 4 friends", "chole", 4),
])
def test_conversational_dish_requests(msg, dish, servings):
    assert classify(msg)[0] == "dish"
    assert dish_name_from(msg) == dish
    assert extract_servings(msg) == servings
    assert extract_pantry(msg) == []          # "I have to cook" is not a pantry statement


def test_for_cooking_stays_a_product_question():
    assert classify("coconut oil for cooking")[0] == "catalogue"
