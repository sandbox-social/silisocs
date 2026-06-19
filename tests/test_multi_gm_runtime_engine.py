"""Tests for the native multi-GM engine preset."""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.base_engines import MultiGMRuntimeEngine


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.observations: list[str] = []
        self.actions: list[str] = []

    def observe(self, observation: str) -> None:
        self.observations.append(observation)

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        self.actions.append(action_spec.prompt)
        return ActionOutput.from_text(f"{self.name}:{action_spec.prompt}")


class _GameMaster:
    def __init__(
        self,
        *,
        name: str,
        selected: list[str],
        agent_flow_tags: dict[str, str] | None = None,
        flow_chains: dict[str, list[str]] | None = None,
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags or {}
        self.flow_chains = flow_chains or {}
        self.resolved: list[tuple[str, ActionOutput]] = []
        self.events: list[str] = []

    def update(self, *, step: int, agents: list[_Agent], context: object | None = None) -> None:
        del context
        self.events.append(f"update:{step}:{','.join(agent.name for agent in agents)}")

    def acting_agents(self, candidate_agents: list[_Agent]) -> list[str]:
        available = {agent.name for agent in candidate_agents}
        return [name for name in self.selected if name in available]

    def action_prompt(self, agent_name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{agent_name}", output_type=OutputType.TEXT)

    def make_observation(self, agent_name: str) -> str:
        return f"obs:{self.name}:{agent_name}"

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        self.resolved.append((agent_name, action))
        return f"resolved:{self.name}:{agent_name}"


def test_multi_gm_runtime_engine_initializes_without_legacy_introspection() -> None:
    engine = MultiGMRuntimeEngine()

    assert not hasattr(engine, "_gm_sequence_names")
    assert not hasattr(engine, "_agent_gm_map")
    assert not hasattr(engine, "get_agent_gms")
    assert not hasattr(engine, "detect_gm_conflicts")


def test_multi_gm_step_strategy_routes_agents_through_flow_chains() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {
                        "built_in": "multi_gm",
                        "params": {"flow_order": ["pre", "default"]},
                    },
                }
            }
        }
    )
    engine = MultiGMRuntimeEngine(config=cfg)
    alice = _Agent("Alice")
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=["Alice", "Bob"],
        agent_flow_tags={"Alice": "pre", "Bob": "default"},
        flow_chains={"pre": ["pre_gm"], "default": ["main_gm"]},
    )
    pre_gm = _GameMaster(name="pre_gm", selected=["Alice"])
    main_gm = _GameMaster(name="main_gm", selected=["Bob"])

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, pre_gm, main_gm],
        agents=[alice, bob],
        verbose=False,
    )

    assert result.active_agent_names == ("Alice", "Bob")
    assert [name for name, _ in pre_gm.resolved] == ["Alice"]
    assert [name for name, _ in main_gm.resolved] == ["Bob"]
    assert primary.events == ["update:0:Alice,Bob"]
    assert pre_gm.events == ["update:0:Alice,Bob"]
    assert main_gm.events == ["update:0:Alice,Bob"]
    assert primary.resolved == []
    assert alice.observations == ["obs:pre_gm:Alice", "resolved:pre_gm:Alice"]
    assert bob.observations == ["obs:main_gm:Bob", "resolved:main_gm:Bob"]


def test_multi_gm_step_strategy_runs_shared_agent_through_serial_chain() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "multi_gm", "params": {"flow_order": ["review"]}},
                }
            }
        }
    )
    engine = MultiGMRuntimeEngine(config=cfg)
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "review"},
        flow_chains={"review": ["audit_gm", "main_gm"]},
    )
    audit_gm = _GameMaster(name="audit_gm", selected=["Alice"])
    main_gm = _GameMaster(name="main_gm", selected=["Alice"])

    result = engine.run_step(
        step_index=3,
        game_masters=[primary, audit_gm, main_gm],
        agents=[alice],
        verbose=False,
    )

    assert result.active_agent_names == ("Alice",)
    assert [name for name, _ in audit_gm.resolved] == ["Alice"]
    assert [name for name, _ in main_gm.resolved] == ["Alice"]
    assert alice.actions == ["audit_gm:Alice", "main_gm:Alice"]
    assert primary.events == ["update:3:Alice"]
    assert audit_gm.events == ["update:3:Alice"]
    assert main_gm.events == ["update:3:Alice"]


def test_multi_gm_step_strategy_uses_materialized_agent_flow_tags() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {
                        "built_in": "multi_gm",
                        "params": {
                            "flow_order": ["override"],
                        },
                    },
                }
            }
        }
    )
    engine = MultiGMRuntimeEngine(config=cfg)
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=["Bob"],
        agent_flow_tags={"Bob": "override"},
        flow_chains={"override": ["override_gm"], "default": ["default_gm"]},
    )
    override_gm = _GameMaster(name="override_gm", selected=["Bob"])
    default_gm = _GameMaster(name="default_gm", selected=["Bob"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, override_gm, default_gm],
        agents=[bob],
        verbose=False,
    )

    assert [name for name, _ in override_gm.resolved] == ["Bob"]
    assert default_gm.resolved == []


def test_multi_gm_step_strategy_falls_back_to_default_gm_for_unbound_flow() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "multi_gm", "params": {"flow_order": ["unbound"]}},
                }
            }
        }
    )
    engine = MultiGMRuntimeEngine(config=cfg)
    alice = _Agent("Alice")
    default_gm = _GameMaster(
        name="default_gm",
        selected=["Alice"],
        agent_flow_tags={"Alice": "unbound"},
        flow_chains={},
    )
    other_gm = _GameMaster(name="other_gm", selected=["Alice"])

    engine.run_step(
        step_index=0,
        game_masters=[default_gm, other_gm],
        agents=[alice],
        verbose=False,
    )

    assert [name for name, _ in default_gm.resolved] == ["Alice"]
    assert other_gm.resolved == []


def test_multi_gm_step_strategy_rejects_unknown_gm_in_chain() -> None:
    engine = MultiGMRuntimeEngine()
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=["Alice"],
        agent_flow_tags={"Alice": "default"},
        flow_chains={"default": ["missing_gm"]},
    )

    with pytest.raises(ValueError, match="Unknown GM 'missing_gm'"):
        engine.run_step(
            step_index=0,
            game_masters=[primary],
            agents=[alice],
            verbose=False,
        )
