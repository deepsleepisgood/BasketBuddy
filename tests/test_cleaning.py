"""Tests for description cleaning: mojibake repair, boilerplate removal, domain map."""
import pytest

from ingest.clean_products import clean_text, map_domain
from ingest.text_fix import fix_mojibake


@pytest.mark.parametrize(
    "broken, fixed",
    [
        ("the â€˜1001 Arabian Nightsâ€™ theme", "the ‘1001 Arabian Nights’ theme"),
        ("LOSORBâ„¢ technology", "LOSORB™ technology"),
        ("Neem oilÂis a vegetable oil", "Neem oilis a vegetable oil"),
    ],
)
def test_fix_mojibake(broken, fixed):
    assert fix_mojibake(broken) == fixed


def test_fix_mojibake_leaves_clean_text_alone():
    text = "Café crème – 100% “pure”"
    assert fix_mojibake(text) == text


@pytest.mark.parametrize(
    "raw",
    [
        "Gentle soap. For Beauty tips, tricks & more visit https://bigbasket.blog/",
        "Gentle soap.For Beauty tips, tricks & more visitÃƒâ€šÃ‚ https://bigbasket.blog/",
        "Gentle soap. For Beauty tips, tricks & more visit http://lookbeautiful.in/",
    ],
)
def test_clean_text_strips_boilerplate(raw):
    assert clean_text(raw) == "Gentle soap."


def test_clean_text_keeps_text_glued_after_url():
    raw = "Soft. For Beauty tips, tricks & more visit https://bigbasket.blog/Extra-Large Dry Pants"
    assert clean_text(raw) == "Soft. Extra-Large Dry Pants"


@pytest.mark.parametrize(
    "category, sub, domain",
    [
        ("Foodgrains, Oil & Masala", "Edible Oils & Ghee", "food"),
        ("Foodgrains, Oil & Masala", "Baby Food & Formula", "baby"),
        ("Beauty & Hygiene", "Hair Care", "personal_care"),
        ("Cleaning & Household", "All Purpose Cleaners", "household"),
        ("Kitchen, Garden & Pets", "Pet Food & Accessories", "pets"),
        ("Kitchen, Garden & Pets", "Cookware & Non Stick", "kitchen"),
        ("Kitchen, Garden & Pets", "Gardening", "other"),
        ("Baby Care", "Diapers & Wipes", "baby"),
    ],
)
def test_map_domain(category, sub, domain):
    assert map_domain(category, sub) == domain
