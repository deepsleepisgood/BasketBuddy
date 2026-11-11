"""Turn cleaned products, recipes and kits into retrieval chunks.

Chunking strategy (explained in docs/PIPELINE.md):
- products: one product = one chunk. A row is an atomic fact (name + price +
  rating + description); splitting would separate a price from its product and
  merging would mix products. Only when header + description exceeds
  PRODUCT_MAX_TOKENS is the description split into overlapping windows, and the
  header line is repeated in every sub-chunk so each stays self-describing.
- recipes: one recipe = one chunk of name + cuisine + course + diet +
  ingredient names. Instructions are not embedded: they would dilute matching
  on dish name and ingredients.
- kits: one kit = one chunk of name + aliases + description + item names.

Run `python -m rag.chunking` to print chunk statistics and write them into
docs/PIPELINE.md.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

import config

PRODUCT_MAX_TOKENS = 350   # header + description budget per product chunk
PRODUCT_OVERLAP_TOKENS = 50

MetaValue = str | int | float | bool


@dataclass(frozen=True)
class Chunk:
    """One unit of retrieval: the text to embed / index, plus filterable metadata."""

    id: str
    text: str
    metadata: dict[str, MetaValue] = field(default_factory=dict)


# --- helpers -------------------------------------------------------------------

def _num(x: float) -> str:
    """220.0 → '220', 27.5 → '27.5'."""
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def _clean_meta(meta: dict[str, Any]) -> dict[str, MetaValue]:
    """Chroma rejects None/NaN metadata values, so missing values are omitted."""
    out: dict[str, MetaValue] = {}
    for k, v in meta.items():
        if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
            continue
        if hasattr(v, "item"):  # numpy / pandas scalars → python
            v = v.item()
        out[k] = v
    return out


def split_tokens(
    text: str, tokenizer: Any, max_tokens: int, overlap: int
) -> list[str]:
    """Split ``text`` into windows of ``max_tokens`` tokens with ``overlap`` tokens shared.

    Uses the tokenizer's character offsets and slices the original string, so
    words and punctuation come back exactly as written (no decode artefacts).
    """
    if max_tokens <= overlap:
        raise ValueError("max_tokens must be larger than overlap")
    enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets: list[tuple[int, int]] = enc["offset_mapping"]
    if len(offsets) <= max_tokens:
        return [text]
    windows, step = [], max_tokens - overlap
    for start in range(0, len(offsets), step):
        end = min(start + max_tokens, len(offsets))
        windows.append(text[offsets[start][0]: offsets[end - 1][1]].strip())
        if end == len(offsets):
            break
    return windows


# --- products -----------------------------------------------------------------

def product_header(row: Any) -> str:
    """The self-describing header line shared by every chunk of a product."""
    rating = f"{_num(row.rating)}/5" if pd.notna(row.rating) else "not rated"
    pack = ""
    if pd.notna(row.pack_unit):
        pack = f" | Pack: {_num(row.pack_qty)} {row.pack_unit}" if pd.notna(row.pack_qty) else f" | Pack: {row.pack_unit}"
    return (
        f"{row.product} | Brand: {row.brand} | {row.category} > {row.sub_category} | "
        f"Type: {row.type} | Price: ₹{_num(row.sale_price)} (MRP ₹{_num(row.market_price)}) | "
        f"Rating: {rating}{pack}"
    )


def product_chunks(
    df: pd.DataFrame,
    tokenizer: Any,
    max_tokens: int = PRODUCT_MAX_TOKENS,
    overlap: int = PRODUCT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """One chunk per product; long descriptions split with the header repeated."""
    chunks: list[Chunk] = []
    headers = [product_header(r) for r in df.itertuples(index=False)]
    header_lens = [len(ids) for ids in tokenizer(headers, add_special_tokens=False)["input_ids"]]
    for row, header, h_len in zip(df.itertuples(index=False), headers, header_lens):
        budget = max(max_tokens - h_len - 2, overlap + 50)  # 2 tokens for the " | " joiner
        parts = split_tokens(row.description, tokenizer, budget, overlap)
        base_meta = {
            "doc_type": "product",
            "product_id": int(row.product_id),
            "product": row.product,
            "brand": row.brand,
            "category": row.category,
            "sub_category": row.sub_category,
            "type": row.type,
            "domain": row.domain,
            "sale_price": float(row.sale_price),
            "market_price": float(row.market_price),
            "rating": row.rating,
            "pack_qty": row.pack_qty,
            "pack_unit": row.pack_unit,
            "pack_base_qty": row.pack_base_qty,
            "pack_base_unit": row.pack_base_unit,
            "n_chunks": len(parts),
        }
        for i, part in enumerate(parts):
            cid = f"P{row.product_id}" if len(parts) == 1 else f"P{row.product_id}-{i}"
            chunks.append(Chunk(cid, f"{header} | {part}", _clean_meta({**base_meta, "chunk_index": i})))
    return chunks


# --- recipes ------------------------------------------------------------------

def recipe_text(row: Any) -> str:
    """Name, cuisine, course, diet and ingredient names: what a dish query matches on."""
    ingredients = row.clean_ingredients if pd.notna(row.clean_ingredients) else row.ingredients
    return (
        f"{row.name} | Cuisine: {row.cuisine} | Course: {row.course} | Diet: {row.diet} | "
        f"Ingredients: {ingredients}"
    )


def recipe_chunks(df: pd.DataFrame) -> list[Chunk]:
    """One chunk per recipe; instructions stay in the dataframe, not in the index."""
    return [
        Chunk(
            f"R{row.recipe_id}",
            recipe_text(row),
            _clean_meta({
                "doc_type": "recipe",
                "recipe_id": int(row.recipe_id),
                "name": row.name,
                "cuisine": row.cuisine,
                "course": row.course,
                "diet": row.diet,
                "servings": row.servings,
                "total_time_mins": row.total_time_mins,
                "is_indian": bool(row.is_indian),
                "has_clean_ingredients": bool(pd.notna(row.clean_ingredients)),
            }),
        )
        for row in df.itertuples(index=False)
    ]


# --- kits ---------------------------------------------------------------------

def load_kits() -> list[dict[str, Any]]:
    """Read the synthetic kits corpus."""
    return json.loads(config.KITS_PATH.read_text(encoding="utf-8"))["kits"]


def kit_chunks(kits: Iterable[dict[str, Any]]) -> list[Chunk]:
    """One chunk per kit: name, aliases, description and item names."""
    chunks = []
    for kit in kits:
        items = ", ".join(it["item"] for it in kit["items"])
        text = (
            f"{kit['name']} | Also known as: {'; '.join(kit['aliases'])} | "
            f"{kit['description']} | Items: {items}"
        )
        chunks.append(Chunk(
            f"K{kit['id']}",
            text,
            {"doc_type": "kit", "kit_id": kit["id"], "name": kit["name"],
             "domain": kit["domain"], "n_items": len(kit["items"]), "synthetic": True},
        ))
    return chunks


# --- build all + stats --------------------------------------------------------

def build_all_chunks(tokenizer: Any) -> dict[str, list[Chunk]]:
    """Chunks for the three collections, keyed by collection name."""
    products = pd.read_parquet(config.PRODUCTS_PARQUET)
    recipes = pd.read_parquet(config.RECIPES_PARQUET)
    return {
        "products": product_chunks(products, tokenizer),
        "recipes": recipe_chunks(recipes),
        "kits": kit_chunks(load_kits()),
    }


def chunk_stats(chunks: dict[str, list[Chunk]], tokenizer: Any) -> pd.DataFrame:
    """Per-collection count and token-length statistics."""
    rows = []
    for name, cs in chunks.items():
        lens = pd.Series([len(x) for x in tokenizer([c.text for c in cs], add_special_tokens=False)["input_ids"]])
        n_docs = len({c.metadata.get("product_id", c.id) for c in cs})
        rows.append({
            "collection": name, "source_docs": n_docs, "chunks": len(cs),
            "split_docs": int(sum(1 for c in cs if c.metadata.get("chunk_index") == 1)),
            "avg_tokens": round(lens.mean(), 1), "median_tokens": int(lens.median()),
            "p95_tokens": int(lens.quantile(0.95)), "max_tokens": int(lens.max()),
            "over_512": int((lens > 510).sum()),
        })
    return pd.DataFrame(rows)


def _stats_markdown(stats: pd.DataFrame) -> str:
    why = {
        "products": "One product = one atomic fact (name + price + rating + description). Split only above "
                    f"{PRODUCT_MAX_TOKENS} tokens, {PRODUCT_OVERLAP_TOKENS}-token overlap, header repeated.",
        "recipes": "One recipe per chunk; matched by dish name, cuisine, diet and ingredient names. "
                   "Instructions not embedded.",
        "kits": "One synthetic need/occasion list per chunk; matched by name, aliases and items.",
    }
    lines = [
        "| Collection | Chunk unit | Source docs | Chunks | Split docs | Avg tokens | Median | p95 | Max | Why |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    unit = {"products": "1 product", "recipes": "1 recipe", "kits": "1 kit"}
    for r in stats.itertuples(index=False):
        lines.append(
            f"| `{r.collection}` | {unit[r.collection]} | {r.source_docs:,} | {r.chunks:,} | {r.split_docs:,} | "
            f"{r.avg_tokens} | {r.median_tokens} | {r.p95_tokens} | {r.max_tokens} | {why[r.collection]} |"
        )
    lines.append("")
    lines.append(f"Token counts use the `{config.EMBED_MODEL}` tokenizer (the model's limit is 512 tokens).")
    return "\n".join(lines)


def main() -> None:
    from ingest.datalog import write_section
    from rag.embeddings import get_tokenizer

    tok = get_tokenizer()
    chunks = build_all_chunks(tok)
    stats = chunk_stats(chunks, tok)
    print(stats.to_string(index=False))
    for name, cs in chunks.items():
        print(f"\n--- sample {name} chunk ---\n{cs[len(cs) // 2].text[:400]}")
    write_section(config.DOCS_DIR / "PIPELINE.md", "chunk-stats", _stats_markdown(stats))
    print("\nWrote chunk statistics → docs/PIPELINE.md")


if __name__ == "__main__":
    main()
