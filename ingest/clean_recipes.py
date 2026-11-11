"""Clean the Archana's Kitchen recipes → data/clean/recipes.parquet.

Run:  python -m ingest.clean_recipes
Primary source: IndianFoodDatasetCSV.csv (has Servings / Course / Diet).
Enrichment: Cleaned_Indian_Food_Dataset.csv, left-joined on URL for the
quantity-free ingredient names in `Cleaned-Ingredients`.
"""
from __future__ import annotations

import re

import pandas as pd

import config
from ingest.datalog import StepLog, write_section

_DEVANAGARI = r"[ऀ-ॿ]"
_RECIPE_WORD_RE = re.compile(r"\s*\(?\brecipe(?:\s+in\s+hindi)?\b\)?", re.IGNORECASE)
_WS_RE = re.compile(r"[\s ​﻿]+")
MAX_PLAUSIBLE_SERVINGS = 20  # a handful of rows say 100–1000 servings (bulk / data errors)

NON_INDIAN_CUISINES = {
    "Continental", "Italian", "Mexican", "Asian", "Thai", "Chinese", "French",
    "Middle Eastern", "American", "Japanese", "Greek", "Korean", "Vietnamese",
    "Mediterranean", "European", "Indonesian", "Malaysian", "Sri Lankan", "Lebanese",
    "Burmese", "British", "Spanish", "Afghan", "Caribbean", "African", "Nepalese",
    "Arab", "Pakistani", "Jewish", "Singapore", "Fusion", "Cuisine",
    "Sichuan", "Cantonese", "Hunan", "Shandong", "World Breakfast",
}


def match_name(name: str) -> str:
    """Name used for matching / dedup: 'Badam Halwa Recipe' → 'Badam Halwa'.

    Removes the word "Recipe" (and "(Recipe In Hindi)") wherever it appears and
    tidies the leftover punctuation; the subtitle after " - " is kept because it
    often names the dish more plainly ("Ragi Semiya Upma - Ragi Millet Vermicelli").
    """
    name = _RECIPE_WORD_RE.sub(" ", name)
    name = re.sub(r"\(\s*\)", " ", name)
    name = _WS_RE.sub(" ", name).strip(" -–,")
    return re.sub(r"\s+-\s*$", "", name)


def normalise_url(url: pd.Series) -> pd.Series:
    """http/https and www differ between the two recipe files; compare without them."""
    return (
        url.fillna("").str.strip().str.lower()
        .str.replace(r"^https?://(www\.)?", "", regex=True).str.rstrip("/")
    )


def normalise_cuisine(c: str) -> str:
    """'Gujarati Recipes\\ufeff' → 'Gujarati'."""
    c = _WS_RE.sub(" ", c or "").strip()
    return re.sub(r"\s+Recipes?$", "", c) or "Unknown"


def main() -> None:
    raw = pd.read_csv(config.RAW_RECIPES_CSV)
    enrich = pd.read_csv(config.RAW_RECIPES_CLEAN_CSV)
    print(f"Loaded {len(raw):,} recipes and {len(enrich):,} enrichment rows")
    log = StepLog(len(raw))
    df = raw.copy()

    for col in ("TranslatedRecipeName", "TranslatedIngredients", "TranslatedInstructions"):
        df[col] = df[col].astype("string").str.replace(_WS_RE, " ", regex=True).str.strip()

    # 1. untranslated (Hindi) rows: unusable for an English cart
    df = df[df["TranslatedIngredients"].fillna("").str.len() > 0]
    log.step("Drop rows with empty TranslatedIngredients", len(df))
    hindi = df["TranslatedRecipeName"].str.contains(_DEVANAGARI) | df["TranslatedIngredients"].str.contains(_DEVANAGARI)
    df = df[~hindi.fillna(False)]
    log.step("Drop rows whose name or ingredients are still Devanagari", len(df))
    hindi_instr = df["TranslatedInstructions"].fillna("").str.contains(_DEVANAGARI)
    df.loc[hindi_instr, "TranslatedInstructions"] = pd.NA  # keep recipe, drop Hindi steps
    n_hindi_instr = int(hindi_instr.sum())

    # 2. duplicates: exact name first, then names that differ only by "Recipe (In Hindi)".
    #    Prefer the English-page version of a recipe when both exist.
    df = df.drop_duplicates(subset="TranslatedRecipeName", keep="first")
    log.step("Drop exact duplicates on TranslatedRecipeName", len(df))
    df["name"] = df["TranslatedRecipeName"].map(match_name)
    df["_hindi_page"] = df["URL"].fillna("").str.contains("in-hindi", case=False)
    df["_key"] = df["name"].str.lower()
    df = df.sort_values(["_hindi_page", "Srno"]).drop_duplicates(subset="_key", keep="first")
    log.step("Drop near-duplicates (same name once 'Recipe'/'In Hindi' removed)", len(df))

    # 3. enrich with clean ingredient names (join on scheme-insensitive URL)
    enrich = enrich.assign(_url=normalise_url(enrich["URL"]))[["_url", "Cleaned-Ingredients", "Ingredient-count"]]
    enrich = enrich.drop_duplicates("_url")
    df["_url"] = normalise_url(df["URL"])
    df = df.merge(enrich, on="_url", how="left")
    matched = df["Cleaned-Ingredients"].notna()

    # 4. tidy fields
    df["cuisine"] = df["Cuisine"].map(normalise_cuisine)
    df["is_indian"] = ~df["cuisine"].isin(NON_INDIAN_CUISINES)
    df["diet"] = df["Diet"].fillna("Unknown").str.replace("Vegeterian", "Vegetarian")
    servings = pd.to_numeric(df["Servings"], errors="coerce")
    bad_servings = servings.gt(MAX_PLAUSIBLE_SERVINGS) | servings.lt(1)
    df["servings"] = servings.mask(bad_servings).astype("Int64")
    df["total_time_mins"] = pd.to_numeric(df["TotalTimeInMins"], errors="coerce").astype("Int64")

    out = pd.DataFrame({
        "recipe_id": df["Srno"].astype(int),
        "name": df["name"],
        "display_name": df["TranslatedRecipeName"],
        "ingredients": df["TranslatedIngredients"],
        "clean_ingredients": df["Cleaned-Ingredients"].astype("string"),
        "ingredient_count": pd.to_numeric(df["Ingredient-count"], errors="coerce").astype("Int64"),
        "servings": df["servings"],
        "total_time_mins": df["total_time_mins"],
        "cuisine": df["cuisine"],
        "is_indian": df["is_indian"],
        "course": df["Course"].fillna("Unknown"),
        "diet": df["diet"],
        "instructions": df["TranslatedInstructions"],
        "url": df["URL"],
    }).sort_values("recipe_id").reset_index(drop=True)
    assert out["recipe_id"].is_unique

    config.CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(config.RECIPES_PARQUET, index=False)
    print(f"Wrote {len(out):,} recipes → {config.RECIPES_PARQUET}")
    print(f"  Cleaned-Ingredients match rate: {matched.mean():.1%}")

    write_section(config.DATA_LOG, "recipes", _report(out, log, n_hindi_instr, int(bad_servings.sum())))


def _report(df: pd.DataFrame, log: StepLog, n_hindi_instr: int, n_bad_servings: int) -> str:
    """Markdown block for DATA_LOG.md."""
    matched = df["clean_ingredients"].notna()
    cuisines = df["cuisine"].value_counts()
    diets = df["diet"].value_counts()
    return "\n".join([
        "## Recipes: cleaning steps (generated by `ingest/clean_recipes.py`)",
        log.to_markdown(),
        "",
        "Other transformations (no rows removed):",
        f"- Recipes whose *instructions* were still Hindi but whose name and ingredients were English: "
        f"kept, instructions set to empty (**{n_hindi_instr:,}** rows). Instructions are never embedded.",
        "- `name` = display name with the word \"Recipe\" / \"(Recipe In Hindi)\" removed, used for matching "
        "and dedup; the original is kept as `display_name`.",
        f"- Left-joined `Cleaned_Indian_Food_Dataset.csv` on URL (ignoring http/https and www, which differ "
        f"between the files): **{matched.sum():,} / {len(df):,} recipes matched ({matched.mean():.1%})**. "
        "Unmatched recipes fall back to regex/LLM extraction from `ingredients` at query time.",
        f"- Servings outside 1–{MAX_PLAUSIBLE_SERVINGS} treated as missing (**{n_bad_servings}** rows, e.g. 1000).",
        "- Cuisine: stripped trailing \" Recipes\" and stray BOM characters; diet typo \"Non Vegeterian\" fixed.",
        f"- **Non-Indian cuisines kept** ({(~df['is_indian']).sum():,} recipes, flagged `is_indian=False`). "
        "Decision: shoppers do cook pasta, burritos and Thai curry, and those dishes still map to BigBasket "
        "products. Revisit only if they hurt dish-name retrieval in the eval.",
        "",
        f"**Final: {len(df):,} recipes, {df['cuisine'].nunique()} cuisines.**",
        "",
        "| Top cuisines | Recipes |", "|---|---:|",
        *[f"| {c} | {n:,} |" for c, n in cuisines.head(12).items()],
        "",
        "| Diet | Recipes |", "|---|---:|",
        *[f"| {d} | {n:,} |" for d, n in diets.items()],
    ])


if __name__ == "__main__":
    main()
