"""Turn policy for the EchoChamberSim replication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EchoChamberSingleActionPolicy:
    """Run observe and resolve under the GM lock, but let LLM work happen outside it."""

    name: str = "echo_chamber_single_action"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
    ) -> str:
        return engine.run_agent_step(
            game_master=game_master,
            agent=agent,
            action_spec=action_spec,
            verbose=verbose,
        ).rendered_action
