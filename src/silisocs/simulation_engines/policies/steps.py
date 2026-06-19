"""Built-in Engine step policies."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from silisocs.agents.base_agent import Agent
from silisocs.runtime.types import ActionSpec
from silisocs.simulation_engines.runtime_base import (
    StepBatch,
    StepResult,
    StepStrategy,
    TurnPolicy,
)


def _group_agents_by_flow(
    engine: Any, game_master: Any, agents: list[Agent]
) -> OrderedDict[str, list[Agent]]:
    """Group agents by their flow tag, resolved against ``game_master``."""
    agents_by_flow: OrderedDict[str, list[Agent]] = OrderedDict()
    for agent in agents:
        flow = engine._agent_flow_tag(game_master, agent.name)
        agents_by_flow.setdefault(flow, []).append(agent)
    return agents_by_flow


def _order_flows(flow_order: tuple[str, ...], available: Iterable[str]) -> list[str]:
    """Order flow names: those named in ``flow_order`` first (in order), then any
    remaining flows in insertion order. Mirrors the legacy per-strategy ordering.
    """
    available_list = list(available)
    available_set = set(available_list)
    ordered: list[str] = []
    for raw in flow_order:
        name = str(raw).strip()
        if name and name in available_set:
            ordered.append(name)
    seen = set(ordered)
    ordered.extend(name for name in available_list if name not in seen)
    return ordered


class BaseStepStrategy(StepStrategy):
    """Single-GM, no flow grouping."""

    name = "base"

    def run(
        self,
        *,
        engine: Any,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        gm = game_masters[0]
        turns = engine._selected_turns(game_master=gm, candidate_agents=cast(list[Agent], agents))
        return engine._execute_batches(
            step_index=0,
            batches=[StepBatch(flow_name="default", game_master=gm, turns=turns)],
            verbose=False,
        )


class SequentialStepStrategy(StepStrategy):
    """Single-GM step strategy that executes selected agents one at a time."""

    name = "sequential"

    def run(
        self,
        *,
        engine: Any,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        gm = game_masters[0]
        turns = engine._selected_turns(game_master=gm, candidate_agents=cast(list[Agent], agents))
        batches = [
            StepBatch(flow_name=f"sequential:{agent.name}", game_master=gm, turns=[turn])
            for turn in turns
            for agent, _spec in [turn]
        ]
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)


@dataclass
class FlowStepStrategy(StepStrategy):
    """Flow-aware step scheduling for a single game master.

    ``flow_turn_policies`` maps a flow tag to a resolved per-flow turn policy.
    Flows absent from the map run under the engine's global turn policy.
    """

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "flow"
    flow_turn_policies: Mapping[str, TurnPolicy] = field(default_factory=dict)

    def run(
        self,
        *,
        engine: Any,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        gm = game_masters[0]
        agents_by_flow = _group_agents_by_flow(engine, gm, cast(list[Agent], agents))
        groups: OrderedDict[str, list[tuple[Agent, ActionSpec]]] = OrderedDict()
        for flow, flow_agents in agents_by_flow.items():
            turns = engine._selected_turns(game_master=gm, candidate_agents=flow_agents)
            groups.setdefault(flow, []).extend(turns)
        batches = [
            StepBatch(
                flow_name=flow_name,
                game_master=gm,
                turns=groups[flow_name],
                turn_policy=self.flow_turn_policies.get(flow_name),
            )
            for flow_name in _order_flows(self.flow_order, groups)
        ]
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)


@dataclass
class MultiGMStepStrategy(StepStrategy):
    """Flow-first multi-GM routing strategy using flow_to_gms chains.

    ``flow_turn_policies`` maps a flow tag to a resolved per-flow turn policy that
    is applied at every GM hop of the flow's chain. Flows absent from the map run
    under the engine's global turn policy.
    """

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "multi_gm"
    flow_turn_policies: Mapping[str, TurnPolicy] = field(default_factory=dict)

    def run(
        self,
        *,
        engine: Any,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        gm_by_name = {str(gm.name): gm for gm in game_masters}
        default_gm = game_masters[0]
        flow_to_agents = _group_agents_by_flow(engine, default_gm, cast(list[Agent], agents))
        flow_chains = dict(getattr(default_gm, "flow_chains", {}) or {})

        batches: list[StepBatch] = []
        for flow_name in _order_flows(self.flow_order, flow_to_agents):
            candidates = flow_to_agents.get(flow_name, [])
            if not candidates:
                continue
            chain = flow_chains.get(flow_name) or [default_gm.name]
            chain_names = [str(name).strip() for name in chain if str(name).strip()]
            for gm_name in chain_names:
                gm = gm_by_name.get(gm_name)
                if gm is None:
                    raise ValueError(
                        f"Unknown GM '{gm_name}' in flow chain for flow '{flow_name}'."
                    )
                turns = engine._selected_turns(game_master=gm, candidate_agents=candidates)
                batches.append(
                    StepBatch(
                        flow_name=flow_name,
                        game_master=gm,
                        turns=turns,
                        turn_policy=self.flow_turn_policies.get(flow_name),
                    )
                )
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)
