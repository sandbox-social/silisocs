"""End-to-end ``silisocs-study run`` reporting: evaluator failures, plan.json, summary.

The "runner" here is a stub script (the study's ``execution.command``), so these
tests exercise the full study execution/organize/summary path — including real
evaluator subprocesses — without launching a simulation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from silisocs.studies.cli import build_parser, cmd_run

pytestmark = pytest.mark.subprocess

# Stub runner: creates the run directory the study planned, drops the events an
# evaluator reads, and prints the line the study runner sniffs for.
_STUB_RUNNER = """
import json, sys
from pathlib import Path

run_dir = Path(sys.argv[1])
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "effective_config.yaml").write_text("num_steps: 1\\n", encoding="utf-8")
posts = int(sys.argv[2])
rows = []
for index in range(posts):
    rows.append(
        {
            "source_user": "Alice",
            "label": "post",
            "data": {},
            "episode": 1,
            "event_type": "action",
            "event_index": index,
        }
    )
rows.append(
    {
        "source_user": "Bob",
        "label": "like",
        "data": {},
        "episode": 1,
        "event_type": "action",
        "event_index": posts,
    }
)
with (run_dir / "action_events.jsonl").open("w", encoding="utf-8") as f:
    for row in rows:
        f.write(json.dumps(row) + "\\n")
print(f"Output directory: {run_dir}")
"""

_FAILING_EVALUATOR = """
import sys
print("evaluator exploded", file=sys.stderr)
sys.exit(1)
"""


def _write_stub_runner(tmp_path: Path) -> Path:
    path = tmp_path / "stub_runner.py"
    path.write_text(_STUB_RUNNER, encoding="utf-8")
    return path


def _run_args(study_path: Path, repo_root: Path) -> Any:
    return build_parser().parse_args(
        [
            "--study",
            str(study_path),
            "--repo-root",
            str(repo_root),
            "run",
            "--yes",
        ]
    )


def _write_study(
    study_dir: Path,
    *,
    runner: Path,
    evaluations: list[dict[str, Any]],
    conditions: dict[str, Any],
    seeds: list[int],
) -> Path:
    study_dir.mkdir(parents=True, exist_ok=True)
    study = {
        "schema_version": 1,
        "study": {
            "name": "stub_study",
            "study_id": "stub_study",
            "run_defaults": {
                "scenario": "default",
                "seeds": seeds,
                "checkpoint_every_n_steps": None,
            },
        },
        "evaluations": evaluations,
        "hypotheses": {
            "h1": {
                "conditions": {
                    cond_id: {
                        "execution": {
                            "command": [
                                sys.executable,
                                str(runner),
                                "{run_id}_dir",
                                str(posts),
                            ]
                        },
                        **extra,
                    }
                    for cond_id, (posts, extra) in conditions.items()
                }
            }
        },
    }
    path = study_dir / "study.yaml"
    path.write_text(yaml.safe_dump(study, sort_keys=False), encoding="utf-8")
    return path


def test_failed_evaluators_are_reported_and_fail_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    boom = tmp_path / "boom.py"
    boom.write_text(_FAILING_EVALUATOR, encoding="utf-8")
    study_path = _write_study(
        repo_root / "study",
        runner=_write_stub_runner(tmp_path),
        evaluations=[{"id": "boom", "command": [sys.executable, str(boom)]}],
        conditions={"c1": (1, {})},
        seeds=[1],
    )

    rc = cmd_run(_run_args(study_path, repo_root))
    captured = capsys.readouterr()

    assert rc == 1, "a study whose evaluators all crash must not exit 0"
    assert "Evaluators failed: 1/1" in captured.err
    assert "Evaluators run: 0/1 succeeded" in captured.out
    # Run failures stay reported separately from evaluator failures.
    assert "Failed/timeout: 0" in captured.out
    assert "Success/reused: 1" in captured.out

    lock = json.loads(
        (
            repo_root / "experiments" / "studies" / "stub_study" / "generated" / "repro_lock.json"
        ).read_text(encoding="utf-8")
    )
    assert lock["records"][0]["evaluations"][0]["status"] == "failed"


def test_run_writes_plan_json_without_dry_run(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    study_path = _write_study(
        repo_root / "study",
        runner=_write_stub_runner(tmp_path),
        evaluations=[],
        conditions={"c1": (1, {})},
        seeds=[1],
    )

    rc = cmd_run(_run_args(study_path, repo_root))

    plan_json = repo_root / "experiments" / "studies" / "stub_study" / "generated" / "plan.json"
    assert rc == 0
    assert plan_json.is_file()
    payload = json.loads(plan_json.read_text(encoding="utf-8"))
    assert [row["run_id"] for row in payload["plan"]] == ["h1__c1__default__seed1"]


def test_builtin_preset_only_study_produces_comparable_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A study using ONLY builtin presets must reach summary.json.

    No builtin evaluator used to emit ``aggregated``, so summary.json came out
    empty — indistinguishable from a study where every run failed.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    study_path = _write_study(
        repo_root / "study",
        runner=_write_stub_runner(tmp_path),
        evaluations=[{"id": "actions", "preset": "builtin.action_metrics_detailed"}],
        conditions={"low": (1, {}), "high": (3, {})},
        seeds=[1, 2],
    )

    rc = cmd_run(_run_args(study_path, repo_root))
    captured = capsys.readouterr()

    assert rc == 0, captured.out + captured.err
    assert "Evaluators run: 4/4 succeeded" in captured.out

    summary = json.loads(
        (
            repo_root
            / "experiments"
            / "studies"
            / "stub_study"
            / "generated"
            / "organized"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )

    stats = summary["metrics_stats_by_condition"]["h1"]
    assert stats, "builtin-preset-only study produced no comparable metrics"
    assert stats["low"]["actions_post"]["mean"] == 1.0
    assert stats["high"]["actions_post"]["mean"] == 3.0
    assert stats["low"]["actions_post"]["n"] == 2
    assert summary["metrics_by_condition"]["h1"]["high"]["total_action_events"] == 4.0

    # Replicate entries are distinguishable (they used to be byte-identical).
    seeds = sorted(entry["seed"] for entry in summary["conditions"] if entry["condition"] == "low")
    assert seeds == [1, 2]
    assert all(entry["run_id"] for entry in summary["conditions"])
