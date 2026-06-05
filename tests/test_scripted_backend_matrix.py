from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from silisocs.runtime.types import ToolCall


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class RedditPostCommentVoteBehavior:
    """Create a Reddit-like post, then comment and upvote visible posts."""

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        tool_names = {_tool_name(spec) for spec in tools}
        post_ids = re.findall(r"Post ID:\s*([0-9]+)", prompt)
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        if post_ids:
            calls: list[ToolCall] = []
            if "create_comment" in tool_names:
                calls.append(
                    ToolCall(
                        "create_comment",
                        {
                            "post_id": int(post_ids[0]),
                            "content": f"{agent} adds a scripted comment.",
                        },
                    )
                )
            if "upvote" in tool_names:
                calls.append(
                    ToolCall("upvote", {"target_id": int(post_ids[0]), "target_type": "post"})
                )
            return calls or [ToolCall("get_home_feed", {"limit": 10})]
        if "create_reddit_post" in tool_names:
            return [
                ToolCall(
                    "create_reddit_post",
                    {
                        "subreddit": "general",
                        "title": f"{agent} scripted post",
                        "content": "Scripted Reddit-like content.",
                    },
                )
            ]
        return [ToolCall("get_home_feed", {"limit": 10})] if "get_home_feed" in tool_names else []


class ResourceMarketBehavior:
    """Exercise role production, transfers, listings, purchases, and upkeep."""

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        tool_names = {_tool_name(spec) for spec in tools}
        agent = str(model.meta_data.get("agent_name", "") or "")
        if "Role: woodworker" in prompt and "TRANSFER_RESOURCE" in tool_names:
            return [
                ToolCall("PRODUCE_RESOURCE", {"resource": "wood", "quantity": 1}),
                ToolCall(
                    "TRANSFER_RESOURCE",
                    {"target_user": "Alex Farmer", "resource": "wood", "quantity": 1},
                ),
            ]
        if "Role: farmer" in prompt and "LIST_RESOURCE" in tool_names:
            return [ToolCall("LIST_RESOURCE", {"resource": "food", "quantity": 1, "price": 1})]
        if (
            agent != "Alex Farmer"
            and "Open listings: none" not in prompt
            and "BUY_LISTING" in tool_names
        ):
            return [ToolCall("BUY_LISTING", {"listing_id": 1})]
        if "PRODUCE_RESOURCE" in tool_names:
            return [ToolCall("PRODUCE_RESOURCE", {"resource": "food", "quantity": 1})]
        return [ToolCall("INSPECT_MARKET", {})] if "INSPECT_MARKET" in tool_names else []


class VirtualSpaceBehavior:
    """Exercise room tasks, persistent notes, and co-located talk."""

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        del model
        tool_names = {_tool_name(spec) for spec in tools}
        if "welcome_board:" in prompt and "[complete]" not in prompt:
            calls: list[ToolCall] = []
            if "WORK_ON_TASK" in tool_names:
                calls.append(ToolCall("WORK_ON_TASK", {"task_id": "welcome_board", "effort": 1}))
            if "LEAVE_NOTE" in tool_names:
                calls.append(
                    ToolCall("LEAVE_NOTE", {"message": "I helped with the welcome board."})
                )
            if calls:
                return calls
        present_match = re.search(r"Present here:\s*([^\n]+)", prompt)
        present = present_match.group(1).strip() if present_match else ""
        if present and present.lower() != "none" and "TALK" in tool_names:
            target = present.split(",", 1)[0].strip()
            return [
                ToolCall("TALK", {"target_user": target, "message": "Hello from a scripted test."})
            ]
        if "MOVE" in tool_names:
            return [ToolCall("MOVE", {"destination": "garden"})]
        return [ToolCall("LOOK", {})] if "LOOK" in tool_names else []


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


def _run_scripted(
    tmp_path: Path,
    *,
    env: str,
    agents: str,
    world: str,
    behavior: str,
    num_agents: int = 2,
    num_steps: int = 2,
    extra_overrides: list[str] | None = None,
) -> Path:
    overlay = tmp_path / f"conf_{env}"
    overlay.mkdir()
    (overlay / "eval.yaml").write_text(
        yaml.safe_dump({"probes": {"deployment": {"enabled": False}, "probes": {}}}),
        encoding="utf-8",
    )
    (overlay / "sim.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "provider": "scripted",
                    "name": "scripted",
                    "extra_kwargs": {"behavior_class_path": behavior},
                },
                "tool_calling": {"mode": "multi"},
                "initialization": {
                    "simulation": {"built_in": "none", "class_path": None, "params": {}}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / f"run_{env}"
    hydra_dir = tmp_path / f"hydra_{env}"
    cmd = [
        sys.executable,
        "-m",
        "silisocs.runtime.runner",
        "--overlay-config-path",
        str(overlay),
        f"world={world}",
        f"agents={agents}",
        f"env={env}",
        f"num_agents={num_agents}",
        f"num_steps={num_steps}",
        "sim.llm.provider=scripted",
        "sim.llm.name=scripted",
        "sim.tool_calling.mode=multi",
        "env.gm.components.resolve.built_in=tool_calling",
        f"output_rootname={output_dir}",
        f"hydra.run.dir={hydra_dir}",
        "hydra.output_subdir=configs",
        *(extra_overrides or []),
    ]
    result = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output_dir


def test_reddit_like_scripted_post_comment_vote(tmp_path: Path) -> None:
    output_dir = _run_scripted(
        tmp_path,
        env="reddit_like",
        agents="default",
        world="default",
        behavior="tests.test_scripted_backend_matrix.RedditPostCommentVoteBehavior",
        extra_overrides=[
            "env.gm.components.update.built_in=disabled",
            "env.gm.components.observe.params.timeline_mode=follower_chronological",
        ],
    )

    rows = _read_jsonl(output_dir / "action_events.jsonl")
    labels = {str(row.get("label", "")) for row in rows}
    assert "post" in labels
    assert "comment" in labels
    assert "upvote" in labels

    prompts = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    assert any("Post ID:" in str(row.get("prompt", "")) for row in prompts)


def test_resource_market_scripted_list_and_buy(tmp_path: Path) -> None:
    output_dir = _run_scripted(
        tmp_path,
        env="resource_market",
        agents="resource_market",
        world="resource_market",
        behavior="tests.test_scripted_backend_matrix.ResourceMarketBehavior",
        extra_overrides=[
            "num_steps=2",
            "env.gm.components.next_acting.built_in=all_agents",
            "env.gm.backend.params.upkeep_interval=1",
        ],
    )

    rows = _read_jsonl(output_dir / "action_events.jsonl")
    messages = "\n".join(str(row.get("message", "")) for row in rows)
    assert "Listing 1 created" in messages
    assert "transferred 1 wood to Alex Farmer" in messages
    assert "bought 1 food" in messages
    assert "met upkeep needs" in messages

    prompts = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    assert any("RESOURCE MARKET STATE" in str(row.get("prompt", "")) for row in prompts)
    assert any("Production capabilities:" in str(row.get("prompt", "")) for row in prompts)
    assert any("Upkeep needs:" in str(row.get("prompt", "")) for row in prompts)


def test_virtual_space_scripted_talks_from_observation(tmp_path: Path) -> None:
    output_dir = _run_scripted(
        tmp_path,
        env="virtual_space",
        agents="virtual_space",
        world="virtual_space",
        behavior="tests.test_scripted_backend_matrix.VirtualSpaceBehavior",
        extra_overrides=[
            "num_steps=2",
            "env.gm.components.next_acting.built_in=all_agents",
        ],
    )

    rows = _read_jsonl(output_dir / "action_events.jsonl")
    messages = "\n".join(str(row.get("message", "")) for row in rows)
    assert "left a note" in messages
    assert "completed task welcome_board" in messages
    assert "told" in messages

    prompts = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    assert any("VIRTUAL SPACE STATE" in str(row.get("prompt", "")) for row in prompts)
    assert any("Room tasks:" in str(row.get("prompt", "")) for row in prompts)
    assert any("Room notes:" in str(row.get("prompt", "")) for row in prompts)


def test_dashboard_saved_config_runs_with_scripted_model(tmp_path: Path) -> None:
    from silisocs.dashboard.config_writer import save_scenario

    world_root = tmp_path / "worlds"
    output_dir = tmp_path / "dashboard_run"
    conf_dir = save_scenario(
        "dashboard_native",
        {
            "scenario_name": "dashboard_native",
            "jobname_format": "Dashboard_N${num_agents}_T${num_steps}_${run_name}",
            "setting": {"name": "Dashboard Native", "background": ["Generated by dashboard."]},
            "event": {"name": "Smoke", "context": "Agents test dashboard output."},
            "data": {},
            "persona_pipeline": {
                "defaults": {
                    "params": {
                        "world_context": "${event.context}",
                        "goal": "Test the dashboard-generated config.",
                        "style": "",
                        "bio": "",
                    },
                    "shared_memories": ["${event.context}"],
                },
                "classes": {
                    "user": {
                        "count": 2,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "sim_role_name": "user",
                        "data": {
                            "source": "inline",
                            "records": [
                                {"name": "Dash Alice", "context": "Alice tests posting."},
                                {"name": "Dash Bob", "context": "Bob tests liking."},
                            ],
                        },
                        "field_map": {"name": "name", "context": "context"},
                    }
                },
            },
            "shared_memories": ["Agents are validating dashboard output."],
            "initial_observations": ["{name} opens the dashboard-generated world."],
            "probes": {"deployment": {"enabled": False}, "probes": {}},
        },
        {
            "num_agents": 2,
            "num_steps": 1,
            "seed": 1,
            "experiment_name": "dashboard_native",
            "run_name": "dashboard_native",
            "output_rootname": str(output_dir),
            "llm.provider": "scripted",
            "llm.name": "scripted",
            "llm.extra_kwargs.behavior_class_path": "tests.test_scripted_backend_matrix.SocialPostLikeBehavior",
            "tool_calling.mode": "multi",
            "initialization.simulation.built_in": "none",
        },
        {
            "gm.components.initialize.params.graph.network_type": "random",
            "gm.components.initialize.params.graph.base_followership_probability": 1.0,
            "gm.components.initialize.params.graph.fully_connected_targets": ["user"],
            "gm.components.next_acting.params.activity_transition_rates.user.inactive_to_active": 1.0,
            "gm.components.next_acting.params.activity_transition_rates.user.active_to_inactive": 0.0,
            "gm.components.observe.params.timeline_mode": "follower_chronological",
            "gm.components.update.built_in": "disabled",
            "gm.components.resolve.built_in": "tool_calling",
        },
        "twitter_like",
        world_root,
        {"probes.deployment.enabled": False},
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "silisocs.runtime.runner",
            "--config-path",
            str(conf_dir),
            "world=default",
            "agents=default",
            "env=twitter_like",
            f"hydra.run.dir={tmp_path / 'dashboard_hydra'}",
            "hydra.output_subdir=configs",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output_dir / "effective_config.yaml").is_file()
    labels = {str(row.get("label", "")) for row in _read_jsonl(output_dir / "action_events.jsonl")}
    assert "post" in labels
