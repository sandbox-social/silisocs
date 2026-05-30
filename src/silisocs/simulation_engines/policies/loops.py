"""Built-in Engine loop policies."""

from __future__ import annotations

import time
from typing import Any

from silisocs.simulation_engines.recorders import probe_empty
from silisocs.simulation_engines.runtime_base import LoopStrategy


class FixedStepsLoopStrategy(LoopStrategy):
    """Default loop strategy: run steps from start_step up to max_steps."""

    name = "fixed_steps"

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
    ) -> None:
        step = max(0, int(start_step))
        while step < int(max_steps):
            t0 = time.time()
            probe_phase = probe_empty(len(agents))
            if engine.probe_runner is not None:
                deployed, selected = engine.probe_runner.maybe_run(
                    step=step,
                    agents=agents,
                    worker_limit=None,
                )
                probe_phase["deployed"] = deployed
                probe_phase["selected_agents"] = selected
            step_result = engine.run_step(
                step_index=step,
                game_masters=game_masters,
                agents=agents,
                verbose=verbose,
            )
            step_result.probe_phase = probe_phase
            duration = time.time() - t0
            engine.recorder.record_episode(
                episode=step,
                duration_s=duration,
                total_agents=len(agents),
                step_result=step_result,
            )
            if checkpoint_callback is not None:
                checkpoint_callback(step + 1)
            if verbose:
                print(f"Episode {step} finished in {duration:.2f}s")
            step += 1
