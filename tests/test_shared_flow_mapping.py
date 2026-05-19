from __future__ import annotations

import pytest

from silisocs.environments.gm.shared_flow_game_master import (
    _build_flow_to_component_map,
)


def test_build_flow_map_uses_observe_flow_map() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
            "observe__episode_observation": object(),
        },
        "resolve": {"resolve": object()},
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
            "resolve": {},
        },
    )

    assert mapping["active"]["observe"] == "observe__timeline_make_observation"
    assert mapping["fixed_pre"]["observe"] == "observe__episode_observation"
    assert mapping["default"]["observe"] == "observe__timeline_make_observation"
    assert mapping["active"]["resolve"] == "resolve"


def test_build_flow_map_routes_all_slots() -> None:
    components_by_role = {
        "initialize": {"initialize": object(), "initialize__custom": object()},
        "action_prompt": {"action_prompt": object(), "action_prompt__active": object()},
        "resolve": {"resolve": object(), "resolve__generic": object()},
        "update": {"update": object(), "update__recs": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "active"},
        components_by_role=components_by_role,
        slot_cfg_by_role={
            "initialize": {"flow_map": {"active": "custom"}},
            "action_prompt": {"flow_map": {"active": "active"}},
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
        "resolve": {"resolve": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "fixed_pre"},
        components_by_role=components_by_role,
        slot_cfg_by_role={
            "observe": {"flow_map": {"fixed_pre": "episode_observation"}},
            "resolve": {},
        },
    )

    assert mapping["fixed_pre"]["observe"] == "observe__episode_observation"


def test_build_flow_map_raises_on_invalid_mapping() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
        },
        "resolve": {"resolve": object()},
    }

    with pytest.raises(ValueError, match="Invalid observe flow_map"):
        _build_flow_to_component_map(
            agent_flow_tags={"alice": "active"},
            components_by_role=components_by_role,
            slot_cfg_by_role={
                "observe": {"flow_map": {"active": "observe__does_not_exist"}},
                "resolve": {},
            },
        )


def test_build_flow_map_uses_default_component_when_no_flow_map() -> None:
    components_by_role = {
        "observe": {
            "observe__timeline_make_observation": object(),
        },
        "resolve": {"resolve": object()},
    }

    mapping = _build_flow_to_component_map(
        agent_flow_tags={"alice": "active"},
        components_by_role=components_by_role,
        slot_cfg_by_role={"observe": {}, "resolve": {}},
    )

    assert mapping["active"] == {
        "observe": "observe__timeline_make_observation",
        "resolve": "resolve",
    }


def test_old_flow_field_override_is_rejected() -> None:
    from silisocs.environments.gm.components.factory import build_observe_component

    with pytest.raises(ValueError, match="`flows` field overrides have been removed"):
        build_observe_component(
            {
                "built_in": "timeline_every_turn",
                "flows": {"active": {"timeline_mode": "pure_recsys"}},
            },
            model=object(),
            agent_names=["alice"],
            sm_app=object(),
        )
