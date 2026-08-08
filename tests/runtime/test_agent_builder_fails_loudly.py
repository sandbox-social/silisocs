"""The persona/fixed-action builders must never silently build the wrong world.

Each case here used to produce a plausible-looking run: an unresolved
``${event.context}`` pasted verbatim into every persona, a ``count: 3`` class that
built one agent, a fixed action whose ``episode`` typo rescheduled it to step 0, or
a plan entry that was simply dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.construction.agent_builders import PersonaPipelineAgentBuilder
from silisocs.runtime.construction.agent_builders.common import to_plain
from silisocs.runtime.construction.agent_builders.fixed_actions import FixedActionBuilder
from silisocs.runtime.construction.agent_configs import build_agent_configs


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
    with pytest.raises(ValueError) as excinfo:
        _TestBuilder(world).build_agent_configs()
    # The raise is right (agent names must be unique, so a params-only class
    # cannot replicate), but only if the message says so and names the fix.
    message = str(excinfo.value)
    assert "Class `user` requests 3 agent" in message
    assert "single-instance" in message
    assert "`data` source" in message
    assert "count: 1" in message


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


# ------------------------------------------------- root-level scenario blocks
#
# A builder is constructed with ``cfg.agents``, but a scenario's ``data`` and
# ``fixed_action_sets`` may be declared at the config ROOT (the ``@package
# _global_`` world-file spelling that config validation already accepts). Both
# are threaded in as reserved builder params; without that, root ``data`` was a
# hard ``ConfigAttributeError`` and root ``fixed_action_sets`` validated but was
# silently never loaded.


def _root_cfg(classes: dict[str, Any], **root: Any) -> DictConfig:
    return OmegaConf.create(
        {"scenario_name": "default", "agents": {"persona_pipeline": {"classes": classes}}, **root}
    )


_NEWS_CLASS: dict[str, Any] = {
    "count": 1,
    "class_path": "silisocs.agents.native.NativeAgent",
    "params": {"name": "Gazette", "context": "The local paper."},
    "use_news_file_posts": True,
    "include_news_images": True,
}


def test_root_level_data_news_file_reaches_the_builder(tmp_path: Path) -> None:
    (tmp_path / "news.json").write_text(json.dumps({"Mayor debates": ["debate.png"]}))
    cfg = _root_cfg(
        {"news_account": dict(_NEWS_CLASS)},
        data={"news_file": str(tmp_path / "news")},
    )

    agents = build_agent_configs(cfg)

    assert agents[0].params["posts"] == {"Mayor debates": "debate.png"}


def test_news_posts_without_a_root_data_block_raises_the_config_error() -> None:
    cfg = _root_cfg({"news_account": dict(_NEWS_CLASS)})

    with pytest.raises(ValueError, match="data.news_file is unset"):
        build_agent_configs(cfg)


def test_root_level_fixed_action_sets_are_loaded(tmp_path: Path) -> None:
    cfg = _root_cfg(
        {
            "scripted": {
                "count": 1,
                "class_path": "silisocs.agents.fixed.FixedAgent",
                "params": {"name": "Bot", "context": "Scripted."},
                "fixed_action": {"enabled": True, "action_set_ref": "opening"},
            }
        },
        fixed_action_sets={
            "inline": {
                "opening": {"actions": [{"action": "create_tweet", "args": {"status": "hello"}}]}
            }
        },
    )

    agents = build_agent_configs(cfg)

    assert agents[0].params["fixed_action_plan"][0][0]["content"] == "hello"


def test_agents_level_fixed_action_sets_still_win() -> None:
    cfg = OmegaConf.create(
        {
            "scenario_name": "default",
            "agents": {
                "fixed_action_sets": {
                    "inline": {
                        "opening": {
                            "actions": [{"action": "create_tweet", "args": {"status": "near"}}]
                        }
                    }
                },
                "persona_pipeline": {
                    "classes": {
                        "scripted": {
                            "count": 1,
                            "class_path": "silisocs.agents.fixed.FixedAgent",
                            "params": {"name": "Bot", "context": "Scripted."},
                            "fixed_action": {"enabled": True, "action_set_ref": "opening"},
                        }
                    }
                },
            },
            "fixed_action_sets": {
                "inline": {
                    "opening": {"actions": [{"action": "create_tweet", "args": {"status": "far"}}]}
                }
            },
        }
    )

    agents = build_agent_configs(cfg)

    assert agents[0].params["fixed_action_plan"][0][0]["content"] == "near"


# ------------------------------------------------------------- sim_role_name


@pytest.mark.parametrize("declared", [None, ""])
def test_explicit_empty_sim_role_name_defaults_to_the_class_name(declared: Any) -> None:
    """An explicit null/empty role means "default", exactly as validation reads it.

    Taking it literally gave every agent of the class a role that matches nothing
    in the follow graph, while ``fully_connected_targets: [voter]`` still passed
    config validation (which resolves the same class as ``voter``).
    """
    cfg = _root_cfg(
        {
            "voter": {
                "count": 1,
                "class_path": "silisocs.agents.native.NativeAgent",
                "sim_role_name": declared,
                "params": {"name": "Vi", "context": "A voter."},
            }
        }
    )

    agents = build_agent_configs(cfg)

    assert agents[0].params["sim_role"]["name"] == "voter"
