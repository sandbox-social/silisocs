"""Native runtime engine contracts and shared dataclasses."""

from __future__ import annotations

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
    """One group of agent turns executed together inside a step strategy."""

    flow_name: str
    game_master: Any
    turns: list[tuple[Any, ActionSpec]]


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
