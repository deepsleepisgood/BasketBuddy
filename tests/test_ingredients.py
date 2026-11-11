"""Tests for the recipe ingredient parser."""
import pytest

from rag.ingredients import parse_ingredient_list, parse_item, split_items


@pytest.mark.parametrize(
    "raw, qty, unit, query, note",
    [
        ("200 gram Paneer (Homemade Cottage Cheese) - cut into cubes", 200, "g", "paneer homemade cottage cheese", "cut into cubes"),
        ("3 Tomatoes - pureed", 3, None, "tomatoes", "pureed"),
        ("1 teaspoon Cumin powder (Jeera)", 1, "tsp", "cumin powder jeera", None),
        ("1/4 cup Fresh cream", 0.25, "cup", "fresh cream", None),
        ("2-1/2 cups Rice", 2.5, "cup", "rice", None),
        ("1 1/2 tablespoon Ghee", 1.5, "tbsp", "ghee", None),
        ("10 to 12 button mushrooms", 12, None, "button mushrooms", None),
        ("2-3 Green Chillies", 3, None, "green chillies", None),
        ("7/8 Green Chillies - crushed", 8, None, "green chillies", "crushed"),   # range typo in data
        ("3/4 Onion - chopped", 0.75, None, "onion", "chopped"),                 # singular: a real fraction
        ("6 cloves Garlic", 6, "clove", "garlic", None),
        ("Salt - to taste", None, None, "salt", "to taste"),
    ],
)
def test_parse_item(raw, qty, unit, query, note):
    p = parse_item(raw)
    assert p is not None
    assert p.quantity == (pytest.approx(qty) if qty is not None else None)
    assert p.unit == unit and p.query == query and p.note == note


def test_cloves_as_the_ingredient_is_not_a_unit():
    p = parse_item("4 Cloves (Laung)")
    assert p.unit is None and p.query == "cloves laung"


def test_water_is_not_purchasable():
    assert parse_item("1 cup Water") is None
    assert parse_item("4 cups Lukewarm Water") is None


def test_split_respects_brackets():
    assert split_items("1 Cabbage (Patta Gobi, Muttaikose) - sliced,Salt") == [
        "1 Cabbage (Patta Gobi, Muttaikose) - sliced", "Salt"]


def test_list_dedupes_by_name():
    names = [p.query for p in parse_ingredient_list("Salt - to taste,1 Onion,Salt - as needed")]
    assert names == ["salt", "onion"]
