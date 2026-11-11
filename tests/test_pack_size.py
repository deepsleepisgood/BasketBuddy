"""Tests for ingest.pack_size.parse_pack_size."""
import pytest

from ingest.pack_size import parse_pack_size


@pytest.mark.parametrize(
    "name, qty, unit, base_qty, base_unit",
    [
        ("Garlic Oil - Vegetarian Capsule 500 mg", 500, "mg", 0.5, "g"),
        ("Basmati Rice 1 kg", 1, "kg", 1000, "g"),
        ("Sunflower Oil 1 L", 1, "l", 1000, "ml"),
        ("Rose Water 200ml", 200, "ml", 200, "ml"),
        ("Toor Dal 500 Gm", 500, "g", 500, "g"),
        ("Sunfeast Yippee Tricolor Pasta - Masala, 2x65 g", 130, "g", 130, "g"),
        ("Coconut Water 1.5 Ltr", 1.5, "l", 1500, "ml"),
        ("Super Bucket - Assorted Colour, 16 L", 16, "l", 16000, "ml"),
        ("Anti Blackhead Masksheet (Pack of 10)", 10, "pcs", 10, "pcs"),
    ],
)
def test_parses_sizes(name, qty, unit, base_qty, base_unit):
    p = parse_pack_size(name)
    assert p is not None
    assert p.qty == pytest.approx(qty)
    assert p.unit == unit
    assert p.base_qty == pytest.approx(base_qty)
    assert p.base_unit == base_unit


@pytest.mark.parametrize(
    "name",
    [
        "White Quinoa",
        "Brahmi Pearls",
        "Brass Angle Deep - Plain, No.2",
        "Lemon Gel 3 in 1",                          # no unit after the number
        "Coconut Flour 400 g + Agar Agar Vegetarian Gelatin 100 g",  # combo
        "Glass Jar 1 Long",                          # 'l' must not match inside a word
        "",
        None,
    ],
)
def test_returns_none_when_no_single_size(name):
    assert parse_pack_size(name) is None


@pytest.mark.parametrize(
    "name", ["Masala Veg Atta Noodles - Pouch", "Liquid Handwash - Refill Pouch"]
)
def test_pouch_without_quantity(name):
    p = parse_pack_size(name)
    assert p is not None and p.unit == "pouch" and p.qty is None


@pytest.mark.parametrize(
    "name", ["Printed Fabric Pencil Pouch - Purple, BB1263DPPL", "Sealing Clips - For Food Bag, Snack Pouch, Assorted"]
)
def test_pouch_as_product_is_not_a_pack(name):
    assert parse_pack_size(name) is None
