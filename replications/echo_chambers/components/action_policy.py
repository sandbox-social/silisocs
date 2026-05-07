"""Action-loop policy for the EchoChamberSim replication."""

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
        entity: Any,
        action_spec: Any,
        skip_actions: bool,
        verbose: bool,
    ) -> str:
        if skip_actions:
            return engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=True,
                verbose=verbose,
            )

        with engine._gm_lock(game_master):
            observation = engine.make_observation(game_master, entity)
        if observation and str(observation).strip():
            entity.observe(str(observation))

        raw_text = str(entity.act(action_spec))
        rendered_action = f"{entity.name}: {raw_text}"

        with engine._gm_lock(game_master):
            result = engine.agent_resolve(game_master, rendered_action, verbose=verbose)
        entity.observe(str(result))
        return rendered_action
