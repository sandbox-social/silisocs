"""Probe scheduling policies for engine probe-phase gating.

These conform to the
:class:`~silisocs.simulation_engines.runtime_base.ProbeSchedulePolicy` protocol.
They live in the engine package (not ``evaluations``) because probe-phase *gating*
is an engine-loop concern — the engine decides whether to invoke the evaluation
layer's ``ProbeRunner`` each step; the evaluation layer owns the probe *deployment*
that runs when gated in. Built via ``build_probe_schedule_policy``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StepProbeSchedulePolicy:
    """Default policy: always let orchestrator evaluate deployment rules."""

    name: str = "step_schedule"

    def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool:
        """Return whether the engine should call probe orchestrator this step."""
        del step, orchestrator
        return True


@dataclass
class FixedIntervalProbeSchedulePolicy:
    """Run probe phase only on fixed intervals regardless of orchestrator cadence."""

    start_step: int = 0
    every_n_steps: int = 1
    name: str = "fixed_interval"

    def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool:
        """Return true when step matches configured fixed-interval rule."""
        del orchestrator
        if step < self.start_step:
            return False
        every = max(1, self.every_n_steps)
        return (step - self.start_step) % every == 0


@dataclass
class DisabledProbeSchedulePolicy:
    """Disable probe phase entirely."""

    name: str = "disabled"

    def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool:
        """Always skip the probe phase."""
        del step, orchestrator
        return False
