"""Engine action-loop policies.

These policies control how many actions an entity may execute per engine step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SingleActionChunkPolicy:
    """Default policy: one action per active entity per step."""

    name: str = "single_action"

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
        """Execute a single observe -> act -> resolve cycle."""
        return engine._run_single_entity_action(
            game_master=game_master,
            entity=entity,
            action_spec=action_spec,
            skip_actions=skip_actions,
            verbose=verbose,
        )


@dataclass
class FixedCountActionChunkPolicy:
    """Execute exactly N actions per active entity each step."""

    count: int = 2
    name: str = "fixed_count"

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
        """Execute a fixed number of actions, reusing the same action spec."""
        if skip_actions:
            return ""

        last_action = ""
        for _ in range(max(1, self.count)):
            action = engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
            )
            if action:
                last_action = action
        return last_action


@dataclass
class OpenEndedActionChunkPolicy:
    """Execute actions until agent emits a 'Finished' action or max cap is reached.

    In open-ended mode, the agent can take multiple sequential actions within a single
    engine step. To support this, a special "Finished action episode" action is injected
    into the LLM's action prompts, allowing the agent to signal when it has concluded
    all desired actions for this cycle.

    When the agent outputs a Finished action, the policy stops iteration and returns
    to the engine loop. Otherwise, it continues up to max_actions iterations.
    """

    max_actions: int = 3
    finished_action_signal: str = "FINISHED"
    name: str = "open_ended"

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
        """Execute repeated actions with explicit agent-controlled stop support.

        The agent can output any valid action or the special instruction to finish
        the action chunk. When "Finished action episode" is detected, iteration stops.

        Returns
        -------
            str: The last executed action before termination (or empty string if skipped).
        """
        if skip_actions:
            return ""

        last_action = ""
        max_actions = max(1, self.max_actions)
        finished_signal = self.finished_action_signal.strip().upper()

        for _ in range(max_actions):
            action = engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
            )

            if not action:
                break

            # Check if the entity indicated they are finished with the action episode
            action_upper = str(action).strip().upper()
            if action_upper == finished_signal or "FINISHED" in action_upper:
                break

            last_action = action

        return last_action
