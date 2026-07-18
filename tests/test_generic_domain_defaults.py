"""Nothing outside the social layer presumes a social backend.

The framework's defaults, its composer, its summaries, and its config errors are
all reachable by a non-social scenario, so each of them has to say something true
for one — or say clearly what to pick instead.
"""
# ruff: noqa: D103

from __future__ import annotations

from typing import Any

import pytest
import yaml
from omegaconf import OmegaConf

from silisocs.evaluations.activity_summary import _summarize_activity
from silisocs.runtime.configuration.validation import RunDirInterpolationCheck
from silisocs.simulation_engines.policies.participation import (
    ActivityMarkovParticipation,
    ActivityProbabilityParticipation,
)
from silisocs.studio.forms import _scenario_relative_files, compose_files

# --- Composer: the components follow the backend --------------------------------

_SOCIAL_DRAFT = {
    "env.yaml": "gm:\n  backend:\n    type: twitter_like\n",
    "sim.yaml": "action_mode: custom\ntool_calling:\n  mode: multi\n",
}


def _env(files: dict[str, str]) -> dict[str, Any]:
    return yaml.safe_load(files["env.yaml"]) or {}


def test_choosing_a_non_social_backend_writes_a_generic_component_pipeline():
    composed = compose_files(dict(_SOCIAL_DRAFT), {"env.gm.backend.type": "resource_market"})
    components = _env(composed)["gm"]["components"]
    assert {role: slot["built_in"] for role, slot in components.items()} == {
        "initialize": "app_initialize",
        "observe": "app_observation",
        "update": "none",
        "action_prompt": "default",
        "resolve": "tool_calling",
    }


def test_the_generic_pipeline_clears_the_social_groups_params_rather_than_emptying_them():
    """``params: {}`` cannot clear a merged sibling; ``params: null`` replaces the node."""
    composed = compose_files(dict(_SOCIAL_DRAFT), {"env.gm.backend.type": "resource_market"})
    env = _env(composed)
    assert all(slot["params"] is None for slot in env["gm"]["components"].values())

    # The real test: merged over the social env group it replaces, the social
    # params (graph/recsys/timeline) must be gone, not merely overlaid.
    merged = OmegaConf.merge(
        OmegaConf.load("src/silisocs/conf/env/twitter_like.yaml"), OmegaConf.create(env)
    )
    for role in ("initialize", "observe", "update"):
        assert merged.gm.components[role].params is None


def test_a_non_social_backend_describes_itself_through_its_own_catalog():
    composed = compose_files(dict(_SOCIAL_DRAFT), {"env.gm.backend.type": "resource_market"})
    assert yaml.safe_load(composed["sim.yaml"])["action_mode"] == "generic"


def test_resolve_follows_the_drafts_tool_calling_mode():
    # Without tool-calling, a generic backend is driven in generic action mode, so
    # the resolver must be generic_action — parsed_action cannot read that output
    # (it wants an ACTION TYPE block and a SocialBackendApp-only dispatch).
    draft = {**_SOCIAL_DRAFT, "sim.yaml": "tool_calling:\n  mode: none\n"}
    composed = compose_files(draft, {"env.gm.backend.type": "resource_market"})
    assert _env(composed)["gm"]["components"]["resolve"]["built_in"] == "generic_action"


def test_switching_back_to_a_social_backend_drops_the_generic_pipeline():
    generic = compose_files(dict(_SOCIAL_DRAFT), {"env.gm.backend.type": "resource_market"})
    social = compose_files(dict(generic), {"env.gm.backend.type": "twitter_like"})
    assert "components" not in _env(social).get("gm", {})


def test_a_backend_this_studio_cannot_import_is_left_alone():
    composed = compose_files(
        dict(_SOCIAL_DRAFT),
        {"env.gm.backend.type": "custom", "env.gm.backend.class_path": "nope.NotReal"},
    )
    assert "components" not in _env(composed).get("gm", {})


def test_unrelated_edits_do_not_rewrite_the_component_pipeline():
    composed = compose_files(dict(_SOCIAL_DRAFT), {"sim.action_mode": "generic"})
    assert composed["env.yaml"] == _SOCIAL_DRAFT["env.yaml"]  # byte-for-byte


# --- Composer: a scenario is not only the default-variant shape -----------------


def test_group_variant_scenario_files_are_listed(tmp_path):
    conf = tmp_path / "conf"
    (conf / "world").mkdir(parents=True)
    (conf / "env").mkdir()
    (conf / "world" / "market.yaml").write_text("num_agents: 4\n")
    (conf / "env" / "market.yaml").write_text("gm: {}\n")
    (conf / "sim.yaml").write_text("action_mode: generic\n")
    assert _scenario_relative_files(conf) == ["sim.yaml", "world/market.yaml", "env/market.yaml"]


def test_the_shipped_non_social_scenario_is_visible_to_the_composer():
    from pathlib import Path

    files = _scenario_relative_files(Path("scenarios/resource_market/conf"))
    assert "world/resource_market.yaml" in files and "env/resource_market.yaml" in files


def test_editing_a_variant_scenario_writes_to_its_own_group_file():
    """Writing world/default.yaml would add a second option to the world group."""
    files = {"world/resource_market.yaml": "# @package _global_\nnum_agents: 4\n"}
    composed = compose_files(files, {"world.num_agents": 9})
    assert "world/default.yaml" not in composed
    assert yaml.safe_load(composed["world/resource_market.yaml"])["num_agents"] == 9


def test_a_new_group_file_still_gets_its_package_directive():
    composed = compose_files({}, {"world.num_agents": 3})
    assert composed["world/default.yaml"].startswith("# @package _global_")


# --- Summaries: count what happened, not what a social run would do -------------


def test_activity_summary_counts_whatever_the_backend_committed():
    events = [
        {"label": "buy_listing", "source_user": "Ada", "episode": 1},
        {"label": "buy_listing", "source_user": "Boris", "episode": 1},
        {"label": "produce_resource", "source_user": "Ada", "episode": 2},
        {"label": "initialize", "source_user": "system", "episode": 0},
    ]
    summary = _summarize_activity(events)
    assert summary["action_counts"] == {"buy_listing": 2, "produce_resource": 1}
    assert summary["unique_users"] == ["Ada", "Boris"]


def test_activity_summary_still_counts_social_labels():
    events = [{"label": label, "source_user": "alice"} for label in ("post", "like", "post")]
    assert _summarize_activity(events)["action_counts"] == {"post": 2, "like": 1}


# --- Participation: a default that silently idles agents says so ----------------


def _agents() -> list[str]:
    return ["Ada", "Boris"]


@pytest.mark.parametrize(
    "policy_class", [ActivityProbabilityParticipation, ActivityMarkovParticipation]
)
def test_rates_that_match_no_agent_warn_once(policy_class, caplog):
    policy = policy_class(
        activity_transition_rates={"user": {"inactive_to_active": 0.9}},
        sim_roles={"Ada": "trader", "Boris": "trader"},
    )
    for step in range(3):
        policy.participating_agents(agent_names=_agents(), step_index=step, seed=1)
    assert caplog.text.count("no activity_transition_rates entry matches") == 1
    assert "trader" in caplog.text  # names the roles it did see
    assert "participation.built_in: all" in caplog.text  # and the fix


@pytest.mark.parametrize(
    "policy_class", [ActivityProbabilityParticipation, ActivityMarkovParticipation]
)
def test_matching_rates_do_not_warn(policy_class, caplog):
    policy = policy_class(
        activity_transition_rates={"trader": {"inactive_to_active": 0.9}},
        sim_roles={"Ada": "trader", "Boris": "trader"},
    )
    policy.participating_agents(agent_names=_agents(), step_index=0, seed=1)
    assert "activity_transition_rates" not in caplog.text


def test_an_explicit_probability_is_not_a_fallback(caplog):
    policy = ActivityProbabilityParticipation(active_probability=0.5)
    policy.participating_agents(agent_names=_agents(), step_index=0, seed=1)
    assert "activity_transition_rates" not in caplog.text


def test_the_warning_never_changes_who_participates():
    kwargs = {
        "activity_transition_rates": {"user": {"inactive_to_active": 0.9}},
        "sim_roles": {"Ada": "trader"},
    }
    quiet = ActivityProbabilityParticipation(**kwargs, _warned_default_rate=True)
    loud = ActivityProbabilityParticipation(**kwargs)
    for step in range(5):
        assert quiet.participating_agents(
            agent_names=_agents(), step_index=step, seed=7
        ) == loud.participating_agents(agent_names=_agents(), step_index=step, seed=7)


# --- Config errors: the Hydra sharp edge explains itself ------------------------


def _cfg(**root: Any) -> Any:
    return OmegaConf.create(
        {
            "hydra": {
                "run": {"dir": "outputs/${scenario_name}/${jobname_format}"},
                "output_subdir": "configs/${jobname_format}",
            },
            **root,
        }
    )


def test_a_world_group_missing_the_universal_params_explains_the_replacement_rule():
    with pytest.raises(ValueError) as excinfo:
        RunDirInterpolationCheck().on_run_start(_cfg(scenario_name="demo"))
    message = str(excinfo.value)
    assert "jobname_format" in message
    assert "REPLACES the base world config group" in message
    assert "# @package _global_" in message


def test_a_resolvable_run_dir_is_a_no_op():
    RunDirInterpolationCheck().on_run_start(_cfg(scenario_name="demo", jobname_format="job"))


def test_every_bundled_scenario_resolves_its_run_dir():
    """The check must not fire for the configs the repo ships."""
    from pathlib import Path

    from hydra import compose, initialize_config_dir

    conf = Path("src/silisocs/conf").resolve()
    with initialize_config_dir(config_dir=str(conf), version_base=None):
        cfg = compose(config_name="experiment", return_hydra_config=True)
        RunDirInterpolationCheck().on_run_start(cfg)
