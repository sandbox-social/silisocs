"""Built-in Engine step policies."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, cast

from silisocs.agents.base_agent import Agent
from silisocs.runtime.types import ActionSpec
from silisocs.simulation_engines.runtime_base import StepBatch, StepResult, StepStrategy


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
    """Flow-aware step scheduling for a single game master."""

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "flow"

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
        groups: OrderedDict[str, list[tuple[Agent, ActionSpec]]] = OrderedDict()
        gm = game_masters[0]
        agents_by_flow: OrderedDict[str, list[Agent]] = OrderedDict()
        for agent in cast(list[Agent], agents):
            flow = engine._agent_flow_tag(gm, agent.name)
            agents_by_flow.setdefault(flow, []).append(agent)
        for flow, flow_agents in agents_by_flow.items():
            turns = engine._selected_turns(game_master=gm, candidate_agents=flow_agents)
            groups.setdefault(flow, []).extend(turns)
        batches: list[StepBatch] = []
        used: set[str] = set()
        for flow in self.flow_order:
            flow_name = str(flow).strip()
            if flow_name and flow_name in groups:
                batches.append(
                    StepBatch(flow_name=flow_name, game_master=gm, turns=groups[flow_name])
                )
                used.add(flow_name)
        for flow_name, flow_turns in groups.items():
            if flow_name not in used:
                batches.append(StepBatch(flow_name=flow_name, game_master=gm, turns=flow_turns))
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)


@dataclass
class MultiGMStepStrategy(StepStrategy):
    """Flow-first multi-GM routing strategy using flow_to_gms chains."""

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "multi_gm"

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
        flow_to_agents: OrderedDict[str, list[Agent]] = OrderedDict()
        default_gm = game_masters[0]
        for agent in cast(list[Agent], agents):
            flow = engine._agent_flow_tag(default_gm, agent.name)
            flow_to_agents.setdefault(flow, []).append(agent)
        flow_chains = dict(getattr(default_gm, "flow_chains", {}) or {})

        batches: list[StepBatch] = []
        ordered_flows: list[str] = []
        seen: set[str] = set()
        for flow in self.flow_order:
            name = str(flow).strip()
            if name and name in flow_to_agents:
                ordered_flows.append(name)
                seen.add(name)
        for flow_name in flow_to_agents:
            if flow_name not in seen:
                ordered_flows.append(flow_name)
        for flow_name in ordered_flows:
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
                batches.append(StepBatch(flow_name=flow_name, game_master=gm, turns=turns))
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)
