"""Download the prebuilt index on a fresh deploy, where index/ is missing (it is not in git: 181 MB).

The zip is a GitHub Release asset made from a local `python -m ingest.build_index` run. Building on a
CPU-only host takes ~45 min, downloading takes seconds. Set INDEX_URL to override the default asset.

Run:  python -m ingest.fetch_index
"""
from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import config

DEFAULT_INDEX_URL = "https://github.com/deepsleepisgood/BasketBuddy/releases/download/index-v1/index.zip"


def index_missing() -> bool:
    """True when any collection's BM25 file or the Chroma store is absent."""
    if not (config.CHROMA_DIR / "chroma.sqlite3").exists():
        return True
    return any(not (config.BM25_DIR / f"{name}.pkl").exists() for name in config.COLLECTIONS)


def fetch_index(url: str | None = None) -> None:
    """Download the index zip and unpack it into the project root (the zip holds index/...)."""
    url = url or os.getenv("INDEX_URL") or DEFAULT_INDEX_URL
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "index.zip"
        with urllib.request.urlopen(url, timeout=120) as r, zpath.open("wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        with zipfile.ZipFile(zpath) as z:
            root = config.ROOT.resolve()
            for name in z.namelist():   # refuse paths that would land outside the project
                if not (root / name).resolve().is_relative_to(root / "index"):
                    raise ValueError(f"Unexpected path in index zip: {name}")
            z.extractall(root)
    if index_missing():
        raise FileNotFoundError(f"Downloaded {url} but the index is still incomplete.")


if __name__ == "__main__":
    fetch_index()
    print(f"Index ready at {config.INDEX_DIR}")
