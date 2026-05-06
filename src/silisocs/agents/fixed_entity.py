"""Fixed-action entity prefab with sequence support.

A lightweight entity type that does not call an LLM. It executes
predetermined actions with support for:

- Action sequences keyed by episode
- Episode-ordered plan execution with cursors
- Optional JSON/YAML plan-file loading

This prefab is useful for deterministic tests and scripted scenarios.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from concordia.associative_memory import basic_associative_memory
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from silisocs.agents.base_agent import Agent


def _parse_episode_number(observation: str) -> int | None:
    # Only treat explicit episode markers as authoritative; timeline post IDs
    # or scores should not change deterministic episode progression.
    """_parse_episode_number.

    :param str observation:
    :type observation: str

    :returns: int | None
    :rtype: int | None
    """

    match = re.search(r"\bepisode\b\s*[:#-]?\s*(\d+)\b", observation or "", re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


class FixedActionEntityRuntime(Agent):
    """Runtime entity implementing observe/act with deterministic action sequences.

    Uses an episode-keyed dict action plan format:
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
        """__init__.
    
        :returns: None
        :rtype: None
        """

        self._params = dict(params)
        self._agent_name = str(params.get("name", "FixedEntity"))
        self.seed_post = str(params.get("seed_post", ""))
        self._current_episode = int(params.get("initial_episode", 0) or 0)
        self._episode_cursors: dict[int, int] = {}
        self._episodes_finished_emitted: set[int] = set()
        self._last_log: dict[str, Any] = {}
        self._last_observation_had_episode = False

        self._allowed_action_types = [
            str(v).strip().upper()
            for v in (params.get("allowed_action_types") or [])
            if str(v).strip()
        ]

        self._action_output_mode = str(params.get("action_output_mode", "auto") or "auto").strip()
        self._advance_without_episode_observation = bool(
            params.get("advance_without_episode_observation", True)
        )
        self._advance_episode_on_non_episode_observation = bool(
            params.get("advance_episode_on_non_episode_observation", False)
        )
        self._emit_finished_on_episode_end = bool(params.get("emit_finished_on_episode_end", False))
        self._finished_action_name = (
            str(params.get("finished_action_name", "FINISHED") or "FINISHED").strip() or "FINISHED"
        )

        # Optional mapping for output-mode-specific action names.
        aliases = params.get("action_aliases", {})
        self._action_aliases: dict[str, dict[str, str]] = {
            "parsed_action": {},
            "generic_action": {},
            "tool_calling": {},
        }
        if isinstance(aliases, Mapping):
            for mode_key in self._action_aliases:
                raw_mode_aliases = aliases.get(mode_key, {})
                if isinstance(raw_mode_aliases, Mapping):
                    self._action_aliases[mode_key] = {
                        str(k).strip().upper(): str(v).strip()
                        for k, v in raw_mode_aliases.items()
                        if str(k).strip() and str(v).strip()
                    }

        # Built-in parsed-mode aliases for compatibility with backend parsers.
        self._parsed_fallback_aliases = {
            "CREATE_TWEET": "POST",
            "POST_TOOT": "POST",
            "CREATE_REDDIT_POST": "POST",
            "CREATE_COMMENT": "COMMENT",
            "UPVOTE_POST": "UPVOTE",
            "DOWNVOTE_POST": "DOWNVOTE",
        }

        plan_file = str(params.get("fixed_action_plan_file", "") or "").strip()
        plan = params.get("fixed_action_plan", None)
        if plan_file:
            if plan not in (None, {}):
                raise ValueError(
                    "Provide only one of fixed_action_plan or fixed_action_plan_file for fixed entities."
                )
            plan = self._load_action_plan_from_file(plan_file)
        if plan is None:
            plan = {}
        self._actions_by_episode = self._normalize_action_plan(plan)
        self._episode_order = sorted(self._actions_by_episode.keys())

    @property
    def name(self) -> str:
        """Return the agent's display name."""
        return self._agent_name

    def set_allowed_action_types(self, action_types: list[str]) -> None:
        """set_allowed_action_types.
    
        :param list[str] action_types:
        :type action_types: list[str]
    
        :returns: None
        :rtype: None
        """

        self._allowed_action_types = [
            str(v).strip().upper() for v in action_types if str(v).strip()
        ]

    def set_action_output_mode(self, action_output_mode: str) -> None:
        """Bind runtime output format to the GM resolve mode."""
        normalized = str(action_output_mode or "").strip()
        if normalized:
            self._action_output_mode = normalized

    def observe(self, observation: str) -> None:
        """observe.
    
        :param str observation:
        :type observation: str
    
        :returns: None
        :rtype: None
        """

        parsed = _parse_episode_number(observation)
        if parsed is None:
            self._last_observation_had_episode = False
            if self._advance_episode_on_non_episode_observation:
                self._current_episode += 1
        else:
            self._last_observation_had_episode = True
            self._current_episode = parsed

    def _load_action_plan_from_file(self, plan_file: str) -> Any:
        """Load fixed_action_plan from a JSON or YAML file."""
        path = Path(plan_file)
        if not path.exists():
            raise ValueError(f"fixed_action_plan_file does not exist: {plan_file}")

        suffix = path.suffix.lower()
        try:
            if suffix == ".json":
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            if suffix in {".yaml", ".yml"}:
                with open(path, encoding="utf-8") as f:
                    return yaml.safe_load(f)
        except Exception as exc:
            raise ValueError(f"Failed to load fixed_action_plan_file '{plan_file}': {exc}") from exc

        raise ValueError(
            f"fixed_action_plan_file must be a .json, .yaml, or .yml file: {plan_file}"
        )

    def _normalize_action_plan(self, plan: Any) -> dict[int, list[dict[str, Any]]]:
        """Validate and normalize fixed_action_plan to dict[episode, actions]."""
        actions_by_episode: dict[int, list[dict[str, Any]]] = {}

        if not isinstance(plan, Mapping):
            raise ValueError(
                "fixed_action_plan must be an episode-keyed dict, e.g. {0: [{action_type: ...}]}"
            )

        for ep, actions in plan.items():
            try:
                episode_int = int(ep)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid episode key in fixed_action_plan: {ep!r}") from exc

            if not isinstance(actions, list):
                raise ValueError(f"fixed_action_plan[{ep!r}] must be a list of action dicts")

            normalized_actions = [dict(a) for a in actions if isinstance(a, Mapping)]
            if len(normalized_actions) != len(actions):
                raise ValueError(f"fixed_action_plan[{ep!r}] contains non-dict action entries")

            if normalized_actions:
                actions_by_episode[episode_int] = normalized_actions

        return actions_by_episode

    def _next_known_episode(self, current_episode: int) -> int | None:
        """_next_known_episode.
    
        :param int current_episode:
        :type current_episode: int
    
        :returns: int | None
        :rtype: int | None
        """

        if not self._episode_order:
            return None
        for episode in self._episode_order:
            if episode > current_episode:
                return episode
        return None

    def _finished_action_item(self, reason: str = "Finished action episode") -> dict[str, Any]:
        """_finished_action_item.
    
        :param str reason:
        :type reason: str
    
        :returns: dict[str, Any]
        :rtype: dict[str, Any]
        """

        return {
            "action_type": self._finished_action_name,
            "target_id": "",
            "content": "",
            "reasoning": reason,
        }

    def _next_action_item(self) -> dict[str, Any]:
        """Get the next action to execute, advancing episodes deterministically."""
        if not self._actions_by_episode:
            return self._finished_action_item("No configured fixed actions.")

        # If current episode has no configured actions, try advancing deterministically.
        if self._current_episode not in self._actions_by_episode:
            next_episode = self._next_known_episode(self._current_episode)
            if next_episode is None:
                return self._finished_action_item("Finished action episode")
            self._current_episode = next_episode

        episode_items = self._actions_by_episode.get(self._current_episode, [])
        idx = self._episode_cursors.get(self._current_episode, 0)
        if idx < len(episode_items):
            self._episode_cursors[self._current_episode] = idx + 1
            return dict(episode_items[idx])

        # Episode exhausted: optionally emit FINISHED once for this episode.
        if (
            self._emit_finished_on_episode_end
            and self._current_episode not in self._episodes_finished_emitted
        ):
            self._episodes_finished_emitted.add(self._current_episode)
            return self._finished_action_item(f"Finished action episode {self._current_episode}")

        # Move to next episode when observations do not provide explicit episode IDs.
        can_auto_advance = (
            self._advance_without_episode_observation and not self._last_observation_had_episode
        )
        if can_auto_advance:
            next_episode = self._next_known_episode(self._current_episode)
            if next_episode is not None:
                self._current_episode = next_episode
                return self._next_action_item()

        # Plans are terminal: do not cycle back to earlier episodes.
        return self._finished_action_item("Finished action episode")

    def get_all_actions_for_episode(self, episode: int) -> list[dict[str, Any]]:
        """Get all actions for a specific episode (for execute_until_done policy)."""
        return [dict(item) for item in self._actions_by_episode.get(episode, [])]

    def _effective_action_output_mode(self, action_spec: Any) -> str:
        """_effective_action_output_mode.
    
        :param Any action_spec:
        :type action_spec: Any
    
        :returns: str
        :rtype: str
        """

        mode = str(self._action_output_mode or "auto").strip().lower()
        if mode and mode != "auto":
            return mode

        call_to_action = str(getattr(action_spec, "call_to_action", "") or "")
        if "### TOOL_CALLING_MODE ###" in call_to_action:
            return "tool_calling"
        if "Respond with EXACTLY ONE action" in call_to_action or "ACTION:" in call_to_action:
            return "generic_action"
        return "parsed_action"

    def _mode_adjusted_action_name(self, action_name: str, mode: str) -> str:
        """_mode_adjusted_action_name.
    
        :param str action_name:
        :type action_name: str
        :param str mode:
        :type mode: str
    
        :returns: str
        :rtype: str
        """

        raw = str(action_name or "").strip()
        if not raw:
            raw = "POST"
        key = raw.upper()

        aliases = self._action_aliases.get(mode, {})
        if key in aliases:
            return aliases[key]

        if mode == "parsed_action":
            return self._parsed_fallback_aliases.get(key, key)

        return raw

    def _format_action(
        self, *, mode: str, action_name: str, target_id: str, content: str, reasoning: str
    ) -> str:
        """_format_action.

        :returns: str
        :rtype: str
        """

        if mode == "tool_calling":
            payload: dict[str, Any] = {}
            normalized_name = str(action_name).strip()
            if normalized_name.upper() != self._finished_action_name.upper():
                if content:
                    payload["status"] = content
                    payload.setdefault("content", content)
                if target_id:
                    payload["post_id"] = target_id
                    payload.setdefault("target_id", target_id)
            return json.dumps(
                {
                    "tool_call": {
                        "name": normalized_name,
                        "arguments": payload,
                    }
                }
            )

        if mode == "generic_action":
            lines = [f"ACTION: {action_name}"]
            if target_id:
                lines.append(f"target_id: {target_id}")
            if content:
                lines.append(f"content: {content}")
            if reasoning:
                lines.append(f"reasoning: {reasoning}")
            return "\n".join(lines)

        return (
            f"ACTION TYPE: {action_name}\n"
            f"TARGET ID: {target_id}\n"
            f"CONTENT: {content}\n"
            f"REASONING: {reasoning}"
        )

    def act(self, action_spec: Any) -> str:
        """act.
    
        :param Any action_spec:
        :type action_spec: Any
    
        :returns: str
        :rtype: str
        """

        item = self._next_action_item()
        mode = self._effective_action_output_mode(action_spec)

        raw_action_type = str(item.get("action_type", "POST") or "POST").strip()
        action_type_upper = raw_action_type.upper()
        if self._allowed_action_types and action_type_upper not in self._allowed_action_types:
            raw_action_type = self._allowed_action_types[0]
            action_type_upper = raw_action_type.upper()

        action_name = self._mode_adjusted_action_name(raw_action_type, mode)
        if (
            str(action_name).strip().upper() == self._finished_action_name.upper()
            and mode == "tool_calling"
        ):
            action_name = self._finished_action_name

        target_id = str(item.get("target_id", "") or "")
        content = str(item.get("content", "") or "")
        reasoning = str(item.get("reasoning", "Fixed action entity response.") or "")

        action_text = self._format_action(
            mode=mode,
            action_name=action_name,
            target_id=target_id,
            content=content,
            reasoning=reasoning,
        )
        self._last_log = {
            "episode": self._current_episode,
            "action": action_text,
            "action_type": action_type_upper,
            "output_mode": mode,
        }
        return action_text

    def _reset_phase(self):
        """Reset phase state for Concordia compatibility after action attempt."""
        # This method helps work around Concordia phase management issues
        if hasattr(self, "_phase"):
            try:
                from concordia.typing import entity_component

                if hasattr(self._phase, "_phase"):
                    self._phase._phase = entity_component.Phase.INITIAL
            except (AttributeError, ImportError):
                pass

    def get_last_log(self) -> dict[str, Any]:
        """get_last_log.
    
        :returns: dict[str, Any]
        :rtype: dict[str, Any]
        """

        return dict(self._last_log)

    def get_state(self) -> dict[str, Any]:
        """Return serializable state for checkpointing."""
        return {
            "current_episode": self._current_episode,
            "episode_cursors": dict(self._episode_cursors),
            "episodes_finished_emitted": sorted(self._episodes_finished_emitted),
            "last_observation_had_episode": self._last_observation_had_episode,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore state from checkpoint."""
        if state:
            self._current_episode = state.get("current_episode", self._current_episode)
            self._episode_cursors = dict(state.get("episode_cursors", {}))
            emitted = state.get("episodes_finished_emitted", [])
            self._episodes_finished_emitted = {
                int(v) for v in emitted if isinstance(v, int) or str(v).isdigit()
            }
            self._last_observation_had_episode = bool(
                state.get("last_observation_had_episode", self._last_observation_had_episode)
            )


@dataclasses.dataclass
class Entity(prefab_lib.Prefab):
    """Prefab wrapper for fixed-action runtime entity."""

    description: str = "A deterministic fixed-action entity with episode-based action sequences"
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "Fixed Entity",
            "context": "",
            "fixed_action_plan": {},
            "fixed_action_plan_file": "",
            "allowed_action_types": [],
            "action_output_mode": "auto",
            "advance_without_episode_observation": True,
            "advance_episode_on_non_episode_observation": False,
            "emit_finished_on_episode_end": False,
            "finished_action_name": "FINISHED",
        }
    )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> FixedActionEntityRuntime:
        """build.

        :param language_model.LanguageModel model:
        :type model: language_model.LanguageModel
        :param basic_associative_memory.AssociativeMemoryBank memory_bank:
        :type memory_bank: basic_associative_memory.AssociativeMemoryBank

        :returns: FixedActionEntityRuntime
        :rtype: FixedActionEntityRuntime
        """

        del model, memory_bank
        return FixedActionEntityRuntime(params=self.params)
