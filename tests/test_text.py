"""Tests for the shared BM25 tokenizer."""
from rag.text import bm25_tokenize


def test_lowercases_and_strips_punctuation():
    assert bm25_tokenize("₹171 Sun-Lite Oil!") == ["171", "sun", "lite", "oil"]


def test_drops_stopwords():
    assert bm25_tokenize("Oil for the hair and skin") == ["oil", "hair", "skin"]


def test_same_output_for_chunk_and_query_text():
    chunk = "Cumin/Jeera/Jeerige - Whole | Brand: bb Popular"
    assert {"cumin", "jeera"} <= set(bm25_tokenize(chunk))
    assert bm25_tokenize("JEERA") == ["jeera"]
