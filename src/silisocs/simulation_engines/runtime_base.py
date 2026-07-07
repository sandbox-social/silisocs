"""Native runtime engine contracts and shared dataclasses.

Seam-style convention: policy interfaces that are pure structural seams
(``TurnPolicy``, ``StepStrategy``, ``LoopStrategy``, ``ProbeRunner``,
``EngineRecorder``, ``ProbeSchedulePolicy``) are :class:`typing.Protocol`s here, so
any conforming object plugs in without nominal inheritance. A branch router follows
the same convention (``policies/routers.py`` defines the ``Router`` Protocol — any
conforming callable, positional-only parameters); only ``ParticipationPolicy``
(``policies/participation.py``), whose base carries shared behavior, is a nominal
``ABC``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from silisocs.runtime.types import ActionOutput, ActionSpec


@dataclass(frozen=True)
class AgentStepResult:
    """Result of one observe/act/resolve cycle for one agent."""

    agent_name: str
    rendered_action: str
    raw_action: ActionOutput
    resolved_result: str


@dataclass(frozen=True)
class StepBatch:
    """One group of agent turns executed together inside a step strategy.

    ``turn_policy`` is an optional per-batch override. When ``None`` the engine
    falls back to its single global turn policy, so non-flow step strategies and
    flows without a configured override behave exactly as before.
    """

    flow_name: str
    game_master: Any
    turns: list[tuple[Any, ActionSpec]]
    turn_policy: TurnPolicy | None = None


@dataclass(frozen=True)
class BranchHop:
    """One chain stage that fans a flow's agents across alternative GMs (a branch node).

    Carried UNRESOLVED through materialization: the branch's router runs only when the
    flow's chain reaches this stage — after its earlier hops have drained — so it can
    read live backend state or involve the agents in the choice. The fields are exactly
    the inputs late resolution needs: the router and its candidate GMs (``gms``, one
    entry per branch choice in config order), the flow's agents to route, the stable
    decision scalars (flow/step/seed), and the flow's per-flow turn policy to stamp
    onto the resolved batches. Loosely typed to keep this contract module free of a
    ``routers``/``agents`` import; the engine resolves it in ``scheduling.py``.
    ``expand_hop`` refuses to flatten it, so an unresolved branch can never silently
    reach a runner.
    """

    router: Any  # routers.Router — any (agents, gms, ctx) -> {agent: gm} callable
    gms: Mapping[str, Any]  # choice GM name -> game master, in config order
    candidates: tuple[Any, ...]  # the flow's agents to route at this stage
    flow_name: str
    step_index: int
    seed: int  # run seed, so the router's decision stays replay-stable
    turn_policy: TurnPolicy | None  # per-flow turn policy applied to the resolved batches


# One chain hop: a normal batch, a branch (resolved at execution time), or None (an
# idle slot). ``expand_hop`` flattens the concrete kinds.
Hop = StepBatch | BranchHop | None


def expand_hop(hop: Hop) -> list[StepBatch]:
    """Flatten one chain hop into its concrete batches: ``[]`` for an idle slot, the
    single batch for a normal hop.

    A :class:`BranchHop` raises — its router runs at execution time and the engine
    resolves it into per-GM batches then; reaching here unresolved is a bug.
    """
    if hop is None:
        return []
    if isinstance(hop, BranchHop):
        raise RuntimeError(
            "A BranchHop resolves at execution time; the engine must route it into "
            "per-GM batches before flattening."
        )
    return [hop]


@dataclass
class StepResult:
    """Per-episode step summary emitted by step strategies."""

    active_agent_names: tuple[str, ...] = ()
    skipped: bool = False
    requested_workers: int = 0
    worker_limit: int = 0
    dynamic_worker_cap: int = 0
    configured_worker_cap: int | None = None
    phase_timings: dict[str, float] = field(default_factory=dict)
    retry_telemetry: dict[str, Any] = field(default_factory=dict)
    probe_phase: dict[str, Any] = field(default_factory=dict)
    action_phase: dict[str, Any] = field(default_factory=dict)
    primary_game_master: str = ""
    failed_turns: tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        """True when at least one agent turn raised instead of producing an action."""
        return bool(self.failed_turns)


class TurnPolicy(Protocol):
    """Configurable per-agent turn policy.

    A turn policy may call ``engine.run_agent_step(...)`` multiple times.

    A policy MAY additionally provide ``run_async(**same kwargs)`` (awaiting
    ``engine.run_agent_step_async``); the asyncio turn executor uses it when
    present and otherwise runs the sync ``run`` on a helper thread, so sync-only
    custom policies keep working under either executor.
    """

    name: str

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: ActionSpec,
        verbose: bool,
    ) -> str: ...


class StepStrategy(Protocol):
    """Strategy for one episode step."""

    name: str

    def run(
        self,
        *,
        engine: Any,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult: ...


class LoopStrategy(Protocol):
    """Strategy for episode lifecycle orchestration."""

    name: str

    def run(
        self,
        *,
        engine: Any,
        game_masters: list[Any],
        agents: list[Any],
        max_steps: int,
        start_step: int,
        verbose: bool,
        checkpoint_callback: Any | None,
    ) -> None: ...


class ProbeRunner(Protocol):
    """Evaluation-owned in-run probe deployment service."""

    def maybe_run(
        self,
        *,
        step: int,
        agents: list[Any],
        worker_limit: int | None,
        agent_flows: Mapping[str, str] | None = None,
    ) -> tuple[bool, int]: ...


class EngineRecorder(Protocol):
    """Structured metrics/log sink for engine execution."""

    def record_episode(
        self,
        *,
        episode: int,
        duration_s: float,
        total_agents: int,
        step_result: StepResult,
    ) -> None: ...


class ProbeSchedulePolicy(Protocol):
    """Gates whether the engine runs its probe phase on a given step.

    The engine owns probe-phase *gating* (this seam), while the evaluation layer
    owns probe *deployment* (:class:`ProbeRunner`); both live here so the seam is
    discoverable next to the other engine policies. Built-ins live in
    ``policies/probe_schedule.py``; select via ``eval.probes.schedule``.
    """

    name: str

    def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool: ...


class RuntimeEngineBase:
    """Abstract runtime engine surface."""

    def initialize(
        self,
        *,
        agents: list[Any],
        game_masters: list[Any],
        agent_initializer: Any | None,
        game_master_initializer: Any | None,
        simulation_initializer: Any | None,
        initialization_context: Any | None,
        initializer_model: Any | None,
    ) -> None:
        """Run startup initialization phases once before the episode loop."""
        raise NotImplementedError

    def run_agent_step(
        self,
        *,
        game_master: Any,
        agent: Any,
        action_spec: ActionSpec,
        verbose: bool,
        observe_before_action: bool = True,
    ) -> AgentStepResult:
        """Run one observe/act/resolve agent step."""
        raise NotImplementedError

    def run_step(
        self,
        *,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        """Run one episode step."""
        raise NotImplementedError

    def run_loop(
        self,
        *,
        game_masters: list[Any],
        agents: list[Any],
        max_steps: int,
        start_step: int,
        verbose: bool,
        checkpoint_callback: Any | None,
    ) -> None:
        """Run the episode loop."""
        raise NotImplementedError
