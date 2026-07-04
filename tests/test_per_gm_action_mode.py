"""Per-GM action_mode + tool_calling override (FEATURE 3).

Today ``sim.action_mode`` and ``sim.tool_calling.mode`` are read once into the
RuntimeProjection and applied to every GM. These tests cover per-GM overrides on
``env.gm_orchestration.gms[*]`` (and ``env.gm`` for the single default GM), with
fallback to the global projection value when unset.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.configuration.projection import (
    _gm_has_tool_calling_override,
    validate_resolve_tool_calling,
)
from silisocs.runtime.execution.session import build_game_masters


def _base_cfg() -> Any:
    return OmegaConf.create(
        {
            "sim": {
                "action_mode": "custom",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": True},
                "initialization": {
                    "agents": {"built_in": "default", "class_path": None, "params": {}},
                    "game_masters": {"built_in": "default", "class_path": None, "params": {}},
                    "simulation": {"built_in": "none", "class_path": None, "params": {}},
                },
            },
            "agents": {
                "shared_memories": ["shared memory"],
                "persona_pipeline": {},
            },
            "env": {
                "gm": {
                    "backend": {
                        "type": "twitter_like",
                        "class_path": None,
                        "params": {},
                        "enabled_actions": None,
                    },
                    "class_path": "silisocs.environments.gm.game_master.ComponentGameMaster",
                    "name": "social-media_game-master",
                    "components": {
                        "initialize": {
                            "built_in": "social_media",
                            "params": {
                                "graph": {
                                    "fully_connected_targets": [],
                                    "base_followership_probability": 0.3,
                                }
                            },
                        },
                        "next_acting": {"built_in": "all_agents"},
                        "observe": {
                            "built_in": "timeline_every_turn",
                            "params": {"timeline_mode": "follower_chronological"},
                        },
                        "resolve": {"built_in": "parsed_action"},
                        "action_prompt": {
                            "built_in": "default",
                            "params": {
                                "action_prompt": "Act on timeline.",
                                "output_style": "",
                            },
                        },
                    },
                },
            },
        }
    )


def _components(base: Any) -> dict[str, Any]:
    return cast(dict[str, Any], OmegaConf.to_container(base.env.gm.components, resolve=True))


def _backend(base: Any) -> dict[str, Any]:
    return cast(dict[str, Any], OmegaConf.to_container(base.env.gm.backend, resolve=True))


def _gms_by_name(cfg: Any) -> dict[str, Any]:
    return {gm.params["name"]: gm for gm in build_game_masters(cfg)}


# ---------------------------------------------------------------------------
# (1) Per-GM action_mode differs across two GMs
# ---------------------------------------------------------------------------
def test_per_gm_action_mode_differs() -> None:
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    # gm0: generic action mode + generic resolve, tool_calling none.
    gm0_components = dict(components)
    gm0_components["resolve"] = {"built_in": "generic_action"}
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm0",
                "sequence": 0,
                "action_mode": "generic",
                "backend": backend,
                "components": gm0_components,
            },
            # gm1 omits action_mode -> inherits the global custom mode.
            {
                "gm_name": "gm1",
                "sequence": 1,
                "backend": backend,
                "components": components,
            },
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    by_name = _gms_by_name(cfg)
    assert by_name["gm0"].params["action_mode"] == "generic"
    assert by_name["gm1"].params["action_mode"] == "custom"
    # generic mode short-circuits the custom-text template build to "".
    assert by_name["gm0"].params["action_prompt_template"] == ""
    assert by_name["gm1"].params["action_prompt_template"] != ""


# ---------------------------------------------------------------------------
# (2) Per-GM tool_calling differs across two GMs (scalar spelling)
# ---------------------------------------------------------------------------
def test_per_gm_tool_calling_mode_differs() -> None:
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    gm0_components = dict(components)
    gm0_components["resolve"] = {"built_in": "tool_calling"}
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm0",
                "sequence": 0,
                "tool_calling": "single",
                "backend": backend,
                "components": gm0_components,
            },
            # gm1 omits tool_calling -> inherits the global none mode.
            {
                "gm_name": "gm1",
                "sequence": 1,
                "backend": backend,
                "components": components,
            },
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    by_name = _gms_by_name(cfg)
    assert by_name["gm0"].params["tool_calling_mode"] == "single"
    assert by_name["gm1"].params["tool_calling_mode"] == "none"


# ---------------------------------------------------------------------------
# The retired per-GM spellings each raise a targeted migration error pointing at
# the surviving scalar ``tool_calling: <mode>`` form.
# ---------------------------------------------------------------------------
def test_per_gm_tool_calling_mode_key_is_retired() -> None:
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm0",
                "sequence": 0,
                "tool_calling_mode": "single",
                "backend": backend,
                "components": components,
            }
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    with pytest.raises(ValueError, match="tool_calling_mode is retired"):
        build_game_masters(cfg)


@pytest.mark.parametrize("mapping", [{"mode": "single"}, {"foo": "bar"}, {}])
def test_per_gm_tool_calling_mapping_form_is_retired(mapping: dict) -> None:
    # The ``tool_calling: {mode: ...}`` block form is retired regardless of contents;
    # a per-GM override must be the scalar ``tool_calling: <mode>``.
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm0",
                "sequence": 0,
                "tool_calling": mapping,
                "backend": backend,
                "components": components,
            }
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    with pytest.raises(ValueError, match="block form is retired"):
        build_game_masters(cfg)


@pytest.mark.parametrize("mapping", [{}, {"foo": "bar"}, {"mode": "single"}])
def test_gm_has_tool_calling_override_true_for_any_mapping(mapping: dict) -> None:
    # A tool_calling mapping (a retired form) is still treated as an override attempt:
    # _gm_has_tool_calling_override returns True so the resolve slot is skipped in the
    # global projection check and the targeted migration error surfaces per-GM.
    cfg = OmegaConf.create({"env": {"gm": {"tool_calling": mapping}}})
    assert _gm_has_tool_calling_override(cfg, "env.gm") is True


def test_gm_has_tool_calling_override_false_when_absent() -> None:
    # No tool_calling key at all -> not an override; the global mode governs.
    cfg = OmegaConf.create({"env": {"gm": {}}})
    assert _gm_has_tool_calling_override(cfg, "env.gm") is False


# ---------------------------------------------------------------------------
# (3) Per-GM resolve-vs-tool-calling validation fires per-GM, both directions
# ---------------------------------------------------------------------------
def test_per_gm_validation_single_with_parsed_action_raises() -> None:
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    # gm0: tool_calling single but resolve parsed_action -> mismatch naming gm0.
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm0",
                "sequence": 0,
                "tool_calling": "single",
                "backend": backend,
                "components": components,  # resolve parsed_action
            },
            {
                "gm_name": "gm1",
                "sequence": 1,
                "backend": backend,
                "components": components,
            },
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    with pytest.raises(ValueError, match=r"gms\[0\].components.resolve.built_in"):
        build_game_masters(cfg)


def test_per_gm_validation_none_with_tool_calling_raises() -> None:
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    gm0_components = dict(components)
    gm0_components["resolve"] = {"built_in": "tool_calling"}
    # gm0: resolve tool_calling but no tool_calling override (inherits global none).
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm0",
                "sequence": 0,
                "backend": backend,
                "components": gm0_components,
            },
            {
                "gm_name": "gm1",
                "sequence": 1,
                "backend": backend,
                "components": components,
            },
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    with pytest.raises(ValueError, match="Tool-calling mode must match"):
        build_game_masters(cfg)


# ---------------------------------------------------------------------------
# (4) Unset everywhere -> projection fallback for all GMs
# ---------------------------------------------------------------------------
def test_unset_falls_back_to_projection_for_all_gms() -> None:
    cfg = _base_cfg()
    components = _components(cfg)
    backend = _backend(cfg)
    cfg.env.gm_orchestration = {
        "gms": [
            {"gm_name": "gm0", "sequence": 0, "backend": backend, "components": components},
            {"gm_name": "gm1", "sequence": 1, "backend": backend, "components": components},
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    by_name = _gms_by_name(cfg)
    for gm in by_name.values():
        assert gm.params["action_mode"] == "custom"
        assert gm.params["tool_calling_mode"] == "none"


def test_single_default_gm_unset_falls_back_to_projection() -> None:
    cfg = _base_cfg()
    [gm] = build_game_masters(cfg)
    assert gm.params["action_mode"] == "custom"
    assert gm.params["tool_calling_mode"] == "none"


# ---------------------------------------------------------------------------
# (5) Default GM env.gm.action_mode/tool_calling_mode override wins over sim.*
# ---------------------------------------------------------------------------
def test_default_gm_override_wins_over_sim() -> None:
    cfg = _base_cfg()
    # sim.action_mode is custom globally; override the single default GM to generic.
    cfg.env.gm.action_mode = "generic"
    cfg.env.gm.components.resolve = {"built_in": "generic_action"}

    [gm] = build_game_masters(cfg)
    assert gm.params["action_mode"] == "generic"
    assert gm.params["action_prompt_template"] == ""


def test_default_gm_tool_calling_override_wins_over_sim() -> None:
    cfg = _base_cfg()
    # sim.tool_calling.mode is none globally; override the default GM to single.
    cfg.env.gm.tool_calling = "single"
    cfg.env.gm.components.resolve = {"built_in": "tool_calling"}

    [gm] = build_game_masters(cfg)
    assert gm.params["tool_calling_mode"] == "single"


# ---------------------------------------------------------------------------
# Unit test of validate_resolve_tool_calling
# ---------------------------------------------------------------------------
def test_validate_resolve_tool_calling_unit() -> None:
    with pytest.raises(ValueError, match="Tool-calling mode must match"):
        validate_resolve_tool_calling(
            tool_calling_mode="single",
            resolve_built_in="parsed_action",
            resolve_path="p",
        )
    with pytest.raises(ValueError, match="Tool-calling mode must match"):
        validate_resolve_tool_calling(
            tool_calling_mode="none",
            resolve_built_in="tool_calling",
            resolve_path="p",
        )
    # Aligned pairs pass.
    validate_resolve_tool_calling(
        tool_calling_mode="single",
        resolve_built_in="tool_calling",
        resolve_path="p",
    )
    validate_resolve_tool_calling(
        tool_calling_mode="none",
        resolve_built_in="parsed_action",
        resolve_path="p",
    )
