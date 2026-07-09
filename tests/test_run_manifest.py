"""Tests for the self-describing per-run manifest (run_manifest.json)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from silisocs.runtime.execution.manifest import (
    MANIFEST_FILENAME,
    build_run_manifest,
    write_run_manifest,
)
from silisocs.runtime.provenance import environment_provenance


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "social").mkdir(parents=True)
    (run / "effective_config.yaml").write_text("x: 1\n", encoding="utf-8")
    (run / "sim_metrics.json").write_text("{}", encoding="utf-8")
    (run / "social" / "action_events.jsonl").write_text("", encoding="utf-8")
    (run / "probe_events.jsonl").write_text("", encoding="utf-8")
    (run / "checkpoints").mkdir()
    return run


def test_build_run_manifest_indexes_artifacts_and_layout(tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    manifest = build_run_manifest(
        output_dir=run,
        status="success",
        meta={
            "scenario": "demo",
            "seed": 1,
            "num_agents": 2,
            "num_steps": 3,
            "llm_name": "gpt-4o-mini",
            "llm_usage": {"totals": {"total_tokens": 7}},
        },
        counters={"agent_turn_failures": 2},
        game_masters=[SimpleNamespace(name="social", backend_type="twitter_like")],
    )

    assert manifest["schema_version"] == 1
    assert manifest["status"] == "success" and manifest["error"] is None
    assert manifest["scenario"] == "demo" and manifest["seed"] == 1
    assert manifest["game_masters"] == [{"name": "social", "backend_type": "twitter_like"}]
    assert manifest["llm_usage"] == {"totals": {"total_tokens": 7}}
    assert manifest["health"]["agent_turn_failures"] == 2
    assert manifest["health"]["backend_action_errors"] == 0

    artifacts = manifest["artifacts"]
    assert artifacts["effective_config"] == "effective_config.yaml"
    assert artifacts["sim_metrics"] == "sim_metrics.json"
    assert artifacts["prompts_and_responses"] is None  # absent -> null, not a guess
    assert artifacts["action_events"] == ["social/action_events.jsonl"]  # per-GM layout
    assert artifacts["probe_events"] == ["probe_events.jsonl"]
    assert artifacts["exposure_events"] == []
    assert artifacts["checkpoints"] == "checkpoints"

    provenance = manifest["provenance"]
    for key in ("git_commit", "git_dirty", "python_version", "silisocs_version"):
        assert key in provenance


def test_write_run_manifest_writes_json_and_never_raises(tmp_path: Path) -> None:
    path = write_run_manifest(output_dir=tmp_path, status="failed", error="boom")
    assert path == tmp_path / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "failed" and data["error"] == "boom"

    # A broken target (missing directory) degrades to None instead of raising.
    assert write_run_manifest(output_dir=tmp_path / "missing" / "deep", status="x") is None


def test_environment_provenance_degrades_outside_a_checkout(tmp_path: Path) -> None:
    provenance = environment_provenance(tmp_path)  # no git repo, no uv.lock
    assert provenance["uv_lock_sha256"] is None
    assert provenance["python_version"]
    assert "captured_at" in provenance
