"""Embedders behind one interface (DESIGN.md section 7.3).

- `bge-small` (the default) and `minilm` are local open models run with fastembed, offline
  after a one-time download to `var/models/`. Both give 384 numbers per text, the size of
  `knowledge_chunk.embedding`.
- `hash` is a deterministic hashing embedder: words and word pairs hashed into 384
  buckets. It needs no model, so unit and integration tests use it. It is lexical only,
  so it is never used for real retrieval.

Models load lazily, on the first text to embed, so building an embedder is cheap.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

DIM = 384


class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


_WORD = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    def __init__(self) -> None:
        self.name = "hash-384"
        self.dim = DIM

    def _vector(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        vector = [0.0] * self.dim
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            vector[bucket] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FastEmbedder:
    """A local model through fastembed; BGE models add their query prefix themselves."""

    def __init__(self, name: str, model_id: str, *, cache_dir: Path | None = None) -> None:
        self.name = name
        self.dim = DIM
        self.model_id = model_id
        self._cache_dir = cache_dir
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding  # imported here: loading onnxruntime is slow

            cache = str(self._cache_dir) if self._cache_dir else None
            self._model = TextEmbedding(self.model_id, cache_dir=cache)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._load().passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        [vector] = list(self._load().query_embed(text))
        return [float(x) for x in vector]


EMBEDDERS: dict[str, Callable[[Path | None], Embedder]] = {
    "bge-small": lambda cache: FastEmbedder(
        "bge-small-en-v1.5", "BAAI/bge-small-en-v1.5", cache_dir=cache
    ),
    "minilm": lambda cache: FastEmbedder(
        "minilm-l6-v2", "sentence-transformers/all-MiniLM-L6-v2", cache_dir=cache
    ),
    "hash": lambda cache: HashEmbedder(),
}


def make_embedder(name: str, *, cache_dir: Path | None = None) -> Embedder:
    if name not in EMBEDDERS:
        raise ValueError(f"unknown embedder {name!r}; choose one of {', '.join(EMBEDDERS)}")
    return EMBEDDERS[name](cache_dir)
