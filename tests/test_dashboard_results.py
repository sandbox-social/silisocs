from __future__ import annotations

import json
import os
from pathlib import Path

from silisocs.dashboard.results import (
    discover_result_run_dirs,
    health_counter_summary,
    load_run_results,
    total_health_issues,
)


def test_load_run_results_accepts_missing_probe_log_and_metrics(tmp_path: Path) -> None:
    run_dir = tmp_path / "outputs" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "action_events.jsonl").write_text(
        json.dumps(
            {
                "event_type": "action",
                "episode": 0,
                "source_user": "Alice",
                "label": "post",
                "data": {"content": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "sim_metrics.json").write_text(
        json.dumps({"counters": {"agent_turn_failures": 1, "backend_action_errors": 2}}),
        encoding="utf-8",
    )

    result = load_run_results(run_dir)
    summary = health_counter_summary(result["metrics"])

    assert len(result["actions"]) == 1
    assert result["probes"] == []
    assert summary is not None
    assert summary["agent_turn_failures"] == 1
    assert summary["backend_action_errors"] == 2
    assert total_health_issues(summary) == 3


def test_load_run_results_tolerates_malformed_metrics_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "outputs" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "sim_metrics.json").write_text("{not-json", encoding="utf-8")

    result = load_run_results(run_dir)

    assert result == {"actions": [], "probes": [], "metrics": None}
    assert health_counter_summary(result["metrics"]) is None


def test_discover_result_run_dirs_sorts_newest_first(tmp_path: Path) -> None:
    older = tmp_path / "outputs" / "older"
    newer = tmp_path / "outputs" / "newer"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "sim_metrics.json").write_text("{}", encoding="utf-8")
    (newer / "action_events.jsonl").write_text("", encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    discovered = discover_result_run_dirs(root=tmp_path)

    assert discovered[:2] == [newer, older]
