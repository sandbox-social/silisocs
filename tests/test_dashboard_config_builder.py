"""Unit tests for the dashboard config builder (dashboard/config_builder.py).

These functions were previously inlined in the Streamlit script reading
``st.session_state`` directly, so they could not be imported or tested without
launching the dashboard. Now parameterized by an explicit ``state`` mapping, the
config-shaping logic is covered here with plain dicts.
"""

from __future__ import annotations

from importlib import resources

import yaml

from silisocs.dashboard.config_builder import (
    _as_int,
    build_hydra_overrides,
    build_world_config,
    collect_activity_rates,
    participation_sim_data,
    world_config_warnings,
)
from silisocs.dashboard.defaults import (
    default_initial_observations,
    default_jobname_format,
    default_persona_defaults,
)


def test_as_int_tolerates_hydra_interpolation_and_junk():
    assert _as_int("5", 0) == 5
    assert _as_int(7, 0) == 7
    assert _as_int(3.9, 0) == 3
    assert _as_int("${num_agents}", 42) == 42  # unresolved interpolation -> default
    assert _as_int("", 9) == 9
    assert _as_int("nope", 11) == 11
    assert _as_int("4.6", 0) == 4  # float-string coerces via float()


def test_build_hydra_overrides_serializes_each_type():
    overrides = build_hydra_overrides(
        sim={"a": None, "b": True, "c": [1, 2], "d": [], "e": "has space", "f": 3},
        env={"backend.x": False},
        backend_group="twitter_like",
        world={},
        eval_cfg={"probes.enabled": True},
    )
    assert "sim.a=null" in overrides
    assert "sim.b=true" in overrides
    assert "sim.c=[1,2]" in overrides
    assert "sim.d=[]" in overrides
    assert 'sim.e="has space"' in overrides
    assert "sim.f=3" in overrides
    assert "env=twitter_like" in overrides
    assert "env.backend.x=false" in overrides
    assert "eval.probes.enabled=true" in overrides
    # env group selector precedes the env.* keys.
    assert overrides.index("env=twitter_like") < overrides.index("env.backend.x=false")


def test_build_hydra_overrides_world_routes_gm_to_env_and_skips_none():
    overrides = build_hydra_overrides(
        sim={},
        env={},
        backend_group="reddit_like",
        world={"gm.components.x": "y", "num_agents": 10, "skip": None, "blank": ""},
    )
    assert "env.gm.components.x=y" in overrides  # gm.* routes to env
    assert "sim.num_agents=10" in overrides  # non-gm routes to sim
    assert not any(o.startswith("sim.skip") for o in overrides)  # None dropped
    assert not any(o.startswith("sim.blank") for o in overrides)  # empty string dropped


def test_participation_defaults_by_backend_and_reads_rates():
    state = {
        "_agent_classes": [{"name": "influencer", "sim_role_name": "influencer"}],
        "act_influencer_i2a": 0.7,
        "act_influencer_a2i": 0.2,
    }
    social = participation_sim_data(state, "twitter_like")
    assert social["engine.participation.built_in"] == "activity_probability"
    rates = social["engine.participation.params.activity_transition_rates"]
    assert rates == {"influencer": {"inactive_to_active": 0.7, "active_to_inactive": 0.2}}

    # A non-social backend defaults to deterministic "all".
    nonsocial = participation_sim_data({"_agent_classes": []}, "resource_market")
    assert nonsocial["engine.participation.built_in"] == "all"
    # No classes -> no rates key.
    assert "engine.participation.params.activity_transition_rates" not in nonsocial


def test_collect_activity_rates_falls_back_to_name_and_default():
    state = {"_agent_classes": [{"name": "bot"}]}  # no sim_role_name, no sliders
    assert collect_activity_rates(state) == {
        "bot": {"inactive_to_active": 0.3, "active_to_inactive": 0.3}
    }


def test_build_world_config_assembles_classes_probes_and_fixed_actions():
    state = {
        "scenario_name_edit": "demo",
        "setting_name": "Town",
        "setting_background": "line one\n\nline two",
        "event_name": "Election",
        "event_context": "context",
        "shared_memories_edit": "mem a\nmem b",
        "_agent_classes": [
            {
                "name": "influencer",
                "count": 3,
                "class_path": "pkg.Cls",
                "sim_role_name": "influencer",
                "data": {"source": "x"},
                "fixed_action": {"enabled": True, "episodes": {}},
            }
        ],
        "fixed_action_sets_inline_yaml": "seedset:\n  - post: hi",
        "_probe_items": [{"probe_name": "p1", "probe_type": "FreeTextProbe", "probe_data": {}}],
        "probes_enabled": True,
        "probe_start": 2,
        "probe_interval": 3,
    }
    cfg = build_world_config(state)
    assert cfg["scenario_name"] == "demo"
    assert cfg["setting"]["background"] == ["line one", "line two"]  # blank lines dropped
    assert cfg["shared_memories"] == ["mem a", "mem b"]
    inf = cfg["persona_pipeline"]["classes"]["influencer"]
    assert inf["count"] == 3 and inf["class_path"] == "pkg.Cls"
    assert inf["fixed_action"]["enabled"] is True
    assert cfg["probes"]["deployment"]["start_step"] == 2
    assert cfg["probes"]["deployment"]["every_n_steps"] == 3
    assert cfg["probes"]["probes"]["p1"]["probe_data"]["name"] == "p1"  # name backfilled
    assert cfg["fixed_action_sets"]["inline"] == {"seedset": [{"post": "hi"}]}


def test_dashboard_defaults_match_packaged_conf():
    """Drift canary: dashboard defaults are read from conf/, never re-typed literals."""
    world = yaml.safe_load(
        resources.files("silisocs").joinpath("conf", "world", "default.yaml").read_text()
    )
    agents = yaml.safe_load(
        resources.files("silisocs").joinpath("conf", "agents", "default.yaml").read_text()
    )
    assert default_jobname_format() == world["jobname_format"]
    assert default_persona_defaults() == agents["persona_pipeline"]["defaults"]["params"]
    assert default_initial_observations() == agents["initial_observations"]
    # The assembled world config carries the base-config values through unchanged.
    cfg = build_world_config({})
    assert cfg["jobname_format"] == world["jobname_format"]
    assert cfg["initial_observations"] == agents["initial_observations"]
    assert (
        cfg["persona_pipeline"]["defaults"]["params"]
        == agents["persona_pipeline"]["defaults"]["params"]
    )


def test_build_world_config_drops_disabled_fixed_action():
    state = {
        "_agent_classes": [
            {"name": "c", "count": 1, "fixed_action": {"enabled": False, "episodes": {}}}
        ]
    }
    assert "fixed_action" not in build_world_config(state)["persona_pipeline"]["classes"]["c"]


def test_world_config_warnings_flags_missing_and_mismatch():
    warnings = world_config_warnings(
        {"num_agents": 5},
        [{"name": "a", "class_path": "", "data": {}}, {"name": "b", "count": 2}],
    )
    joined = " ".join(warnings)
    assert "no agent class set" in joined
    assert "no data source" in joined
    # class counts (2) != declared num_agents (5)
    assert "sum to 2" in joined and "num_agents is 5" in joined

    assert world_config_warnings({}, []) == [
        "No agent classes defined. Add at least one in the Agent Classes tab."
    ]
