"""Embedders: the hashing embedder used offline and in tests, and the registry."""

import math

import pytest

from sr_mcp.knowledge.embedder import EMBEDDERS, HashEmbedder, make_embedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hash_vectors_are_deterministic_and_unit_length() -> None:
    embedder = HashEmbedder()
    [a] = embedder.embed_documents(["Clinic PMS token expired"])
    [b] = HashEmbedder().embed_documents(["Clinic PMS token expired"])

    assert a == b
    assert len(a) == embedder.dim == 384
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)


def test_texts_that_share_words_are_closer() -> None:
    embedder = HashEmbedder()
    query = embedder.embed_query("PMS token expired")
    near, far = embedder.embed_documents(
        ["The clinic PMS token expired at noon", "The PDF was too large to generate"]
    )
    assert cosine(query, near) > cosine(query, far)


def test_every_registered_embedder_has_the_schema_dimension() -> None:
    assert set(EMBEDDERS) >= {"bge-small", "minilm", "hash"}
    for name in EMBEDDERS:
        embedder = make_embedder(name)  # local models load lazily, on first use
        assert embedder.dim == 384
        assert embedder.name.startswith(name.split("-")[0])


def test_an_unknown_embedder_is_refused() -> None:
    with pytest.raises(ValueError, match="voyage"):
        make_embedder("voyage")
