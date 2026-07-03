"""Built-in Engine step policies."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from silisocs.agents.base_agent import Agent
from silisocs.runtime.types import ActionSpec
from silisocs.simulation_engines.policies.routers import BranchSpec, RouteContext
from silisocs.simulation_engines.runtime_base import (
    BranchHop,
    StepBatch,
    StepResult,
    StepStrategy,
    TurnPolicy,
    expand_hop,
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


@dataclass(frozen=True)
class _MultiGMPlan:
    """Materialized flow scheduling shared by every multi-GM traversal.

    ``hops_by_flow`` maps a flow to its ordered hop batches, with ``None`` marking an
    "empty slot" — a stage the flow idles through (only meaningful under the staged
    traversal; the serial/concurrent traversals just drop the ``None`` hops).
    """

    ordered_flow_names: list[str]
    listed: set[str]
    hops_by_flow: dict[str, list[StepBatch | BranchHop | None]]


@dataclass
class MultiGMStepStrategy(StepStrategy):
    """Concurrent (default) multi-GM routing strategy over flow_to_gms chains.

    ``flow_turn_policies`` maps a flow tag to a resolved per-flow turn policy that
    is applied at every GM hop of the flow's chain. Flows absent from the map run
    under the engine's global turn policy.

    Flows listed in ``flow_order`` run first as a strict serial prefix (preserving
    declared precedence such as seed-then-act); every other flow then runs as an
    independent pipeline through its GM chain. Distinct flows advance concurrently
    and a flow's next hop starts as soon as its previous hop finishes — turns
    serialize only when two flows touch the same GM at once (enforced by the
    engine's per-GM lock). A single agent's own chain hops always stay serial, since
    each hop observes the prior hop's resolution.

    The serial (``MultiGMSerialStepStrategy``) and staged
    (``MultiGMStagedStepStrategy``) variants subclass this: they share flow
    grouping, ordering, and hop materialization and override only ``_schedule`` —
    how the non-prefix flows traverse their chains.
    """

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "multi_gm"
    flow_turn_policies: Mapping[str, TurnPolicy] = field(default_factory=dict)
    # The flow -> GM-sequence routing topology, resolved from config and supplied
    # by build_engine. Owned here (engine/step config), not read off a game master.
    flow_chains: Mapping[str, Any] = field(default_factory=dict)
    # Run seed, supplied by build_engine; routers derive replay-stable per-agent
    # decisions from it (with the flow name, step index, and agent name).
    seed: int = 0

    def run(
        self,
        *,
        engine: Any,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        plan = self._materialize(engine, game_masters, cast(list[Agent], agents), step_index)
        return self._schedule(engine, plan)

    def _materialize(
        self, engine: Any, game_masters: list[Any], agents: list[Agent], step_index: int
    ) -> _MultiGMPlan:
        gm_by_name = {str(gm.name): gm for gm in game_masters}
        default_gm = game_masters[0]
        flow_to_agents = _group_agents_by_flow(engine, default_gm, agents)
        flow_chains = dict(self.flow_chains or {})
        ordered_flow_names = [
            flow_name
            for flow_name in _order_flows(self.flow_order, flow_to_agents)
            if flow_to_agents.get(flow_name)
        ]
        hops_by_flow = {
            flow_name: self._hops(
                engine, flow_name, flow_to_agents, flow_chains, gm_by_name, default_gm, step_index
            )
            for flow_name in ordered_flow_names
        }
        # Strip entries to match _order_flows' normalization, so a whitespace-padded
        # flow_order entry is still recognized as a listed (serial-prefix) flow.
        listed = {str(flow).strip() for flow in self.flow_order}
        return _MultiGMPlan(
            ordered_flow_names=ordered_flow_names, listed=listed, hops_by_flow=hops_by_flow
        )

    def _hops(
        self,
        engine: Any,
        flow_name: str,
        flow_to_agents: Mapping[str, list[Agent]],
        flow_chains: Mapping[str, Any],
        gm_by_name: Mapping[str, Any],
        default_gm: Any,
        step_index: int,
    ) -> list[StepBatch | BranchHop | None]:
        """Materialize one flow's GM chain into ordered hops.

        A hop is a normal ``StepBatch``, ``None`` (an idle slot), or a ``BranchHop``
        (a resolved branch — one stage that fans the flow's agents across several GMs).
        Turn selection (next_acting/action_prompt, which carry mutable state) stays
        single-threaded here, before any concurrent execution begins. Each hop's
        ActionSpec is therefore materialized up-front, before earlier hops resolve; a
        custom ActionPromptComponent must not depend on backend state mutated by an
        earlier hop of the same chain (the live observation, which does reflect prior
        hops, is built at execution time in run_agent_step).
        """
        candidates = flow_to_agents.get(flow_name, [])
        chain = flow_chains.get(flow_name) or [default_gm.name]
        hop_batches: list[StepBatch | BranchHop | None] = []
        for entry in chain:
            if isinstance(entry, BranchSpec):
                hop_batches.append(
                    self._branch_hop(engine, flow_name, entry, candidates, gm_by_name, step_index)
                )
                continue
            gm_name = "" if entry is None else str(entry).strip()
            if not gm_name:
                hop_batches.append(None)  # empty slot: the flow idles this stage
                continue
            gm = gm_by_name.get(gm_name)
            if gm is None:
                raise ValueError(f"Unknown GM '{gm_name}' in flow chain for flow '{flow_name}'.")
            turns = engine._selected_turns(game_master=gm, candidate_agents=candidates)
            hop_batches.append(
                StepBatch(
                    flow_name=flow_name,
                    game_master=gm,
                    turns=turns,
                    turn_policy=self.flow_turn_policies.get(flow_name),
                )
            )
        return hop_batches

    def _branch_hop(
        self,
        engine: Any,
        flow_name: str,
        branch: BranchSpec,
        candidates: list[Agent],
        gm_by_name: Mapping[str, Any],
        step_index: int,
    ) -> BranchHop:
        """Resolve a branch into one ``StepBatch`` per chosen GM (per-agent routing).

        Each candidate is routed by ``branch.router`` to exactly one of the branch's
        choices; agents that pick the same GM are batched together. The decision is
        made here, at materialization, before any turn runs — so under the staged
        traversal the router has already acted before the stage barrier begins.
        """
        if branch.router is None:
            raise ValueError(
                f"Branch in flow '{flow_name}' was not resolved; build_engine must build "
                "its router before the step strategy runs."
            )
        by_choice: OrderedDict[str, list[Agent]] = OrderedDict()
        for agent in candidates:
            choice = str(
                branch.router.route(
                    RouteContext(
                        agent_name=agent.name,
                        flow_name=flow_name,
                        step_index=step_index,
                        seed=self.seed,
                        choices=branch.choices,
                    )
                )
            ).strip()
            if choice not in branch.choices:
                raise ValueError(
                    f"Router for flow '{flow_name}' returned '{choice}', which is not one "
                    f"of its choices {list(branch.choices)}."
                )
            by_choice.setdefault(choice, []).append(agent)
        sub_batches: list[StepBatch] = []
        for gm_name, gm_agents in by_choice.items():
            gm = gm_by_name.get(gm_name)
            if gm is None:
                raise ValueError(f"Unknown GM '{gm_name}' in branch for flow '{flow_name}'.")
            turns = engine._selected_turns(game_master=gm, candidate_agents=gm_agents)
            sub_batches.append(
                StepBatch(
                    flow_name=flow_name,
                    game_master=gm,
                    turns=turns,
                    turn_policy=self.flow_turn_policies.get(flow_name),
                )
            )
        return BranchHop(tuple(sub_batches))

    def _prefix_and_rest(
        self, plan: _MultiGMPlan
    ) -> tuple[list[StepBatch], list[list[StepBatch | BranchHop | None]]]:
        """Split flows into the serial prefix (flow_order-listed, hops flattened) and
        the per-flow hop lists for the remaining flows (idle/branch hops preserved).

        flow_order flows cannot contain a branch (rejected at build time), so the
        prefix flatten is a plain idle-slot drop in practice; ``expand_hop`` keeps it
        correct regardless.
        """
        prefix: list[StepBatch] = []
        rest: list[list[StepBatch | BranchHop | None]] = []
        for flow_name in plan.ordered_flow_names:
            hops = plan.hops_by_flow[flow_name]
            if flow_name in plan.listed:
                for hop in hops:
                    prefix.extend(expand_hop(hop))
            else:
                rest.append(hops)
        return prefix, rest

    def _schedule(self, engine: Any, plan: _MultiGMPlan) -> StepResult:
        prefix, rest = self._prefix_and_rest(plan)
        # The engine drops idle (None) slots and runs each remaining flow's chain as
        # an independent pipeline.
        return engine._execute_chain_groups(ordered_batches=prefix, rest_chains=rest)


@dataclass
class MultiGMSerialStepStrategy(MultiGMStepStrategy):
    """Legacy row-major multi-GM traversal.

    Every flow runs its full GM chain to completion before the next flow starts,
    one batch at a time, in deterministic flow-order. (Was ``chain_execution:
    sequential``.)
    """

    name: str = "multi_gm_serial"

    def _schedule(self, engine: Any, plan: _MultiGMPlan) -> StepResult:
        batches: list[StepBatch] = []
        for flow_name in plan.ordered_flow_names:
            for hop in plan.hops_by_flow[flow_name]:
                batches.extend(expand_hop(hop))
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)


@dataclass
class MultiGMStagedStepStrategy(MultiGMStepStrategy):
    """Staged (column-major) multi-GM traversal with a global per-stage barrier.

    Flows listed in ``flow_order`` run first as a strict serial prefix (same as the
    concurrent default). Every remaining flow then advances one stage at a time:
    all flows' stage-N hops run concurrently, and stage N+1 does not begin until
    ALL of stage N's turns have finished (the global barrier). A ``None``/empty
    chain entry is an "empty slot" — the flow idles that stage and resumes at its
    next non-empty hop, which is what lets flows with different chain shapes stay
    stage-aligned.
    """

    name: str = "multi_gm_staged"

    def _schedule(self, engine: Any, plan: _MultiGMPlan) -> StepResult:
        prefix, rest = self._prefix_and_rest(plan)
        # The engine transposes each remaining flow's hop list into stage columns
        # (idle None slots drop out) and runs them with a global per-stage barrier.
        return engine._execute_staged_groups(ordered_batches=prefix, rest_chains=rest)
