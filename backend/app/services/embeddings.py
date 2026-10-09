"""Local text embeddings for Q&A retrieval (planning Q14: fastembed, no PyTorch).

The model, BAAI/bge-small-en-v1.5 (about 65 MB, 384 dimensions), is downloaded
once into data/models/ on first use and runs on the CPU, so no paper text leaves
the machine to be embedded. Vectors are unit length, so cosine similarity is a
dot product. A chunk's vector is stored on its row as float32 bytes.

Tests replace `get_embedder()` with a fake; nothing here may download a model in tests.
"""

import logging
import threading
from typing import Protocol

import numpy as np

from app.config import settings
from app.models import Chunk

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
# BGE's instruction for short queries that look for passages; passages get none.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingError(Exception):
    """The embedding model couldn't be loaded or run; the message is safe to show the user."""


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Unit-length float32 vectors, one row per text."""
        ...


class FastEmbedder:
    dim = 384

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # slow import; only when first needed

                settings.model_dir.mkdir(parents=True, exist_ok=True)
                try:
                    self._model = TextEmbedding(MODEL_NAME, cache_dir=str(settings.model_dir))
                except Exception as exc:
                    log.exception("Loading the embedding model failed")
                    raise EmbeddingError(
                        "The local embedding model couldn't be loaded. It is downloaded once "
                        "(about 65 MB) on first use, so check your internet connection and try again."
                    ) from exc
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        try:
            return np.asarray(list(model.embed(texts, batch_size=32)), dtype=np.float32)
        except Exception as exc:
            log.exception("Embedding %d texts failed", len(texts))
            raise EmbeddingError("The local embedding model failed while reading the paper.") from exc


_embedder = FastEmbedder()


def get_embedder() -> Embedder:
    return _embedder


def passage_text(chunk: Chunk) -> str:
    # The section name helps questions like "what are the limitations?" find their section.
    return f"{chunk.section}\n{chunk.text}"


def embed_query(text: str) -> np.ndarray:
    return get_embedder().embed([QUERY_PREFIX + text])[0]


def to_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def has_embedding(chunk: Chunk, dim: int) -> bool:
    return chunk.embedding is not None and len(chunk.embedding) == dim * 4


def embed_chunks(chunks: list[Chunk]) -> int:
    """Store a vector on every chunk that lacks a current one. Returns how many were embedded."""
    embedder = get_embedder()
    todo = [c for c in chunks if not has_embedding(c, embedder.dim)]
    if todo:
        vectors = embedder.embed([passage_text(c) for c in todo])
        for chunk, vector in zip(todo, vectors):
            chunk.embedding = to_bytes(vector)
    return len(todo)
