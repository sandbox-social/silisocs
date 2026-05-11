"""Concordia-native observation components for social-media game masters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from concordia.components.game_master import make_observation as make_observation_component
from concordia.typing import entity as entity_lib

from silisocs.environments.gm.components.base import FlowComponent
from silisocs.runtime.config import ConfigStore

_CALL_TO_MAKE_OBSERVATION = "{name}"


def _active_entity_from_call_to_action(call_to_action: str, fallback: Any = None) -> str:
    """Extract active entity name from Concordia observation call text."""
    import re

    match = re.search(r"What is the current situation faced by (.+?)\?", call_to_action)
    if match:
        return match.group(1).strip()

    if fallback is not None:
        try:
            return str(fallback(call_to_action)).strip()
        except Exception:
            pass
    return call_to_action.strip()


class AppObservationComponent(FlowComponent, make_observation_component.MakeObservation):
    """Generic observation component that delegates to ``EnvironmentApp.observe``."""

    def __init__(
        self,
        *,
        model: Any,
        player_names: Sequence[str],
        env_app: Any | None = None,
        sm_app: Any | None = None,
        entity_flow_tags: dict[str, str] | None = None,
        observation_params: dict[str, Any] | None = None,
        call_to_make_observation: str = _CALL_TO_MAKE_OBSERVATION,
    ):
        """Initialize a generic app-backed observation component."""
        FlowComponent.__init__(self)
        make_observation_component.MakeObservation.__init__(
            self,
            model=model,
            player_names=player_names,
            components=(),
            call_to_make_observation=call_to_make_observation,
        )
        self._env_app = env_app if env_app is not None else sm_app
        self._entity_flow_tags = dict(entity_flow_tags or {})
        self._observation_params = dict(observation_params or {})

    def _get_active_entity_name_from_call_to_action(self, call_to_action: str) -> str:
        """Extract active entity name from action spec text."""
        return _active_entity_from_call_to_action(
            call_to_action,
            fallback=super()._get_active_entity_name_from_call_to_action,
        )

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Return the environment app observation for the active entity."""
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""

        active_entity_name = self._get_active_entity_name_from_call_to_action(
            action_spec.call_to_action
        )
        flow_tag = self._entity_flow_tags.get(active_entity_name, "default")
        current_episode = getattr(getattr(self._env_app, "action_logger", None), "episode_idx", 0)
        observe = getattr(self._env_app, "observe", None)
        if not callable(observe):
            result = ""
        else:
            result = str(
                observe(
                    active_entity_name,
                    step=current_episode,
                    flow_tag=flow_tag,
                    **self._observation_params,
                )
            )

        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": result[:100] + "..." if len(result) > 100 else result,
                "Value": result,
                "Active Entity": active_entity_name,
            }
        )
        return result


class TimelineMakeObservation(FlowComponent, make_observation_component.MakeObservation):
    """Always fetch timeline when MAKE_OBSERVATION is requested."""

    FLOW_FIELDS = {
        "timeline_mode": str,
        "recsys_type": str,
    }

    def __init__(
        self,
        *,
        model: Any,
        player_names: Sequence[str],
        sm_app: Any,
        entity_flow_tags: dict[str, str] | None = None,
        episode_observation_flows: Sequence[str] | None = None,
        timeline_mode: str | None = None,
        recsys_type: str | None = None,
        timeline_config: dict[str, Any] | None = None,
        call_to_make_observation: str = _CALL_TO_MAKE_OBSERVATION,
    ):
        """__init__."""
        FlowComponent.__init__(self)
        make_observation_component.MakeObservation.__init__(
            self,
            model=model,
            player_names=player_names,
            components=(),
            call_to_make_observation=call_to_make_observation,
        )
        self._sm_app = sm_app
        self._entity_flow_tags = dict(entity_flow_tags or {})
        self._episode_observation_flows = {
            str(flow).strip() for flow in (episode_observation_flows or ()) if str(flow).strip()
        }
        self._timeline_mode = str(timeline_mode or "follower_chronological").strip()
        self._default_recsys_type = str(recsys_type or "").strip() or None
        self._timeline_config = dict(timeline_config or {})

    def _get_active_entity_name_from_call_to_action(self, call_to_action: str) -> str:
        """Extract active entity name from call_to_action.

        Handles both templates:
        1. Simple: "{name}" (our template)
        2. Full question: "What is the current situation faced by {name}?..." (Concordia template)
        """
        return _active_entity_from_call_to_action(
            call_to_action,
            fallback=super()._get_active_entity_name_from_call_to_action,
        )

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Return formatted timeline observation for the active entity."""
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""

        active_entity_name = self._get_active_entity_name_from_call_to_action(
            action_spec.call_to_action
        )

        flow_type = self._entity_flow_tags.get(active_entity_name, "default")
        if flow_type in self._episode_observation_flows:
            current_episode = getattr(
                getattr(self._sm_app, "action_logger", None), "episode_idx", 0
            )
            result = f"EPISODE: {current_episode}"
            self._logging_channel(
                {
                    "Key": self._pre_act_label,
                    "Summary": result,
                    "Value": result,
                    "Active Entity": active_entity_name,
                }
            )
            return result

        cfg = ConfigStore.get_config()
        timeline_posts = getattr(getattr(cfg, "env", object()), "timeline_posts", None)
        if timeline_posts is None:
            timeline_posts = 10
        recsys_type = self.get_flow_field("recsys_type", flow_type)
        if not recsys_type:
            recsys_type = self._default_recsys_type
        flow_timeline_mode = self.get_flow_field("timeline_mode", flow_type)
        if not flow_timeline_mode:
            flow_timeline_mode = self._timeline_mode

        timeline = self._sm_app.get_timeline_mode(
            flow_timeline_mode,
            active_entity_name,
            timeline_posts,
            recsys_type=recsys_type,
            **self._timeline_config,
        )
        result = (
            "STARTING SOCIAL MEDIA SESSION\n\n TIMELINE:\n\n"
            + self._sm_app.format_timeline_for_observation(timeline)
        )

        # Ensure non-empty result even when timeline is empty
        if not result or not result.strip():
            result = "## Timeline\n\nNo posts available in your feed yet."

        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": result[:100] + "..." if len(result) > 100 else result,
                "Value": result,
                "Active Entity": active_entity_name,
            }
        )
        return result


class EpisodeObservation(make_observation_component.MakeObservation):
    """Return episode number instead of timeline for specific flows.

    Used to differentiate agent behavior based on entity_flow_tags
    (e.g., fixed_pre agents see only episode numbers, not timelines).
    """

    def __init__(
        self,
        *,
        model: Any,
        player_names: Sequence[str],
        sm_app: Any,
        entity_flow_tags: dict[str, str] | None = None,
        episode_observation_flow: str = "fixed_pre",
        call_to_make_observation: str = _CALL_TO_MAKE_OBSERVATION,
    ):
        """__init__."""
        super().__init__(
            model=model,
            player_names=player_names,
            components=(),
            call_to_make_observation=call_to_make_observation,
        )
        self._sm_app = sm_app
        self._entity_flow_tags = dict(entity_flow_tags or {})
        self._episode_observation_flow = str(episode_observation_flow).strip()

    def _get_active_entity_name_from_call_to_action(self, call_to_action: str) -> str:
        """Extract active entity name from call_to_action.

        Handles both templates:
        1. Simple: "{name}" (our template)
        2. Full question: "What is the current situation faced by {name}?..." (Concordia template)
        """
        return _active_entity_from_call_to_action(
            call_to_action,
            fallback=super()._get_active_entity_name_from_call_to_action,
        )

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Return episode number if entity is in episode observation flow."""
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""

        active_entity_name = self._get_active_entity_name_from_call_to_action(
            action_spec.call_to_action
        )

        flow_type = self._entity_flow_tags.get(active_entity_name, "default")
        if flow_type != self._episode_observation_flow:
            return ""

        current_episode = getattr(getattr(self._sm_app, "action_logger", None), "episode_idx", 0)
        result = f"EPISODE: {current_episode}"
        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": result,
                "Value": result,
                "Active Entity": active_entity_name,
            }
        )
        return result
