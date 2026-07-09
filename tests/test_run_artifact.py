"""Contract tests for the Run Artifact Module (load_run / load_study)."""

from __future__ import annotations

import json
from pathlib import Path

from silisocs.evaluations.run_artifact import load_run, load_study


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _manifest_run(tmp_path: Path) -> Path:
    run = tmp_path / "manifest_run"
    _write_jsonl(run / "social" / "action_events.jsonl", [{"source_user": "Alice"}])
    _write_jsonl(run / "probe_events.jsonl", [{"agent": "Alice"}])
    (run / "sim_metrics.json").write_text(
        json.dumps({"meta": {"scenario": "meta-scenario"}, "counters": {}}), encoding="utf-8"
    )
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "success",
                "scenario": "demo",
                "seed": 7,
                "num_agents": 3,
                "num_steps": 5,
                "llm_name": "gpt-4o-mini",
                "game_masters": [{"name": "social", "backend_type": "twitter_like"}],
                "llm_usage": {"totals": {"total_tokens": 42}, "estimated_cost_usd": 0.5},
                "health": {"agent_turn_failures": 1},
                "artifacts": {
                    "action_events": ["social/action_events.jsonl"],
                    "exposure_events": [],
                    "probe_events": ["probe_events.jsonl"],
                },
                "provenance": {"git_commit": "abc123"},
            }
        ),
        encoding="utf-8",
    )
    return run


def _legacy_run(tmp_path: Path) -> Path:
    run = tmp_path / "legacy_run"
    _write_jsonl(run / "action_events.jsonl", [{"source_user": "Bob"}, {"source_user": "Cara"}])
    (run / "sim_metrics.json").write_text(
        json.dumps(
            {
                "meta": {"scenario": "old", "seed": 2, "num_agents": 4, "num_steps": 6},
                "counters": {"backend_action_errors": 3},
            }
        ),
        encoding="utf-8",
    )
    return run


def test_load_run_manifest_backed(tmp_path: Path) -> None:
    artifact = load_run(_manifest_run(tmp_path))
    assert artifact.status == "success"
    assert artifact.scenario == "demo"  # manifest wins over sim_metrics meta
    assert (artifact.seed, artifact.num_agents, artifact.num_steps) == (7, 3, 5)
    assert artifact.game_masters == [{"name": "social", "backend_type": "twitter_like"}]
    assert artifact.llm_usage == {"totals": {"total_tokens": 42}, "estimated_cost_usd": 0.5}
    assert artifact.health["agent_turn_failures"] == 1
    assert artifact.provenance == {"git_commit": "abc123"}
    assert [row["source_user"] for row in artifact.iter_actions()] == ["Alice"]
    assert [row["agent"] for row in artifact.iter_probes()] == ["Alice"]
    assert list(artifact.iter_exposures()) == []


def test_load_run_legacy_falls_back_to_discovery(tmp_path: Path) -> None:
    artifact = load_run(_legacy_run(tmp_path))
    assert artifact.manifest is None and artifact.status is None
    assert artifact.scenario == "old"  # recovered from sim_metrics meta
    assert artifact.num_agents == 4
    assert artifact.health == {
        "agent_turn_failures": 0,
        "action_parse_failures": 0,
        "action_invalid_targets": 0,
        "backend_action_errors": 3,
    }
    assert len(list(artifact.iter_actions())) == 2


def test_load_run_stale_manifest_paths_fall_back_to_resolvers(tmp_path: Path) -> None:
    run = _manifest_run(tmp_path)
    # The manifest points at social/action_events.jsonl; move the log to the
    # flat layout to simulate a relocated/stale index.
    (run / "social" / "action_events.jsonl").rename(run / "action_events.jsonl")
    artifact = load_run(run)
    assert [row["source_user"] for row in artifact.iter_actions()] == ["Alice"]


def test_load_run_malformed_manifest_treated_as_legacy(tmp_path: Path) -> None:
    run = _legacy_run(tmp_path)
    (run / "run_manifest.json").write_text("{not-json", encoding="utf-8")
    artifact = load_run(run)
    assert artifact.manifest is None
    assert len(list(artifact.iter_actions())) == 2


def test_load_study_reads_plan_summary_and_provenance(tmp_path: Path) -> None:
    study = tmp_path / "study"
    generated = study / "generated"
    (generated / "organized").mkdir(parents=True)
    (generated / "plan.json").write_text(
        json.dumps({"schema_version": 3, "plan": [{"run_id": "r1"}]}), encoding="utf-8"
    )
    (generated / "repro_lock.json").write_text(
        json.dumps({"environment": {"git_commit": "abc"}, "records": []}), encoding="utf-8"
    )
    (generated / "organized" / "study_summary.yaml").write_text(
        "study_id: demo\nhypotheses: []\n", encoding="utf-8"
    )

    artifact = load_study(study)
    assert artifact.plan == {"schema_version": 3, "plan": [{"run_id": "r1"}]}
    assert artifact.summary == {"study_id": "demo", "hypotheses": []}
    assert artifact.provenance == {"git_commit": "abc"}

    empty = load_study(tmp_path / "nope")
    assert empty.plan is None and empty.summary is None and empty.provenance == {}
