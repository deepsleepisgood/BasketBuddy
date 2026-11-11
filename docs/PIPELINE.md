# How BasketBuddy works

BasketBuddy answers from one source of truth, the BigBasket catalogue, and shows its evidence. A message is
**planned** (what kind of request, which items), and each item is **retrieved** with hybrid search inside the
right part of the catalogue. Products are **picked and priced in Python**, and the LLM only **writes the
summary** from a context block of retrieved products. A guard checks that summary before it is shown. If
retrieval is weak or the guard fails, the bot says so instead of guessing.

```
 OFFLINE                                                   ONLINE (per message)
 ───────                                                   ────────────────────
 BigBasket CSV ─┐                                          user message (+ sidebar: mode, servings, pantry)
 Recipes CSV ×2 ┼─► clean ─► chunk ─► embed (bge-small)          │
 kits.json ─────┘   ingest/   1 product /     │                  ▼
                              1 recipe /      │          DECOMPOSE (rag/decompose.py), rule-based intent
                              1 kit per chunk │            out_of_scope ───────────────────────────► FALLBACK
                                    ┌─────────┴───┐        dish → recipe → ingredients × servings − pantry
                                    ▼             ▼        pasted recipe → LLM extract → ingredients
                              BM25 (pickle)   Chroma       need → team kit (≥0.70) or AI list → user ticks
                              per collection  (cosine)     catalogue → LLM split + brand/price/rating filters
                                    └──────┬──────┘                  │ sub-queries, each with a domain
                                           │                         ▼
                                           └──────────────► HYBRID RETRIEVE per sub-query (rag/retriever.py)
                                                           synonyms → vector top-20 + BM25 top-20
                                                           → RRF (k=60), inside domain / sub-category filter
                                                                     │
                                                                     ▼
                                                           PICK (rag/cart.py): relevance guards, mode,
                                                           totals in Python
                                                                     │ weak (< 0.70) / no match ──► FALLBACK
                                                                     ▼
                                                           LLM WITH CONTEXT (rag/generator.py)
                                                           only [P<id>] products, exact prices
                                                                     │
                                                                     ▼
                                                           GUARD: citations ∈ retrieved, ₹ = metadata,
                                                           no invented sizes
                                                             fail → regenerate once → fail → FALLBACK
                                                                     │ pass
                                                                     ▼
                                                           answer + cart table + 🔍 Evidence (app.py)
```

## The online steps

**1. Decompose (`rag/decompose.py`).** Rules first, so most requests need no LLM call to plan:
- **Out of scope** (stock, delivery, other shops, medical advice) → fallback with 0 LLM calls.
- **Pasted recipe** (several quantities) → the fast LLM extracts the ingredients as JSON, and every extracted
  name must appear in the pasted text, so no item is invented.
- **Need** ("I need to…", "getting a pet…") → a team-written kit if its similarity is ≥ 0.70. Otherwise an
  **AI-generated list**, which the user edits before anything is searched.
- **Dish** ("… for 4", a known recipe title, or similarity ≥ 0.80 to a recipe) → the recipe's ingredients,
  scaled to the servings, minus pantry items.
- **Catalogue** (everything else) → the fast LLM splits the question into sub-queries and constraints (max
  price, min rating, sort). Brands are detected from the catalogue's brand list, and a rule-based split is the
  backup.

Each sub-query carries a **domain** (food, personal_care, household, kitchen, baby or pets):
- Recipe ingredients are food, and kit items carry their own domain.
- Catalogue questions infer it, in order:
  1. a purpose phrase ("for hair" → personal_care);
  2. keyword rules;
  3. a vote over the top 10 unfiltered hits, which needs ≥ 70% agreement.

  If the vote isn't confident, no filter is used.

**2. Hybrid retrieval (`rag/retriever.py`).** For each sub-query:
1. Expand Hinglish words (71 entries, e.g. *jeera* → cumin, *haldi* → turmeric).
2. Take the vector top 20 (Chroma, cosine, with the BGE query instruction) and the BM25 top 20.
3. Fuse them with **Reciprocal Rank Fusion** (k = 60) and collapse sub-chunks to one product per entry.
4. Search inside the sub-query's domain, plus a sub-category hint from a hand-made map (*tomato* → Fresh
   Vegetables). The hint is dropped when its best similarity is below 0.65.

All the ingredients of a dish are embedded in one batched call.

**3. Pick and price (`rag/cart.py`).**
- **Unmatched:** hits below 0.62 similarity go to "Couldn't find on BigBasket", with a reason.
- **Relevance guards:**
  - *Food:* a raw ingredient is never matched to a processed product (*chicken* ≠ Chicken Fries, *tomato* ≠
    Tomato Ketchup). Spice powders are allowed for spices.
  - *Non-food:* the product must share a specific word with the item, and products of the right type are
    preferred (*moisturiser* ≠ face wash).
- **Modes**, among the top 5 relevant products: Best match = highest RRF, Budget = cheapest, Top rated =
  highest rating. Packs that cover the needed quantity are preferred when sizes are known.
- **Budget:** if a budget is set and exceeded, the cart is re-picked in Budget mode.

All totals are computed here, never by the LLM.

**4. Grounded generation (`rag/generator.py`).** The answer model gets a CONTEXT block that lists every allowed
product as `[P<id>]` with its price, MRP, rating, category, pack and a description snippet. It also gets the
app-computed totals and the budget verdict. The system prompt:
- allows only these products and prices;
- forbids stock, delivery and medical claims, and pack sizes that aren't in the data;
- asks for the sentinel `INSUFFICIENT_CONTEXT` when nothing in the context answers.

Temperature is 0.1.

**5. Guard and fallback (`rag/generator.py`, `rag/pipeline.py`).** Before an answer is shown, the guard checks:
- every `[P<id>]` is a retrieved product;
- every ₹ amount is a context price, MRP or app-computed total, and in a sentence citing one product, it is
  that product's own price or MRP;
- there is at least one citation;
- no pack size appears unless a product name or recipe quantity contains it;
- the sentinel is never embedded inside an answer.

On failure the answer is regenerated once, with the problems listed. If it fails again, the fallback is shown.

| Fallback trigger | When |
|---|---|
| `out_of_scope` | scope rules (stock, delivery, other platforms, medical) or the LLM's scope check |
| `weak_retrieval` | a catalogue question whose best vector similarity is below **0.70**, or nothing passes the relevance checks |
| `no_match` | no recipe, kit or item matched anything |
| `llm_sentinel` | the LLM says the context doesn't contain the answer |
| `guard` | the answer failed the guard twice |
| `llm_error` | the LLM is unavailable (a cart is still shown, without the written summary) |

## Thresholds and why

| Setting | Value | How it was chosen |
|---|---|---|
| `SIM_THRESHOLD` (catalogue fallback) | 0.70 | Phase 6: 20 answerable questions scored ≥ 0.717 and 15 unanswerable ones (laptop, iPhone, sofa …) ≤ 0.682. Re-checked in the eval (below). |
| `ITEM_MATCH_SIM` (item → "couldn't find") | 0.62 | Below it, ingredient matches in Phase 5 were consistently the wrong product. |
| `KIT_MATCH_SIM` / `RECIPE_MATCH_SIM` | 0.70 | "deep-clean my bathroom" → bathroom kit 0.72 vs the next kit 0.65; "Creamy Dal Makhani" 0.73. |
| `SUBCAT_RELAX_SIM` | 0.65 | A sub-category hint that finds nothing similar is wrong for that item, so it is dropped and the search uses the domain only. |
| `RRF_K`, candidates | 60, 20 + 20 | The standard RRF constant (Cormack et al., 2009); the brief's top 20 per method. |
| LLM calls per dish | ≤ 2 | 0 to plan (rules), 1 for the answer, and 1 regeneration only if the guard fails. |

## Evaluation results (Phase 8)

Full tables: [`eval/results.md`](../eval/results.md); failures and fixes: [`eval/failure_cases.md`](../eval/failure_cases.md).

- **16 end-to-end questions** (fresh LLM calls, no cache):
  - 15/16 fallbacks as expected. The miss is the AI-suggested pet-tortoise list, where nothing matched
    BigBasket.
  - Evidence present in 12/13 and **faithful in 12/12** answered questions.
  - Median latency 2.1 s (max 4.0 s); at most 2 LLM calls per question.
- **Retrieval ablation, 46 labelled sub-queries across all domains:**

  | Variant | hit@5 | MRR@5 | Wrong-domain top-1 |
  |---|---:|---:|---:|
  | vector only | 0.978 | 0.888 | 0.0% |
  | hybrid (vector + BM25) | 0.957 | 0.890 | 4.3% |
  | **hybrid + category filter** | **0.978** | **0.962** | **0.0%** |
  | hybrid + inferred domain (catalogue path) | 0.978 | 0.962 | 0.0% |

  BM25 on its own adds cross-domain matches (*haldi* → a turmeric supplement, *lemon* → lemon essential oil).
  The category filter removes them and ranks the right product first more often (MRR 0.89 → 0.96).
- **SIM_THRESHOLD re-check:** answerable catalogue questions scored ≥ 0.710 and unanswerable ones ≤ 0.692.
  0.70 keeps 15/15 and blocks 7/7 of those that reach retrieval; the other 8 are stopped earlier by the scope
  rules or the LLM's scope check. The margin is narrow (0.018), which is why the scope checks come first.


## Why we kept the whole catalogue

Words shared across categories are where pure vector search goes wrong:
"coconut oil" is both a cooking oil and a hair oil, "cleaner" can be a floor
cleaner or a face cleanser, "wipes" can be baby wipes or kitchen wipes, and
"oats" can be breakfast or an ingredient in a face scrub. In our data, only 8 of
the 182 products named "tomato" are fresh tomatoes; the rest are ketchup,
sauces and snacks. Keeping every category makes these collisions real, and that
is why BasketBuddy uses hybrid retrieval (vector + BM25) plus category-aware
metadata filters.

## Chunking strategy

We index three separate collections, because each has a different natural unit.

**Products: one product = one chunk.** Each row of the BigBasket list is an
atomic fact: a name, a brand, a category, a price, a rating and a description.
Splitting a row would separate a price from the product it belongs to, and
merging rows would mix two products' prices in one chunk, so the product row
is the chunk. The chunk text puts the structured fields first, then the
description:

```
{product} | Brand: {brand} | {category} > {sub_category} | Type: {type} |
Price: ₹{sale_price} (MRP ₹{market_price}) | Rating: {rating}/5 [| Pack: {size}] | {description}
```

- **Chunk size: 350 tokens** (measured with the embedding model's own
  tokenizer). The model accepts 512, but the median product chunk is 137 tokens
  and the 95th percentile is 331, so 350 keeps ~96% of products whole while
  stopping the longest descriptions (up to ~860 tokens) from being silently
  truncated at 512.
- **Overlap: 50 tokens**, used only when a description exceeds the budget. The
  window slides by 300 tokens, so a sentence cut at a boundary appears whole in
  the next sub-chunk.
- **Header repeated in every sub-chunk.** A sub-chunk that only said "...apply
  twice daily" would be useless on its own; repeating the name, brand, price
  and category keeps every sub-chunk self-describing and citable.
- **Structured fields are also metadata** (`product_id`, `brand`, `category`,
  `sub_category`, `domain`, `sale_price`, `rating`, `pack_*`), so retrieval can
  filter by domain or category and the cart can compute totals from metadata,
  never from LLM text.

**Recipes: one recipe = one chunk** of name + cuisine + course + diet + ingredient
names (the quantity-free `Cleaned-Ingredients` where available). Instructions
are not embedded: they are long and talk about frying and stirring, which would
pull the vector away from what a user types ("rajma chawal for 3"). They stay
in the dataframe for display.

**Kits: one kit = one chunk** of name + aliases + description + item names. Kits
are synthetic need/occasion lists written by the team (see `data/kits/kits.json`).

<!-- BEGIN chunk-stats (generated) -->
| Collection | Chunk unit | Source docs | Chunks | Split docs | Avg tokens | Median | p95 | Max | Why |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `products` | 1 product | 25,992 | 27,081 | 985 | 157.3 | 137 | 331 | 350 | One product = one atomic fact (name + price + rating + description). Split only above 350 tokens, 50-token overlap, header repeated. |
| `recipes` | 1 recipe | 6,163 | 6,163 | 0 | 83.2 | 79 | 138 | 273 | One recipe per chunk; matched by dish name, cuisine, diet and ingredient names. Instructions not embedded. |
| `kits` | 1 kit | 26 | 26 | 0 | 62.7 | 59 | 82 | 84 | One synthetic need/occasion list per chunk; matched by name, aliases and items. |

Token counts use the `BAAI/bge-small-en-v1.5` tokenizer (the model's limit is 512 tokens).
<!-- END chunk-stats -->
