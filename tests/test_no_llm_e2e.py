from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.subprocess


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_scripted_model_runs_real_runner_engine_backend_path(tmp_path: Path) -> None:
    """Run a tiny public smoke test without external LLM or network access."""
    overlay = tmp_path / "conf"
    overlay.mkdir()
    (overlay / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "probes": {
                    "deployment": {
                        "enabled": True,
                        "start_step": 0,
                        "every_n_steps": 1,
                        "include_agents": [],
                        "exclude_agents": [],
                    },
                    "probes": {
                        "smoke": {
                            "probe_name": "smoke",
                            "probe_type": "BinaryProbe",
                            "probe_data": {
                                "name": "SmokeProbe",
                                "question": "Reply yes or no.",
                            },
                        },
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (overlay / "sim.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "extra_kwargs": {
                        "text_response": "Q0: yes",
                    },
                },
                "initialization": {
                    "simulation": {
                        "built_in": "seed_posts",
                        "class_path": None,
                        "params": {
                            "type": "agent",
                            "params": {},
                        },
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "run"
    hydra_dir = tmp_path / "hydra"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "silisocs.runtime.runner",
            "--overlay-config-path",
            str(overlay),
            "world=default",
            "agents=default",
            "env=twitter_like",
            "eval=base",
            "num_agents=2",
            "num_steps=1",
            "sim.llm.provider=scripted",
            "sim.llm.name=scripted",
            "env.gm.components.observe.params.timeline_mode=follower_chronological",
            "env.gm.components.update.built_in=disabled",
            f"output_dir={output_dir}",
            f"hydra.run.dir={hydra_dir}",
            "hydra.output_subdir=configs",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (output_dir / "effective_config.yaml").is_file()
    assert (output_dir / "twitter_like.db").is_file()
    assert (output_dir / "sim_metrics.json").is_file()
    prompt_rows = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    assert prompt_rows
    assert all("prompt" in row and "output" in row for row in prompt_rows)
    assert any("Q0: yes" in str(row.get("output", "")) for row in prompt_rows)

    action_rows = _read_jsonl(output_dir / "action_events.jsonl")
    assert any(row.get("label") == "post" for row in action_rows)
    assert sum(1 for row in action_rows if row.get("label") == "post") >= 2

    probe_rows = _read_jsonl(output_dir / "probe_events.jsonl")
    assert probe_rows
    assert all("probe_type" in row["data"] for row in probe_rows)
    assert all("probe_return" in row["data"] for row in probe_rows)
    assert any(row["data"]["probe_return"] == "Yes" for row in probe_rows)

    metrics = json.loads((output_dir / "sim_metrics.json").read_text(encoding="utf-8"))
    assert metrics["meta"]["llm_name"] == "scripted"
    assert "Simulation complete: status=success" in (output_dir / "run_stats.log").read_text(
        encoding="utf-8"
    )
