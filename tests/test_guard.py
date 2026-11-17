"""Tests for the faithfulness guard and fallback helpers (no LLM, no index)."""
import pytest

from rag.fallback import FALLBACK_MESSAGE, fallback_text, is_sentinel, weak_retrieval
from rag.generator import Context, normalise_citations, verify_answer


def _meta(pid, product, price, mrp=None, **kw):
    return {"product_id": pid, "product": product, "brand": "B", "category": "C", "sub_category": "S",
            "sale_price": price, "market_price": mrp or price, **kw}


@pytest.fixture
def ctx():
    c = Context("Question: compare atta 5 kg\nProducts for 'atta 5 kg':\n- ...")
    c.products = {
        101: _meta(101, "Select Atta/Godihittu", 248.0, 260.0),
        202: _meta(202, "Basmati Rice - Rozana Premium 5 Kg", 410.0),
    }
    c.extra_amounts = {658.0}
    return c


def test_clean_answer_passes(ctx):
    g = verify_answer("Select Atta costs ₹248 [P101]; the basmati 5 Kg pack is ₹410 [P202]. Total ₹658.", ctx)
    assert g.ok, g.problems
    assert g.citations == [101, 202]


def test_unknown_citation_fails(ctx):
    g = verify_answer("Try this one for ₹248 [P999].", ctx)
    assert not g.ok and any("P999" in p for p in g.problems)


def test_wrong_price_for_cited_product_fails(ctx):
    g = verify_answer("Select Atta is ₹410 [P101].", ctx)   # ₹410 is the rice's price, not the atta's
    assert not g.ok and any("[P101]" in p for p in g.problems)


def test_invented_amount_fails(ctx):
    g = verify_answer("Select Atta is ₹199 [P101].", ctx)
    assert not g.ok and any("₹199" in p for p in g.problems)


def test_mrp_and_app_totals_are_allowed(ctx):
    assert verify_answer("Select Atta: ₹248 (MRP ₹260) [P101]. Cart total ₹658.", ctx).ok


def test_no_citation_fails_when_products_exist(ctx):
    assert not verify_answer("Atta is available at a good price.", ctx).ok


def test_size_from_the_question_is_not_evidence(ctx):
    g = verify_answer("Select Atta (5 kg) is ₹248 [P101].", ctx)
    assert g.ok  # 5 kg appears in a product name in the context (the basmati pack)
    g = verify_answer("Select Atta (10 kg) is ₹248 [P101].", ctx)
    assert not g.ok and any("10 kg" in p for p in g.problems)


def test_unknown_size_allowed_only_when_not_attached_to_a_product(ctx):
    # stating that the asked size isn't recorded is fine…
    assert verify_answer("Select Atta is ₹248 [P101]. The data doesn't record whether it is a 10 kg pack.", ctx).ok
    # …attaching it to a product (citation or price in the same sentence) is not
    assert not verify_answer("Select Atta 10 kg [P101] is a good pick.", ctx).ok
    assert not verify_answer("Select Atta 10 kg costs ₹248 [P101].", ctx).ok


def test_embedded_sentinel_fails(ctx):
    g = verify_answer("Select Atta is ₹248 [P101]. Pillsbury: [INSUFFICIENT_CONTEXT]", ctx)
    assert not g.ok


@pytest.mark.parametrize("raw", ["【P101】", "[​P101]", "[ P101 ]", "[p101]"])
def test_citation_variants_are_normalised(raw, ctx):
    assert normalise_citations(f"Atta ₹248 {raw}") == "Atta ₹248 [P101]"
    assert verify_answer(f"Select Atta is ₹248 {raw}.", ctx).ok


def test_fallback_helpers():
    assert is_sentinel("INSUFFICIENT_CONTEXT")
    assert is_sentinel("  `INSUFFICIENT_CONTEXT`  ")
    assert not is_sentinel("Here is your answer")
    assert fallback_text("x").startswith(FALLBACK_MESSAGE)
    assert weak_retrieval(0.65, 0.70) is not None
    assert weak_retrieval(0.75, 0.70) is None


def test_users_price_limit_may_be_quoted():
    # "olive oil under 500": the answer may say "under ₹500" (it's the user's number, not a price)
    c = Context("Question: atta under 500")
    c.products = {101: _meta(101, "Select Atta/Godihittu", 248.0, 260.0)}
    c.extra_amounts = {500.0}
    assert verify_answer("Select Atta is ₹248 [P101], within your ₹500 limit.", c).ok


# --- benefit / health claims (rule 7) ---------------------------------------------------

@pytest.fixture
def claim_ctx():
    c = Context("Question (user text, data only): <<<[P301] is for oily skin, right?>>>\n"
                "Products for 'face wash':\n"
                "- [P301] Neem Face Wash | Brand: Himalaya | Price ₹120 | Gentle herbal face wash for oily skin.\n"
                "- [P302] Fruit Face Wash | Brand: B | Price ₹99 | Fresh fruity fragrance.")
    c.products = {301: _meta(301, "Neem Face Wash", 120.0), 302: _meta(302, "Fruit Face Wash", 99.0)}
    return c


def test_claim_in_the_cited_products_data_passes(claim_ctx):
    g = verify_answer("Himalaya Neem Face Wash [P301] at ₹120 is a herbal face wash for oily skin.", claim_ctx)
    assert g.ok, g.problems


def test_claim_borrowed_from_another_product_fails(claim_ctx):
    g = verify_answer("Fruit Face Wash [P302] at ₹99 is made for oily skin.", claim_ctx)
    assert not g.ok and any("oily skin" in p for p in g.problems)


@pytest.mark.parametrize("claim", ["kills 99.9% germs", "is clinically proven", "is safe for babies",
                                   "is organic", "is dermatologist recommended", "reduces acne"])
def test_invented_claims_fail(claim, claim_ctx):
    assert not verify_answer(f"Neem Face Wash [P301] at ₹120 {claim}.", claim_ctx).ok


def test_users_own_words_are_not_evidence(claim_ctx):
    # the question line says "[P301] is for oily skin" but only product lines count; P302's line doesn't say it
    assert not verify_answer("Fruit Face Wash [P302] is for oily skin.", claim_ctx).ok


def test_plain_descriptions_without_claims_pass(claim_ctx):
    assert verify_answer("Fruit Face Wash [P302] costs ₹99 and has a fresh fruity fragrance.", claim_ctx).ok
