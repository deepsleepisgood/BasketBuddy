"""Central configuration: paths, model names, thresholds and retrieval sizes."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- data paths -------------------------------------------------------------
RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"
KITS_PATH = ROOT / "data" / "kits" / "kits.json"

RAW_PRODUCTS_CSV = RAW_DIR / "bigbasket" / "BigBasket Products.csv"
RAW_RECIPES_CSV = RAW_DIR / "recipes" / "IndianFoodDatasetCSV.csv"
RAW_RECIPES_CLEAN_CSV = RAW_DIR / "recipes_clean" / "Cleaned_Indian_Food_Dataset.csv"

PRODUCTS_PARQUET = CLEAN_DIR / "products.parquet"
RECIPES_PARQUET = CLEAN_DIR / "recipes.parquet"

DOCS_DIR = ROOT / "docs"
DATA_LOG = DOCS_DIR / "DATA_LOG.md"

# --- index ------------------------------------------------------------------
INDEX_DIR = ROOT / "index"
CHROMA_DIR = INDEX_DIR / "chroma"
BM25_DIR = INDEX_DIR / "bm25"
COLLECTIONS = ("products", "recipes", "kits")

# --- models -----------------------------------------------------------------
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# --- retrieval --------------------------------------------------------------
VECTOR_CANDIDATES = 20   # top-n from each method before fusion (brief: top 20 each)
BM25_CANDIDATES = 20
RRF_K = 60               # Reciprocal Rank Fusion constant (Cormack et al. 2009)
TOP_K = 5                # fused results returned per sub-query
# A sub-category hint (e.g. tomato → Fresh Vegetables) is dropped back to the
# domain filter when its best vector similarity is below this. Tuned in Phase 8.
SUBCAT_RELAX_SIM = 0.65
# Domain classifier: share of the unfiltered top-10 that must agree on one domain.
DOMAIN_VOTE_SHARE = 0.7

# --- LLM --------------------------------------------------------------------
# Provider comes from LLM_PROVIDER in .env (groq | gemini | anthropic | openai),
# else the first provider whose key is set. Models can be overridden with
# LLM_MODEL / LLM_FAST_MODEL. Only the Groq defaults were benchmarked (11 Nov 2026:
# gpt-oss-20b 0.87 s, gpt-oss-120b 1.16 s on a JSON extraction task).
LLM_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "groq": {"answer": "openai/gpt-oss-120b", "fast": "openai/gpt-oss-20b"},
    "gemini": {"answer": "gemini-2.5-flash", "fast": "gemini-2.5-flash"},
    "anthropic": {"answer": "claude-opus-5-5", "fast": "claude-haiku-5-5"},
    "openai": {"answer": "gpt-5-mini", "fast": "gpt-5-mini"},
}
LLM_TEMPERATURE = 0.1          # brief: 0–0.2 (ignored where a model rejects sampling params)
LLM_TIMEOUT_S = 20.0
LLM_CACHE = ROOT / "cache" / "llm_cache.sqlite"   # identical prompts → cached answer
LLM_CACHE_ENABLED = True
LLM_CACHE_READ = True     # eval --fresh sets False: real latency, but answers are still written for the demo

# --- decomposition / cart ---------------------------------------------------
KIT_MATCH_SIM = 0.70       # kit similarity needed to use a hand-written kit (Phase 3: 0.723 vs 0.650)
RECIPE_MATCH_SIM = 0.70    # recipe similarity needed to accept a dish match ("Creamy Dal Makhani" = 0.73)
ITEM_MATCH_SIM = 0.62      # below this, an ingredient goes to "Couldn't find on BigBasket"
MODE_POOL = 5              # Budget / Top rated choose among the top-5 relevant products

# --- grounding / fallback ----------------------------------------------------
# Fallback when the best hybrid hit's vector similarity is below this. Calibrated on
# 11 Nov 2026: 20 in-catalogue questions scored ≥ 0.717, 15 off-catalogue questions
# (laptops, iPhone, cricket bat, sofa …) scored ≤ 0.682 (mean 0.623). Re-checked in Phase 8.
SIM_THRESHOLD = 0.70
ANSWER_MAX_TOKENS = 1200
CONTEXT_DESC_CHARS = 180     # description snippet per product in the LLM context (Groq free tier: 8k tokens/min)

# --- logging ----------------------------------------------------------------
LOG_DIR = ROOT / "logs"
WRONG_DOMAIN_LOG = LOG_DIR / "wrong_domain.jsonl"
AUDIT_WRONG_DOMAIN = True   # also run the unfiltered search to log domain mistakes
