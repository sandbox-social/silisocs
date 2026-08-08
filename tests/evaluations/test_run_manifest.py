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
    assert manifest["game_masters"] == [
        {
            "name": "social",
            "backend_type": "twitter_like",
            "backend_class_path": None,
            "database": None,
            "visualizer": None,
            "event_semantics": None,
        }
    ]
    assert manifest["llm_usage"] == {"totals": {"total_tokens": 7}}
    assert manifest["health"]["agent_turn_failures"] == 2
    assert manifest["health"]["backend_action_errors"] == 0
    assert manifest["health"]["silent_backends"] == []  # gm carries no committed-events mirror

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


def test_manifest_carries_portable_custom_backend_capabilities(tmp_path: Path) -> None:
    class CustomBackend:
        db_path = str(tmp_path / "run" / "state.sqlite")
        visualizer = None
        event_semantics = {
            "roles": {"world.transition": {"move"}},
            "fields": {"world.object": ("payload.object",)},
            "labels": {"move": ("world.transition",)},
        }

    manifest = build_run_manifest(
        output_dir=_run_dir(tmp_path),
        status="success",
        game_masters=[
            SimpleNamespace(name="world", backend_type="custom_world", backend=CustomBackend())
        ],
    )

    record = manifest["game_masters"][0]
    assert record["backend_type"] == "custom_world"
    assert record["event_semantics"] == {
        "roles": {"world.transition": ["move"]},
        "fields": {"world.object": ["payload.object"]},
        "labels": {"move": ["world.transition"]},
    }


def test_manifest_derived_tags_survive_to_a_fresh_analysis_process(tmp_path: Path) -> None:
    from silisocs.analysis.panels._shared import semantics_for_backend
    from silisocs.environments.backends.base import BackendApp, app_action

    class CustomBackend(BackendApp):
        db_path = None
        visualizer = None

        def name(self) -> str:
            return "custom"

        def description(self) -> str:
            return "custom"

        @app_action(
            tags=("world.broadcast", "content.created"),
            fields={"content.text": "message"},
        )
        def broadcast(self, agent_name: str, message: str) -> str:
            return message

    manifest = build_run_manifest(
        output_dir=_run_dir(tmp_path),
        status="success",
        game_masters=[
            SimpleNamespace(name="w", backend_type="custom_social", backend=CustomBackend())
        ],
    )
    record = manifest["game_masters"][0]
    assert record["event_semantics"] == {
        "roles": {
            "content.created": ["broadcast"],
            "control": ["finish_action_episode"],
            "world.broadcast": ["broadcast"],
        },
        "fields": {"content.text": ["message"]},
        "labels": {
            "broadcast": ["world.broadcast", "content.created"],
            "finish_action_episode": ["control"],
        },
    }

    artifact = SimpleNamespace(game_masters=[record])
    semantics = semantics_for_backend(artifact, "custom_social")
    assert semantics.tags_of("broadcast") == ("world.broadcast", "content.created")
    assert semantics.value({"message": "hello"}, "content.text") == "hello"


def test_every_registered_health_counter_reaches_the_manifest(tmp_path: Path) -> None:
    """One registry drives every health surface.

    ``evaluations.vocabulary.HEALTH_COUNTERS`` is what the run-end warning, the
    manifest, and ``RunArtifact.health`` all read, so a counter an emitter bumps
    (e.g. harness tool failures, routing fallbacks) cannot be counted and then go
    unreported.
    """
    from silisocs.agents.harness.bridge import HARNESS_TOOL_FAILURES_COUNTER
    from silisocs.evaluations.run_artifact import load_run
    from silisocs.evaluations.vocabulary import HEALTH_COUNTERS
    from silisocs.simulation_engines.policies.routers import ROUTING_FALLBACKS_COUNTER

    assert {HARNESS_TOOL_FAILURES_COUNTER, ROUTING_FALLBACKS_COUNTER} <= set(HEALTH_COUNTERS)

    run = tmp_path / "run"
    run.mkdir()
    manifest = build_run_manifest(
        output_dir=run,
        status="success",
        meta={},
        counters=dict.fromkeys(HEALTH_COUNTERS, 3),
        game_masters=[],
    )
    (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert all(manifest["health"][name] == 3 for name in HEALTH_COUNTERS)
    assert load_run(run).health == {**dict.fromkeys(HEALTH_COUNTERS, 3), "silent_backends": 0}


def test_manifest_health_records_silent_backend(tmp_path: Path) -> None:
    # A backend that committed nothing must leave a trace in the health block so
    # Studio's Run-health panel does not read all-green over blank analysis panels.
    class SilentBackend:
        def count_committed_events(self) -> int:
            return 0

    class ActiveBackend:
        def count_committed_events(self) -> int:
            return 4

    manifest = build_run_manifest(
        output_dir=_run_dir(tmp_path),
        status="success",
        game_masters=[
            SimpleNamespace(name="quiet", backend_type="custom", backend=SilentBackend()),
            SimpleNamespace(name="loud", backend_type="twitter_like", backend=ActiveBackend()),
        ],
    )
    assert manifest["health"]["silent_backends"] == ["quiet"]


def test_environment_provenance_degrades_outside_a_checkout(tmp_path: Path) -> None:
    provenance = environment_provenance(tmp_path)  # no git repo, no uv.lock
    assert provenance["uv_lock_sha256"] is None
    assert provenance["python_version"]
    assert "captured_at" in provenance
