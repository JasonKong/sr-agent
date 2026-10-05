"""The public-repository boundary (CLAUDE.md): no real organisation's names anywhere.

The denylist holds truncated sha256 hashes of lower-case words, so the names themselves never
appear in this public repository. To add a word locally:

    python3 -c "import hashlib; print(hashlib.sha256(b'word').hexdigest()[:16])"
"""

import hashlib
import os
import re
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pytest

from sr_simulator.config import SimConfig
from sr_simulator.generate import generate

DENYLIST = {
    "1f3f20bc677fc9c6",
    "2d0eadb2a28864da",
    "5e2542f560732cc1",
    "79e750deb43dce97",
    "923eb498db65ea26",
    "a7dcc03ccbbaea8c",
    "b6a818b89601fc4d",
    "db98eaded3252c9f",
    "dd90aca9c76c6e46",
    "e44297129df363a1",
    "e70b0e3f3651e65a",
}
REPO = Path(__file__).resolve().parents[3]
SCANNED = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".sql",
    ".toml",
    ".json",
    ".ts",
    ".tsx",
    ".js",
    ".html",
    ".css",  # the web console
}
SKIPPED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "var",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "playwright-report",
    "test-results",
}


def _hash(word: str) -> str:
    return hashlib.sha256(word.encode()).hexdigest()[:16]


def _words(text: str) -> Iterator[str]:
    """Each word, plus each pair of adjacent words joined (for names written as two words)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    yield from words
    yield from (a + b for a, b in pairwise(words))


def _denied(text: str) -> set[str]:
    return {w for w in _words(text) if _hash(w) in DENYLIST}


def _repo_files() -> Iterator[Path]:
    # Skipped directories are pruned during the walk: node_modules alone is huge.
    for directory, subdirectories, files in os.walk(REPO):
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIPPED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in SCANNED:
                yield path


def test_the_check_itself_works() -> None:
    assert _hash("x") not in DENYLIST
    assert len(DENYLIST) >= 10


@pytest.mark.parametrize("path", sorted(_repo_files()), ids=lambda p: str(p.relative_to(REPO)))
def test_repository_files_name_no_real_organisation(path: Path) -> None:
    assert _denied(path.read_text(errors="ignore")) == set()


def test_generated_text_names_no_real_organisation() -> None:
    world = generate(SimConfig(referrals=300))
    texts: list[str] = []
    for _, rows in world.tables():
        for row in rows:
            texts += [v for v in vars_of(row) if isinstance(v, str)]
    texts += [line.message for line in world.logs]
    assert _denied("\n".join(texts)) == set()


def vars_of(row: object) -> list[object]:
    return [getattr(row, name) for name in row.__slots__]  # type: ignore[attr-defined]
