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
    """Execute actions until agent emits stop token or max cap is reached."""

    max_actions: int = 3
    done_token: str = "DONE"
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
        """Execute repeated actions with explicit agent-controlled stop support."""
        if skip_actions:
            return ""

        last_action = ""
        max_actions = max(1, self.max_actions)
        done_token = self.done_token.strip().lower()

        for _ in range(max_actions):
            action = engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
                return_raw_action=True,
            )

            if not action:
                break

            rendered_action = str(action.get("rendered", ""))
            raw_action = str(action.get("raw", ""))
            if rendered_action:
                last_action = rendered_action

            if done_token and raw_action.strip().lower() == done_token:
                break

        return last_action
