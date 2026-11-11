"""Tests for rag.chunking: splitting, header repetition, ids and metadata."""
import pandas as pd
import pytest

from rag.chunking import kit_chunks, load_kits, product_chunks, product_header, split_tokens
from rag.embeddings import get_tokenizer


@pytest.fixture(scope="module")
def tok():
    return get_tokenizer()


def _product(**overrides):
    row = dict(
        product_id=7, product="Sun Lite - Sunflower Refined Oil", brand="Fortune",
        category="Foodgrains, Oil & Masala", sub_category="Edible Oils & Ghee", type="Sunflower Oils",
        domain="food", sale_price=171.0, market_price=199.0, rating=4.1,
        pack_qty=pd.NA, pack_unit=pd.NA, pack_base_qty=pd.NA, pack_base_unit=pd.NA,
        description="Light and healthy oil.",
    )
    row.update(overrides)
    return pd.DataFrame([row])


def test_short_text_is_not_split(tok):
    assert split_tokens("a short description", tok, 50, 10) == ["a short description"]


def test_split_windows_overlap(tok):
    text = " ".join(f"word{i}" for i in range(300))
    parts = split_tokens(text, tok, max_tokens=100, overlap=20)
    assert len(parts) > 1
    for a, b in zip(parts, parts[1:]):
        tail = a.split()[-3:]
        assert " ".join(tail) in b, "consecutive windows must share the overlap"
    assert parts[-1].endswith("word299")


def test_split_rejects_bad_overlap(tok):
    with pytest.raises(ValueError):
        split_tokens("x", tok, 10, 10)


def test_short_product_is_one_chunk_with_template(tok):
    [c] = product_chunks(_product(), tok)
    assert c.id == "P7"
    assert c.text.startswith("Sun Lite - Sunflower Refined Oil | Brand: Fortune | Foodgrains, Oil & Masala > Edible Oils & Ghee")
    assert "Price: ₹171 (MRP ₹199)" in c.text and "Rating: 4.1/5" in c.text
    assert c.metadata["sale_price"] == 171.0 and c.metadata["domain"] == "food"


def test_long_product_repeats_header_in_every_subchunk(tok):
    long_desc = " ".join(f"Sentence number {i} about this oil." for i in range(150))
    df = _product(description=long_desc)
    chunks = product_chunks(df, tok)
    header = product_header(next(df.itertuples(index=False)))
    assert len(chunks) > 1
    assert [c.id for c in chunks] == [f"P7-{i}" for i in range(len(chunks))]
    for c in chunks:
        assert c.text.startswith(header)
        assert len(tok(c.text, add_special_tokens=False)["input_ids"]) <= 360
        assert c.metadata["n_chunks"] == len(chunks)


def test_missing_values_are_omitted_from_metadata(tok):
    [c] = product_chunks(_product(rating=float("nan")), tok)
    assert "rating" not in c.metadata and "pack_unit" not in c.metadata
    assert "Rating: not rated" in c.text
    assert all(v is not None for v in c.metadata.values())


def test_kits_are_labelled_synthetic_and_unique():
    chunks = kit_chunks(load_kits())
    assert len(chunks) >= 25
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(c.metadata["synthetic"] is True for c in chunks)
