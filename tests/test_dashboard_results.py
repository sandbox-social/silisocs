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

    assert result == {"actions": [], "probes": [], "exposures": [], "metrics": None}
    assert health_counter_summary(result["metrics"]) is None


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_load_run_results_merges_per_gm_multi_gm_logs(tmp_path: Path) -> None:
    """Multi-GM runs keep per-GM logs under <run>/<gm>/; loading must merge them."""
    run_dir = tmp_path / "outputs" / "run"
    _write_jsonl(run_dir / "social" / "action_events.jsonl", [{"source_user": "Alice"}])
    _write_jsonl(run_dir / "world" / "action_events.jsonl", [{"source_user": "Bob"}])
    _write_jsonl(run_dir / "social" / "exposure_events.jsonl", [{"agent": "Alice", "posts": []}])
    # Probe logs are engine-level and stay flat at the run root.
    _write_jsonl(run_dir / "probe_events.jsonl", [{"agent": "Alice"}])
    (run_dir / "sim_metrics.json").write_text("{}", encoding="utf-8")

    result = load_run_results(run_dir)

    assert {row["source_user"] for row in result["actions"]} == {"Alice", "Bob"}
    assert len(result["exposures"]) == 1
    assert len(result["probes"]) == 1


def test_discover_result_run_dirs_lists_multi_gm_run_once(tmp_path: Path) -> None:
    """A multi-GM run is one run (its root), not one entry per GM subdirectory."""
    run_dir = tmp_path / "outputs" / "run"
    _write_jsonl(run_dir / "social" / "action_events.jsonl", [{"source_user": "Alice"}])
    _write_jsonl(run_dir / "world" / "action_events.jsonl", [{"source_user": "Bob"}])
    (run_dir / "sim_metrics.json").write_text("{}", encoding="utf-8")

    assert discover_result_run_dirs(root=tmp_path) == [run_dir]


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
