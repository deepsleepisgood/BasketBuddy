"""Tests for Hinglish ↔ English query expansion."""
import pytest

from rag.synonyms import HINGLISH_TO_ENGLISH, expand_query


def test_map_has_about_forty_entries():
    assert len(HINGLISH_TO_ENGLISH) >= 40


@pytest.mark.parametrize(
    "query, must_contain",
    [
        ("jeera aur haldi kitne ka hai?", ["cumin", "turmeric"]),
        ("rajma chawal for 3", ["kidney beans", "rice"]),
        ("dahi", ["curd"]),
        ("besan", ["gram flour"]),
        ("Atta 5 kg", ["wheat flour"]),
    ],
)
def test_hinglish_adds_english(query, must_contain):
    out = expand_query(query)
    assert out.startswith(query)
    for word in must_contain:
        assert word in out


def test_english_adds_hinglish_for_catalogue_words():
    assert "jeera" in expand_query("cumin powder")
    assert "besan" in expand_query("gram flour")


def test_generic_english_is_not_reversed():
    assert expand_query("coconut oil") == "coconut oil"   # no "tel"
    assert expand_query("salt") == "salt"                 # no "namak"


def test_phrase_beats_single_word():
    out = expand_query("kasuri methi")
    assert "dried fenugreek leaves" in out
    assert out.count("fenugreek") == 1


def test_word_boundaries():
    assert expand_query("Haldiram bhujia") == "Haldiram bhujia"  # 'haldi' inside a word
    assert expand_query("raisins") == "raisins"                  # no partial matches


def test_no_duplicate_expansion():
    assert expand_query("jeera cumin") == "jeera cumin"


def test_patta_means_leaves():
    # Phase 8 eval: "dhaniya patta" matched coriander *seeds* before these phrases existed
    assert "coriander leaves" in expand_query("dhaniya patta")
    assert "mint leaves" in expand_query("pudina patta")
