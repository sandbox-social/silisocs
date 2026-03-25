from __future__ import annotations

import json
from pathlib import Path

import pytest

from mastodon_sim.agents.fixed_entity import FixedActionEntityRuntime


def test_fixed_entity_advances_episode_without_observation() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "parsed_action",
            "advance_without_episode_observation": True,
            "fixed_action_plan": {
                0: [
                    {"action_type": "post", "content": "episode-0-a"},
                    {"action_type": "post", "content": "episode-0-b"},
                ],
                1: [
                    {"action_type": "post", "content": "episode-1-a"},
                ],
            },
        }
    )

    first = entity.act(None)
    second = entity.act(None)
    third = entity.act(None)

    assert "CONTENT: episode-0-a" in first
    assert "CONTENT: episode-0-b" in second
    assert "CONTENT: episode-1-a" in third


def test_fixed_entity_non_episode_observation_advances_action_then_episode() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "parsed_action",
            "advance_without_episode_observation": True,
            "fixed_action_plan": {
                0: [
                    {"action_type": "post", "content": "episode-0-a"},
                    {"action_type": "post", "content": "episode-0-b"},
                ],
                1: [
                    {"action_type": "post", "content": "episode-1-a"},
                ],
            },
        }
    )

    entity.observe("timeline update without explicit step id")
    first = entity.act(None)
    entity.observe("still no explicit episode number")
    second = entity.act(None)
    entity.observe("no episode again")
    third = entity.act(None)

    assert "CONTENT: episode-0-a" in first
    assert "CONTENT: episode-0-b" in second
    assert "CONTENT: episode-1-a" in third


def test_fixed_entity_ignores_numeric_non_episode_observation() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "parsed_action",
            "advance_without_episode_observation": True,
            "fixed_action_plan": {
                0: [{"action_type": "post", "content": "episode-0-a"}],
                1: [{"action_type": "post", "content": "episode-1-a"}],
            },
        }
    )

    entity.observe("Home feed: Post ID: 42, Score: 10")
    first = entity.act(None)
    second = entity.act(None)

    assert "CONTENT: episode-0-a" in first
    assert "CONTENT: episode-1-a" in second


def test_fixed_entity_uses_explicit_episode_marker_from_observation() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "parsed_action",
            "advance_without_episode_observation": True,
            "fixed_action_plan": {
                0: [{"action_type": "post", "content": "episode-0-a"}],
                2: [{"action_type": "post", "content": "episode-2-a"}],
            },
        }
    )

    entity.observe("EPISODE: 2")
    result = entity.act(None)

    assert "CONTENT: episode-2-a" in result


def test_fixed_entity_emits_tool_call_format_for_finished() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "tool_calling",
            "fixed_action_plan": {
                0: [
                    {"action_type": "FINISHED"},
                ]
            },
        }
    )

    result = entity.act(None)
    payload = json.loads(result)

    assert payload["tool_call"]["name"] == "FINISHED"
    assert payload["tool_call"]["arguments"] == {}


def test_fixed_entity_can_emit_finished_at_episode_end() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "generic_action",
            "emit_finished_on_episode_end": True,
            "advance_without_episode_observation": False,
            "fixed_action_plan": {
                0: [
                    {"action_type": "create_tweet", "content": "hello"},
                ]
            },
        }
    )

    first = entity.act(None)
    second = entity.act(None)

    assert "ACTION: create_tweet" in first
    assert "ACTION: FINISHED" in second


def test_fixed_entity_does_not_cycle_after_exhaustion() -> None:
    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "parsed_action",
            "advance_without_episode_observation": True,
            "fixed_action_plan": {
                0: [{"action_type": "post", "content": "episode-0-a"}],
                1: [{"action_type": "post", "content": "episode-1-a"}],
            },
        }
    )

    first = entity.act(None)
    second = entity.act(None)
    third = entity.act(None)
    fourth = entity.act(None)

    assert "CONTENT: episode-0-a" in first
    assert "CONTENT: episode-1-a" in second
    assert "ACTION TYPE: FINISHED" in third
    assert "ACTION TYPE: FINISHED" in fourth


def test_fixed_entity_requires_dict_action_plan() -> None:
    with pytest.raises(ValueError, match="fixed_action_plan must be an episode-keyed dict"):
        FixedActionEntityRuntime(
            params={
                "name": "Fixed",
                "fixed_action_plan": [{"action_type": "post", "content": "legacy-list"}],
            }
        )


def test_fixed_entity_loads_action_plan_from_json_file(tmp_path: Path) -> None:
    plan_path = tmp_path / "fixed_plan.json"
    plan_path.write_text(
        json.dumps({"0": [{"action_type": "post", "content": "from-file"}]}),
        encoding="utf-8",
    )

    entity = FixedActionEntityRuntime(
        params={
            "name": "Fixed",
            "action_output_mode": "parsed_action",
            "fixed_action_plan_file": str(plan_path),
        }
    )

    result = entity.act(None)
    assert "CONTENT: from-file" in result
