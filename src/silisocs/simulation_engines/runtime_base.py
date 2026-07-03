"""Native runtime engine contracts and shared dataclasses."""

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
    """One chain stage that fans a flow's agents across several GMs — a resolved branch.

    Holds one ``StepBatch`` per chosen GM and occupies a single chain position, so the
    staged traversal's stage-column alignment is preserved (a branch is one stage, not
    several). The serial and concurrent traversals flatten it via ``expand_hop``.
    """

    sub_batches: tuple[StepBatch, ...]


def expand_hop(hop: StepBatch | BranchHop | None) -> list[StepBatch]:
    """Flatten one chain hop into its concrete batches: ``[]`` for an idle slot, the
    single batch for a normal hop, or every sub-batch of a branch.
    """
    if hop is None:
        return []
    if isinstance(hop, BranchHop):
        return list(hop.sub_batches)
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
