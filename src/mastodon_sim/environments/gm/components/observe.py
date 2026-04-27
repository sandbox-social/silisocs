"""Concordia-native observation components for social-media game masters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from concordia.components.game_master import make_observation as make_observation_component
from concordia.typing import entity as entity_lib

from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.runtime.config import ConfigStore

_CALL_TO_MAKE_OBSERVATION = "{name}"


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
        import re

        # Try to extract from full question template: "What is the current situation faced by NAME?"
        match = re.search(r"What is the current situation faced by (.+?)\?", call_to_action)
        if match:
            return match.group(1).strip()

        # Fallback to parent implementation for simple "{name}" template
        try:
            return super()._get_active_entity_name_from_call_to_action(call_to_action)
        except Exception:
            # Last resort: return the call_to_action as-is
            return call_to_action.strip()

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
            timeline_posts = getattr(cfg.sim, "timeline_posts", 10)
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
        import re

        # Try to extract from full question template: "What is the current situation faced by NAME?"
        match = re.search(r"What is the current situation faced by (.+?)\?", call_to_action)
        if match:
            return match.group(1).strip()

        # Fallback to parent implementation for simple "{name}" template
        try:
            return super()._get_active_entity_name_from_call_to_action(call_to_action)
        except Exception:
            # Last resort: return the call_to_action as-is
            return call_to_action.strip()

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
