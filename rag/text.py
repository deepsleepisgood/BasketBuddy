"""Text normalisation shared by index build and query time (BM25 must tokenise both identically)."""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small English stop-word list: BM25 gains nothing from "the" or "with", and
# our chunk headers repeat words like "brand", "type", "price" in every chunk
# (IDF already makes those near-zero, so they are left in).
STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to "
    "was were will with your you our we can all also".split()
)


def bm25_tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on non-alphanumerics, drop stop-words.

    '₹171 Sun-Lite Oil!' → ['171', 'sun', 'lite', 'oil']
    """
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]
