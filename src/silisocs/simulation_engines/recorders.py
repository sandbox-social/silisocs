"""Engine recorder implementations and episode summary helpers."""

from __future__ import annotations

from typing import Any

from silisocs.runtime.telemetry import SimMetricsCollector, append_episode_run_stats
from silisocs.simulation_engines.runtime_base import EngineRecorder, StepResult


def retry_empty() -> dict[str, Any]:
    """Return an empty retry telemetry payload."""
    return {
        "calls": 0,
        "failed_calls": 0,
        "retries": 0,
        "retry_per_call": 0.0,
        "failure_ratio": 0.0,
        "models_with_activity": 0,
    }


def probe_empty(total_agents: int) -> dict[str, Any]:
    """Return an empty probe phase payload."""
    return {
        "deployed": False,
        "total_agents": total_agents,
        "selected_agents": 0,
        "duration_s": 0.0,
        "dynamic_worker_cap": 0,
        "worker_limit": 0,
        "retry": retry_empty(),
    }


class DefaultEngineRecorder(EngineRecorder):
    """Small metrics/log writer used by the runtime loop."""

    def __init__(self, *, output_rootname: str) -> None:
        self._output_rootname = output_rootname
        self._metrics = SimMetricsCollector.get()

    def record_episode(
        self,
        *,
        episode: int,
        duration_s: float,
        total_agents: int,
        step_result: StepResult,
    ) -> None:
        retry_telemetry = dict(step_result.retry_telemetry or {})
        probe_phase = dict(step_result.probe_phase or probe_empty(total_agents))
        action_phase = dict(step_result.action_phase or {})
        phase_timings = dict(step_result.phase_timings or {})

        append_episode_run_stats(
            output_rootname=self._output_rootname,
            episode=episode,
            duration_s=duration_s,
            requested_workers=int(step_result.requested_workers),
            dynamic_worker_cap=int(step_result.dynamic_worker_cap),
            configured_worker_cap=step_result.configured_worker_cap,
            worker_limit=int(step_result.worker_limit),
            probe_dynamic_worker_cap=int(probe_phase.get("dynamic_worker_cap", 0)),
            probe_worker_limit=int(probe_phase.get("worker_limit", 0)),
            retry_telemetry=retry_telemetry,
            phase_timings=phase_timings,
            probe_phase=probe_phase,
            action_phase=action_phase,
        )

        active_names = sorted(step_result.active_agent_names)
        self._metrics.log_episode(
            episode=episode,
            duration_s=round(duration_s, 4),
            total_agents=total_agents,
            active_agents=len(active_names),
            active_agent_names=active_names,
            skipped=bool(step_result.skipped),
            game_master=step_result.primary_game_master,
            worker_limit=int(step_result.worker_limit),
            requested_workers=int(step_result.requested_workers),
            phase_timings=phase_timings,
            configured_worker_cap=step_result.configured_worker_cap,
            retry_telemetry=retry_telemetry,
            probe_phase=probe_phase,
            action_phase=action_phase,
        )
        self._metrics.snapshot_resources(label=f"episode_{episode}_end")
