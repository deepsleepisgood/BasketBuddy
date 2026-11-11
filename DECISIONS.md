# Decisions log

A running, chronological record of what was built, every decision taken and
why, and how each step was verified. Newest phase at the bottom. Detailed row
counts live in `docs/DATA_LOG.md`; chunking rationale lives in `docs/PIPELINE.md`.

---

## Phase 0: Setup (11 Nov 2026)

**What was done**
- Project lives directly in `RAG CHATBOT ASSGN/` (no `basketbuddy/` subfolder).
- Virtual environment `.venv` created from **Python 3.11**.
- Installed: pandas, pyarrow, streamlit, sentence-transformers, chromadb, rank_bm25,
  python-dotenv, pyyaml, tqdm, pytest, kaggle and the four LLM SDKs.

**Decisions**
| Decision | Why |
|---|---|
| Python 3.11, not the system default 3.14 | ChromaDB and torch wheels for 3.14 on Windows were a risk; 3.11 is well supported. |
| Download Kaggle files over the public API without a token | The BigBasket and both recipe datasets download anonymously, so no `kaggle.json` was needed. |
| Original recipe dataset (`kanishk307/...`) not used | Kaggle returned HTTP 403 (page removed). Replaced, at the team's instruction, by `sukhmandeepsinghbrar/indian-food-dataset` (primary) + `atharvaarya25/indian-food-recipes-dataset-cleaned` (ingredient names). |

**Verification:** all three CSVs matched the expected row counts (27,555 / 6,871 / 5,938)
and column names exactly.

---

## Phase 1: Data cleaning (11 Nov 2026)

**What was done**
- `ingest/clean_products.py` → `data/clean/products.parquet` (**27,555 → 25,992 rows**, all 11 categories kept).
- `ingest/clean_recipes.py` → `data/clean/recipes.parquet` (**6,871 → 6,163 recipes**).
- Helpers: `ingest/pack_size.py` (pack-size parser), `ingest/text_fix.py` (mojibake repair),
  `ingest/datalog.py` (writes generated sections into `docs/DATA_LOG.md`).
- `docs/DATA_LOG.md`: sources, licences, Mendeley DOI provenance, 9 assumptions, per-step row counts.

**Decisions: products**
| Decision | Why |
|---|---|
| Keep every category | Cross-category word collisions ("coconut oil", "cleaner", "tomato") are the core technical story for hybrid + filtered retrieval. |
| Drop only genuinely broken rows: no name (1), empty description after cleaning (153) | The brief says to drop only broken records. Short but real descriptions ("Made with Potato") are kept, so the earlier idea of a "<4 words" cut-off was dropped. |
| Duplicates on (product, brand, sale_price): keep one, store the other sub-category in `also_in` (1,409 removed) | Inspection showed these are the same item listed under two sub-categories, so nothing is lost. |
| Same product at different prices kept as separate rows | They are different pack sizes (e.g. Fortune Sun Lite ₹866 vs ₹171) even though the size is not recorded. |
| Repair mojibake (`â€™` → `’`) in ~735 descriptions with a small custom function | UTF-8 text had been mis-decoded as Windows-1252, sometimes twice. A ~40-line repair avoids adding a dependency; unrecoverable fragments are scrubbed to a space. |
| Strip "For Beauty tips, tricks & more visit …" boilerplate (1,101 rows) | Marketing text with no product facts. Two sites appeared (`bigbasket.blog` and `lookbeautiful.in`), and the URL was sometimes glued to the next sentence, so the pattern stops at the domain. |
| Normalise non-breaking / zero-width spaces in names (~90 rows) | They broke string matching and displayed as `�`. |
| Missing ratings stay empty (31%), never 0 | Unrated is unknown, not bad. "Top rated" mode will rank them last. |
| `domain` column: food / personal_care / household / baby / kitchen / pets / other | Coarse retrieval filter. Baby food under Foodgrains → `baby`; pet sub-category → `pets`; Gardening → `other`. |
| Pack size parsed from the name only | Only ~1% of names contain one (286 products); descriptions mention unrelated sizes ("500 mg capsule"). Combo packs ("A 500 g + B 250 g") return no size rather than a wrong one. "Pencil Pouch" is a product, so "pouch" only counts as a trailing pack marker. "(Pack of 10)" → 10 pcs. |

**Decisions: recipes**
| Decision | Why |
|---|---|
| Drop recipes whose name or ingredients are still Hindi (569) | They cannot drive an English ingredient search. |
| Keep recipes whose *instructions* only are Hindi (266), blanking the instructions | Instructions are not embedded, so the recipe is still useful for a cart. |
| Two-step dedup: exact name (67), then name without "Recipe" / "(Recipe In Hindi)" (66) | Many "X (Recipe In Hindi)" rows repeat an English "X Recipe"; the English-page version is preferred. |
| Join the cleaned-ingredients file on URL ignoring http/https and www | A naive join matched only 44%; the files differ only in URL scheme. After normalising: **94.8%** matched. |
| Servings outside 1–20 treated as missing (48 rows) | Values like 1,000 are data errors and would break servings scaling. |
| Keep non-Indian cuisines, flagged `is_indian=False` (1,966) | Shoppers do cook pasta and Thai curry; revisit only if they hurt retrieval in the eval. |

**Verification:** 36 pytest tests (pack-size parser, mojibake repair, boilerplate removal,
domain map). Re-running both scripts rewrites their DATA_LOG sections in place (idempotent).
Spot checks: 0 boilerplate left, 6 / 25,992 descriptions with residual garbled characters.

**Findings that shape later phases**
- Pack size is mostly missing, so the "Fortune sunflower oil **1 L**" eval question
  cannot be answered exactly: both prices will be shown. Expected failure case.
- Only 8 of 182 "tomato" products are fresh tomatoes, so the category filter is necessary.

---

## Phase 2: Chunking and kits (11 Nov 2026)

**What was done**
- `rag/chunking.py`: product, recipe and kit chunkers, plus `python -m rag.chunking`, which
  prints statistics and writes them into `docs/PIPELINE.md`.
- `rag/embeddings.py`: the one place the embedding model is defined (`BAAI/bge-small-en-v1.5`).
- `data/kits/kits.json`: **26 synthetic kits, 173 items**, labelled
  "Synthetic kit lists, authored by the team; products and prices come from real BigBasket data."
- `docs/PIPELINE.md`: chunking rationale and stats table.

**Results**
| Collection | Chunk unit | Chunks | Median tokens | p95 | Max |
|---|---|---:|---:|---:|---:|
| products | 1 product | 27,081 (from 25,992; 985 split) | 137 | 331 | 350 |
| recipes | 1 recipe | 6,163 | 79 | 138 | 273 |
| kits | 1 kit | 26 | 59 | 82 | 84 |

**Decisions**
| Decision | Why |
|---|---|
| Measure chunk size in the embedding model's own tokens | "350 tokens" is only meaningful in the tokenizer that will embed it. |
| 350-token budget, 50-token overlap, header repeated in every sub-chunk | Median product is 137 tokens, so ~96% stay whole; the longest (~860 tokens) would otherwise be truncated at the model's 512 limit. Repeating the header keeps every sub-chunk citable. |
| Split by slicing the original text at token offsets | Avoids decode artefacts (lost capitals, odd spacing) that token-decoding introduces. |
| Product header adds `Pack: …` only when a size is known | Keeps the brief's template; no invented sizes. |
| Missing metadata values omitted, not stored as None | Chroma rejects None/NaN metadata. |
| Recipe chunks use quantity-free ingredient names, no instructions | Instructions ("fry until golden") would pull vectors away from what users type (a dish name). |
| Each kit item carries `domain` and, where ambiguous, `sub_category` | Gives retrieval its category filter (e.g. "coconut hair oil" → Hair Care, not Edible Oils). |
| Kit items validated against the catalogue; 14 reworded | e.g. "moisturiser gel" → "oil control moisturiser"; "mosquito repellent cream" moved to household where BigBasket stocks it. |
| "gujiya" deliberately kept in the Holi kit | Not in the snapshot, so it demonstrates the "Couldn't find on BigBasket" path. |
| Kit added beyond the brief's list: basic hair care, pantry staples, period care, first-aid box, movie night, chai for guests | Covers the "coconut oil for hair" demo and common needs; first-aid kit says "product descriptions only, not medical advice". |

**Verification:** 44 pytest tests passing (overlap between windows, header repeated, ids
unique, ≤360 tokens per sub-chunk, no None metadata, kits labelled synthetic). A real long
product (a sheet mask, 4 sub-chunks of ≤349 tokens) inspected by hand.

---

## Phase 3: Indexing (11 Nov 2026)

**What was done**
- `ingest/build_index.py`: chunks → bge embeddings → three persistent Chroma collections
  (`products`, `recipes`, `kits`) in `index/chroma/`, plus one pickled BM25 index per
  collection in `index/bm25/`.
- `rag/text.py`: `bm25_tokenize`, shared by index build and query time.

**Decisions**
| Decision | Why |
|---|---|
| Compute embeddings ourselves and pass them to Chroma (`embedding_function=None`) | One embedding code path for indexing and querying; stops Chroma downloading its own default model. |
| Cosine space (`configuration={"hnsw": {"space": "cosine"}}`), normalised vectors | bge is trained for cosine similarity; the API form was checked against the installed ChromaDB 1.5.9 rather than guessed. |
| BGE query instruction prepended to queries only, not passages | The model card's recommended usage for short-query → passage retrieval. |
| BM25 tokeniser: lowercase, strip punctuation, small stop-word list | The brief's spec; the same function runs at query time so tokens always line up. |
| BM25 pickle stores ids, texts and metadata alongside the index | BM25 hits can be shown and filtered without a round-trip to Chroma. |
| Idempotent via a fingerprint (model + ids + texts + metadata) in `index/manifest.json` | A second run skips collections that haven't changed; `--force` rebuilds. Manifest is saved after each collection, so an interrupted build resumes. |

**Build speed: CPU was too slow, so the build moved to the GPU**
- The first build ran on CPU and was stopped after ~33 min, unfinished. A benchmark showed
  **~12 chunks/s on CPU** (Ryzen 9 5900HX). That is close to the CPU's real limit for
  ~160-token chunks: the "100+/s" estimate given earlier applies to short sentences, so the
  original "10–20 min" estimate was wrong. The ONNX Runtime backend was tried and was no
  faster (12/s, identical vectors), confirming the CPU was the bottleneck. Two PS-07 eval
  jobs sharing the CPU slowed it further.
- The laptop has an **NVIDIA RTX 3050 Ti (4 GB)**. With the team's approval, the venv's torch
  was switched from `2.15.0+cpu` to **`2.15.0+cu134`** (the driver supports CUDA 13.5; torch
  2.15 is only published for cu132/cu134). Measured: **~196 chunks/s, about 16× faster**.
- `rag/embeddings.py` now picks `cuda` when available and falls back to `cpu`, so the app
  still runs on a machine without a GPU (queries are a few short strings, fast on CPU).
- Batch size 32 benchmarked fastest on this GPU (196/s vs 178/s at 64, 137/s at 128).
- Side effect: installing `sentence-transformers[onnx]` for the ONNX test downgraded
  sentence-transformers 6.2.0 → **5.7.0** (required by `optimum-onnx`). Everything works on
  5.7.0; ONNX is not used by the app.

**Results**
| Collection | Chunks | Embed + write time | BM25 time |
|---|---:|---:|---:|
| products | 27,081 | 229.1 s | 4.1 s |
| recipes | 6,163 | 29.0 s | 0.4 s |
| kits | 26 | 0.2 s | 0.0 s |
| **Total build** | **33,270** | **298 s (~5 min)** | |

Index on disk: `index/chroma/` 235 MB, `index/bm25/` 45 MB.

**Verification**
- Chroma count = BM25 count = chunk count for all three collections, with identical id sets.
- Second run of `python -m ingest.build_index`: all three collections skipped as up to date (43 s,
  spent rebuilding chunks to compute fingerprints).
- 47 pytest tests passing (3 new for `bm25_tokenize`).
- Smoke queries (top-3, vector vs BM25):

| Query | Vector search | BM25 |
|---|---|---|
| coconut oil for hair | #1 "Coconut Hair & Skin Oil" (**Skin Care**), then Hair Care | all Hair Care (Vatika) |
| coconut oil for cooking | cooking spray, cold-pressed coconut oil (food) | same, food |
| tomato | fresh tomatoes (**Fresh Vegetables**) | **ketchup and puree** |
| floor cleaner | floor cleaners | floor cleaners |
| recipes: "dal makhani" | Jain Style Dal Makhani, Creamy Dal Makhani, Masoor Dal Makhani | |
| kits: "deep-clean my bathroom" | Bathroom deep-clean (0.723), Kitchen deep-clean (0.650) | |

**Findings that shape later phases**
- **"tomato": BM25 is the method that fails**, returning ketchup and puree, while vector
  search finds fresh tomatoes. This is the opposite of the brief's assumption and shows why
  fusion plus a category filter beats either method alone. Candidate failure case for the eval.
- **Cold start: the first query took 23.5 s; later queries take ~35 ms.** CUDA initialisation,
  model load and Chroma loading its HNSW index all happen on first use. The Streamlit app must
  load everything with `st.cache_resource` and run one warm-up query at startup, so the first
  live demo question doesn't stall.
- Dish lookup returns variants first ("Jain Style Dal Makhani" above "Creamy Dal Makhani"), so the
  dish path should show the matched recipe name, as the brief requires, and may need a
  plain-name preference.
- The kit similarity gap (0.723 vs 0.650) suggests a kit threshold of around 0.70; to be tuned in Phase 4/6.

---

## Phase 4: Retrieval (11 Nov 2026)

**What was done**
- `rag/retriever.py`: `vector_search`, `bm25_search`, `hybrid_search` (RRF), `search_item` /
  `search_items` (per-ingredient search with hints, relaxation and audit), `infer_domain`,
  `warm_up`.
- `rag/synonyms.py`: Hinglish ↔ English map (**71 entries**) and `expand_query`.
- `rag/category_map.py`: `ITEM_CATEGORY_MAP` (178 terms) and `lookup_subcategories`.
- `config.py`: retrieval constants (`VECTOR_CANDIDATES=20`, `BM25_CANDIDATES=20`, `RRF_K=60`,
  `TOP_K=5`, `SUBCAT_RELAX_SIM=0.65`, `DOMAIN_VOTE_SHARE=0.7`) and the wrong-domain log path.

**How a sub-query is searched (`search_item`)**
1. Expand synonyms ("jeera" → "jeera cumin").
2. Embed once; compute BM25 scores over every chunk once (then masked per filter).
3. Look up a sub-category hint for (item, domain), e.g. tomato + food → Fresh Vegetables.
4. Hybrid search under `domain + sub_category`; if the best vector similarity is below 0.65,
   relax to `domain` only (recorded on the result).
5. Audit: take the *unfiltered* top-1 and log it to `logs/wrong_domain.jsonl` when it is in
   another domain.

**Decisions**
| Decision | Why |
|---|---|
| RRF over the top 20 of each method, k = 60 | As specified; rank-based, so BM25 scores and cosine similarities need no normalisation. Documents found by both methods outrank a document that is first in only one. |
| Collapse sub-chunks to one hit per product, keeping its best rank per method | Otherwise one long product could fill several of the top-5 slots. |
| Every fused hit gets a vector similarity, even BM25-only hits (fetched from Chroma and dotted with the query) | The fallback threshold (Phase 6) needs a similarity for the best hit whichever method found it. |
| BM25 scores computed once per query and masked with precomputed numpy arrays of domain/sub-category | The filtered, unfiltered (audit) and relaxed passes reuse one scoring pass; filtering 27k rows is a vectorised mask, not a Python loop. |
| Filter = Chroma `where` + BM25 post-filter, as two views of one `Filter` object | Both methods always see the same candidate set, so fusion is fair. |
| Sub-category hint relaxes to domain when best similarity < 0.65; never relaxes past the domain | A wrong hint shouldn't starve the search, but a recipe ingredient must never fall through to dog food or hair oil. |
| Synonyms expand both ways, but English → Hinglish only for words BigBasket uses in names (Jeera, Besan, Rajma, Toor Dal, Paneer …) | "cumin" should also hit "Jeera Powder"; reversing generic words ("oil" → "tel", "salt" → "namak") only adds noise. |
| Synonym and hint matching on word boundaries, longest phrase first | "haldi" must not match inside "Haldiram"; "kasuri methi" must beat "methi". |
| Fresh-produce hints switched off for processed forms (puree, paste, juice, chips, powder …) | "tomato puree" and "ginger garlic paste" are not fresh vegetables. Any "… powder"/"… masala" in food goes to spices. |
| Context-dependent entries: "coconut oil" → edible oils (food) / Hair & Skin Care (personal care); "cleaner", "wipes", "oats", "soap", "shampoo" likewise | The brief's cross-category story, made explicit and testable. |
| `infer_domain`: purpose phrase ("for hair") → keyword rules → retrieval vote (≥ 70% of the unfiltered top-10), else None = search everything | Cheap and explainable; no LLM call. Ambiguous queries ("wipes") are not forced into a domain, as the brief specifies. |
| `warm_up()` at app start | Cold start measured at **34 s** (27.7 s of it importing torch/sentence-transformers and loading the model; CPU and GPU load equally slowly, so the device isn't the cause). After warm-up a query takes ~100 ms. |
| Keep the GPU for query embedding | A single query embeds faster on the GPU than the 165 ms it takes on the CPU; the code falls back to CPU automatically. |

**Results (real index)**
| Sub-query (domain) | Filter used | Top result | Unfiltered #1 (audit) |
|---|---|---|---|
| tomato (food) | sub-category | Tomato - Hybrid, Fresh Vegetables, ₹14 | same |
| coconut oil (food) | sub-category | Coconut Oil - Cold Pressed, Oils & Vinegar | same |
| coconut oil (personal_care) | sub-category | Organic Virgin Coconut Oil, **Hair Care** | cooking oil → **logged** |
| haldi (food) | sub-category | Organic Turmeric Powder/Haldi, Masalas & Spices | haldi *health supplement* → **logged** |
| chicken (food) | domain | Chicken Fries, Sausages | **dog food** → **logged** |
| ginger garlic paste (food) | domain (no fresh hint) | Paste - Ginger Garlic, Masalas & Spices | same |
| gujiya (food) | domain | best similarity **0.58**, BM25 found nothing | (not in catalogue) |

- Domain guesses: "coconut oil for hair" → personal_care, "…for cooking" → food, "cheapest floor
  cleaner" → household, "Compare Dove and Pears bathing soaps" → personal_care, "wipes" → not confident.
- **Latency: a 12-ingredient dish takes 1.44 s of retrieval** (~100–140 ms per ingredient, including
  the audit pass); target is < 6 s for the whole answer.

**Verification:** 89 pytest tests passing: RRF formula, rank reporting and ties (6); synonym expansion
(11); category map, including a check that every hinted sub-category exists in the catalogue (13);
and 13 integration tests on the real index (coconut oil by context, tomato is fresh, floor cleaner never
a face cleanser, one hit per product, gujiya scores low, audit logs chicken → dog food, domain guesses).

**Findings that shape later phases**
- **The fresh-chicken gap:** the snapshot has no raw chicken, only sausages, marinades and masalas.
  A chicken-curry cart must put chicken under "Couldn't find", not sell chicken fries. Needs an
  ingredient-level relevance threshold in Phase 5.
- **Unmatched threshold:** gujiya's best similarity is 0.58 vs 0.69–0.86 for real matches, so the
  "Couldn't find" cut-off is likely ~0.62–0.65. "Chicken" scored 0.655 for chicken fries, though, so
  similarity alone won't catch every bad match; to be tuned on the eval set in Phase 8.
- **RRF rewards agreement over a single strong rank:** for "floor cleaner" the top fused hits were
  vector #10–14 / BM25 #7–12 (all genuine floor cleaners). That's correct RRF behaviour, but it is a
  talking point for Q&A.
- The audit log already holds three real wrong-domain cases (coconut oil, haldi, chicken), which are
  evidence for `eval/failure_cases.md`.

---

## Phase 5: Query decomposition and carts (11 Nov 2026)

**What was done**
- `rag/llm.py`: one interface over groq | gemini | anthropic | openai (`LLM_PROVIDER` in `.env`,
  else the first key found), plus a sqlite disk cache (`cache/llm_cache.sqlite`). `.env.example` added.
- `rag/decompose.py`: rule-based intent classifier, entity extraction (servings, budget, mode,
  pantry), and one planner per intent → `Plan` (sub-queries plus a human-readable `trace` for the
  Evidence panel).
- `rag/ingredients.py`: recipe ingredient parser ("200 gram Paneer (…) - cubed" → 200 g, paneer, note).
- `rag/cart.py`: per-item product choice (Best match / Budget / Top rated), relevance guards, pack
  coverage, budget switch, unmatched list, Python-computed totals; and `retrieve_catalogue` (price /
  rating filters and sorting).
- Retriever extensions: a `brand` filter and an explicit `sub_categories` override (`ItemSpec`).
- Kit change: removed the alias "coconut oil for hair" from the hair-care kit, so the brief's
  "coconut oil for hair vs cooking" demo is answered as a catalogue question, not a kit.

**LLM provider (team decision): Groq.** Key in `.env` (gitignored). Models picked from the key's
live model list and benchmarked on a pasted-recipe extraction (all three extracted all 10 items, none invented):

| Model | Latency | Role |
|---|---:|---|
| `openai/gpt-oss-20b` | 0.87 s | fast: extraction, decomposition, suggested lists |
| `openai/gpt-oss-120b` | 1.16 s | answer (Phase 6) |
| `qwen/qwen3.8-27b` | 2.28 s | not used |

**Decisions**
| Decision | Why |
|---|---|
| Classify with rules, not an LLM (order: out-of-scope → pasted recipe → need → dish → catalogue) | Free, instant, and explainable in Q&A; the rule that fired is shown in the trace. The LLM is only used where rules can't do the job. |
| LLM calls per path: dish 0, need 0 (kit matched) or 1 (suggested list), recipe_text 1, catalogue 1; Phase 6 adds 1 | Meets the brief's "at most 2 LLM calls per dish query" with room to spare. |
| The catalogue LLM call also re-checks scope (`in_scope: false`) | Catches unrelated questions the rules miss, at no extra call. |
| "… for N" with no product-question words → dish | "paneer butter masala for 4", "rajma chawal for 3" and "chicken curry for 2" were first misrouted to catalogue. |
| "cooking" removed from dish verbs | "coconut oil for cooking" was misrouted to the dish path. |
| Dish → recipe: exact title match first, then rank candidates (similarity ≥ 0.70) by literal word overlap → synonym overlap → fewest *variant* words ("jain", "masoor", "without onion") → fewest extra words → RRF | Retrieval alone picked "Jain Style Dal Makhani", "Paneer **Matar** Butter Masala" (an exact "Paneer Butter Masala" exists) and "Cajun Kidney Beans Rice" for rajma chawal (synonym expansion made "kidney beans" count as "rajma"). Now: Creamy Dal Makhani, Paneer Butter Masala (exact), Spiced Rajma Pulao. Descriptor words ("creamy", "restaurant style") are not penalised. `RECIPE_MATCH_SIM` lowered 0.75 → 0.70 because "Creamy Dal Makhani" scores 0.73. |
| No recipe above threshold → no cart, no invented recipe | Grounding beats coverage. |
| Recipe ingredients and quantities from **our own parser of `TranslatedIngredients`**, not from dataset 3's `Cleaned-Ingredients` | **Deviation from the brief, on evidence.** Cleaned names glitch ("gram paneer", "cloves garlic", "oilasafoetida") and carry no quantities, so they would need fuzzy re-alignment to the raw list. Our parser yields name + quantity + unit in one step. `clean_ingredients` is still kept and shown as evidence. |
| Parser rules: "2-1/2" = 2.5; ranges ("2-3", "10 to 12") → upper end; "7/8 Green Chillies" (plural, no unit) → 8; "3/4 Onion" → 0.75; "6 cloves Garlic" → unit clove but "4 Cloves (Laung)" → the spice; water skipped | Each rule fixes a real string from the dataset; 16 parser tests. |
| Shelf hint looked up on the ingredient name *without* its bracketed alias | "Coriander (Dhania) Leaves" produced "coriander dhania leaves", which missed the fresh-herb hint and matched coriander **powder**. |
| Servings scaling = user servings ÷ recipe servings, applied to every quantity | e.g. chicken curry ×0.50 (4 → 2), rajma ×0.75 (4 → 3). |
| Pantry exclusion: message ("I already have salt and ghee") + sidebar; matched through synonyms and plurals | "haldi" excludes turmeric powder; "salt" does **not** exclude salted butter. Excluded items are listed, not hidden. |
| Need → kit if kit similarity ≥ 0.70, else an LLM list labelled "Suggested list (AI-generated)" | Bathroom 0.72 and oily skin 0.86 match their kits; "pet tortoise" (best kit 0.51) gets an AI list. |
| **Food relevance guard:** a raw ingredient may not match a processed product (fries, ketchup, masala, drops, fried strips …) unless the ingredient names that form; powder/masala allowed only for a spice named on its own | Fixes chicken → "Chicken Fries" / "Masala - Chicken" / "Fried Strips - Chicken" (now **Couldn't find**: the snapshot has no raw chicken), cinnamon stick → "Dalchini Drop", red chilli sauce → chilli powder, cardamom pods → cardamom powder. "haldi" → turmeric powder is still allowed. |
| **Non-food relevance guard:** the product must share a specific word with the item, and products naming the item's head noun are preferred | "oil control moisturiser" had become an "Oil Control Face Wash" (now a real moisturiser); "tortoise food mix" had become cat food (now Couldn't find). |
| Modes: Best match = top RRF; Budget = cheapest of top 5 relevant; Top rated = best rating of top 5, unrated last | As specified. If a budget is set and the cart is over it, picks are redone in Budget mode without re-retrieval, and the switch is reported. |
| Pack coverage preferred when the recipe quantity is in g/kg/ml/l and pack sizes are known | Implemented and tested, but only ~1% of products have a size, so it rarely fires. |
| Catalogue: brand filter when a brand that sells in the question's domain is named; generic brand names ignored | "Dove" vs "Pears" searched separately; "Pears" is a brand only outside food (fresh pears stay fruit). |
| Catalogue constraints (max price, min rating, sort) applied in Python over a 20-hit pool | The LLM only extracts them; it never filters or sorts. |

**Results on the brief's questions** (end to end, warm)
| Question | Intent | Outcome |
|---|---|---|
| dal makhani for 4, budget mode, have salt & ghee | dish | Creamy Dal Makhani; 16 items, ₹607.50, salt excluded |
| paneer butter masala for 4 | dish | exact recipe; 13 items |
| chicken curry for 2 | dish | scaled ×0.5; **chicken → Couldn't find** (no raw chicken in snapshot) |
| deep-clean my bathroom | need | kit (0.72); 8 items, ₹912.10 |
| oily-skin routine under ₹1000 | need | kit (0.86); over budget in Best match → Budget, **₹817.79, fits** |
| pet tortoise | need | AI-generated list; 1 matched, 4 Couldn't find |
| pasted poha recipe | recipe_text | 10 items extracted, all matched (incl. haldi) |
| jeera aur haldi kitne ka hai? | catalogue | split into jeera/cumin and haldi/turmeric |
| Dove vs Pears; Aashirvaad vs Pillsbury | catalogue | two sub-queries each, brand-filtered |
| Fortune sunflower oil 1 L | catalogue | both Sun Lite variants (₹171, ₹866); pack size not in data |
| coconut oil for hair / for cooking | catalogue | Hair Care / Edible Oils & Ghee |
| cheapest floor cleaner | catalogue | sorted by price, household only (₹50, ₹55, ₹70) |
| paneer in stock in Koramangala / Blinkit vs BigBasket | out_of_scope | 0 LLM calls, with a reason |

Latency: decomposition ≤ 1.1 s with an uncached LLM call (0–150 ms for rules-only paths); cart retrieval
0.25–1.7 s (dal makhani, 16 items: 1.65 s).

**Verification:** 150 pytest tests passing (61 new): classification on all brief questions, entity
extraction, pantry matching, recipe choice (plain over variants, literal over synonyms, refusal of weak
matches), the rule-based catalogue split, ingredient parsing (16), the processed and head-noun guards,
modes and pack coverage.

**Known weaknesses, kept as failure-case evidence for Phase 8**
- **Generic staples map to specialty products in Best match:** salt → "Lite - 15% Low Sodium Iodised Salt",
  sugar → "50% Less Calories Sugar", tomatoes → "Tomato - Green". This is the brief's predicted failure.
- **Best match ignores price and pack size:** "sunflower oil (as required)" → a ₹1,995 cold-pressed oil,
  inflating the paneer cart. Root cause: no pack-size metadata.
- **Cinnamon stick → Couldn't find**, although whole cinnamon may exist below the 0.62 threshold. To check
  during threshold tuning.
- Combo packs win sometimes (cashew → "Cashew Whole 500 g + Raisins", ₹604).

---

## Phase 6: Grounded generation, fallback and faithfulness guard (11 Nov 2026)

**What was done**
- `rag/generator.py`: context builders for carts and catalogue answers, the grounded system prompt,
  `generate()`, citation normalisation and the faithfulness guard `verify_answer()`.
- `rag/fallback.py`: the fallback message, the sentinel (`INSUFFICIENT_CONTEXT`) and the
  weak-retrieval check.
- `rag/pipeline.py`: `answer(message, mode, extra_pantry, default_servings, confirmed_items)`, the single
  entry point for the app and the eval. It returns a `BotResponse` (kind, text, cart or catalogue
  results, guard result, fallback trigger and reason, timings, LLM calls, and the trace).
- `decompose()` now takes the sidebar's servings (`default_servings`); servings in the message win.
- Housekeeping from Phases 4–5 not logged earlier: `logs/` (wrong-domain audit) and `cache/` (LLM cache)
  added to `.gitignore`; end-to-end checks run from scratch scripts outside the repo (`run_plans.py`,
  `run_answers.py` in the session scratchpad); they will become `eval/run_eval.py` in Phase 8.

**How an answer is produced**
1. Out of scope → fallback immediately (0 LLM calls).
2. Cart paths: no sub-queries / nothing matched → fallback. AI-generated need list → a "suggestion"
   is returned first; retrieval runs only on the items the user keeps (unticked items are dropped, not
   treated as "already have").
3. Catalogue: if the best hit's vector similarity < `SIM_THRESHOLD` (0.70), or no product passes the
   relevance and filter checks → fallback, without calling the LLM.
4. The LLM writes the answer from a CONTEXT block listing every allowed product as `[P<id>]` with price,
   MRP, rating, category, pack (if known) and a 180-character description snippet. Cart totals, line totals,
   budget and the fits/over verdict are computed in Python and given as facts.
5. The guard checks the answer. On failure: regenerate once with the problems listed; if it still fails →
   fallback. The LLM replying with the sentinel → fallback.

**Decisions**
| Decision | Why |
|---|---|
| `SIM_THRESHOLD = 0.70` | Calibrated on 20 in-catalogue questions (lowest best-hit similarity 0.717) vs 15 off-catalogue ones that pass the scope rules (laptop, iPhone, cricket bat, sofa …: highest 0.682, mean 0.623). The gap is narrow, so the LLM's `in_scope` check and the product-word overlap guard back it up. Re-checked in Phase 8. |
| Guard rules: (1) every `[P<id>]` is a retrieved product; (2) every ₹ amount equals a context price, MRP or app-computed total; (3) in a sentence citing one product, its ₹ amount must be that product's price or MRP; (4) at least one citation when products exist; (5) pack sizes only if a product name, pack field or recipe quantity has them; (6) the sentinel never embedded in an answer | (1)–(3) are the brief's checks. (5) and (6) were added after real failures: the model wrote "Aashirvaad whole wheat atta **(5 kg)**", copying the size from the question (no Aashirvaad product says 5 kg), and dropped `[INSUFFICIENT_CONTEXT]` in the middle of a partial answer. The guard caught the 5 kg and the regenerated answer removed it. |
| Normalise citations before checking and displaying (`【P1】`, `[​P1]` with a zero-width space, `[ P1 ]`, `[p1]` → `[P1]`) | The first run rejected two **correct** answers ("jeera aur haldi", "cheapest floor cleaner") because gpt-oss wrote full-width brackets and invisible characters. That was a guard parsing bug, not a hallucination. |
| An exact "List source" line in the cart context | The model first called the dal makhani *recipe* and the bathroom *kit* "AI-generated" because the instruction mentioned AI lists. It now quotes the source: recipe from the dataset / team-written synthetic kit / extracted from the pasted recipe / AI-generated and user-confirmed. |
| Prompt: partial answers allowed ("say which part isn't in the data"); sentinel only when nothing answers; under 120 words; at most 3 products per thing asked; no sizes unless in that product's line | Shorter answers were also faster (see latency) and cheaper on the free-tier token budget. |
| Answer model stays `gpt-oss-120b` | After the prompt fixes, 5 representative answers took 4.8 s total on 120b vs 3.7 s on 20b, all passing the guard; 120b is the stronger model for ~0.2 s more per answer. |
| If the LLM fails on a cart, still show the cart (computed without the LLM) with a short note; catalogue questions fall back | Graceful degradation: a cart is fully grounded without the summary; a catalogue answer is not. |
| Bare dish names: the "is this a dish?" check now uses the exact-title lookup too | "paneer butter masala" (no "for N") went to catalogue and ended in a guard fallback. Now: dish, scaled ×1.5 for the sidebar's 6 servings. |
| Description snippet in the context cut 260 → 180 characters | Fewer tokens per call under the Groq free-tier limit (below). |

**Groq free-tier rate limit (found while testing)**
`gpt-oss-120b` allows **8,000 tokens per minute** (`x-ratelimit-limit-tokens: 8000`); one answer uses
~1.5–3k tokens. Back-to-back questions hit the cap and the SDK silently waits and retries, which is why some
answers took 5–11 s inside a burst while the same prompt takes ~1 s alone. Consequences:
- **Demo:** ask one question at a time, and run every demo question once before presenting. The disk
  cache then answers them instantly.
- **Eval (Phase 8):** pace the questions, and report latency with and without the cache.

**Results (all brief questions, end to end)**
| Question | Result | LLM calls | Total time |
|---|---|---:|---:|
| Fortune sunflower oil 1 L | both variants (₹171, ₹866), says pack sizes aren't recorded | 2 | 2.7 s |
| Aashirvaad vs Pillsbury atta 5 kg | guard caught "5 kg" → regenerated; Pillsbury reported as not in the data | 3 | 2.9 s |
| dal makhani for 4, budget, have salt & ghee | recipe named correctly, 3 cited products, ₹607.50, salt excluded | 1 | 3.6 s |
| pasted poha recipe | source "extracted from the recipe the user pasted", ₹1,035.10 | 2 | 3.1 s |
| jeera aur haldi kitne ka hai? | 3 cumin + 3 turmeric products, cited | 2 | (rate-limited burst) |
| bathroom deep-clean / oily skin under ₹1000 | "synthetic item list written by the BasketBuddy team"; ₹912.10 / ₹817.79 "under the ₹1,000 budget" | 1 | 0.5–1 s + generation |
| coconut oil for hair / for cooking | Hair Care oils / edible coconut oils, pack-size note | 2 | (rate-limited burst) |
| Compare Dove vs Pears | 3 products each, price and rating ranges | 2 | (rate-limited burst) |
| paneer in stock / Blinkit / laptop / cricket bat | fallback with a reason (laptop via the LLM scope check) | 0–1 | < 0.1 s |
| pet tortoise → confirm 2 of 5 items | cart of 1, "tortoise food mix" under Couldn't find | 2 | 0.8 s |
| paneer butter masala (sidebar: 6 servings, Top rated) | dish, ×1.5, ₹789.14 | 1 | 2.2 s |

Every non-fallback answer passed the guard (one after regeneration).

**Verification:** 163 pytest tests passing (13 new in `tests/test_guard.py`: valid answer, unknown citation,
wrong price for a cited product, invented amount, MRP and totals allowed, missing citation, size taken from
the question, embedded sentinel, four citation variants, fallback helpers).

**Failure-case candidates found in this phase**
- **Top rated trades relevance for rating:** in Top rated mode "paneer" became "Organic **Tofu**-Soy Paneer"
  (it's within the top 5 relevant hits and rated higher).
- **The LLM is stricter than the data:** for Pillsbury it reports "no whole-wheat atta" although "Atta - Chakki
  Fresh" is a whole-wheat atta; the name doesn't say "whole wheat".
- The guard checks sizes against *any* product in the context, not the cited one, so a size borrowed from
  another product would pass (tested and documented in `test_guard.py`).

---

## Phase 7: Streamlit app (11 Nov 2026)

**What was done**
- `app.py`, a single file that only renders; every answer comes from `rag.pipeline.answer()`.
  - **Chat** with history in `st.session_state`.
  - **Sidebar:** product choice, servings, a pantry multiselect plus free text, a "Show retrieval details"
    toggle, the LLM indicator (`rag.llm.describe()`), the warm-up timings and Clear chat.
  - **Carts:** the answer, then the cart table (item, needed, product, brand, pack, qty, price, line total,
    rating, match, product ID), **Total ₹**, a budget verdict, "Couldn't find on BigBasket" with the reason
    and closest product, the excluded pantry items and a **CSV download**.
  - **Catalogue answers:** the answer plus a product table per sub-query.
  - **🔍 Evidence** expander (shown when the toggle is on):
    - intent, LLM calls, model, fallback trigger and timings;
    - the numbered plan trace;
    - the matched recipe with its source link, other close recipes and the raw ingredient list;
    - the guard result and citations;
    - for every sub-query, the expanded query, the filter (including a relaxed sub-category hint and the
      wrong-domain audit) and the fused hits with vector rank, cosine, BM25 rank, RRF score and ✓ for chosen
      products;
    - the exact context sent to the LLM.
  - **AI-suggested lists:** tick boxes in a form; "Build cart from ticked items" calls
    `answer(..., confirmed_items=...)`.
  - **Kits:** labelled "Synthetic kit list, authored by the BasketBuddy team…".
  - **"How it works" tab:** renders `docs/PIPELINE.md`.
  - **8 example-question buttons** on the empty chat, one per demo flow.
  - `st.cache_resource` around `warm_up()`.

**Decisions**
| Decision | Why |
|---|---|
| Product choice has a 4th option, **Auto** (the default): use the mode stated in the message, else Best match | The brief lists three modes. But with a fixed sidebar mode, "dal makhani … **budget mode** …" was overridden by the sidebar's Best match (₹1,18x instead of ₹607.50) because the pipeline lets an explicit mode win. Auto makes the message win unless the user deliberately picks a mode. |
| Total shown as a heading plus caption, not `st.metric` | `st.metric` in narrow columns truncated "₹1,18…" in the browser check. |
| Citations shown as code chips (`P16295`); `$` escaped | They match the "Product ID" column of the cart table, so a reader can check them. Streamlit treats `$…$` as LaTeX. |
| Errors in `answer()` are caught and shown in the chat | One bad question must not crash the demo (graceful degradation). |
| Warm-up at start, cached per server process | Cold start: model 15–25 s, products index ~2 s; after that a question takes 2–5 s. |

**Verification (Playwright against `streamlit run app.py` on port 8599)**
- The home screen renders with sidebar, tabs and example buttons, and no console errors.
- **Dal makhani (example button, Auto):** Budget mode from the message, recipe caption, 16-line cart,
  **₹607.50**, "Excluded: Salt", CSV button, 2.7 s, 1 LLM call. The Evidence panel shows the trace, hit
  tables and LLM context.
- **Pet tortoise:** a 5-item AI-generated suggestion with tick boxes. "Build cart" gives a cart of 1 (pet
  wipes, ₹395) plus 4 "Couldn't find" rows with reasons.
- **Dove vs Pears:** cited answer plus two product tables, 2.1 s.
- **"Is paneer in stock right now?":** fallback with reason, 0 LLM calls.
- **"How it works":** PIPELINE.md renders.
- The server was killed afterwards and port 8599 is free.

---

## Phase 8: Evaluation (11 Nov 2026)

**What was done**
- `eval/test_questions.yaml` holds three things:
  - **16 questions**: every question the brief requires, plus an off-catalogue question (laptop), an
    AI-suggested list with confirmation (pet tortoise) and a bare dish with sidebar servings in Top rated mode;
  - **46 labelled ablation sub-queries** across all seven domains, including 6 Hinglish and 5 ambiguous ones;
  - **30 threshold-probe questions** (15 answerable, 15 not).
- `eval/run_eval.py` (`python -m eval.run_eval [--fresh] [--only Q1,Q2] [--skip-…]`) writes `eval/results.md`
  and `eval/results.json`:
  - Per question: top-3 retrieved, evidence present (keyword check on retrieved product names), the answer,
    faithfulness, fallback expected vs actual (with trigger), a category check, latency, LLM calls and an
    empty **manual score (1–3)** column.
  - Faithfulness is checked **independently** of the in-pipeline guard: every citation must be retrieved,
    and a ₹ amount in a single-citation sentence must be that product's price, MRP or an app total.
- A 4-way ablation: (a) vector only, (b) hybrid, (c) hybrid + category filter, and (d) hybrid + *inferred*
  domain (the realistic catalogue path). It reports hit@5, MRR@5, wrong-domain top-1 and the wrong-domain
  share of the top 5.
- The `SIM_THRESHOLD` re-check runs the real catalogue path (decompose → retrieve_catalogue).
- `eval/failure_cases.md` documents 7 failures observed in the runs, each with root cause, fix and whether it
  was applied.

**Decisions**
| Decision | Why |
|---|---|
| `--fresh` disables LLM **cache reads** but still writes (new `config.LLM_CACHE_READ`) | Latency in the report is real (no cache hits), and the eval run also warms the cache for the demo. |
| Pace 20 s between questions in `--fresh` | Groq free tier: 8k tokens/min. Without pacing the SDK silently retries and the latency figures become rate-limit artefacts. |
| A 4th ablation variant (inferred domain) | Variant (c) uses the labelled domain, which is right for carts (recipes are food, kits carry domains) but optimistic for catalogue questions. (d) shows what a user actually gets. |
| MRR@5 reported next to hit@5 | Single-word item queries saturate hit@5 (~0.96–0.98 for every variant); the difference is *where* the right product ranks, which is what Best match uses. |
| Probe YAML keys renamed `on`/`off` → `answerable`/`unanswerable` | YAML 1.1 parses `on`/`off` as booleans; the first run crashed with `KeyError: 'on'`. |
| Commas in sub-category names quoted in the YAML | `[Disposables, Garbage Bag]` parsed as two items, so 3 labels silently had no relevant product. The runner now warns about labels with zero support in the catalogue. |
| Two label corrections (lemon also in *Herbs & Seasonings*; sunscreen also in *Men's Grooming*) | Both were correct products filed on another shelf, so they were labelling errors, not retrieval misses. |
| Q15's expectation (a cart) kept even though the final run fell back | Changing the expectation to match the output would hide failure case 6. |
| No `tabulate` dependency; a small markdown-table helper | `DataFrame.to_markdown` needs an optional package; not worth a dependency. |

**Fixes applied from the eval (each with a test)**
- **Pack size from the question:** a context note plus a sentence-level guard rule (Q2 had fallen back after
  failing the guard twice).
- **Synonyms:** "dhaniya/dhania patta", "hara dhaniya" → coriander leaves; "pudina patta" → mint leaves;
  "methi patta" → fenugreek leaves.
- **Hints on the expanded query:** the sub-category hint is also looked up on the synonym-expanded query.
- **Processed forms:** *achar, syrup, sharbat* added.
- **Peanuts hint:** *peanut, groundnut* → Dals & Pulses / Organic Staples.
- **Domain keywords:** *face, skin, lip(s), scalp* added to the personal-care keywords.
- **App:** a `no_match` fallback now lists every searched item with its reason and closest product.

**Results (final run, `--fresh`)**
- **End to end:**
  - 15/16 fallbacks as expected; the miss is the pet-tortoise AI list, which had no BigBasket matches.
  - Evidence present in 12/13; **faithful in 12/12** answered questions.
  - Latency: median **2.1 s**, max 4.0 s; at most 2 LLM calls per question.
- **Ablation (46 sub-queries):**

  | Variant | hit@5 | MRR@5 | Wrong-domain top-1 | Wrong-domain share of top-5 |
  |---|---:|---:|---:|---:|
  | vector only | 0.978 | 0.888 | 0.0% | 1.7% |
  | hybrid | 0.957 | 0.890 | 4.3% | 2.2% |
  | hybrid + category filter | 0.978 | **0.962** | **0.0%** | **0.0%** |
  | hybrid + inferred domain | 0.978 | **0.962** | **0.0%** | **0.0%** |

  - Before the fixes, (c) was 0.957 / 0.918 and (d) 0.935 / 0.897 with 2.2% wrong-domain.
  - Honest finding: plain hybrid is *not* better than vector-only here. BM25 pulls literal words across
    domains (haldi → supplement, lemon → essential oil). The gain comes from hybrid **plus** the category
    filter.
- **Threshold:** answerable ≥ 0.710, unanswerable ≤ 0.692, so **0.70 is kept**. It keeps 15/15 and blocks 7/7
  of those reaching retrieval; 8/15 unanswerable questions are stopped earlier by scope checks.
- **Not fixed, documented:** "nimbu" (the Hinglish token pulls the embedding towards Nimbu Achar; a cart now
  reports it as not found rather than adding a pickle), salt → Lite Low Sodium Salt, lemon juice → ₹285
  concentrate, Top rated → tofu for paneer, and the tortoise list.

**Verification:** 166 pytest tests passing (3 new). Three `--fresh` eval runs: the first crashed at the probe
(YAML booleans); the second is the baseline before the fixes; the third is the final run with the fixes, which
is what `results.md` shows. App re-checked with Streamlit `AppTest` after the fixes.

---

## Phase 9: Documentation (11 Nov 2026)

**What was done**
- `docs/PIPELINE.md` rewritten:
  - a one-screen summary and an **ASCII diagram** from data → clean → chunk → embed → Chroma + BM25 →
    decompose → hybrid retrieve → pick → LLM with context → guard/fallback → answer + cart;
  - the five online steps, the fallback-trigger table and the **thresholds with how each was chosen**;
  - the eval summary;
  - the original catalogue rationale and chunking strategy, with the generated chunk-stats block kept
    between its markers so `python -m rag.chunking` can still refresh it.
- `docs/DATA_LOG.md`: kit count corrected (~25 → 26 kits, 173 items); assumption 10 (AI-suggested lists are
  not data); a "Hand-made resources" table (kits, synonyms, category hints, domain map, eval labels).
  Sources, licences, the Mendeley DOI, cleaning row counts and the other assumptions were already there.
- `README.md`: what it is, setup (Python 3.11 venv, requirements, optional CUDA torch, `.env`, Kaggle
  downloads, clean → index, run), tests and eval commands, a **5-minute demo script** with the four required
  flows, backup questions, the project layout and the things to do by hand.

**Decisions**
| Decision | Why |
|---|---|
| ASCII diagram, not mermaid | The "How it works" tab renders PIPELINE.md with `st.markdown`, which doesn't render mermaid; a code block looks the same on GitHub and in the app. |
| Demo script starts with "warm the cache" | Groq's free-tier limit (8k tokens/min) can turn a 2 s answer into 8–10 s in a burst; cached answers return instantly. |
| Demo uses the example buttons, typing only for the coconut-oil contrast and Blinkit | The brief asks not to type live; the contrast needs two specific queries that aren't buttons. |

---

## Post-Phase 9: fixes from manual testing (11 Nov 2026)

Two real questions typed into the app failed:

| Question | What happened | Root cause |
|---|---|---|
| "i need to cook butter chicken for 5 ppl" | Fallback: *no recipe matches 'need butter chicken ppl' (0.69)* | `dish_name_from` only stripped a fixed filler list, so "need" and "ppl" stayed in the dish name and the similarity fell just below `RECIPE_MATCH_SIM` (the clean "butter chicken" scores 0.75). "ppl" also wasn't a people word. |
| "my skin is oily, what cleanser should i use" | Fallback: *question about skincare advice, not a specific product* | The catalogue LLM prompt listed "medical or health advice" as out of scope, and the model applied it to a product recommendation. |

Probing similar phrasings showed the same pattern:
- "gotta cook biryani for 6 guests" left *"gotta biryani"*;
- "want to make rajma chawal…" left *"want rajma chawal"*;
- "making pasta for my family of 4" wasn't recognised as a dish;
- "I have to cook…" would have been read as a pantry statement ("I have …").

**Fixes**
- **People words:** *ppl, peeps, folks, friends, kids, family members, of us* and "family of N" count as
  servings.
- **Dish words:** *making* and *cooking* count, except "for cooking" ("coconut oil for cooking" stays a
  product question).
- **Dish name:** conversational lead-ins (*I need to, have to, gotta, gonna, want to, wanna, help me, let's,
  I'm …*) and people or meal words are stripped from it.
- **Pantry:** "I have **to** …" is no longer a pantry statement.
- **LLM scope prompt:** finding, pricing, comparing or **recommending** products is in scope, including for a
  skin type, hair type or use, with worked examples. Out of scope only for stock and delivery, other apps,
  **diagnosing or treating** a condition (dosages, symptoms), and things BigBasket doesn't sell.
- **Fallback text:** a reason that already ends with "." no longer gets a second one.

**Verification**
- 7 new tests (173 passing).
- End to end through `answer()`:
  - butter chicken → 14-item cart from "Butter Chicken - The Classic Indian Chicken Gravy", ×1.25 for 5;
  - oily-skin cleanser, frizzy-hair shampoo, "something for dandruff" and sensitive-teeth toothpaste → cited
    product answers;
  - the medical-dosage question, paneer-in-stock and laptop → still refused.
- Both questions added to the eval as Q17 and Q18, and the full eval re-run (`results.md`).

---

## Edge-case battery: 56 realistic messages (11 Nov 2026)

`eval/edge_cases.py` + `eval/run_edge_cases.py` (`python -m eval.run_edge_cases [numbers]`): 56 messages
run end to end through `answer()`:
- 16 dishes (conversational, typos, Hinglish, one that isn't in the data);
- 8 needs or occasions;
- 9 product recommendations;
- 10 catalogue facts;
- 13 out-of-scope messages, prompt injection, greetings and junk.

Every cart and answer was **read**, not just its result type.

**First run: 4 wrong result types, plus content problems found by reading the carts**
| Problem | Example | Root cause → fix |
|---|---|---|
| Budget amount rejected by the guard | "olive oil under 500" → fallback (₹500 "not a price") | The user's own limit wasn't a known amount → `max_price` and the budget are added to the catalogue context's allowed amounts |
| Wrong domain | "my hair is falling, which oil" searched **food** oils | "hair" wasn't a personal-care keyword → added *hair, dandruff, frizz, acne, pimple* |
| Wrong recipe | chicken biryani → *Chicken **Kappa** Biryani*; kadhai paneer → *Paneer Chilli **Dosa***; "panner butter masala" → *Baby Corn Butter Masala*; aloo gobi → *Aloo **Patta** Gobi* | Candidates were only the top-8 semantic hits, with no typo tolerance → `correct_dish_spelling` (difflib against recipe-title words) and `literal_recipe`: titles containing every dish word (per-word spelling variants, e.g. kadhai ~ kadai), choosing the plainest (penalising variant and unrequested-protein words; *one pot, sabzi, curry* are descriptors). Semantic search only when no title matches. |
| Ingredient → wrong product | protein powder → *Fenugreek* Powder; milk powder → *Strawberry Milk **Shake*** Powder; boneless chicken → a coconut-milk **combo**; potato masala → *Dal Tadka Masala*; all-purpose flour → *Jowar* Flour | Food had no "the product must name the item" check → `_rank_food_by_words`: at least one key word (or synonym) must be in the name, where form words like *powder, masala, fresh* don't count; more matched words rank first; combo packs rank last. *shake, milkshake, crispy* added as processed forms. Synonyms: *all purpose / plain flour → maida, cottage cheese → paneer, yogurt → curd/dahi*. |
| Product question routed to a kit | "best diapers for a newborn", "food for my puppy" | Need words (*newborn, puppy*) fired → a product **for** a baby or pet is a catalogue question unless the message asks for "stuff/everything/essentials" |
| Wrongly refused | "mosquito repellent safe for babies" | The medical scope rule contained "safe for" → removed (doses and treatment are still refused) |
| "for 2" read as servings | "book a table for 2 at a restaurant" → dish | Scope rule for restaurant bookings, food ordering and tickets |
| Small talk | "hi", "thanks", "?" went to the LLM | New `smalltalk` intent → a friendly "what I can do" reply (0 LLM calls, kind `help`) |
| AI list item on the wrong shelf | "olive oil" (labelled kitchen) → oil dispenser bottle | A confident keyword/purpose rule overrides the LLM's domain label |
| LLM too literal on scope (found by the eval probe) | "compare toothpaste brands" refused: "no specific brands mentioned" | Prompt: broad questions are in scope (the product type is the sub-query). Code: if the rules found a product word and no scope rule fired, the LLM's refusal is overridden. Laptop, iPhone and sofa are still refused. |
| More dish phrasings | "whats needed for…", "how do i make…", "can u help me cook…", "tonight's dinner:", "bake a cake", "rajma chawal **banana hai 4 logo ke liye**", "gulab jamun **for diwali**", "pav bhaji for a **party of 10**" | More lead-ins, Hinglish (*banana hai, logo ke liye*), occasion phrases and "party/group of N". A need-worded message with servings and a matching recipe title becomes a dish. |

**Final run: 56/56 behave as expected.** The eval was re-run after all fixes: 17/18 (only the tortoise list, as
before), faithful 14/14, ablation unchanged, and the threshold probe 15/15 answerable kept and 11/11 blocked.
202 pytest tests (29 new for these cases).

**Still imperfect (data or semantics, documented):**
- Some substitutions remain: rice vinegar → a rice product, seaweed sheets → seaweed crackers,
  tea leaves → a tulsi green tea. The catalogue lacks the exact item, and the closest one still names it.
- AI-suggested lists can include items BigBasket doesn't sell (balloons, resistance bands); they are listed
  under "Couldn't find".
- "sushi for 2" builds a cart: the dataset *does* have a "Vegetarian Sushi" recipe.
