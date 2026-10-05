"""Reciprocal rank fusion and query parsing for hybrid search."""

import pytest

from sr_mcp.knowledge.search import fuse, keyword_query


def test_rank_fusion_rewards_agreement_between_the_two_lists() -> None:
    vector = ["a", "b", "c"]
    keyword = ["c", "d", "a"]
    fused = fuse([vector, keyword], k=60)

    assert [chunk for chunk, _ in fused][:2] == ["a", "c"]  # in both lists
    assert set(dict(fused)) == {"a", "b", "c", "d"}
    assert dict(fused)["a"] == pytest.approx(1 / 61 + 1 / 63)


def test_one_list_alone_keeps_its_order() -> None:
    assert [c for c, _ in fuse([["x", "y", "z"]])] == ["x", "y", "z"]
    assert fuse([[], []]) == []


def test_keyword_queries_match_any_word_and_drop_punctuation() -> None:
    assert keyword_query("Specialist never got it — PMS token?") == (
        "specialist | never | got | it | pms | token"
    )
    assert keyword_query("'); DROP TABLE x; --") == "drop | table | x"
    assert keyword_query("?!") == ""
