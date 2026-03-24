from __future__ import annotations

import pytest

from concordia.components import game_master as gm_components  # type: ignore[attr-defined]

from mastodon_sim.environments.gm.shared_flow_game_master import (
    _build_flow_to_component_map,
    _expand_shared_flow_map_alias,
)


def test_build_flow_map_uses_observe_flow_map() -> None:
    observe_components = {
        "observe__timeline_make_observation": object(),
        "observe__episode_observation": object(),
    }
    resolve_key = gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY

    mapping = _build_flow_to_component_map(
        entity_action_flows={"alice": "active", "bob": "fixed_pre"},
        observe_components=observe_components,
        resolve_component_key=resolve_key,
        observe_slot_cfg={
            "flow_map": {
                "active": "observe__timeline_make_observation",
                "fixed_pre": "observe__episode_observation",
                "default": "observe__timeline_make_observation",
            }
        },
        resolve_slot_cfg={},
    )

    assert mapping["active"]["observe"] == "observe__timeline_make_observation"
    assert mapping["fixed_pre"]["observe"] == "observe__episode_observation"
    assert mapping["default"]["observe"] == "observe__timeline_make_observation"
    assert mapping["active"]["resolve"] == resolve_key


def test_build_flow_map_accepts_shorthand_observe_component_names() -> None:
    observe_components = {
        "observe__timeline_make_observation": object(),
        "observe__episode_observation": object(),
    }
    resolve_key = gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY

    mapping = _build_flow_to_component_map(
        entity_action_flows={"alice": "fixed_pre"},
        observe_components=observe_components,
        resolve_component_key=resolve_key,
        observe_slot_cfg={"flow_map": {"fixed_pre": "episode_observation"}},
        resolve_slot_cfg={},
    )

    assert mapping["fixed_pre"]["observe"] == "observe__episode_observation"


def test_build_flow_map_raises_on_invalid_observe_mapping() -> None:
    observe_components = {
        "observe__timeline_make_observation": object(),
    }
    resolve_key = gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY

    with pytest.raises(ValueError, match="Invalid observe flow_map"):
        _build_flow_to_component_map(
            entity_action_flows={"alice": "active"},
            observe_components=observe_components,
            resolve_component_key=resolve_key,
            observe_slot_cfg={"flow_map": {"active": "observe__does_not_exist"}},
            resolve_slot_cfg={},
        )


def test_build_flow_map_raises_on_invalid_resolve_mapping() -> None:
    observe_components = {
        "observe__timeline_make_observation": object(),
    }
    resolve_key = gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY

    with pytest.raises(ValueError, match="Invalid resolve flow_map"):
        _build_flow_to_component_map(
            entity_action_flows={"alice": "active"},
            observe_components=observe_components,
            resolve_component_key=resolve_key,
            observe_slot_cfg={},
            resolve_slot_cfg={"flow_map": {"active": "resolve__generic_action"}},
        )


def test_expand_shared_flow_map_alias_routes_and_fields() -> None:
    expanded = _expand_shared_flow_map_alias(
        {
            "observe": {"instances": {"timeline": {"built_in": "timeline_every_turn"}}},
            "recommend": {"built_in": "recommendation_component"},
            "flow_map": {
                "active": {
                    "observe": "timeline_make_observation",
                    "recommend": {
                        "recsys_type": "twitter",
                    },
                },
                "fixed_pre": {
                    "observe": {
                        "instance": "episode_observation",
                    },
                    "recommend": {
                        "recsys_type": "reddit",
                    },
                },
            },
        }
    )

    observe_cfg = expanded["observe"]
    recommend_cfg = expanded["recommend"]

    assert observe_cfg["flow_map"]["active"] == "timeline_make_observation"
    assert observe_cfg["flow_map"]["fixed_pre"] == "episode_observation"
    assert recommend_cfg["flows"]["active"]["recsys_type"] == "twitter"
    assert recommend_cfg["flows"]["fixed_pre"]["recsys_type"] == "reddit"
    assert "flow_map" not in expanded


def test_expand_shared_flow_map_alias_merges_with_existing_slot_maps() -> None:
    expanded = _expand_shared_flow_map_alias(
        {
            "recommend": {
                "flows": {
                    "default": {"recsys_type": "reddit"},
                }
            },
            "flow_map": {
                "active": {
                    "recommend": {
                        "recsys_type": "twitter",
                        "max_posts": 20,
                    }
                }
            },
        }
    )

    recommend_cfg = expanded["recommend"]
    assert recommend_cfg["flows"]["default"]["recsys_type"] == "reddit"
    assert recommend_cfg["flows"]["active"]["recsys_type"] == "twitter"
    assert recommend_cfg["flows"]["active"]["max_posts"] == 20
