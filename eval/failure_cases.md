# Failure cases (observed in the Phase 8 eval runs)

All of these were observed in real runs of `python -m eval.run_eval` on 11 Nov 2026: three `--fresh` runs
plus the retrieval ablation. None is hypothetical. For each: the query, what happened, the root cause, the fix,
and whether we applied it. "After" results are from the final run in [results.md](results.md).

| # | Failure | Layer | Fixed? |
|---|---|---|---|
| 1 | Asked-for pack size ("5 kg") copied onto products → guard rejected twice → fallback | prompt / guard | ✅ applied |
| 2 | Hinglish: "dhaniya patta" → coriander **seeds**; "nimbu" → **Nimbu Achar** (pickle) | synonyms / retrieval | ✅ dhaniya patta; ⚠️ nimbu partly |
| 3 | Generic ingredient → specialty product: "peanuts" → *Peanuts - Hing Jeera* (namkeen), "salt" → *Lite Low Sodium Salt* | retrieval metadata | ✅ peanuts; ❌ salt (documented) |
| 4 | Without a domain filter, BM25 pulls the literal word from another domain: "haldi" → a turmeric **supplement**, "lemon" → lemon **essential oil** | retrieval | ✅ by design (category filter); measured in the ablation |
| 5 | "oats face scrub" inferred as **food** (keyword "oats") → breakfast oats | domain inference | ✅ applied |
| 6 | AI-suggested pet-tortoise list: most items aren't sold on BigBasket, and the list changes between runs | data coverage / LLM | ⚠️ partly (shown honestly) |
| 7 | Top rated mode picked "Organic **Tofu** - Soy Paneer" for paneer | cart mode | ❌ documented |

---

## 1. Pack size from the question copied onto products

- **Query (Q2):** "Compare Aashirvaad and Pillsbury whole wheat atta 5 kg on price and rating."
- **What happened (run 1, `--fresh`):** the answer said "Aashirvaad Whole Wheat Atta (5 kg) … [P23556]". The
  guard flagged `pack size '5 kg' is not in the product data`. The regenerated answer repeated it, so the bot
  showed the fallback for a question it could answer. (In Phase 6 the regeneration happened to drop the size,
  so this was intermittent.)
- **Root cause:** the dataset has no pack sizes for these products. The question's "5 kg" sits in the context
  (question line and sub-query headers), and the model attaches it to the product. The guard also rejected
  *any* mention of an unrecorded size, so "the data doesn't record whether these are 5 kg packs" would have
  failed too.
- **Fix (prompt + guard), applied:**
  - `catalogue_context` adds an explicit note when the question names a size that no retrieved product
    records: don't attach it to a product, and say in a separate sentence without citations or prices that
    sizes aren't recorded.
  - The guard now rejects an unrecorded size only in a sentence that cites a product or quotes a price.

  Two new tests in `tests/test_guard.py`.
- **After:** Q2 answers on the first attempt, with no regeneration: "Aashirvaad – Whole Wheat Atta, ₹406
  [P23556] … *The dataset does not record pack sizes.*"

## 2. Hinglish misses: "dhaniya patta" and "nimbu"

- **Queries (ablation):** `dhaniya patta` (coriander leaves), `nimbu` (lemon).
- **What happened (before):** in all four ablation variants, `dhaniya patta` returned *Coriander Seeds/Kottambari
  Beeja* and `nimbu` returned *Nimbu Achar* (lemon pickle). The hit@5 was 0 for both.
- **Root causes:**
  1. The synonym map knew *dhaniya* → coriander but not *patta* → leaves, so the query became "coriander"
     and seeds won. No sub-category hint fired, because the hint map is keyed on English words and was looked
     up on the raw Hinglish text.
  2. For `nimbu`, BM25 matches the literal word in "Nimbu Achar". The Hinglish token also pulls the
     embedding: fresh *Lemon* scores only 0.62 for "nimbu lemon" (0.69 for "lemon"). That is below
     `SUBCAT_RELAX_SIM` (0.65), so the fresh-produce hint is dropped and the pickle (0.70) wins.
- **Fixes applied:**
  - Synonyms: "dhaniya/dhania patta", "hara dhaniya" → coriander leaves; "pudina patta" → mint leaves;
    "methi patta" → fenugreek leaves.
  - `search_item` looks up the sub-category hint on the *expanded* query when the raw one has none.
  - *achar/achaar*, *syrup* and *sharbat* added to the processed-form words, so a raw ingredient never becomes
    a pickle or a syrup in a cart.

  Tests were added in `test_synonyms.py` and `test_cart.py`.
- **After (final run):** `dhaniya patta` → *Coriander Leaves* (Herbs & Seasonings) with the category filter
  (variants c and d). For `nimbu`, retrieval still ranks the
  pickle first, but a cart now reports *"nimbu: only processed products found ('achar', e.g. Nimbu Achar)"*
  under "Couldn't find" instead of adding a pickle. Grounding beats coverage.
- **Not applied:** embed the *English translation* of a Hinglish query ("lemon"), while BM25 keeps both
  forms. That would fix `nimbu` at the retrieval level, but it changes the similarity of every Hinglish query
  and so every calibrated threshold. We judged it too risky this late, and it is the first thing to try next.

## 3. Generic ingredient matched to a specialty product

- **Query (Q4, pasted poha recipe):** "… salt to taste, … and peanuts."
- **What happened (before):** the cart had *peanuts* → "Peanuts - Hing Jeera" (a flavoured namkeen snack,
  Snacks & Namkeen) and *salt* → "Lite - 15% Low Sodium Iodised Salt". The same happened in the ablation, where
  "salt" got the low-sodium salt as the hybrid top-1.
- **Root cause:** Best match = highest RRF. Short names that repeat the query word score well on both BM25 and
  vectors, and nothing marks a product as "plain". The processed-form guard looks for words like *masala* or
  *fried* in the name; "Hing Jeera" and "Lite" aren't such words. *Peanuts* also had no sub-category hint, so
  the whole food domain was searched.
- **Fix for peanuts, applied:** a sub-category hint (*peanut, groundnut* → Dals & Pulses / Organic Staples,
  where BigBasket keeps raw peanuts). After (final run): *Peanuts/Kadalekayi - Raw*.
- **Same pattern, not fixed:** *lemon juice* → "Lemon Juice Concentrate" (₹285) in the same cart, where a fresh
  lemon (₹6–10) is what the recipe means. The ingredient names a juice, so the processed-form guard
  correctly allows a juice; resolving "lemon juice" to "lemon" needs an ingredient-normalisation rule.
- **Fix for salt, not applied:** prefer the plain variant when the ingredient has no modifier, by penalising
  names with specialty words (*lite, low sodium, rock, pink, black, flavoured*) unless the recipe asks for
  them. Deferred, because a hand-made list of specialty words would grow item by item. The cart table shows
  the product name, so the user can see it.

## 4. Cross-domain matches without a category filter (ablation)

- **Queries:** `haldi`, `lemon` (labelled food).
- **What happened:** with plain hybrid retrieval (no filter), the top-1 for `haldi` was *Haldi Powder - Supports
  Anti-inflammatory Response…* (**Health & Medicine**, a supplement). For `lemon` it was *Lemon Essential Oil*
  (**Skin Care**). Vector-only got both right at top-1, so BM25's literal match is what pulls in the other
  domain. That is why hybrid without a filter has the worst wrong-domain rate in the ablation (4.3% of top-1s).
- **Root cause:** BM25 is domain-blind. "haldi" in a supplement's name is as good a match as in a spice's.
- **Fix:** this is what the category-aware filter is for (the domain from the recipe or kit, plus the
  sub-category hint). With the filter, the wrong-domain rate is 0 and MRR@5 is the best of all variants (see
  `results.md`). It was already part of the design; the ablation is the evidence.

## 5. Domain inference: "oats face scrub" → food

- **Query (ablation, catalogue path = variant d):** `oats face scrub`.
- **What happened (before):** `infer_domain` returned **food** (keyword rule: "oats"), so the search was
  filtered to food and returned *Rolled Oats + Instant Oats*. Hit@5 was 0, and the top-1 was in the wrong domain.
- **Root cause:** the personal-care keyword list had product types (face wash, serum …) but not body words, so
  "oats" was the only keyword that fired.
- **Fix, applied:** *face, skin, lip(s), scalp* added to the personal-care keywords. "oats face scrub" is now a
  tie (food 1, personal care 1), which falls through to the retrieval vote.

## 6. AI-suggested list for an uncovered need (pet tortoise)

- **Query (Q15):** "I'm getting a pet tortoise, what should I buy?" (no team kit matches; best: *New puppy*, 0.51).
- **What happened:**
  - **Run 1:** the LLM suggested *herbivore diet mix, tortoise enclosure, UVB lighting fixture, thermometer,
    substrate, water dish*. None matched a BigBasket product above 0.62, so the result was a `no_match`
    fallback.
  - **Run 2:** with the same prompt at temperature 0.1, a different list gave a cart of 1 (pet wipes, ₹395) and
    4 "Couldn't find" rows.
  - **Final run:** a `no_match` fallback again. This is the one question whose expected outcome (a cart) the
    final run missed, so the summary shows 15/16 fallbacks as expected. We kept the expectation instead of
    editing it to match: a user who asks this gets no cart.
  - In run 1 the app showed only the fallback text, not *what* had been searched.
- **Root cause:** a data-coverage gap (BigBasket has no reptile supplies), plus LLM non-determinism in the
  suggested list.
- **Fixes:**
  - **Applied:** the app now lists every searched item with its reason and closest product under "Searched but
    not found on BigBasket" when this fallback fires.
  - **Applied:** the user always reviews an AI list before searching (Phase 6).
  - **Applied:** the LLM cache makes a repeated question return the same list.
  - **Not applied:** making the list deterministic across fresh calls; Groq's models aren't deterministic even
    at low temperature.

## 7. Top rated mode trades relevance for rating

- **Query (Q16):** "paneer butter masala" with the sidebar on **Top rated**, 6 servings.
- **What happened:** *Paneer* → "Organic **Tofu** - Soy Paneer" (₹85.14). It is among the top 5 relevant hits and
  has the highest rating, but tofu isn't paneer.
- **Root cause:** Top rated chooses the highest rating among the top `MODE_POOL` (5) relevant hits. A substitute
  that mentions the ingredient's name passes the relevance guards.
- **Fix, not applied:** in Top rated / Budget mode, only consider hits within a similarity margin of the best
  hit (e.g. 0.05), or add substitute words (*tofu, soy, vegan, analogue*) to the mismatch list unless the user
  asked for them. Documented for a later iteration. The cart shows the product name, and Best match picks real
  paneer.
