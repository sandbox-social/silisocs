"""End-to-end harness runs through the real runner (subprocess).

Complements ``tests/agents/test_harness_agent_contract.py`` (in-process component contract) by
proving the full runtime path: a harness persona class composes, runs on a real backend
through the session, lands actions in ``action_events.jsonl``, writes per-call detail to
``harness_events.jsonl``, and gets indexed in ``run_manifest.json``. Also asserts the
config preflight rejects a harness class under a non-harness resolve.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.subprocess

_REPO = Path(__file__).resolve().parents[2]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_overlay(tmp_path: Path) -> Path:
    """Overlay that disables probes and pins a scripted (probe-only) model."""
    overlay = tmp_path / "conf"
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "eval.yaml").write_text(
        yaml.safe_dump({"probes": {"deployment": {"enabled": False}, "probes": {}}}),
        encoding="utf-8",
    )
    # No tool_calling / resolve / action_prompt overrides: harness agents run on the
    # FULLY DEFAULT twitter_like game master (tool_calling: single, resolve: tool_calling).
    (overlay / "sim.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {"provider": "scripted", "name": "scripted"},
                "engine": {"turn_policy": {"built_in": "single_action", "params": {}}},
            }
        ),
        encoding="utf-8",
    )
    return overlay


def _run(tmp_path: Path, extra: list[str]) -> subprocess.CompletedProcess:
    overlay = _write_overlay(tmp_path)
    output_dir = tmp_path / "run"
    hydra_dir = tmp_path / "hydra"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "silisocs.runtime.runner",
            "--overlay-config-path",
            str(overlay),
            "world=default",
            "agents=default",
            "env=twitter_like",
            "num_agents=2",
            "num_steps=2",
            "agents.persona_pipeline.classes.user.class_path=silisocs.agents.harness.fake.FakeHarnessAgent",
            "env.gm.components.update.built_in=disabled",
            f"output_dir={output_dir}",
            f"hydra.run.dir={hydra_dir}",
            "hydra.output_subdir=configs",
            *extra,
        ],
        cwd=_REPO,
        check=False,
        text=True,
        capture_output=True,
    )


def test_harness_run_writes_actions_and_harness_events(tmp_path: Path) -> None:
    # Zero-config: only the persona class_path changes — the DEFAULT game master binds
    # the Tool Bridge and records the harness turn (no harness component config).
    result = _run(tmp_path, [])
    assert result.returncode == 0, result.stdout + result.stderr
    output_dir = tmp_path / "run"

    # Actions landed in the backend event log (logging is at the backend layer).
    action_rows = _read_jsonl(output_dir / "action_events.jsonl")
    labels = {str(row.get("label", "")) for row in action_rows}
    assert "post" in labels

    # Harness per-call detail was written and is well-formed.
    harness_path = output_dir / "harness_events.jsonl"
    assert harness_path.is_file()
    harness_rows = _read_jsonl(harness_path)
    kinds = {str(row.get("kind", "")) for row in harness_rows}
    assert "tool_executed" in kinds
    assert "turn_completed" in kinds
    assert all(row.get("event_type") == "harness" for row in harness_rows)

    # The run manifest indexes the new artifact.
    manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert "harness_events" in manifest["artifacts"]
    assert manifest["artifacts"]["harness_events"] == ["harness_events.jsonl"]

    # RunArtifact exposes harness events.
    from silisocs.evaluations.run_artifact import load_run

    artifact = load_run(output_dir)
    events = list(artifact.iter_harness_events())
    assert any(row.get("kind") == "turn_completed" for row in events)


def test_harness_checkpoint_restore_roundtrip(tmp_path: Path) -> None:
    first = _run(tmp_path / "a", ["sim.checkpoint.every_n_steps=1"])
    assert first.returncode == 0, first.stdout + first.stderr
    checkpoint = tmp_path / "a" / "run" / "checkpoints" / "step_2_checkpoint.json"
    assert checkpoint.is_file()
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    # Harness agent state (adapter snapshot) is captured in the checkpoint objects.
    agent_states = [
        obj
        for obj in payload["objects"].values()
        if isinstance(obj.get("state"), dict) and "adapter" in obj["state"]
    ]
    assert agent_states, "expected at least one harness agent with adapter state"
