"""Clean the BigBasket product list → data/clean/products.parquet.

Run:  python -m ingest.clean_products
Keeps every category; removes only broken, duplicate or empty records, and
writes row counts plus the category distribution into docs/DATA_LOG.md.
"""
from __future__ import annotations

import html
import re

import pandas as pd

import config
from ingest.datalog import StepLog, write_section
from ingest.pack_size import parse_pack_size
from ingest.text_fix import fix_mojibake

FOOD_CATEGORIES = {
    "Foodgrains, Oil & Masala", "Fruits & Vegetables", "Bakery, Cakes & Dairy",
    "Snacks & Branded Foods", "Gourmet & World Food", "Beverages", "Eggs, Meat & Fish",
}

# Marketing boilerplate appended to ~1,100 descriptions ("For Beauty tips, tricks & more
# visit https://bigbasket.blog/" or http://lookbeautiful.in/); it carries no product facts.
# The URL is sometimes glued to the next sentence ("bigbasket.blog/Extra-Large Dry..."),
# so the match stops at the domain's trailing slash instead of the next whitespace.
_BLOG_URL = r"(?:https?://)?(?:www\.)?(?:bigbasket\.blog|lookbeautiful\.in)/?"
_BOILERPLATE_RE = re.compile(
    rf"for\s+beauty\s+tips,?\s*tricks\s*(?:&|and)\s*more\s+visit\W*(?:{_BLOG_URL})?"
    rf"|(?:visit\W*)?{_BLOG_URL}",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[\s ​﻿]+")
_LETTER_RE = re.compile(r"[A-Za-z]")


def map_domain(category: str, sub_category: str) -> str:
    """Coarse domain used as a retrieval filter (food / personal_care / household / ...)."""
    if sub_category == "Baby Food & Formula" or category == "Baby Care":
        return "baby"
    if category in FOOD_CATEGORIES:
        return "food"
    if category == "Beauty & Hygiene":
        return "personal_care"
    if category == "Cleaning & Household":
        return "household"
    if category == "Kitchen, Garden & Pets":
        if sub_category == "Pet Food & Accessories":
            return "pets"
        if sub_category == "Gardening":
            return "other"
        return "kitchen"
    return "other"


def clean_text(text: object) -> str:
    """Repair mojibake, unescape HTML, drop tags and blog boilerplate, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    text = _TAG_RE.sub(" ", html.unescape(fix_mojibake(text)))
    text = _BOILERPLATE_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def is_gibberish(text: str) -> bool:
    """True when a description has (almost) no letters, e.g. '...' or '1234'."""
    return len(_LETTER_RE.findall(text)) < 3


def main() -> None:
    raw = pd.read_csv(config.RAW_PRODUCTS_CSV)
    print(f"Loaded {len(raw):,} rows from {config.RAW_PRODUCTS_CSV.name}")
    log = StepLog(len(raw))
    df = raw.rename(columns={"index": "product_id"}).copy()

    # 1. normalise text fields and numerics first, so "empty" checks see cleaned values
    for col in ("product", "category", "sub_category", "brand", "type"):
        # non-breaking / zero-width spaces appear in ~90 names; normalise them
        df[col] = (
            df[col].astype("string").map(fix_mojibake, na_action="ignore").astype("string")
            .str.replace(_WS_RE, " ", regex=True).str.strip()
        )
    raw_desc = df["description"].fillna("")
    boilerplate_rows = int(raw_desc.map(lambda t: bool(_BOILERPLATE_RE.search(fix_mojibake(t)))).sum())
    mojibake_rows = int((raw_desc.map(fix_mojibake) != raw_desc).sum())
    df["description"] = df["description"].map(clean_text)
    for col in ("sale_price", "market_price", "rating"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 2. drop genuinely broken rows
    df = df[df["product"].fillna("").str.len() > 0]
    log.step("Drop rows with empty product name", len(df))
    df = df[df["sale_price"].notna() & (df["sale_price"] > 0)]
    log.step("Drop rows with missing / non-positive sale price", len(df))
    df = df[df["description"].str.len() > 0]
    log.step("Drop rows with empty description (after cleaning)", len(df))
    df = df[~df["description"].map(is_gibberish)]
    log.step("Drop rows with gibberish description (<3 letters)", len(df))

    # 3. exact duplicates on (product, brand, sale_price): the same item listed under
    #    two sub-categories. Keep the first listing and remember the others in `also_in`.
    key = ["product", "brand", "sale_price"]
    also_in = (
        df.groupby(key, dropna=False)["sub_category"]
        .agg(lambda s: " | ".join(sorted(set(s.dropna())) ) )
        .rename("also_in")
    )
    df = df.drop_duplicates(subset=key, keep="first").merge(also_in, on=key, how="left")
    df["also_in"] = [
        " | ".join(x for x in a.split(" | ") if x != s) or None
        for a, s in zip(df["also_in"].fillna(""), df["sub_category"])
    ]
    log.step("Drop exact duplicates on (product, brand, sale_price)", len(df))

    # 4. derived columns
    df["brand"] = df["brand"].fillna("Unbranded")
    df["market_price"] = df["market_price"].fillna(df["sale_price"])
    df["domain"] = [map_domain(c, s) for c, s in zip(df["category"], df["sub_category"])]
    packs = df["product"].map(parse_pack_size)
    df["pack_qty"] = packs.map(lambda p: p.qty if p else None).astype("Float64")
    df["pack_unit"] = packs.map(lambda p: p.unit if p else None).astype("string")
    df["pack_base_qty"] = packs.map(lambda p: p.base_qty if p else None).astype("Float64")
    df["pack_base_unit"] = packs.map(lambda p: p.base_unit if p else None).astype("string")
    df["discount_pct"] = ((1 - df["sale_price"] / df["market_price"]) * 100).round(1)

    cols = [
        "product_id", "product", "brand", "category", "sub_category", "type", "domain",
        "sale_price", "market_price", "discount_pct", "rating",
        "pack_qty", "pack_unit", "pack_base_qty", "pack_base_unit", "also_in", "description",
    ]
    df = df[cols].sort_values("product_id").reset_index(drop=True)
    assert df["product_id"].is_unique, "product_id must stay unique"

    config.CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.PRODUCTS_PARQUET, index=False)
    print(f"Wrote {len(df):,} rows → {config.PRODUCTS_PARQUET}")

    write_section(config.DATA_LOG, "products", _report(df, log, boilerplate_rows, mojibake_rows))


def _report(df: pd.DataFrame, log: StepLog, boilerplate_rows: int, mojibake_rows: int) -> str:
    """Markdown block for DATA_LOG.md."""
    dom = df["domain"].value_counts()
    cat = df.groupby(["category", "domain"]).size().rename("rows").reset_index()
    sub = df.groupby(["category", "sub_category"]).size().rename("rows").reset_index()
    words = df["description"].str.split().str.len()
    parts = [
        "## Products: cleaning steps (generated by `ingest/clean_products.py`)",
        log.to_markdown(),
        "",
        "Other transformations (no rows removed):",
        f"- Repaired mojibake (UTF-8 mis-decoded as Windows-1252, e.g. `â€™` → `’`) in **{mojibake_rows:,}** "
        "descriptions (`ingest/text_fix.py`). Runs that lost bytes and can't be restored are scrubbed to a space.",
        f"- Stripped 'For Beauty tips, tricks & more visit bigbasket.blog / lookbeautiful.in' boilerplate "
        f"from **{boilerplate_rows:,}** descriptions; unescaped HTML entities, removed tags, collapsed whitespace "
        "(including non-breaking and zero-width spaces in names).",
        "- Coerced `sale_price`, `market_price`, `rating` to numeric. Missing ratings stay empty "
        f"(**{df['rating'].isna().sum():,}** rows, {df['rating'].isna().mean():.0%}); they are never filled with 0.",
        f"- Missing brand → `Unbranded` ({(df['brand'] == 'Unbranded').sum()} rows).",
        f"- Duplicate listings kept once; their other sub-categories are kept in `also_in` "
        f"({df['also_in'].notna().sum():,} products).",
        f"- `pack_qty`/`pack_unit` parsed from the product name for **{df['pack_unit'].notna().sum():,}** "
        f"products ({df['pack_unit'].notna().mean():.1%}). The rest have no size in the data.",
        f"- Description length (words): median {words.median():.0f}, 90th pct {words.quantile(.9):.0f}, "
        f"max {words.max()}.",
        "",
        f"**Final: {len(df):,} products, {df['category'].nunique()} categories, "
        f"{df['sub_category'].nunique()} sub-categories, {df['brand'].nunique():,} brands.**",
        "",
        "### Domain distribution",
        "| Domain | Products |", "|---|---:|",
        *[f"| {d} | {n:,} |" for d, n in dom.items()],
        "",
        "### Category → domain",
        "| Category | Domain | Products |", "|---|---|---:|",
        *[f"| {r.category} | {r.domain} | {r.rows:,} |" for r in cat.itertuples()],
        "",
        "<details><summary>Sub-category distribution</summary>",
        "",
        "| Category | Sub-category | Products |", "|---|---|---:|",
        *[f"| {r.category} | {r.sub_category} | {r.rows:,} |" for r in sub.itertuples()],
        "",
        "</details>",
    ]
    return "\n".join(parts)


if __name__ == "__main__":
    main()
