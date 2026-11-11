# BasketBuddy 🧺

A retrieval-augmented chatbot over the full **BigBasket product catalogue** (25,992 products) that turns a
need into a cart:

- a **dish** ("dal makhani for 4") or a **pasted recipe** → an ingredient cart with prices and a total;
- a **task or occasion** ("deep-clean my bathroom", "oily-skin routine under ₹1000") → a kit cart;
- **catalogue questions** ("compare Dove and Pears soaps", "jeera aur haldi kitne ka hai?") → cited answers;
- anything the data can't answer (stock, delivery, other shops, laptops) → a clear **fallback**.

Every product, price and rating comes from the dataset and is cited as `[P<id>]`. Totals are computed in
Python, and a faithfulness guard checks every answer before it is shown.

- How it works: [docs/PIPELINE.md](docs/PIPELINE.md) (also the "How it works" tab in the app)
- Data sources, licences and cleaning: [docs/DATA_LOG.md](docs/DATA_LOG.md)
- Evaluation: [eval/results.md](eval/results.md) and [eval/failure_cases.md](eval/failure_cases.md)
- Every decision, phase by phase: [DECISIONS.md](DECISIONS.md)

## Setup

Requirements: Python **3.11** (chromadb and torch wheels are not reliable on 3.13+), ~3 GB of disk space,
and an API key for one LLM provider (Groq's free tier works). An NVIDIA GPU is optional; it makes the
one-off index build ~5 min instead of ~45 min.

```bash
# 1. virtual environment
py -3.11 -m venv .venv            # macOS/Linux: python3.11 -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# optional, NVIDIA GPU: install the CUDA build of torch from https://pytorch.org/get-started/locally/

# 2. LLM key
copy .env.example .env            # macOS/Linux: cp .env.example .env
#    then set LLM_PROVIDER=groq and GROQ_API_KEY=... (or gemini / anthropic / openai)

# 3. data (public Kaggle datasets; these download without a Kaggle token)
curl -L -o bb.zip https://www.kaggle.com/api/v1/datasets/download/surajjha101/bigbasket-entire-product-list-28k-datapoints
curl -L -o rec.zip https://www.kaggle.com/api/v1/datasets/download/sukhmandeepsinghbrar/indian-food-dataset
curl -L -o recc.zip https://www.kaggle.com/api/v1/datasets/download/atharvaarya25/indian-food-recipes-dataset-cleaned
#    unzip into data/raw/bigbasket/, data/raw/recipes/ and data/raw/recipes_clean/ respectively
#    (if a download asks for auth, put a kaggle.json token in ~/.kaggle and use `kaggle datasets download`)

# 4. clean → chunk → index (idempotent; re-running skips unchanged collections)
python -m ingest.clean_products
python -m ingest.clean_recipes
python -m ingest.build_index      # add --force to rebuild everything

# 5. run
streamlit run app.py              # first start loads the model and indexes (~30 s)
```

Tests and evaluation:

```bash
pytest                            # unit tests + retrieval integration tests
python -m eval.run_eval --fresh   # full eval: ~10 min, paced for Groq's 8k tokens/min free tier
```

Never commit `.env`. It is in `.gitignore`.

## 5-minute demo script

**Before presenting (about 3 minutes):** start the app, then click every example button once (or run
`python -m eval.run_eval`). Answers are cached on disk, so during the demo they come back instantly and the
Groq free-tier rate limit can't slow you down. Turn on **Show retrieval details** in the sidebar.

| Time | Flow | What to do | What to point out |
|---|---|---|---|
| 0:00–0:30 | Intro | Show the empty chat and the sidebar | One catalogue, three uses: dish → cart, need → cart, catalogue Q&A. LLM shown in the sidebar. |
| 0:30–1:45 | **1. Dish → cart** | Click *"Dal makhani for 4, budget mode, I already have salt and ghee"* | Recipe matched from the dataset (caption), quantities for 4, Budget mode taken from the message, salt excluded, **Total ₹**, CSV download. Open 🔍 Evidence: the trace, the matched recipe, and one ingredient's hit table (vector rank, BM25 rank, RRF). |
| 1:45–2:45 | **2. Non-food need → cart** | Click *"I want to deep-clean my bathroom this weekend"* (or *"Oily skin starter kit under ₹1000"*) | The kit label: the item list is synthetic and written by the team, while products and prices are real. The budget verdict for the skincare kit. No face cleanser in a bathroom cart. |
| 2:45–4:00 | **3. Cross-category contrast** | Type *"coconut oil for hair"*, then *"coconut oil for cooking"*; open Evidence on both | Same words, different shelves: Hair Care vs Edible Oils. The Evidence filter line shows the inferred domain; pack-size variants are explained, not guessed. |
| 4:00–4:40 | **4. Unsupported → fallback** | Click *"Is paneer in stock right now?"*, then type *"Is Blinkit cheaper than BigBasket?"* | Refuses with a reason and makes 0 LLM calls; says what it *can* do instead. Grounding beats coverage. |
| 4:40–5:00 | Wrap-up | Open the "How it works" tab | The pipeline diagram; eval numbers in `eval/results.md`. |

Backup questions if there's time: *"jeera aur haldi kitne ka hai?"* (Hinglish), *"Compare Dove and Pears
soaps"* (brand filter), *"I'm getting a pet tortoise, what should I buy?"* (AI-suggested list → tick boxes
→ cart).

## Project layout

```
app.py                 Streamlit UI (renders only; calls rag.pipeline.answer)
config.py              paths, models, thresholds
ingest/                clean_products, clean_recipes, pack_size, text_fix, build_index
rag/                   chunking, embeddings, synonyms, category_map, retriever (hybrid + RRF + filters),
                       ingredients, decompose, cart, llm, generator (context + guard), fallback, pipeline
data/kits/kits.json    26 synthetic need/occasion kits (team-written)
eval/                  test_questions.yaml, run_eval.py, results.md, failure_cases.md
tests/                 pytest: pack sizes, cleaning, chunking, synonyms, RRF, retriever, decomposition,
                       cart, faithfulness guard
docs/                  PIPELINE.md, DATA_LOG.md
```

## Things to do by hand

- Put your own LLM key in `.env`; the app shows "not configured" in the sidebar otherwise.
- Fill in the **Manual score (1–3)** column in `eval/results.md`.
- Licences: the BigBasket data is CC BY-NC-SA 4.0 (non-commercial, share-alike), so keep this project
  non-commercial.
