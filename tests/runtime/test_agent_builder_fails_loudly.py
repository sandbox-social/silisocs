"""The persona/fixed-action builders must never silently build the wrong world.

Each case here used to produce a plausible-looking run: an unresolved
``${event.context}`` pasted verbatim into every persona, a ``count: 3`` class that
built one agent, a fixed action whose ``episode`` typo rescheduled it to step 0, or
a plan entry that was simply dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.construction.agent_builders import PersonaPipelineAgentBuilder
from silisocs.runtime.construction.agent_builders.common import to_plain
from silisocs.runtime.construction.agent_builders.fixed_actions import FixedActionBuilder


class _TestBuilder(PersonaPipelineAgentBuilder):
    """Concrete builder for persona-pipeline tests."""


def _world(classes: dict[str, Any], **extra: Any) -> DictConfig:
    return OmegaConf.create(
        {"scenario_name": "default", "persona_pipeline": {"classes": classes}, **extra}
    )


# ------------------------------------------------------------------- to_plain


def test_unresolved_interpolation_raises_instead_of_leaking_the_literal() -> None:
    cfg = OmegaConf.create({"persona_pipeline": {"defaults": {"params": {"c": "${event.contxt}"}}}})
    with pytest.raises(ValueError, match=r"event\.contxt"):
        to_plain(cfg.persona_pipeline, where="agents.persona_pipeline")


def test_resolvable_interpolation_still_resolves() -> None:
    cfg = OmegaConf.create(
        {"event": {"context": "An election."}, "block": {"c": "${event.context}"}}
    )
    assert to_plain(cfg.block) == {"c": "An election."}


def test_builder_surfaces_an_unresolved_persona_interpolation() -> None:
    world = _world(
        {
            "user": {
                "count": 1,
                "class_path": "silisocs.agents.native.NativeAgent",
                "data": {"source": "inline", "records": [{"name": "A", "persona": "P"}]},
                "field_map": {"name": "name", "context": "persona"},
                "params": {"world_context": "${event.context}"},
            }
        }
    )
    with pytest.raises(ValueError, match="agents.persona_pipeline"):
        _TestBuilder(world).build_agent_configs()


# ----------------------------------------------------------------- class count


def test_count_greater_than_one_without_data_raises() -> None:
    world = _world(
        {
            "user": {
                "count": 3,
                "class_path": "silisocs.agents.native.NativeAgent",
                "params": {"name": "Solo", "context": "Alone."},
            }
        }
    )
    with pytest.raises(ValueError, match="Class `user` requests 3 agent"):
        _TestBuilder(world).build_agent_configs()


def test_negative_count_raises() -> None:
    world = _world(
        {
            "user": {
                "count": -1,
                "class_path": "silisocs.agents.native.NativeAgent",
                "data": {"source": "inline", "records": [{"name": "A", "persona": "P"}]},
                "field_map": {"name": "name", "context": "persona"},
            }
        }
    )
    with pytest.raises(ValueError, match="Class `user` count must be a non-negative integer"):
        _TestBuilder(world).build_agent_configs()


def test_non_numeric_count_raises() -> None:
    world = _world({"user": {"count": "many", "class_path": "silisocs.agents.native.NativeAgent"}})
    with pytest.raises(ValueError, match="Class `user` count must be a non-negative integer"):
        _TestBuilder(world).build_agent_configs()


def test_count_one_without_data_still_builds_one_agent() -> None:
    world = _world(
        {
            "user": {
                "count": 1,
                "class_path": "silisocs.agents.native.NativeAgent",
                "params": {"name": "Solo", "context": "Alone."},
            }
        }
    )
    assert len(_TestBuilder(world).build_agent_configs()) == 1


# ---------------------------------------------------------- fixed-action plans


def _plan(actions: Any) -> dict[int, list[dict[str, Any]]]:
    return FixedActionBuilder.normalize_fixed_action_plan(actions)


def test_malformed_episode_raises_instead_of_firing_at_step_zero() -> None:
    with pytest.raises(ValueError, match="non-numeric `episode`"):
        _plan([{"action": "create_tweet", "args": {"status": "hi"}, "episode": "two"}])


def test_negative_episode_raises() -> None:
    with pytest.raises(ValueError, match="negative `episode`"):
        _plan([{"action_type": "create_tweet", "episode": -1}])


def test_entry_without_an_action_name_raises_instead_of_being_dropped() -> None:
    with pytest.raises(ValueError, match=r"declares neither `action` nor `action_type`"):
        _plan([{"args": {"status": "hi"}}])


def test_non_list_plan_raises_instead_of_returning_an_empty_plan() -> None:
    with pytest.raises(ValueError, match="must be a list of action entries"):
        _plan({"action": "create_tweet"})


def test_non_mapping_plan_entry_raises() -> None:
    with pytest.raises(ValueError, match=r"\[0\] must be a mapping"):
        _plan(["create_tweet"])


def test_valid_plan_groups_by_episode() -> None:
    plan = _plan(
        [
            {"action": "create_tweet", "args": {"status": "a", "episode": 0}},
            {"action": "create_tweet", "args": {"status": "b"}, "episode": 2},
        ]
    )
    assert sorted(plan) == [0, 2]
    assert plan[0][0]["content"] == "a"


def test_action_set_entry_without_an_action_raises_naming_the_class() -> None:
    builder = FixedActionBuilder(OmegaConf.create({}), Path)
    with pytest.raises(ValueError, match="declares no `action`"):
        builder.build_fixed_action_config(
            class_cfg={"enabled": True, "action_set_ref": "s"},
            fixed_action_sets={"s": [{"args": {"status": "hi"}}]},
            render_context={},
            where="agents.persona_pipeline.classes.news.fixed_action",
        )
