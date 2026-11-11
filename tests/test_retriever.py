"""Integration tests against the built index (skipped when the index is missing).

These pin down the cross-category behaviour the demo relies on.
"""
import pytest

import config

pytestmark = pytest.mark.skipif(
    not (config.BM25_DIR / "products.pkl").exists(), reason="run `python -m ingest.build_index` first"
)


@pytest.fixture(scope="module")
def r():
    from rag import retriever

    retriever.warm_up()
    return retriever


def _subcats(result):
    return {h.metadata["sub_category"] for h in result.hits}


def test_coconut_oil_differs_by_context(r):
    food = r.search_item("coconut oil", "food", audit=False)
    hair = r.search_item("coconut oil", "personal_care", audit=False)
    assert _subcats(food) <= {"Edible Oils & Ghee", "Oils & Vinegar", "Organic Staples"}
    assert _subcats(hair) <= {"Hair Care", "Skin Care"}


def test_tomato_in_a_recipe_is_fresh(r):
    res = r.search_item("tomato", "food", audit=False)
    assert res.best.metadata["sub_category"] == "Fresh Vegetables"
    assert all("ketchup" not in h.metadata["product"].lower() for h in res.hits)


def test_floor_cleaner_is_never_face_cleanser(r):
    res = r.search_item("cheapest floor cleaner", "household", audit=False)
    assert all(h.metadata["domain"] == "household" for h in res.hits)


def test_hinglish_finds_spices(r):
    res = r.search_item("jeera", "food", audit=False)
    assert "cumin" in res.expanded_query
    assert res.best.metadata["sub_category"] in {"Masalas & Spices", "Organic Staples"}


def test_hits_are_one_per_product_with_evidence(r):
    res = r.hybrid_search("face wash for oily skin", k=5)
    keys = [h.doc_key for h in res.hits]
    assert len(keys) == len(set(keys)) == 5
    for h in res.hits:
        assert h.vector_sim is not None and h.rrf_score > 0
        assert h.vector_rank is not None or h.bm25_rank is not None


def test_unknown_item_scores_low(r):
    # gujiya is not in the snapshot: similarity stays well below a real match
    assert r.search_item("gujiya", "food", audit=False).best_vector_sim < 0.62
    assert r.search_item("tomato", "food", audit=False).best_vector_sim > 0.7


def test_audit_flags_wrong_domain(r, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WRONG_DOMAIN_LOG", tmp_path / "wd.jsonl")
    res = r.search_item("chicken", "food", audit=True)
    assert res.wrong_domain  # unfiltered #1 is dog food
    assert (tmp_path / "wd.jsonl").read_text(encoding="utf-8").count("\n") == 1


@pytest.mark.parametrize(
    "q, domain",
    [
        ("coconut oil for hair", "personal_care"),
        ("coconut oil for cooking", "food"),
        ("cheapest floor cleaner", "household"),
        ("Compare Dove and Pears bathing soaps on price and rating", "personal_care"),
    ],
)
def test_infer_domain(r, q, domain):
    assert r.infer_domain(q)[0] == domain


def test_ambiguous_query_is_not_forced(r):
    assert r.infer_domain("wipes")[0] is None
