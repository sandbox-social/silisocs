from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from silisocs.runtime.types import ToolCall

pytestmark = pytest.mark.subprocess


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class SocialPostLikeBehavior:
    """Post on the first step, then like visible timeline posts."""

    def sample_text(self, prompt: str, *, model: Any) -> str:
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        episode = int(model.meta_data.get("episode_idx", -1) or -1)
        if "seed post" in prompt.lower():
            return f"{agent}: seed post"
        return f"{agent} update at episode {episode}"

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        tool_names = {_tool_name(spec) for spec in tools}
        tool_names.discard("")
        episode = int(model.meta_data.get("episode_idx", -1) or -1)
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        tweet_ids = re.findall(r"Tweet ID:\s*([0-9]+)", prompt)
        if episode <= 0 and "create_tweet" in tool_names:
            return [ToolCall("create_tweet", {"status": f"{agent} says hello at step {episode}"})]
        if "like_tweet" in tool_names and tweet_ids:
            return [ToolCall("like_tweet", {"post_id": tweet_ids[0]})]
        if "create_tweet" in tool_names:
            return [ToolCall("create_tweet", {"status": f"{agent} update at step {episode}"})]
        return [ToolCall(sorted(tool_names)[0], {})] if tool_names else []


def _tool_name(spec: dict[str, Any]) -> str:
    function = spec.get("function")
    if isinstance(function, dict):
        return str(function.get("name", "") or "").strip()
    return str(spec.get("name", "") or "").strip()


def test_scripted_social_posts_and_likes_with_timeline_context(tmp_path: Path) -> None:
    """End-to-end scripted social run with timeline context checks."""
    overlay = tmp_path / "conf"
    overlay.mkdir()
    (overlay / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "probes": {
                    "deployment": {
                        "enabled": False,
                    },
                    "probes": {},
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
                    "provider": "scripted",
                    "name": "scripted",
                    "extra_kwargs": {
                        "behavior_class_path": "tests.test_scripted_social_e2e.SocialPostLikeBehavior",
                    },
                },
                "tool_calling": {"mode": "multi"},
                "initialization": {
                    "simulation": {
                        "built_in": "none",
                        "class_path": None,
                        "params": {},
                    }
                },
                "engine": {
                    "turn_policy": {
                        "built_in": "single_action",
                        "class_path": None,
                        "params": {},
                    }
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
            "num_agents=2",
            "num_steps=2",
            "sim.llm.provider=scripted",
            "sim.llm.name=scripted",
            "sim.tool_calling.mode=multi",
            "env.gm.components.observe.params.timeline_mode=follower_chronological",
            "env.gm.components.initialize.params.graph.base_followership_probability=1.0",
            "env.gm.components.initialize.params.graph.fully_connected_targets=[user]",
            "env.gm.components.resolve.built_in=tool_calling",
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

    action_rows = _read_jsonl(output_dir / "action_events.jsonl")
    prompt_rows = _read_jsonl(output_dir / "prompts_and_responses.jsonl")

    labels = {str(row.get("label", "")) for row in action_rows}
    assert "post" in labels
    assert "like" in labels

    engine_turn_label = "agent" + "_turn"
    assert not [row for row in action_rows if row.get("label") == engine_turn_label]

    assert prompt_rows
    assert all("agent_name" in row and "episode_idx" in row for row in prompt_rows)
    later_prompts = [row for row in prompt_rows if int(row.get("episode_idx", -1)) >= 1]
    assert later_prompts
    assert any("Tweet ID:" in str(row.get("prompt", "")) for row in later_prompts)
    assert any("tool_calls:" in str(row.get("output", "")) for row in prompt_rows)


def test_scripted_social_checkpoint_restore_uses_backend_state(tmp_path: Path) -> None:
    overlay = tmp_path / "conf"
    overlay.mkdir()
    (overlay / "sim.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "provider": "scripted",
                    "name": "scripted",
                    "extra_kwargs": {
                        "behavior_class_path": "tests.test_scripted_social_e2e.SocialPostLikeBehavior",
                    },
                },
                "tool_calling": {"mode": "multi"},
                "checkpoint": {"every_n_steps": 1},
                "engine": {
                    "turn_policy": {"built_in": "single_action", "params": {}},
                    # Opt into activity gating: this scenario's post-then-like
                    # cadence across the checkpoint boundary depends on sparse
                    # per-step activation, not the `all` default.
                    "participation": {
                        "built_in": "activity_probability",
                        "params": {
                            "active_probability": None,
                            "min_active_agents": 1,
                            "activity_transition_rates": {
                                "user": {"inactive_to_active": 0.3, "active_to_inactive": 0.3},
                            },
                        },
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    first_output = tmp_path / "first"
    first_hydra = tmp_path / "first_hydra"
    common = [
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
        "sim.llm.provider=scripted",
        "sim.llm.name=scripted",
        "sim.tool_calling.mode=multi",
        "env.gm.components.observe.params.timeline_mode=follower_chronological",
        "env.gm.components.initialize.params.graph.base_followership_probability=1.0",
        "env.gm.components.initialize.params.graph.fully_connected_targets=[user]",
        "env.gm.components.resolve.built_in=tool_calling",
        "env.gm.components.update.built_in=disabled",
        "hydra.output_subdir=configs",
    ]
    first = subprocess.run(
        [*common, f"output_dir={first_output}", f"hydra.run.dir={first_hydra}"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    checkpoint = first_output / "checkpoints" / "step_2_checkpoint.json"
    assert checkpoint.is_file()
    checkpoint_payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert checkpoint_payload["objects"]
    gm_payload = checkpoint_payload["objects"]["twitter_like_gm"]
    assert gm_payload["state"]["backend"]["backend_type"] == "twitter_like"
    assert gm_payload["state"]["backend"]["state"]["db_snapshot_b64"]

    second_output = tmp_path / "second"
    second_hydra = tmp_path / "second_hydra"
    second = subprocess.run(
        [
            *common,
            f"output_dir={second_output}",
            f"hydra.run.dir={second_hydra}",
            f"sim.checkpoint.source_run={first_output}",
            "sim.checkpoint.restore.built_in=social_action_event_replay",
            "num_steps=3",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )
    assert second.returncode == 0, second.stdout + second.stderr

    restored_rows = _read_jsonl(second_output / "action_events.jsonl")
    labels = [str(row.get("label", "")) for row in restored_rows]
    assert "post" in labels
    assert not [row for row in restored_rows if row.get("label") == "agent" + "_turn"]
    with sqlite3.connect(second_output / "twitter_like.db") as conn:
        rows = conn.execute("SELECT content FROM posts ORDER BY id").fetchall()
    contents = [str(row[0]) for row in rows]
    assert any("says hello" in content for content in contents)
