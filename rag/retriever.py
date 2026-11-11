"""Retrieval: vector search, BM25, hybrid fusion (RRF) and category-aware filters.

Entry points
- vector_search / bm25_search: single-method search over one collection.
- hybrid_search: both methods (top 20 each), fused with Reciprocal Rank Fusion.
  Each hit keeps its vector rank/similarity and BM25 rank/score, so the UI can
  show *why* a product was picked.
- search_item: what the decomposition paths call per ingredient / kit item. Adds
  synonym expansion, the ITEM_CATEGORY_MAP hint, filter relaxation and the
  wrong-domain audit log.
- infer_domain: domain guess for free-form catalogue questions.

Sub-chunks of one long product (P292-0, P292-1, ...) are collapsed to one hit
per product, keeping that product's best rank in each method.
"""
from __future__ import annotations

import json
import pickle
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import numpy as np

import config
from rag.category_map import lookup_subcategories
from rag.synonyms import expand_query
from rag.text import bm25_tokenize

Collection = Literal["products", "recipes", "kits"]
FilterLevel = Literal["sub_category", "domain", "none"]


# --- data types ------------------------------------------------------------------

@dataclass(frozen=True)
class Filter:
    """Metadata filter applied to both methods (Chroma `where` + BM25 mask)."""

    domain: str | None = None
    sub_categories: tuple[str, ...] | None = None
    brand: str | None = None   # exact brand name, for comparisons ("Dove" vs "Pears")

    @property
    def level(self) -> FilterLevel:
        return "sub_category" if self.sub_categories else "domain" if self.domain else "none"

    def to_where(self) -> dict[str, Any] | None:
        conds: list[dict[str, Any]] = []
        if self.domain:
            conds.append({"domain": self.domain})
        if self.sub_categories:
            conds.append({"sub_category": {"$in": list(self.sub_categories)}})
        if self.brand:
            conds.append({"brand": self.brand})
        if not conds:
            return None
        return conds[0] if len(conds) == 1 else {"$and": conds}


NO_FILTER = Filter()


@dataclass
class Hit:
    """One retrieved document (product / recipe / kit) with per-method evidence."""

    doc_key: str                      # "P123", "R45", "Kbathroom-deep-clean"
    chunk_id: str                     # best-matching chunk of that doc
    text: str
    metadata: dict[str, Any]
    rrf_score: float = 0.0
    vector_rank: int | None = None    # 1-based; None = not in the vector top-n
    vector_sim: float | None = None   # cosine similarity to the (expanded) query
    bm25_rank: int | None = None
    bm25_score: float | None = None


@dataclass
class SearchResult:
    """Fused hits for one query, plus how the search was run."""

    query: str
    expanded_query: str
    collection: str
    filter: Filter
    hits: list[Hit]
    relaxed_from: Filter | None = None      # set when a sub-category filter was dropped
    unfiltered_top: Hit | None = None       # audit: top hit with no filter at all
    wrong_domain: bool = False              # audit: unfiltered top-1 was in another domain
    latency_ms: float = 0.0

    @property
    def best(self) -> Hit | None:
        return self.hits[0] if self.hits else None

    @property
    def best_vector_sim(self) -> float:
        sims = [h.vector_sim for h in self.hits if h.vector_sim is not None]
        return max(sims) if sims else 0.0


# --- index loading ----------------------------------------------------------------

@dataclass
class CollectionIndex:
    """Everything needed to search one collection, loaded once per process."""

    name: str
    chroma: Any
    bm25: Any
    ids: list[str]
    texts: list[str]
    metadatas: list[dict[str, Any]]
    doc_keys: np.ndarray
    domains: np.ndarray
    sub_categories: np.ndarray
    brands: np.ndarray
    row_of: dict[str, int] = field(default_factory=dict)

    def mask(self, flt: Filter) -> np.ndarray | None:
        """Boolean row mask for ``flt`` (vectorised, so BM25 post-filtering is cheap)."""
        if flt.level == "none" and not flt.brand:
            return None
        m = np.ones(len(self.ids), dtype=bool)
        if flt.domain:
            m &= self.domains == flt.domain
        if flt.sub_categories:
            m &= np.isin(self.sub_categories, list(flt.sub_categories))
        if flt.brand:
            m &= self.brands == flt.brand
        return m


def doc_key(meta: dict[str, Any], chunk_id: str) -> str:
    """Stable per-document key, shared by all sub-chunks of a product."""
    if "product_id" in meta:
        return f"P{meta['product_id']}"
    if "recipe_id" in meta:
        return f"R{meta['recipe_id']}"
    return chunk_id


@lru_cache(maxsize=1)
def _chroma_client() -> Any:
    import chromadb

    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


@lru_cache(maxsize=None)
def get_index(name: Collection) -> CollectionIndex:
    """Load the Chroma collection and the pickled BM25 index for ``name``."""
    path = config.BM25_DIR / f"{name}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing. Run `python -m ingest.build_index` first.")
    with path.open("rb") as f:
        payload = pickle.load(f)
    metas = payload["metadatas"]
    return CollectionIndex(
        name=name,
        chroma=_chroma_client().get_collection(name),
        bm25=payload["bm25"],
        ids=payload["ids"],
        texts=payload["texts"],
        metadatas=metas,
        doc_keys=np.array([doc_key(m, i) for m, i in zip(metas, payload["ids"])], dtype=object),
        domains=np.array([m.get("domain", "") for m in metas], dtype=object),
        sub_categories=np.array([m.get("sub_category", "") for m in metas], dtype=object),
        brands=np.array([m.get("brand", "") for m in metas], dtype=object),
        row_of={cid: i for i, cid in enumerate(payload["ids"])},
    )


# --- single-method search ----------------------------------------------------------

def _embed(queries: list[str]) -> np.ndarray:
    from rag.embeddings import embed_queries

    return embed_queries(queries)


def vector_search(
    query: str | np.ndarray,
    k: int = config.VECTOR_CANDIDATES,
    where: Filter = NO_FILTER,
    collection: Collection = "products",
) -> list[tuple[str, float]]:
    """Top-``k`` (chunk_id, cosine similarity), one entry per document, best first."""
    idx = get_index(collection)
    qvec = _embed([query])[0] if isinstance(query, str) else query
    # over-fetch a little: sub-chunks of the same product collapse to one doc
    res = idx.chroma.query(
        query_embeddings=[qvec.tolist()], n_results=min(k + 10, len(idx.ids)),
        where=where.to_where(), include=["distances"],
    )
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for cid, dist in zip(res["ids"][0], res["distances"][0]):
        key = idx.doc_keys[idx.row_of[cid]]
        if key in seen:
            continue
        seen.add(key)
        out.append((cid, 1.0 - float(dist)))  # cosine distance → similarity
        if len(out) == k:
            break
    return out


def bm25_scores(query: str, collection: Collection = "products") -> np.ndarray:
    """Raw BM25 score of every chunk for ``query`` (computed once, masked per filter)."""
    return np.asarray(get_index(collection).bm25.get_scores(bm25_tokenize(query)))


def bm25_search(
    query: str,
    k: int = config.BM25_CANDIDATES,
    filter: Filter = NO_FILTER,  # noqa: A002 - name from the brief
    collection: Collection = "products",
    scores: np.ndarray | None = None,
) -> list[tuple[str, float]]:
    """Top-``k`` (chunk_id, BM25 score > 0), one entry per document, post-filtered."""
    idx = get_index(collection)
    s = bm25_scores(query, collection) if scores is None else scores.copy()
    mask = idx.mask(filter)
    if mask is not None:
        s[~mask] = -np.inf
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for row in np.argsort(-s):
        if s[row] <= 0 or len(out) == k:
            break
        key = idx.doc_keys[row]
        if key not in seen:
            seen.add(key)
            out.append((idx.ids[row], float(s[row])))
    return out


# --- fusion -------------------------------------------------------------------------

def rrf_merge(
    ranked_lists: dict[str, list[str]], k: int = config.RRF_K
) -> list[tuple[str, float, dict[str, int]]]:
    """Reciprocal Rank Fusion: score(d) = Σ_method 1 / (k + rank_method(d)).

    ``ranked_lists`` maps method name → doc keys, best first. Returns
    (doc_key, fused score, {method: 1-based rank}) sorted by score; ties keep
    the order in which documents were first seen (vector list first).
    """
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for method, keys in ranked_lists.items():
        for rank, key in enumerate(keys, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(key, {})[method] = rank
    order = sorted(scores, key=lambda d: -scores[d])  # stable sort keeps first-seen order on ties
    return [(d, scores[d], ranks[d]) for d in order]


def _cosine_for(idx: CollectionIndex, chunk_ids: list[str], qvec: np.ndarray) -> dict[str, float]:
    """Cosine similarity for chunks that BM25 found but the vector top-n didn't."""
    if not chunk_ids:
        return {}
    got = idx.chroma.get(ids=chunk_ids, include=["embeddings"])
    return {cid: float(np.dot(vec, qvec)) for cid, vec in zip(got["ids"], got["embeddings"])}


def hybrid_search(
    query: str,
    k: int = config.TOP_K,
    filter: Filter = NO_FILTER,  # noqa: A002
    collection: Collection = "products",
    qvec: np.ndarray | None = None,
    bm25_all: np.ndarray | None = None,
    expand: bool = True,
) -> SearchResult:
    """Vector top-20 + BM25 top-20 → RRF → top-``k`` hits with per-method evidence."""
    t0 = time.perf_counter()
    idx = get_index(collection)
    expanded = expand_query(query) if expand else query
    if qvec is None:
        qvec = _embed([expanded])[0]
    if bm25_all is None:
        bm25_all = bm25_scores(expanded, collection)

    vec = vector_search(qvec, config.VECTOR_CANDIDATES, filter, collection)
    kw = bm25_search(expanded, config.BM25_CANDIDATES, filter, collection, scores=bm25_all)
    vec_by_key = {idx.doc_keys[idx.row_of[c]]: (c, s) for c, s in vec}
    kw_by_key = {idx.doc_keys[idx.row_of[c]]: (c, s) for c, s in kw}

    fused = rrf_merge({"vector": list(vec_by_key), "bm25": list(kw_by_key)})[:k]
    missing = [kw_by_key[d][0] for d, _, r in fused if "vector" not in r]
    extra_sims = _cosine_for(idx, missing, qvec)

    hits: list[Hit] = []
    for key, score, ranks in fused:
        v, b = vec_by_key.get(key), kw_by_key.get(key)
        cid = (v or b)[0]
        row = idx.row_of[cid]
        hits.append(Hit(
            doc_key=key, chunk_id=cid, text=idx.texts[row], metadata=idx.metadatas[row],
            rrf_score=round(score, 5),
            vector_rank=ranks.get("vector"),
            vector_sim=round(v[1] if v else extra_sims.get(cid, 0.0), 4),
            bm25_rank=ranks.get("bm25"),
            bm25_score=round(b[1], 3) if b else None,
        ))
    return SearchResult(query, expanded, collection, filter, hits,
                        latency_ms=round((time.perf_counter() - t0) * 1000, 1))


# --- per-item search with hints, relaxation and audit -------------------------------

def search_item(
    item: str,
    domain: str | None = None,
    k: int = config.TOP_K,
    collection: Collection = "products",
    qvec: np.ndarray | None = None,
    audit: bool = config.AUDIT_WRONG_DOMAIN,
    sub_categories: tuple[str, ...] | None = None,
    brand: str | None = None,
) -> SearchResult:
    """Search one ingredient / kit item inside its target domain.

    1. Expand Hinglish synonyms.
    2. Sub-category hint: an explicit ``sub_categories`` (e.g. from a kit item)
       wins; otherwise ITEM_CATEGORY_MAP is consulted. If the hinted search's best
       vector similarity is below SUBCAT_RELAX_SIM, drop back to the domain-only
       filter (recorded in ``relaxed_from``).
    3. Audit: also take the unfiltered top hit and log when it is in another
       domain. Those log lines are the evidence for our failure-case analysis.
    """
    t0 = time.perf_counter()
    expanded = expand_query(item)
    if qvec is None:
        qvec = _embed([expanded])[0]
    bm25_all = bm25_scores(expanded, collection)
    # look the hint up on the expanded text too: "nimbu" → "nimbu lemon" finds the fresh-produce hint
    hint = (list(sub_categories) if sub_categories
            else lookup_subcategories(item, domain) or lookup_subcategories(expanded, domain))

    flt = Filter(domain, tuple(hint) if hint else None, brand)
    result = hybrid_search(item, k, flt, collection, qvec=qvec, bm25_all=bm25_all)
    if hint and result.best_vector_sim < config.SUBCAT_RELAX_SIM:
        relaxed = hybrid_search(item, k, Filter(domain, None, brand), collection, qvec=qvec, bm25_all=bm25_all)
        relaxed.relaxed_from = flt
        result = relaxed

    if audit and domain:
        top = hybrid_search(item, 1, NO_FILTER, collection, qvec=qvec, bm25_all=bm25_all).best
        result.unfiltered_top = top
        if top is not None and top.metadata.get("domain") != domain:
            result.wrong_domain = True
            _log_wrong_domain(item, domain, top, result.best)
    result.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return result


@dataclass(frozen=True)
class ItemSpec:
    """One item to search: text plus its target domain and optional filters."""

    text: str
    domain: str | None = None
    sub_categories: tuple[str, ...] | None = None
    brand: str | None = None


def search_items(
    items: list[ItemSpec | tuple[str, str | None]], k: int = config.TOP_K, collection: Collection = "products"
) -> list[SearchResult]:
    """Batch version of search_item: all query embeddings in one model call."""
    if not items:
        return []
    specs = [it if isinstance(it, ItemSpec) else ItemSpec(*it) for it in items]
    qvecs = _embed([expand_query(s.text) for s in specs])
    return [
        search_item(s.text, s.domain, k, collection, qvec=v, sub_categories=s.sub_categories, brand=s.brand)
        for s, v in zip(specs, qvecs)
    ]


def _log_wrong_domain(item: str, target: str, unfiltered: Hit, filtered: Hit | None) -> None:
    def brief(h: Hit | None) -> dict[str, Any] | None:
        if h is None:
            return None
        m = h.metadata
        return {"id": h.doc_key, "product": m.get("product"), "domain": m.get("domain"),
                "sub_category": m.get("sub_category"), "vector_rank": h.vector_rank, "bm25_rank": h.bm25_rank}

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "query": item, "target_domain": target,
            "unfiltered_top": brief(unfiltered), "filtered_top": brief(filtered)}
    with config.WRONG_DOMAIN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# --- domain classifier for free-form catalogue questions ----------------------------

# Purpose phrases ("for hair", "for cooking") are the strongest signal, so they win.
_PURPOSE_RULES: list[tuple[str, str]] = [
    (r"\bfor (my )?(hair|scalp|skin|face|body|lips|beard|shaving)\b", "personal_care"),
    (r"\bfor (cooking|frying|baking|recipes?|tadka|eating)\b", "food"),
    (r"\bfor (the )?(floor|toilet|bathroom|kitchen (floor|counter|sink)|clothes|laundry|dishes|utensils)\b", "household"),
    (r"\bfor (my |a )?(baby|newborn|infant|toddler|kids?)\b", "baby"),
    (r"\bfor (my |a )?(dog|puppy|cat|kitten|pet)s?\b", "pets"),
]
_WORD_RULES: dict[str, str] = {
    "personal_care": r"\b(shampoo|conditioner|soaps?|bathing|body ?wash|face ?wash|deo(dorant)?|perfume|"
                     r"lipstick|sunscreen|moisturi[sz]er|serum|toothpaste|toothbrush|razor|skincare|haircare|"
                     r"sanitary|cleanser|face|skin|lips?|scalp)\b",
    "household": r"\b(floor|toilet|detergent|dishwash|mop|broom|repellent|garbage|agarbatti|camphor|"
                 r"pooja|puja|freshener|bleach|disinfectant)\b",
    "baby": r"\b(baby|diapers?|infant|newborn|toddler)\b",
    "pets": r"\b(dog|puppy|cat|kitten|pets?)\b",
    "kitchen": r"\b(cookware|frying pan|kadai|pressure cooker|utensils?|tiffin|lunch ?box|casserole|"
               r"chopping board|knife|storage container)\b",
    "food": r"\b(atta|rice|dal|oil|ghee|masala|spices?|tea|coffee|biscuits?|chips|snacks?|juice|"
            r"milk|paneer|flour|sugar|salt|noodles|chocolate|cereal|oats|eggs?|jeera|haldi|cumin|turmeric)\b",
}


def infer_domain(query: str) -> tuple[str | None, float, str]:
    """Guess the domain of a free-form question: (domain or None, confidence, method).

    1. Purpose phrase ("coconut oil for hair") → that domain, confidence 1.0.
    2. Keyword rules: a single winning domain → confidence = its share of keyword hits.
    3. Otherwise vote over the unfiltered top-10 hybrid hits; confident only when
       one domain holds ≥ DOMAIN_VOTE_SHARE of them. Not confident → None, and the
       caller searches all categories (as the brief specifies).
    """
    q = query.lower()
    for pattern, dom in _PURPOSE_RULES:
        if re.search(pattern, q):
            return dom, 1.0, "purpose phrase"
    counts = Counter({d: len(re.findall(p, q)) for d, p in _WORD_RULES.items()})
    counts = +counts  # drop zeros
    if counts:
        (top, n), *rest = counts.most_common()
        if not rest or n > rest[0][1]:
            return top, round(n / sum(counts.values()), 2), "keyword rule"
    hits = hybrid_search(query, 10, NO_FILTER).hits
    votes = Counter(h.metadata.get("domain") for h in hits)
    if votes:
        dom, n = votes.most_common(1)[0]
        share = n / len(hits)
        if share >= config.DOMAIN_VOTE_SHARE:
            return dom, round(share, 2), "retrieval vote"
        return None, round(share, 2), "retrieval vote (not confident)"
    return None, 0.0, "no signal"


# --- warm-up --------------------------------------------------------------------------

def warm_up() -> dict[str, float]:
    """Load model + all indexes and run one query, so the first real query is fast.

    The first query in a fresh process took ~23 s in Phase 3 (CUDA init, model load,
    Chroma HNSW load). The app calls this once at startup.
    """
    timings: dict[str, float] = {}
    t = time.perf_counter()
    _embed(["warm up"])
    timings["model_s"] = round(time.perf_counter() - t, 2)
    for name in config.COLLECTIONS:
        t = time.perf_counter()
        hybrid_search("warm up", 1, NO_FILTER, name)  # type: ignore[arg-type]
        timings[f"{name}_s"] = round(time.perf_counter() - t, 2)
    return timings
