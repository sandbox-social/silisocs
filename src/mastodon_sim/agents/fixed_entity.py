"""Fixed-action entity prefab with sequence support.

A lightweight entity type that does not call the LLM. It executes predetermined
actions with support for:
- Single action per episode (legacy, default)
- Action sequences per episode (new, for OASIS alignment)
- Episode-based organization

Actions execute sequentially, with cursor tracking to ensure proper sequencing
across multiple act() calls within the same flow.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import Any

from concordia.associative_memory import basic_associative_memory
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from mastodon_sim.agents.base_agent import Agent


def _parse_episode_number(observation: str) -> int | None:
    match = re.search(r"(\d+)", observation or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


class FixedActionEntityRuntime(Agent):
    """Runtime entity implementing observe/act with predetermined action sequences.

    Supports two action plan formats:
    1. List format (legacy): Cyclic action iteration
       fixed_action_plan:
         - action_type: create_post
           episode: 1
         - action_type: like_post
           episode: 2

    2. Dict format (new): Episode-based action sequences
       fixed_action_plan:
         1:  # Episode 1
           - action_type: create_post
             target_id: ""
             content: "Post 1"
           - action_type: create_post
             target_id: ""
             content: "Post 2"
           - action_type: FINISHED
         2:  # Episode 2
           - action_type: like_post
             target_id: "1"
    """

    def __init__(self, *, params: Mapping[str, Any]) -> None:
        self._params = dict(params)
        self._agent_name = str(params.get("name", "FixedEntity"))
        self.seed_post = str(params.get("seed_post", ""))
        self._current_episode = int(params.get("initial_episode", 0) or 0)
        self._default_cursor = 0
        self._episode_cursors: dict[int, int] = {}
        self._last_log: dict[str, Any] = {}

        self._allowed_action_types = [
            str(v).strip().upper()
            for v in (params.get("allowed_action_types") or [])
            if str(v).strip()
        ]

        # Parse fixed_action_plan - supports both list and dict formats
        plan = params.get("fixed_action_plan") or []
        self._default_actions: list[dict[str, Any]] = []
        self._actions_by_episode: dict[int, list[dict[str, Any]]] = {}
        self._is_dict_format = False

        if isinstance(plan, dict):
            # Dict format: episode -> [actions]
            self._is_dict_format = True
            for ep, actions in plan.items():
                try:
                    episode_int = int(ep)
                    if isinstance(actions, list):
                        self._actions_by_episode[episode_int] = [
                            dict(a) for a in actions if isinstance(a, Mapping)
                        ]
                except (TypeError, ValueError):
                    pass

        elif isinstance(plan, list):
            # List format: actions with optional episode field
            for item in plan:
                if not isinstance(item, Mapping):
                    continue
                action = dict(item)
                episode = action.get("episode")
                if episode is None:
                    self._default_actions.append(action)
                    continue
                try:
                    episode_int = int(episode)
                except (TypeError, ValueError):
                    self._default_actions.append(action)
                    continue
                self._actions_by_episode.setdefault(episode_int, []).append(action)

    @property
    def name(self) -> str:
        """Return the agent's display name."""
        return self._agent_name

    def set_allowed_action_types(self, action_types: list[str]) -> None:
        self._allowed_action_types = [
            str(v).strip().upper() for v in action_types if str(v).strip()
        ]

    def observe(self, observation: str) -> None:
        parsed = _parse_episode_number(observation)
        if parsed is None:
            self._current_episode += 1
        else:
            self._current_episode = parsed

    def _next_action_item(self) -> dict[str, Any]:
        """Get the next action to execute.

        Returns the next action from the current episode's sequence,
        or from default actions if no episode-specific actions exist.
        """
        episode_items = self._actions_by_episode.get(self._current_episode, [])
        if episode_items:
            idx = self._episode_cursors.get(self._current_episode, 0)
            item = episode_items[idx % len(episode_items)]
            self._episode_cursors[self._current_episode] = idx + 1
            return item

        if self._default_actions:
            item = self._default_actions[self._default_cursor % len(self._default_actions)]
            self._default_cursor += 1
            return item

        return {
            "action_type": "POST",
            "target_id": "",
            "content": str(self._params.get("seed_post", "")),
            "reasoning": "Default fixed action fallback.",
        }

    def get_all_actions_for_episode(self, episode: int) -> list[dict[str, Any]]:
        """Get all actions for a specific episode (for execute_until_done policy)."""
        return self._actions_by_episode.get(episode, []) or self._default_actions

    def act(self, action_spec: Any) -> str:
        del action_spec
        item = self._next_action_item()

        action_type = str(item.get("action_type", "POST")).strip().upper() or "POST"
        if self._allowed_action_types and action_type not in self._allowed_action_types:
            action_type = self._allowed_action_types[0]

        target_id = str(item.get("target_id", "") or "")
        content = str(item.get("content", "") or "")
        reasoning = str(item.get("reasoning", "Fixed action entity response.") or "")

        action_text = (
            f"ACTION TYPE: {action_type}\n"
            f"TARGET ID: {target_id}\n"
            f"CONTENT: {content}\n"
            f"REASONING: {reasoning}"
        )
        self._last_log = {
            "episode": self._current_episode,
            "action": action_text,
            "action_type": action_type,
        }
        return action_text

    def _reset_phase(self):
        """Reset phase state for Concordia compatibility after action attempt."""
        # This method helps work around Concordia phase management issues
        if hasattr(self, '_phase'):
            try:
                from concordia.typing import entity_component
                if hasattr(self._phase, '_phase'):
                    self._phase._phase = entity_component.Phase.INITIAL
            except (AttributeError, ImportError):
                pass

    def get_last_log(self) -> dict[str, Any]:
        return dict(self._last_log)

    def get_state(self) -> dict[str, Any]:
        """Return serializable state for checkpointing."""
        return {
            "current_episode": self._current_episode,
            "default_cursor": self._default_cursor,
            "episode_cursors": dict(self._episode_cursors),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore state from checkpoint."""
        if state:
            self._current_episode = state.get("current_episode", self._current_episode)
            self._default_cursor = state.get("default_cursor", self._default_cursor)
            self._episode_cursors = dict(state.get("episode_cursors", {}))


@dataclasses.dataclass
class Entity(prefab_lib.Prefab):
    """Prefab wrapper for fixed-action runtime entity."""

    description: str = "A deterministic fixed-action entity with episode-based action sequences"
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "Fixed Entity",
            "context": "",
            "fixed_action_plan": [],
            "action_flow": "fixed_pre",
            "allowed_action_types": [],
        }
    )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> FixedActionEntityRuntime:
        del model, memory_bank
        return FixedActionEntityRuntime(params=self.params)
