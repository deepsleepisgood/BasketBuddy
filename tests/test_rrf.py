"""Tests for Reciprocal Rank Fusion (pure function, no index needed)."""
import pytest

from rag.retriever import Filter, rrf_merge


def test_rrf_scores_match_formula():
    fused = dict((d, s) for d, s, _ in rrf_merge({"vector": ["a", "b"], "bm25": ["b", "c"]}, k=60))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)


def test_agreement_beats_single_first_place():
    # "b" is 2nd in both lists; "a" is 1st in only one. Fusion rewards agreement.
    order = [d for d, _, _ in rrf_merge({"vector": ["a", "b"], "bm25": ["c", "b"]})]
    assert order[0] == "b"


def test_ranks_are_reported_per_method():
    fused = {d: r for d, _, r in rrf_merge({"vector": ["a", "b"], "bm25": ["b"]})}
    assert fused["a"] == {"vector": 1}
    assert fused["b"] == {"vector": 2, "bm25": 1}


def test_ties_keep_first_seen_order():
    order = [d for d, _, _ in rrf_merge({"vector": ["a"], "bm25": ["b"]})]
    assert order == ["a", "b"]


def test_empty_lists():
    assert rrf_merge({"vector": [], "bm25": []}) == []


def test_filter_to_chroma_where():
    assert Filter().to_where() is None
    assert Filter("food").to_where() == {"domain": "food"}
    assert Filter("food", ("Dairy",)).to_where() == {
        "$and": [{"domain": "food"}, {"sub_category": {"$in": ["Dairy"]}}]
    }
    assert Filter("food", ("Dairy",)).level == "sub_category"
