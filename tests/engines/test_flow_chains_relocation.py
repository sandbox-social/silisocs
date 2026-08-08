"""Guards for relocating flow_chains off the GameMaster onto the step strategy.

The flow->GM routing topology (``flow_chains``) is engine/scheduling config: the
multi-GM step strategy and the checkpoint replay router consume it, the GameMaster
does not. These tests lock in that the strategy (not the GM) owns it, that custom
``class_path`` strategies still receive it, and that the GM checkpoint no longer
carries it (while keeping the agent->flow tag drift warning).
"""

from __future__ import annotations

import logging
from typing import Any

from omegaconf import OmegaConf

from silisocs.environments.backends.base import BackendApp, app_action
from silisocs.environments.gm import game_master as gm_module
from silisocs.environments.gm.game_master import ComponentGameMaster
from silisocs.runtime.checkpointing import CheckpointRestoreStrategy, run_checkpoint_restores
from silisocs.runtime.construction.engines import _build_step_strategy, build_engine
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.steps import (
    MultiGMStepStrategy,
    SequentialStepStrategy,
)

_MULTI_GM_PATH = "silisocs.simulation_engines.policies.steps.MultiGMStepStrategy"
_SEQUENTIAL_PATH = "silisocs.simulation_engines.policies.steps.SequentialStepStrategy"


def _multi_gm_cfg() -> Any:
    return OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "multi_gm", "params": {}},
                }
            }
        }
    )


def test_build_engine_threads_flow_chains_into_strategy() -> None:
    chains = {"review": ["audit_gm", "main_gm"], "default": ["main_gm"]}
    engine = build_engine(_multi_gm_cfg(), flow_chains=chains)

    assert isinstance(engine, RuntimeEngine)
    assert isinstance(engine.step_strategy, MultiGMStepStrategy)
    assert engine.step_strategy.flow_chains == chains


def test_build_engine_defaults_flow_chains_to_empty_when_omitted() -> None:
    engine = build_engine(_multi_gm_cfg())

    assert isinstance(engine.step_strategy, MultiGMStepStrategy)
    assert engine.step_strategy.flow_chains == {}


def test_build_engine_warns_when_multi_gm_built_without_chains(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="silisocs.runtime.construction.engines"):
        build_engine(_multi_gm_cfg())

    assert any("multi-GM routing strategy" in rec.message for rec in caplog.records)


def test_custom_class_path_multi_gm_strategy_receives_flow_chains() -> None:
    # The blocker case: a custom step strategy supplied via class_path that is a
    # multi-GM router must still receive the resolved chains (it can no longer read
    # them off the default game master).
    chains = {"flow_a": ["gm_a"], "flow_b": ["gm_b"]}
    strategy = _build_step_strategy({"class_path": _MULTI_GM_PATH, "params": {}}, chains)

    assert isinstance(strategy, MultiGMStepStrategy)
    assert strategy.flow_chains == chains


def test_custom_class_path_strategy_without_chains_field_is_unaffected() -> None:
    # A custom strategy that does not declare a flow_chains field (and has no
    # **kwargs) must not be rejected for the injected kwarg.
    strategy = _build_step_strategy(
        {"class_path": _SEQUENTIAL_PATH, "params": {}}, {"flow_a": ["gm_a"]}
    )

    assert isinstance(strategy, SequentialStepStrategy)
    assert not getattr(strategy, "flow_chains", None)


class _ChainsAwareStrategy(CheckpointRestoreStrategy):
    """Declares flow_chains -> must receive the resolved topology."""

    def __init__(self) -> None:
        self.seen: Any = "unset"

    def restore(self, *, flow_chains=None, **_: Any) -> None:
        self.seen = flow_chains


class _LegacyStrategy(CheckpointRestoreStrategy):
    """Pre-dates flow_chains and declares neither it nor **_ -> must not break."""

    def __init__(self) -> None:
        self.called = False

    def restore(  # type: ignore[override]  # deliberately legacy: no flow_chains/**_
        self,
        *,
        game_masters,
        action_events_files,
        checkpoint_step,
        authoritative_gm_names=frozenset(),
    ) -> None:
        del game_masters, action_events_files, checkpoint_step, authoritative_gm_names
        self.called = True


def test_run_checkpoint_restores_passes_flow_chains_to_aware_strategy(tmp_path) -> None:
    strategy = _ChainsAwareStrategy()
    chains = {"social": ["audit_gm", "social_gm"]}
    run_checkpoint_restores(
        game_masters=[_Entity("gm")],
        default_strategy=strategy,
        per_gm_strategies={},
        action_events_files=[tmp_path / "x.jsonl"],
        checkpoint_step=1,
        authoritative_gm_names=frozenset(),
        flow_chains=chains,
    )
    assert strategy.seen == chains


def test_run_checkpoint_restores_tolerates_strategy_without_flow_chains(tmp_path) -> None:
    # A custom strategy whose restore() signature predates flow_chains is called
    # without it instead of raising TypeError.
    strategy = _LegacyStrategy()
    run_checkpoint_restores(
        game_masters=[_Entity("gm")],
        default_strategy=strategy,
        per_gm_strategies={},
        action_events_files=[tmp_path / "x.jsonl"],
        checkpoint_step=1,
        authoritative_gm_names=frozenset(),
        flow_chains={"social": ["gm"]},
    )
    assert strategy.called


class _GenericApp(BackendApp):
    def name(self) -> str:
        return "generic"

    def description(self) -> str:
        return "Generic app"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del agent_names, kwargs

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        del kwargs
        return f"{actor_name} observes"

    @app_action(selectable_name="ACT", description="Act")
    def act(self, agent_name: str) -> str:
        return f"{agent_name} acted"


class _Entity:
    def __init__(self, name: str) -> None:
        self.name = name

    def set_allowed_action_types(self, values: list[str]) -> None:
        del values

    def set_action_output_mode(self, mode: str) -> None:
        del mode


def _build_gm(tmp_path: Any, monkeypatch: Any, *, flow_tags: dict[str, str]) -> ComponentGameMaster:
    monkeypatch.setattr(gm_module, "create_backend_app", lambda **kwargs: _GenericApp())
    return ComponentGameMaster(
        model=object(),
        agents=[_Entity("Alice"), _Entity("Bob")],
        name="gm",
        sim_roles={"Alice": "worker", "Bob": "worker"},
        agent_flow_tags=flow_tags,
        owned_flows=("default",),
        backend_config={
            "backend_type": "resource_market",
            "output_dir": str(tmp_path),
            "enabled_actions": ["ACT"],
            "turn_policy_built_in": "single_action",
            "app_description": "Generic app",
        },
        components={
            "initialize": {"built_in": "app_initialize", "class_path": None, "params": {}},
            "next_acting": {"built_in": "fixed_order"},
            "observe": {"built_in": "app_observation", "params": {"limit": 3}},
            "resolve": {"built_in": "generic_action"},
            "update": {"built_in": "disabled"},
        },
        action_mode="generic",
        tool_calling_mode="none",
        prompt_config={"add_action_count_guidance": False},
    )


def test_game_master_constructor_does_not_accept_flow_chains() -> None:
    import inspect

    params = inspect.signature(ComponentGameMaster.__init__).parameters
    assert "flow_chains" not in params
    assert "agent_flow_tags" in params  # the GM keeps its component-variation surface


def test_game_master_checkpoint_scheduling_omits_flow_chains(tmp_path, monkeypatch) -> None:
    gm = _build_gm(tmp_path, monkeypatch, flow_tags={"Alice": "default", "Bob": "default"})

    scheduling = gm.get_state()["scheduling"]
    assert "flow_chains" not in scheduling
    assert scheduling["agent_flow_tags"] == {"Alice": "default", "Bob": "default"}
    assert scheduling["owned_flows"] == ["default"]


def test_old_checkpoint_with_flow_chains_block_loads_without_chain_drift_warning(
    tmp_path, monkeypatch, caplog
) -> None:
    # Back-compat: an old checkpoint whose scheduling block still carries flow_chains
    # must load cleanly, and the now-removed chains dimension must not warn.
    gm = _build_gm(tmp_path, monkeypatch, flow_tags={"Alice": "default", "Bob": "default"})
    legacy_block = {
        "scheduling": {
            "agent_flow_tags": {"Alice": "default", "Bob": "default"},
            "owned_flows": ["default"],
            "flow_chains": {"default": ["some_other_gm", "another_gm"]},
        }
    }

    with caplog.at_level(logging.WARNING, logger="silisocs.environments.gm.game_master"):
        gm.set_state(legacy_block)

    assert not any("flow_chains" in rec.message for rec in caplog.records)
    assert not any("scheduling" in rec.message for rec in caplog.records)


def test_changed_agent_flow_tag_still_warns_on_resume(tmp_path, monkeypatch, caplog) -> None:
    # The agent->flow tag drift warning (the GM's own component-routing surface) is
    # preserved with intersection-only semantics.
    gm = _build_gm(tmp_path, monkeypatch, flow_tags={"Alice": "default", "Bob": "default"})
    drifted = {
        "scheduling": {
            "agent_flow_tags": {"Alice": "review", "Bob": "default"},  # Alice changed
            "owned_flows": ["default"],
        }
    }

    with caplog.at_level(logging.WARNING, logger="silisocs.environments.gm.game_master"):
        gm.set_state(drifted)

    assert any("agent->flow tags" in rec.message for rec in caplog.records)


def test_roster_change_alone_does_not_warn(tmp_path, monkeypatch, caplog) -> None:
    # Adding/removing an agent (present in only one of saved/live) must NOT warn —
    # only a changed existing tag does.
    gm = _build_gm(tmp_path, monkeypatch, flow_tags={"Alice": "default", "Bob": "default"})
    roster_changed = {
        "scheduling": {
            "agent_flow_tags": {"Alice": "default"},  # Bob absent (removed); no tag changed
            "owned_flows": ["default"],
        }
    }

    with caplog.at_level(logging.WARNING, logger="silisocs.environments.gm.game_master"):
        gm.set_state(roster_changed)

    assert not any("agent->flow tags" in rec.message for rec in caplog.records)
