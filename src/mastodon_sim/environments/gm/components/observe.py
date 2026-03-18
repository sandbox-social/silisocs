"""Concordia-native observation components for social-media game masters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from concordia.components.game_master import make_observation as make_observation_component
from concordia.typing import entity as entity_lib

from mastodon_sim.runtime.config import ConfigStore

_CALL_TO_MAKE_OBSERVATION = "{name}"


class TimelineMakeObservation(make_observation_component.MakeObservation):
    """Always fetch timeline when MAKE_OBSERVATION is requested."""

    def __init__(
        self,
        *,
        model: Any,
        player_names: Sequence[str],
        sm_app: Any,
        entity_action_flows: dict[str, str] | None = None,
        episode_observation_flows: Sequence[str] | None = None,
        call_to_make_observation: str = _CALL_TO_MAKE_OBSERVATION,
    ):
        super().__init__(
            model=model,
            player_names=player_names,
            components=(),
            call_to_make_observation=call_to_make_observation,
        )
        self._sm_app = sm_app
        self._entity_action_flows = dict(entity_action_flows or {})
        self._episode_observation_flows = {
            str(flow).strip() for flow in (episode_observation_flows or ()) if str(flow).strip()
        }

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Return formatted timeline observation for the active entity."""
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""

        active_entity_name = self._get_active_entity_name_from_call_to_action(
            action_spec.call_to_action
        )

        flow_type = self._entity_action_flows.get(active_entity_name, "default")
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
        timeline_posts = getattr(cfg.sim, "timeline_posts", 10)
        timeline = self._sm_app.get_timeline(active_entity_name, timeline_posts)
        result = self._sm_app.format_timeline_for_observation(timeline)
        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": result,
                "Value": result,
                "Active Entity": active_entity_name,
            }
        )
        return result


class ChunkStartMakeObservation(TimelineMakeObservation):
    """Chunk-aware observation component.

    With current single-action-per-step engine behavior this is equivalent to
    TimelineMakeObservation. It is kept for compatibility with planned
    multi-action chunk policies.
    """
