"""Tests for the native multi-GM engine preset."""

from __future__ import annotations

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
        gm_orchestration: dict[str, object] | None = None,
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags or {}
        self.gm_orchestration = gm_orchestration or {}
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
        gm_orchestration={"flow_chains": {"pre": ["pre_gm"], "default": ["main_gm"]}},
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
