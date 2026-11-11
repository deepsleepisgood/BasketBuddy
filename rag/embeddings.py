"""Embedding model wrapper. Swap the model by changing config.EMBED_MODEL only.

bge-small-en-v1.5 (384-dim, ~33M params) runs comfortably on a laptop CPU.
BGE models expect a short instruction in front of *queries* (not passages)
for query→passage retrieval, so `embed_queries` adds it.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:  # heavy imports stay lazy so tests and CLIs start fast
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from transformers import PreTrainedTokenizerBase

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def pick_device() -> str:
    """'cuda' if a GPU build of torch sees a GPU, else 'cpu'.

    The index build (~33k chunks) takes ~1-2 min on the RTX 3050 Ti vs ~45 min on
    CPU. At query time we embed a handful of short strings, which is fast on either.
    """
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def get_model() -> "SentenceTransformer":
    """Load the sentence-transformers model once per process, on GPU if available."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(config.EMBED_MODEL, device=pick_device())


@lru_cache(maxsize=1)
def get_tokenizer() -> "PreTrainedTokenizerBase":
    """The embedding model's own tokenizer, so chunk sizes are measured in its tokens."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(config.EMBED_MODEL)
    # We tokenize whole descriptions only to *measure and split* them, so the
    # "longer than 512" warning is expected noise here.
    tok.model_max_length = 1_000_000
    return tok


def embed_passages(texts: list[str], batch_size: int = 32, show_progress: bool = False) -> "np.ndarray":
    """Embed documents/chunks. Vectors are L2-normalised, so dot product = cosine."""
    return get_model().encode(
        texts, batch_size=batch_size, normalize_embeddings=True,
        show_progress_bar=show_progress, convert_to_numpy=True,
    )


def embed_queries(queries: list[str]) -> "np.ndarray":
    """Embed search queries with the BGE retrieval instruction prepended."""
    return get_model().encode(
        [BGE_QUERY_INSTRUCTION + q for q in queries],
        normalize_embeddings=True, convert_to_numpy=True,
    )
