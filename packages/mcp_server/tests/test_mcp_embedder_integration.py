"""The real local embedders (downloaded once to var/models; needs network the first time)."""

import pytest

from sr_mcp.knowledge.embedder import make_embedder
from sr_mcp.paths import project_root

pytestmark = pytest.mark.integration

RUNBOOKS = [
    "Clinic PMS authorisation failed. The token expired; pushes fail before any attempt.",
    "PDF too large. Attached documents push the referral past the 15 MB limit.",
    "Routed for manual delivery. The clinic has no active account.",
]


@pytest.mark.parametrize("name", ["bge-small", "minilm"])
def test_a_local_model_finds_the_matching_runbook(name: str) -> None:
    embedder = make_embedder(name, cache_dir=project_root() / "var" / "models")
    documents = embedder.embed_documents(RUNBOOKS)
    query = embedder.embed_query("the clinic's system rejected our login token")

    assert all(len(v) == embedder.dim == 384 for v in documents)
    scores = [sum(q * d for q, d in zip(query, doc, strict=True)) for doc in documents]
    assert scores.index(max(scores)) == 0
