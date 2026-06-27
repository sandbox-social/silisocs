"""Tests for per-GM turn policies (sim.engine.step.params.gm_turn_policies).

Per-GM turn policies let a backend/GM set its own per-step action cadence, keyed by
GM name. Resolution precedence (most specific wins): per-flow > per-GM > global.
"""

from __future__ import annotations

from omegaconf import OmegaConf

from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.turns import FixedCountTurnPolicy


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.actions: list[str] = []

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        self.actions.append(action_spec.prompt)
        return ActionOutput.from_text(self.name)


class _GameMaster:
    def __init__(
        self,
        *,
        name: str,
        selected: list[str],
        agent_flow_tags: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags or {}
        self.resolved: list[str] = []

    def update(self, *, step: int, agents: list[_Agent], context: object | None = None) -> None:
        del step, agents, context

    def acting_agents(self, candidate_agents: list[_Agent]) -> list[str]:
        available = {agent.name for agent in candidate_agents}
        return [name for name in self.selected if name in available]

    def action_prompt(self, agent_name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{agent_name}", output_type=OutputType.TEXT)

    def make_observation(self, agent_name: str) -> str:
        del agent_name
        return ""

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        del action
        self.resolved.append(agent_name)
        return ""


def _fixed(count: int) -> dict[str, object]:
    return {"built_in": "fixed_count", "params": {"count": count}}


def _single() -> dict[str, object]:
    return {"built_in": "single_action"}


def _multi_engine(
    *,
    gm_turn_policies: dict[str, object] | None = None,
    flow_turn_policies: dict[str, object] | None = None,
    flow_order: list[str] | None = None,
    flow_chains: dict[str, list[str]] | None = None,
) -> RuntimeEngine:
    params: dict[str, object] = {}
    if flow_order is not None:
        params["flow_order"] = flow_order
    if gm_turn_policies is not None:
        params["gm_turn_policies"] = gm_turn_policies
    if flow_turn_policies is not None:
        params["flow_turn_policies"] = flow_turn_policies
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "multi_gm", "params": params},
                }
            }
        }
    )
    return build_engine(cfg, flow_chains=flow_chains)


def test_gm_turn_policy_sets_action_cadence_per_hop() -> None:
    # A flow chain spanning two GMs with different per-GM cadences: 3 actions in the
    # first hop, 1 in the second. The per-GM key disambiguates hops of one flow.
    engine = _multi_engine(
        gm_turn_policies={"tw_gm": _fixed(3), "rd_gm": _single()},
        flow_chains={"browse": ["tw_gm", "rd_gm"]},
    )
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "browse"},
    )
    tw_gm = _GameMaster(name="tw_gm", selected=["Alice"])
    rd_gm = _GameMaster(name="rd_gm", selected=["Alice"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, tw_gm, rd_gm],
        agents=[alice],
        verbose=False,
    )

    assert tw_gm.resolved == ["Alice", "Alice", "Alice"]
    assert rd_gm.resolved == ["Alice"]


def test_flow_turn_policy_overrides_gm_turn_policy() -> None:
    # Precedence: a per-flow policy (fixed_count 2) wins over the per-GM default
    # (single_action) for the same GM.
    engine = _multi_engine(
        gm_turn_policies={"gm1": _single()},
        flow_turn_policies={"browse": _fixed(2)},
        flow_chains={"browse": ["gm1"]},
    )
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "browse"},
    )
    gm1 = _GameMaster(name="gm1", selected=["Alice"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, gm1],
        agents=[alice],
        verbose=False,
    )

    assert gm1.resolved == ["Alice", "Alice"]


def test_gm_turn_policy_threads_from_config() -> None:
    engine = _multi_engine(gm_turn_policies={"tw_gm": _fixed(3)})
    assert set(engine.gm_turn_policies) == {"tw_gm"}
    assert isinstance(engine.gm_turn_policies["tw_gm"], FixedCountTurnPolicy)


def test_gm_turn_policy_applies_under_base_step_mode() -> None:
    # gm_turn_policies is resolved per batch by GM name, so it works under any step
    # mode, not just flow/multi_gm.
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {
                        "built_in": "base",
                        "params": {"gm_turn_policies": {"main": _fixed(2)}},
                    },
                }
            }
        }
    )
    engine = build_engine(cfg)
    alice = _Agent("Alice")
    main = _GameMaster(name="main", selected=["Alice"])

    engine.run_step(step_index=0, game_masters=[main], agents=[alice], verbose=False)

    assert main.resolved == ["Alice", "Alice"]
