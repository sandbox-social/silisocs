from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from silisocs.runtime.types import ToolCall

pytestmark = pytest.mark.subprocess

# Each scripted test spawns a full `silisocs.runtime.runner` subprocess. A cold
# import of the package (first run, or after the OS page cache is evicted by a
# large full-suite run) can take far longer than a warm ~2s run, so the timeout
# is generous to avoid flaky timeouts under full-suite load.
_SUBPROCESS_TIMEOUT_S = 300


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


class PublicGoodsBehavior:
    """Contribute a fixed per-agent amount each round (one deliberate free-rider)."""

    _AMOUNTS = {"Alex": 20, "Blair": 10, "Casey": 5, "Devon": 0}

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        del prompt
        tool_names = {_tool_name(spec) for spec in tools}
        if "CONTRIBUTE" not in tool_names:
            return []
        agent = str(model.meta_data.get("agent_name", "") or "")
        return [ToolCall("CONTRIBUTE", {"amount": self._AMOUNTS.get(agent, 0)})]


class MessagingBehavior:
    """Each agent privately pings the next in the roster; Devon broadcasts."""

    _TARGETS = {"Alex": "Blair", "Blair": "Casey", "Casey": "Alex"}

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        del prompt
        tool_names = {_tool_name(spec) for spec in tools}
        agent = str(model.meta_data.get("agent_name", "") or "")
        episode = int(model.meta_data.get("episode_idx", 0) or 0)
        if agent == "Devon" and "BROADCAST" in tool_names:
            return [ToolCall("BROADCAST", {"text": f"hello everyone (ep {episode})"})]
        if agent in self._TARGETS and "SEND_MESSAGE" in tool_names:
            return [
                ToolCall(
                    "SEND_MESSAGE",
                    {
                        "recipient": self._TARGETS[agent],
                        "text": f"{agent} pings you (ep {episode})",
                    },
                )
            ]
        return []


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
    config_path: str | None = None,
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
        # A scenario shipped outside the packaged config groups (repo content
        # under scenarios/) composes via --config-path, same as the CLI docs.
        *(["--config-path", config_path] if config_path else []),
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
        "sim.engine.participation.built_in=all",
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
        timeout=_SUBPROCESS_TIMEOUT_S,
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
    # Market events are structured: who acted and which action are fields, and
    # the human-readable narration rides along inside the payload.
    assert {"list_resource", "transfer_resource", "buy_listing", "upkeep_met"} <= {
        str(row.get("label", "")) for row in rows
    }
    assert {"Alex Farmer", "Blair Woodworker"} <= {str(row.get("source_user", "")) for row in rows}
    messages = "\n".join(str((row.get("data") or {}).get("message", "")) for row in rows)
    assert "Listing 1 created" in messages
    assert "transferred 1 wood to Alex Farmer" in messages
    assert "bought 1 food" in messages
    assert "met upkeep needs" in messages

    prompts = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    assert any("RESOURCE MARKET STATE" in str(row.get("prompt", "")) for row in prompts)
    assert any("Production capabilities:" in str(row.get("prompt", "")) for row in prompts)
    assert any("Upkeep needs:" in str(row.get("prompt", "")) for row in prompts)


def test_public_goods_scripted_full_pipeline_and_eval(tmp_path: Path) -> None:
    """The public-goods scenario runs end-to-end and its evaluator scores it.

    This is the structural smoke for the capability-ladder study: real engine,
    real tool-calling resolve, committed CONTRIBUTE rows, a resolved round, a
    run manifest — and the study's eval.py producing the exact expected metrics
    from that output (a `sim.llm.disabled` run cannot do this: the no-op model
    emits no tool calls, so every turn degrades).
    """
    output_dir = _run_scripted(
        tmp_path,
        env="public_goods_game",
        agents="public_goods_game",
        world="public_goods_game",
        behavior="tests.test_scripted_backend_matrix.PublicGoodsBehavior",
        num_agents=4,
        num_steps=2,
        config_path="scenarios/public_goods_game/conf",
    )

    rows = _read_jsonl(output_dir / "action_events.jsonl")
    contribute = [row for row in rows if row.get("label") == "contribute"]
    assert {
        (row["source_user"], row["data"]["round"], row["data"]["contribution"])
        for row in contribute
    } == {
        (agent, rnd, amount)
        for rnd in range(2)
        for agent, amount in PublicGoodsBehavior._AMOUNTS.items()
    }
    resolved = [row for row in rows if row.get("label") == "round_resolved"]
    assert [row["data"]["round"] for row in resolved] == [0]  # final round has no update
    assert resolved[0]["data"]["pool"] == 35.0  # 20 + 10 + 5 + 0

    # The study's evaluator scores the real run output: rate = 35/80 per round,
    # rounds from the manifest (so the update-less final round still counts).
    from tests.test_public_goods_eval import _EVAL

    result = _EVAL.evaluate_run_dir(output_dir)
    assert result["summary"]["rounds"] == 2  # noqa: PLR2004
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(35.0 / 80.0)
    assert result["aggregated"]["free_rider_share"] == pytest.approx(0.25)  # Devon


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
    assert {"leave_note", "work_on_task", "talk"} <= {str(row.get("label", "")) for row in rows}
    messages = "\n".join(str((row.get("data") or {}).get("message", "")) for row in rows)
    assert "left a note" in messages
    assert "completed task welcome_board" in messages
    assert "told" in messages

    prompts = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    assert any("VIRTUAL SPACE STATE" in str(row.get("prompt", "")) for row in prompts)
    assert any("Room tasks:" in str(row.get("prompt", "")) for row in prompts)
    assert any("Room notes:" in str(row.get("prompt", "")) for row in prompts)


def test_messaging_scripted_private_and_broadcast_delivery(tmp_path: Path) -> None:
    """The built-in messaging env runs end-to-end: send, deliver, observe.

    Real engine, real tool-calling resolve; step-1 prompts must contain the
    messages committed in step 0 (private ones only for their recipients).
    """
    output_dir = _run_scripted(
        tmp_path,
        env="messaging",
        agents="messaging",
        world="messaging",
        behavior="tests.test_scripted_backend_matrix.MessagingBehavior",
        num_agents=4,
        extra_overrides=["num_steps=2"],
    )

    rows = _read_jsonl(output_dir / "action_events.jsonl")
    labels = [str(row.get("label", "")) for row in rows]
    assert labels.count("send_message") == 6  # noqa: PLR2004 — 3 senders x 2 steps
    assert labels.count("broadcast_message") == 2  # noqa: PLR2004 — Devon x 2 steps
    sends = [row for row in rows if row.get("label") == "send_message"]
    assert {str((row.get("data") or {}).get("recipient")) for row in sends} == {
        "Alex",
        "Blair",
        "Casey",
    }

    prompts = _read_jsonl(output_dir / "prompts_and_responses.jsonl")
    prompt_text = "\n".join(str(row.get("prompt", "")) for row in prompts)
    assert "MESSAGES" in prompt_text
    # Step-0 traffic is visible in step-1 observations.
    assert "Alex -> You: Alex pings you (ep 0)" in prompt_text
    assert "Devon broadcast: hello everyone (ep 0)" in prompt_text
