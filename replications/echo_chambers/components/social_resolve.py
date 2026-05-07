"""Tool-call resolve plus agent-level belief measurement for loose studies."""

from __future__ import annotations

import re
from typing import Any

from concordia.typing import entity as entity_lib

from replications.echo_chambers.components.resolve import _extract_json
from silisocs.environments.gm.components.resolve import ToolCallingResolveComponent


def _extract_tool_names(action_text: str) -> list[str]:
    payload = _extract_json(action_text)
    if not isinstance(payload, dict):
        return []
    calls = payload.get("tool_calls")
    if isinstance(calls, list):
        return [
            str(item.get("name", "")).strip()
            for item in calls
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
    call = payload.get("tool_call")
    if isinstance(call, dict) and str(call.get("name", "")).strip():
        return [str(call.get("name")).strip()]
    return []


def _fallback_probe(active_entity: str, sm_app: Any, episode: int) -> dict[str, Any]:
    echo_state = getattr(sm_app, "echo_state", None)
    if echo_state is None:
        return {"episode": episode}
    return {
        "episode": episode,
        "belief": int(echo_state.current_beliefs.get(active_entity, 0)),
        "opinion": str(echo_state.current_opinions.get(active_entity, "")),
        "reasoning": "No valid structured belief-probe JSON was produced.",
        "short_term_memory": "",
        "long_term_memory": str(echo_state.long_memory.get(active_entity, "")),
        "contact_ids": [],
    }


def _extract_contact_ids(action_text: str) -> list[int]:
    contact_ids: list[int] = []
    for match in re.finditer(r'"post_id"\s*:\s*"?(\d+)"?', action_text):
        contact_ids.append(int(match.group(1)))
    return contact_ids


def _terminal_probe_payload(action_text: str) -> dict[str, Any] | None:
    payload = _extract_json(action_text)
    if not isinstance(payload, dict):
        return None
    probe = payload.get("echo_belief_probe_result")
    return probe if isinstance(probe, dict) else None


class EchoSocialToolResolve(ToolCallingResolveComponent):
    """Execute social-media tool calls and record agent-reported opinion state.

    The GM/app do not infer latent belief. Resolution records the terminal
    structured update produced by the active agent after its social action
    window.
    """

    def __init__(
        self,
        *,
        sm_app: Any,
        model: Any,
        call_to_action_str: str = "",
        entities_by_name: dict[str, Any] | None = None,
        probe_belief: bool = True,
        belief_options: list[str] | tuple[str, ...] = ("-2", "-1", "0", "1", "2"),
        belief_probe_tag: str = "echo_belief_probe",
    ) -> None:
        super().__init__(sm_app=sm_app, model=model, call_to_action_str=call_to_action_str)
        self._entities_by_name = dict(entities_by_name or {})
        self._probe_belief = bool(probe_belief)
        self._belief_options = tuple(str(option) for option in belief_options)
        self._belief_probe_tag = str(belief_probe_tag)

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.RESOLVE:
            return ""
        if ":" not in action_spec.call_to_action:
            return ""
        active_entity, action_text = action_spec.call_to_action.split(":", 1)
        active_entity = active_entity.strip()

        terminal_probe = _terminal_probe_payload(action_text)
        if terminal_probe is not None:
            episode = int(getattr(getattr(self.sm_app, "action_logger", None), "episode_idx", 0))
            replication_episode = max(0, episode - 1)
            update = self._update_from_reported_belief(
                active_entity=active_entity,
                reported=terminal_probe,
                episode=replication_episode,
                contact_ids=list(terminal_probe.get("contact_ids", []) or []),
            )
            if hasattr(self.sm_app, "echo_stage_update"):
                self.sm_app.echo_stage_update(
                    name=active_entity,
                    episode=replication_episode,
                    update=update,
                )
            result = f"Recorded terminal belief probe for {active_entity}."
            self._logging_channel(
                {
                    "Key": "echo_social_belief_probe_resolve",
                    "Summary": result,
                    "Value": {
                        "active_entity": active_entity,
                        "belief": update.get("belief"),
                        "action_count": terminal_probe.get("action_count"),
                    },
                }
            )
            return result

        social_result = self.resolve(active_entity=active_entity, action_text=action_text)
        if self._probe_belief:
            episode = int(getattr(getattr(self.sm_app, "action_logger", None), "episode_idx", 0))
            replication_episode = max(0, episode - 1)
            update = self._agent_reported_update(
                active_entity=active_entity,
                action_text=action_text,
                social_result=social_result,
                episode=replication_episode,
            )
            if hasattr(self.sm_app, "echo_stage_update"):
                self.sm_app.echo_stage_update(
                    name=active_entity,
                    episode=replication_episode,
                    update=update,
                )

        result = social_result or f"{active_entity} completed a social-media action."
        self._logging_channel(
            {
                "Key": "echo_social_tool_resolve",
                "Summary": result,
                "Value": {
                    "active_entity": active_entity,
                    "social_result": social_result,
                    "tool_names": _extract_tool_names(action_text),
                },
            }
        )
        return result

    def _update_from_reported_belief(
        self,
        *,
        active_entity: str,
        reported: Any,
        episode: int,
        contact_ids: list[int],
    ) -> dict[str, Any]:
        fallback = _fallback_probe(active_entity, self.sm_app, episode)
        echo_state = getattr(self.sm_app, "echo_state", None)
        if echo_state is None:
            return fallback

        previous_belief = int(echo_state.current_beliefs.get(active_entity, 0))
        previous_opinion = str(echo_state.current_opinions.get(active_entity, ""))
        previous_long = str(echo_state.long_memory.get(active_entity, ""))
        reported_payload = reported if isinstance(reported, dict) else {"belief": reported}
        try:
            belief = int(str(reported_payload.get("belief", previous_belief)).strip())
        except (TypeError, ValueError):
            belief = previous_belief
        belief = max(-2, min(2, belief))
        opinion = str(reported_payload.get("opinion", previous_opinion) or previous_opinion)
        reasoning = str(
            reported_payload.get(
                "reasoning",
                "Agent self-reported opinion and belief through a private structured probe.",
            )
            or ""
        )

        short_memory = ""
        long_memory = previous_long
        agent = self._entities_by_name.get(active_entity)
        if agent is not None:
            try:
                from replications.echo_chambers.components.agent_concordia import (
                    SOCIAL_MEMORY_STORE_KEY,
                    EchoSocialMemoryStore,
                )

                store = agent.get_component(SOCIAL_MEMORY_STORE_KEY, type_=EchoSocialMemoryStore)
                short_memory = store.short_term_memory
                long_memory = store.long_term_memory or previous_long
                store.current_belief = belief
                store.current_opinion = opinion
                store.reasoning = reasoning
            except Exception:
                pass

        return {
            "episode": episode,
            "belief": belief,
            "opinion": opinion,
            "reasoning": reasoning,
            "short_term_memory": short_memory,
            "long_term_memory": long_memory,
            "contact_ids": contact_ids,
        }

    def _agent_reported_update(
        self,
        *,
        active_entity: str,
        action_text: str,
        social_result: str,
        episode: int,
    ) -> dict[str, Any]:
        fallback = _fallback_probe(active_entity, self.sm_app, episode)
        echo_state = getattr(self.sm_app, "echo_state", None)
        agent = self._entities_by_name.get(active_entity)
        if echo_state is None or agent is None:
            return fallback

        previous_belief = int(echo_state.current_beliefs.get(active_entity, 0))
        previous_opinion = str(echo_state.current_opinions.get(active_entity, ""))
        previous_long = str(echo_state.long_memory.get(active_entity, ""))
        call_to_action = (
            "This is a private measurement probe, not a social-media action.\n"
            f"You are {active_entity}. Report your current belief after your latest "
            "timeline observation and social-media action.\n"
            f"Topic: {echo_state.topic}\n"
            f"Previous belief: {previous_belief}\n"
            f"Previous opinion: {previous_opinion}\n"
            f"Previous long-term memory: {previous_long}\n\n"
            f"Action payload: {action_text}\n"
            f"Environment result: {social_result}\n\n"
            "Choose one belief value: -2 strongly oppose, -1 somewhat oppose, "
            "0 neutral, 1 somewhat support, 2 strongly support. "
            "Return only the selected value."
        )
        try:
            reported = agent.act(
                action_spec=entity_lib.ActionSpec(
                    call_to_action=call_to_action,
                    output_type=entity_lib.OutputType.CHOICE,
                    options=self._belief_options,
                    tag=self._belief_probe_tag,
                )
            )
        except Exception:
            return fallback

        update = self._update_from_reported_belief(
            active_entity=active_entity,
            reported=reported,
            episode=episode,
            contact_ids=_extract_contact_ids(action_text),
        )
        return {
            **update,
            "episode": episode,
        }
