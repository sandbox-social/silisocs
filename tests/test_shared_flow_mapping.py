from __future__ import annotations

import pytest
from concordia.components import game_master as gm_components  # type: ignore[attr-defined]

from mastodon_sim.environments.gm.shared_flow_game_master import (
    _build_flow_to_component_map,
)


def test_build_flow_map_uses_observe_flow_map() -> None:
    observe_components = {
        "observe__timeline_make_observation": object(),
        "observe__episode_observation": object(),
    }
    resolve_key = gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY

    mapping = _build_flow_to_component_map(
        entity_flow_tags={"alice": "active", "bob": "fixed_pre"},
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
        entity_flow_tags={"alice": "fixed_pre"},
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
            entity_flow_tags={"alice": "active"},
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
            entity_flow_tags={"alice": "active"},
            observe_components=observe_components,
            resolve_component_key=resolve_key,
            observe_slot_cfg={},
            resolve_slot_cfg={"flow_map": {"active": "resolve__generic_action"}},
        )
