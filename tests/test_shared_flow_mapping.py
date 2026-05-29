from __future__ import annotations

import pytest

from silisocs.environments.gm.game_master import (
    _build_flow_to_component_map,
)


def test_build_flow_map_uses_observe_flow_map() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
            "observe__episode_observation": object(),
        },
        "initialize": {"initialize": object()},
        "next_acting": {"next_acting": object()},
        "action_prompt": {"action_prompt": object()},
        "resolve": {"resolve": object()},
        "update": {"update": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "active", "bob": "fixed_pre"},
        components_by_role=components_by_role,
        slot_cfg_by_role={
            "observe": {
                "flow_map": {
                    "active": "observe__timeline_make_observation",
                    "fixed_pre": "observe__episode_observation",
                    "default": "observe__timeline_make_observation",
                }
            },
            "initialize": {},
            "next_acting": {},
            "action_prompt": {},
            "resolve": {},
            "update": {},
        },
    )

    assert mapping["active"]["observe"] == "observe__timeline_make_observation"
    assert mapping["fixed_pre"]["observe"] == "observe__episode_observation"
    assert mapping["default"]["observe"] == "observe__timeline_make_observation"
    assert mapping["active"]["resolve"] == "resolve"


def test_build_flow_map_routes_all_slots() -> None:
    components_by_role = {
        "initialize": {"initialize": object(), "initialize__custom": object()},
        "next_acting": {"next_acting": object()},
        "action_prompt": {"action_prompt": object(), "action_prompt__active": object()},
        "observe": {"observe": object()},
        "resolve": {"resolve": object(), "resolve__generic": object()},
        "update": {"update": object(), "update__recs": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "active"},
        components_by_role=components_by_role,
        slot_cfg_by_role={
            "initialize": {"flow_map": {"active": "custom"}},
            "next_acting": {},
            "action_prompt": {"flow_map": {"active": "active"}},
            "observe": {},
            "resolve": {"flow_map": {"active": "generic"}},
            "update": {"flow_map": {"active": "recs"}},
        },
    )

    assert mapping["active"]["initialize"] == "initialize__custom"
    assert mapping["active"]["action_prompt"] == "action_prompt__active"
    assert mapping["active"]["resolve"] == "resolve__generic"
    assert mapping["active"]["update"] == "update__recs"


def test_build_flow_map_accepts_shorthand_component_names() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
            "observe__episode_observation": object(),
        },
        "initialize": {"initialize": object()},
        "next_acting": {"next_acting": object()},
        "action_prompt": {"action_prompt": object()},
        "resolve": {"resolve": object()},
        "update": {"update": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "fixed_pre"},
        components_by_role=components_by_role,
        slot_cfg_by_role={
            "observe": {"flow_map": {"fixed_pre": "episode_observation"}},
            "initialize": {},
            "next_acting": {},
            "action_prompt": {},
            "resolve": {},
            "update": {},
        },
    )

    assert mapping["fixed_pre"]["observe"] == "observe__episode_observation"


def test_build_flow_map_raises_on_invalid_mapping() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
        },
        "initialize": {"initialize": object()},
        "next_acting": {"next_acting": object()},
        "action_prompt": {"action_prompt": object()},
        "resolve": {"resolve": object()},
        "update": {"update": object()},
    }

    with pytest.raises(ValueError, match="Invalid observe flow_map"):
        _build_flow_to_component_map(
            agent_flow_tags={"alice": "active"},
            components_by_role=components_by_role,
            slot_cfg_by_role={
                "observe": {"flow_map": {"active": "observe__does_not_exist"}},
                "initialize": {},
                "next_acting": {},
                "action_prompt": {},
                "resolve": {},
                "update": {},
            },
        )


def test_build_flow_map_uses_default_component_when_no_flow_map() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
        },
        "initialize": {"initialize": object()},
        "next_acting": {"next_acting": object()},
        "action_prompt": {"action_prompt": object()},
        "resolve": {"resolve": object()},
        "update": {"update": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "active"},
        components_by_role=components_by_role,
        slot_cfg_by_role={
            "initialize": {},
            "next_acting": {},
            "action_prompt": {},
            "observe": {},
            "resolve": {},
            "update": {},
        },
    )

    assert mapping["active"]["observe"] == "observe__timeline_make_observation"
    assert mapping["active"]["resolve"] == "resolve"
