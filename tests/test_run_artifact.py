"""Contract tests for the Run Artifact Module (load_run / load_study)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _manifestless_run(tmp_path: Path) -> Path:
    run = tmp_path / "manifestless_run"
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


def test_load_run_requires_manifest(tmp_path: Path) -> None:
    run = _manifestless_run(tmp_path)
    with pytest.raises(FileNotFoundError, match="Run manifest not found"):
        load_run(run)


def test_load_run_rejects_stale_manifest_paths(tmp_path: Path) -> None:
    run = _manifest_run(tmp_path)
    # The manifest points at social/action_events.jsonl; move the log to the
    # flat layout to simulate a relocated/stale index.
    (run / "social" / "action_events.jsonl").rename(run / "action_events.jsonl")
    artifact = load_run(run)
    with pytest.raises(FileNotFoundError, match="missing action_events artifact"):
        list(artifact.iter_actions())


def test_running_manifest_discovers_event_files_live(tmp_path: Path) -> None:
    """A provisional (status "running") manifest must not freeze the artifact list.

    The session writes it at launch, before any event log exists; logs that
    appear later — flat or per-GM — must be visible to every load without a
    manifest rewrite. The final manifest overwrite restores strict indexing.
    """
    run = tmp_path / "live_run"
    run.mkdir()
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "running",
                "scenario": "demo",
                "game_masters": [
                    {"name": "talk_gm", "backend_type": "messaging"},
                    {"name": "game_gm", "backend_type": "public_goods"},
                ],
                "artifacts": {"action_events": [], "probe_events": []},  # launch-time snapshot
            }
        ),
        encoding="utf-8",
    )
    assert list(load_run(run).iter_actions()) == []  # nothing written yet

    # Logs appear as the run executes — discovered live, stale index ignored.
    _write_jsonl(run / "talk_gm" / "action_events.jsonl", [{"source_user": "Alice"}])
    _write_jsonl(run / "game_gm" / "action_events.jsonl", [{"source_user": "Bob"}])
    artifact = load_run(run)
    assert sorted(row["source_user"] for row in artifact.iter_actions()) == ["Alice", "Bob"]
    assert list(artifact.iter_probes()) == []


def test_load_run_rejects_malformed_manifest(tmp_path: Path) -> None:
    run = _manifestless_run(tmp_path)
    (run / "run_manifest.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON object"):
        load_run(run)


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


def test_health_surfaces_silent_backends_as_count(tmp_path: Path) -> None:
    run = tmp_path / "silent_run"
    run.mkdir()
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "health": {"agent_turn_failures": 0, "silent_backends": ["quiet", "hushed"]},
                "artifacts": {},
            }
        ),
        encoding="utf-8",
    )
    health = load_run(run).health
    assert health["silent_backends"] == 2  # count, so zero=green rendering stays honest
    assert health["agent_turn_failures"] == 0


def _write_study(study: Path, definition: dict) -> None:
    study.mkdir(parents=True, exist_ok=True)
    import yaml

    (study / "study.yaml").write_text(yaml.safe_dump(definition), encoding="utf-8")


def _mark_complete(study: Path, hyp: str, cond: str, scenario: str, seed: int) -> None:
    run = study / "runs" / hyp / cond / scenario / f"seed_{seed}" / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "RUN_COMPLETE.json").write_text("{}", encoding="utf-8")


def test_progress_honors_explicit_run_default_seeds(tmp_path: Path) -> None:
    # run_defaults.seeds (not seed_start/seed_repeats) is the seed source the
    # runner enumerates; progress must project the same seed_3/seed_7 matrix.
    study = tmp_path / "study"
    _write_study(
        study,
        {
            "study": {
                "name": "demo",
                "scenarios": ["default"],
                "run_defaults": {"seeds": [3, 7]},
            },
            "hypotheses": {"h1": {"conditions": {"baseline": {"overrides": {}}}}},
        },
    )
    _mark_complete(study, "h1", "baseline", "default", 3)
    _mark_complete(study, "h1", "baseline", "default", 7)

    rows = load_study(study).progress
    by_seed = {row["seed"]: row["status"] for row in rows}
    assert by_seed == {3: "complete", 7: "complete"}  # not falsely "pending"


def test_progress_honors_condition_seed_and_base_scenarios(tmp_path: Path) -> None:
    study = tmp_path / "study"
    _write_study(
        study,
        {
            "study": {"name": "demo", "base_scenarios": ["alpha"], "run_defaults": {}},
            "hypotheses": {"h1": {"conditions": {"c1": {"seed": 5}, "c2": {"seeds": [1, 2]}}}},
        },
    )
    _mark_complete(study, "h1", "c1", "alpha", 5)

    rows = load_study(study).progress
    keyed = {(r["condition"], r["scenario"], r["seed"]): r["status"] for r in rows}
    assert keyed[("c1", "alpha", 5)] == "complete"
    assert keyed[("c2", "alpha", 1)] == "pending"
    assert keyed[("c2", "alpha", 2)] == "pending"


def test_progress_marks_reuse_and_output_override_not_pending(tmp_path: Path) -> None:
    study = tmp_path / "study"
    _write_study(
        study,
        {
            "study": {
                "name": "demo",
                "scenarios": ["default"],
                "run_defaults": {"seed": 1, "output_root_override": "custom/{seed}"},
            },
            "hypotheses": {
                "h1": {
                    "conditions": {
                        "reuse_c": {
                            "execution": {"mode": "reuse_existing"},
                            "reuse": {
                                "runs": [{"source": "/prior/run", "scenario": "s", "seed": 9}]
                            },
                        },
                        "run_c": {"overrides": {}},
                    }
                }
            },
        },
    )
    rows = load_study(study).progress
    statuses = {r["condition"]: r["status"] for r in rows}
    assert statuses["reuse_c"] == "reused"
    assert statuses["run_c"] == "skipped"  # output_root_override path not modeled
    assert "pending" not in set(statuses.values())
