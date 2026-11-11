"""Build the retrieval indexes: chunks → embeddings → Chroma, plus one BM25 index per collection.

Run:  python -m ingest.build_index            (skips collections that are already up to date)
      python -m ingest.build_index --force    (rebuild everything)

Idempotent: each collection's chunks are fingerprinted (model name + ids +
texts + metadata). If the fingerprint, the Chroma count and the BM25 file all
match index/manifest.json, that collection is skipped. Run it once before the demo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import time
from typing import Any

from tqdm import tqdm

import config
from rag.chunking import Chunk, build_all_chunks
from rag.text import bm25_tokenize

MANIFEST = config.INDEX_DIR / "manifest.json"
CHROMA_ADD_BATCH = 2000


def fingerprint(chunks: list[Chunk]) -> str:
    """Stable hash of everything that would change the index contents."""
    h = hashlib.sha256(config.EMBED_MODEL.encode())
    for c in chunks:
        h.update(c.id.encode())
        h.update(c.text.encode())
        h.update(json.dumps(c.metadata, sort_keys=True, default=str).encode())
    return h.hexdigest()


def bm25_path(collection: str):
    return config.BM25_DIR / f"{collection}.pkl"


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def _chroma_client():
    import chromadb

    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def _is_current(client, name: str, fp: str, n: int, manifest: dict[str, Any]) -> bool:
    entry = manifest.get(name, {})
    if entry.get("fingerprint") != fp or not bm25_path(name).exists():
        return False
    try:
        return client.get_collection(name).count() == n
    except Exception:  # collection missing
        return False


def build_vector_collection(client, name: str, chunks: list[Chunk]) -> float:
    """(Re)create a Chroma collection with our own bge embeddings. Returns seconds taken."""
    from rag.embeddings import embed_passages, pick_device

    t0 = time.perf_counter()
    print(f"  device: {pick_device()}")
    try:
        client.delete_collection(name)
    except Exception:
        pass
    collection = client.create_collection(
        name,
        configuration={"hnsw": {"space": "cosine"}},
        embedding_function=None,  # we embed ourselves; stops Chroma loading its default model
        metadata={"embed_model": config.EMBED_MODEL},
    )
    print(f"  embedding {len(chunks):,} chunks with {config.EMBED_MODEL} ...")
    vectors = embed_passages([c.text for c in chunks], show_progress=True)
    for start in tqdm(range(0, len(chunks), CHROMA_ADD_BATCH), desc=f"  writing {name} to Chroma", unit="batch"):
        batch = chunks[start: start + CHROMA_ADD_BATCH]
        collection.add(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[c.metadata for c in batch],
            embeddings=vectors[start: start + CHROMA_ADD_BATCH],
        )
    return time.perf_counter() - t0


def build_bm25(name: str, chunks: list[Chunk]) -> float:
    """Tokenise chunk texts, build BM25Okapi and pickle it with ids/texts/metadata."""
    from rank_bm25 import BM25Okapi

    t0 = time.perf_counter()
    tokens = [bm25_tokenize(c.text) for c in tqdm(chunks, desc=f"  tokenising {name} for BM25", unit="chunk")]
    payload = {
        "ids": [c.id for c in chunks],
        "texts": [c.text for c in chunks],
        "metadatas": [c.metadata for c in chunks],
        "bm25": BM25Okapi(tokens),
    }
    config.BM25_DIR.mkdir(parents=True, exist_ok=True)
    with bm25_path(name).open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    return time.perf_counter() - t0


def main(force: bool = False) -> None:
    from rag.embeddings import get_tokenizer

    t_start = time.perf_counter()
    print("Building chunks ...")
    all_chunks = build_all_chunks(get_tokenizer())
    client = _chroma_client()
    manifest = _load_manifest()

    for name in config.COLLECTIONS:
        chunks = all_chunks[name]
        fp = fingerprint(chunks)
        if not force and _is_current(client, name, fp, len(chunks), manifest):
            print(f"[{name}] up to date ({len(chunks):,} chunks), skipping")
            continue
        print(f"[{name}] building {len(chunks):,} chunks")
        t_vec = build_vector_collection(client, name, chunks)
        t_bm25 = build_bm25(name, chunks)
        manifest[name] = {
            "fingerprint": fp, "count": len(chunks), "embed_model": config.EMBED_MODEL,
            "vector_seconds": round(t_vec, 1), "bm25_seconds": round(t_bm25, 1),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        MANIFEST.write_text(json.dumps(manifest, indent=2))  # save after each collection
        print(f"[{name}] done: vectors {t_vec:.1f}s, BM25 {t_bm25:.1f}s")

    print(f"\nTotal time: {time.perf_counter() - t_start:.1f}s. Index at {config.INDEX_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rebuild every collection")
    main(force=parser.parse_args().force)
