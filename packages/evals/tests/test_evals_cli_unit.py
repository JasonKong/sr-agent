"""`sr-evals run`: case selection, the seeded-world check, and the files it writes."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import Client

from sr_agent.toolbox import McpToolbox, open_toolbox
from sr_evals.cli import SMOKE, main, select_cases
from sr_evals.dataset import DATASET_V1, load_dataset
from sr_evals.report import EvalReport
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps


@asynccontextmanager
async def in_process(url: str, token: str) -> AsyncIterator[McpToolbox]:
    async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
        yield toolbox


@pytest.fixture
def labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCP_HTTP_TOKEN", "t" * 40)
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"config": load_dataset(DATASET_V1).world}))
    return path


def test_case_sets_are_all_smoke_or_a_list() -> None:
    dataset = load_dataset(DATASET_V1)
    assert len(select_cases(dataset, "all")) == 75
    smoke = select_cases(dataset, "smoke")
    assert [c.case_id for c in smoke] == SMOKE
    assert len({c.code for c in smoke}) == 12
    assert [c.case_id for c in select_cases(dataset, "F07-2,D-in_progress-1")] == [
        "F07-2",
        "D-in_progress-1",
    ]
    with pytest.raises(ValueError, match="F99-1"):
        select_cases(dataset, "F07-2,F99-1")


def test_a_dry_run_writes_the_report_and_every_run(
    labels: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reports, runs = tmp_path / "reports", tmp_path / "runs"
    argv = ["run", "--dry-run", "--cases", "F07-1,F06-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(reports), "--runs-dir", str(runs), "--label", "unit"]
    code = main(argv, connect=in_process)

    out, err = capsys.readouterr()
    assert code == 0
    [json_path] = list(reports.glob("*.json"))
    assert json_path.stem.endswith("-oracle-unit")
    report = EvalReport.model_validate_json(json_path.read_text())
    assert [s.case_id for s in report.scores] == ["F07-1", "F06-1"]
    assert report.case_set == "F07-1,F06-1"
    assert report.flags["dry_run"] is True
    assert (reports / f"{json_path.stem}.md").exists()
    assert sorted(p.name for p in (runs / json_path.stem).iterdir()) == ["F06-1.json", "F07-1.json"]
    assert "F07-1" in err
    assert "F06-1" in err
    assert str(reports / f"{json_path.stem}.md") in out


def test_a_second_run_is_compared_with_the_first(labels: Path, tmp_path: Path) -> None:
    argv = ["run", "--dry-run", "--cases", "F07-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(tmp_path / "reports"), "--runs-dir", str(tmp_path / "runs")]
    assert main([*argv, "--label", "a"], connect=in_process) == 0
    assert main([*argv, "--label", "b"], connect=in_process) == 0

    second = sorted((tmp_path / "reports").glob("*-b.json"))[-1]
    report = EvalReport.model_validate_json(second.read_text())
    assert report.comparison is not None
    assert report.comparison.previous_id.endswith("-a")


def test_a_world_other_than_the_datasets_is_refused(
    labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels.write_text(json.dumps({"config": {"seed": 1}}))
    code = main(["run", "--dry-run", "--labels", str(labels)], connect=in_process)

    assert code == 2
    assert "make seed" in capsys.readouterr().err


def test_missing_labels_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--dry-run", "--labels", str(tmp_path / "none.json")], connect=in_process)
    assert code == 2
    assert "make seed" in capsys.readouterr().err


def test_the_prompt_and_tool_descriptions_are_recorded(labels: Path, tmp_path: Path) -> None:
    argv = ["run", "--dry-run", "--cases", "F07-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(tmp_path / "r"), "--runs-dir", str(tmp_path / "runs")]
    argv += ["--prompt", "v2", "--tool-descriptions", "v2"]
    assert main(argv, connect=in_process) == 0

    [path] = list((tmp_path / "r").glob("*.json"))
    report = EvalReport.model_validate_json(path.read_text())
    assert report.prompt_version == "v2"
    assert report.flags["tool_descriptions"] == "v2"
    [run] = list((tmp_path / "runs").glob("*/F07-1.json"))
    assert '"prompt_version": "v2"' in run.read_text()
