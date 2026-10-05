"""`sr-evals run`: case selection, the seeded-world check, and the files it writes."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import Client

from sr_agent.toolbox import McpToolbox, open_toolbox
from sr_evals.cli import SMOKE, main, select_cases
from sr_evals.dataset import DATASET_V1, DATASETS, load_dataset
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


def test_the_model_settings_and_cost_cap_are_recorded(labels: Path, tmp_path: Path) -> None:
    argv = ["run", "--dry-run", "--cases", "F07-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(tmp_path / "r"), "--runs-dir", str(tmp_path / "runs")]
    argv += ["--max-cost-usd", "2.5"]
    assert main(argv, connect=in_process) == 0

    [path] = list((tmp_path / "r").glob("*.json"))
    flags = EvalReport.model_validate_json(path.read_text()).flags
    assert flags["max_cost_usd"] == 2.5
    assert flags["fallback"] is False  # the oracle is no model
    assert flags["thinking"] == "n/a"


@asynccontextmanager
async def must_not_connect(url: str, token: str) -> AsyncIterator[McpToolbox]:
    raise AssertionError("connected before checking the model settings")
    yield  # pragma: no cover


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--model", "claude-haiku-4-5", "--effort", "low"], "has no effort levels"),
        (["--model", "claude-typo"], "no request profile"),
    ],
)
def test_bad_model_settings_stop_before_connecting(
    extra: list[str],
    message: str,
    labels: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    code = main(["run", "--labels", str(labels), *extra], connect=must_not_connect)

    assert code == 2
    assert message in capsys.readouterr().err


def test_the_cost_cap_reaches_the_runner(labels: Path, tmp_path: Path) -> None:
    # A cap of $0 is reached before the first case, so nothing runs.
    argv = ["run", "--dry-run", "--cases", "F07-1,F06-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(tmp_path / "r"), "--runs-dir", str(tmp_path / "runs")]
    assert main([*argv, "--max-cost-usd", "0"], connect=in_process) == 0

    [path] = list((tmp_path / "r").glob("*.json"))
    report = EvalReport.model_validate_json(path.read_text())
    assert report.metrics.errors == 2
    assert {s.error for s in report.scores} == {"not run: the cost cap of $0.00 was reached"}


def test_an_unreadable_earlier_report_does_not_lose_the_run(labels: Path, tmp_path: Path) -> None:
    reports, runs = tmp_path / "r", tmp_path / "runs"
    reports.mkdir()
    (reports / "20200101-000000-oracle.json").write_text("{}")  # an old, unreadable format
    argv = ["run", "--dry-run", "--cases", "F07-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(reports), "--runs-dir", str(runs)]
    assert main(argv, connect=in_process) == 0

    [path] = [p for p in reports.glob("*.json") if not p.name.startswith("2020")]
    assert EvalReport.model_validate_json(path.read_text()).comparison is None
    assert (runs / path.stem / "F07-1.json").exists()


def test_the_runs_are_saved_before_anything_that_could_fail(
    labels: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the report step failed")

    monkeypatch.setattr("sr_evals.cli.find_previous", broken)
    argv = ["run", "--dry-run", "--cases", "F07-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(tmp_path / "r"), "--runs-dir", str(tmp_path / "runs")]
    with pytest.raises(RuntimeError):
        main(argv, connect=in_process)

    assert [p.name for p in (tmp_path / "runs").glob("*/*.json")] == ["F07-1.json"]


def dry_run(labels: Path, tmp_path: Path, label: str, *extra: str) -> EvalReport:
    reports = tmp_path / "reports"
    argv = ["run", "--dry-run", "--cases", "F07-1,F06-1", "--labels", str(labels)]
    argv += ["--reports-dir", str(reports), "--runs-dir", str(tmp_path / "runs")]
    assert main([*argv, "--label", label, *extra], connect=in_process) == 0
    [path] = sorted(reports.glob(f"*-{label}.json"))
    return EvalReport.model_validate_json(path.read_text())


def test_compare_to_names_the_base_run(labels: Path, tmp_path: Path) -> None:
    a = dry_run(labels, tmp_path, "a")
    dry_run(labels, tmp_path, "b")
    c = dry_run(labels, tmp_path, "c", "--compare-to", a.report_id)

    assert c.comparison is not None
    assert c.comparison.previous_id == a.report_id  # not b, the latest
    assert c.comparison.verdict == "within noise"


def test_compare_to_an_unknown_run_is_refused(
    labels: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["run", "--dry-run", "--labels", str(labels), "--compare-to", "20200101-nope"]
    argv += ["--reports-dir", str(tmp_path / "r"), "--runs-dir", str(tmp_path / "runs")]
    assert main(argv, connect=in_process) == 2
    assert "20200101-nope" in capsys.readouterr().err


def test_compare_prints_both_runs_and_the_verdict(
    labels: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = dry_run(labels, tmp_path, "a")
    b = dry_run(labels, tmp_path, "b")
    capsys.readouterr()
    reports = str(tmp_path / "reports")
    assert main(["compare", a.report_id, b.report_id, "--reports-dir", reports]) == 0

    out = capsys.readouterr().out
    assert a.report_id in out
    assert b.report_id in out
    assert "| Root-cause accuracy | 100.0% | 100.0% |" in out
    assert "Verdict: **within noise** (0 fixed, 0 broke; sign test p = 1.000)" in out


def test_rescore_scores_the_stored_runs_again(labels: Path, tmp_path: Path) -> None:
    a = dry_run(labels, tmp_path, "a")
    # Change one stored answer, as a scorer change would change one score.
    stored = tmp_path / "runs" / a.report_id / "F06-1.json"
    stored.write_text(
        stored.read_text().replace(
            '"root_cause_code": "F06_MANUAL_ROUTING"',
            '"root_cause_code": "F05_ATTACHMENT_FETCH_FAILED"',
        )
    )
    argv = ["rescore", a.report_id]  # no database: the dataset regenerates its world
    argv += ["--reports-dir", str(tmp_path / "reports"), "--runs-dir", str(tmp_path / "runs")]
    assert main(argv) == 0

    rescored = EvalReport.model_validate_json(
        (tmp_path / "reports" / f"{a.report_id}-rescored.json").read_text()
    )
    assert rescored.flags["rescored_from"] == a.report_id
    assert (rescored.model, rescored.git_commit) == (a.model, a.git_commit)
    assert rescored.metrics.accuracy == pytest.approx(0.5)
    assert rescored.comparison is not None
    assert rescored.comparison.previous_id == a.report_id
    assert rescored.comparison.broke == ["F06-1"]


def test_build_writes_the_holdout_set_from_its_own_seed(tmp_path: Path) -> None:
    out = tmp_path / "holdout.json"
    assert main(["build", "--set", "holdout_v1", "--out", str(out)]) == 0

    dataset = load_dataset(out)
    assert dataset.version == "holdout_v1"
    assert dataset.world["seed"] == 43
    assert len(dataset.cases) == 75


def test_a_dataset_can_be_named_instead_of_given_as_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCP_HTTP_TOKEN", "t" * 40)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"config": load_dataset(DATASETS["holdout_v1"]).world}))
    argv = ["run", "--dry-run", "--dataset", "holdout_v1", "--cases", "F07-1"]
    argv += ["--labels", str(labels), "--reports-dir", str(tmp_path / "r")]
    argv += ["--runs-dir", str(tmp_path / "runs")]
    assert main(argv, connect=in_process) == 0

    [path] = list((tmp_path / "r").glob("*.json"))
    assert EvalReport.model_validate_json(path.read_text()).dataset_version == "holdout_v1"
