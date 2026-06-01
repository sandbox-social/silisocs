"""Tool-call resolve plus agent-level belief measurement for loose studies."""

from __future__ import annotations

import re
from typing import Any

from replications.echo_chambers.components.resolve import _extract_json
from silisocs.environments.gm.components.resolve import ToolCallingResolveComponent
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType


def _fallback_probe(agent_name: str, backend: Any, episode: int) -> dict[str, Any]:
    echo_state = getattr(backend, "echo_state", None)
    if echo_state is None:
        return {"episode": episode}
    return {
        "episode": episode,
        "belief": int(echo_state.current_beliefs.get(agent_name, 0)),
        "opinion": str(echo_state.current_opinions.get(agent_name, "")),
        "reasoning": "No valid structured belief-probe JSON was produced.",
        "short_term_memory": "",
        "long_term_memory": str(echo_state.long_memory.get(agent_name, "")),
        "contact_ids": [],
    }


def _extract_contact_ids(action_text: str) -> list[int]:
    return [int(match.group(1)) for match in re.finditer(r'"post_id"\s*:\s*"?(\d+)"?', action_text)]


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
        backend: Any,
        model: Any,
        action_prompt_template: str = "",
        agents_by_name: dict[str, Any] | None = None,
        probe_belief: bool = True,
        belief_options: list[str] | tuple[str, ...] = ("-2", "-1", "0", "1", "2"),
        belief_probe_tag: str = "echo_belief_probe",
    ) -> None:
        super().__init__(
            backend=backend,
            model=model,
            action_prompt_template=action_prompt_template,
        )
        self._agents_by_name = dict(agents_by_name or {})
        self._probe_belief = bool(probe_belief)
        self._belief_options = tuple(str(option) for option in belief_options)
        self._belief_probe_tag = str(belief_probe_tag)

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        active_agent = str(agent_name).strip()
        action_text = action.text if isinstance(action, ActionOutput) else str(action or "")

        terminal_probe = _terminal_probe_payload(action_text)
        if terminal_probe is not None:
            episode = int(getattr(getattr(self.backend, "action_logger", None), "episode_idx", 0))
            replication_episode = max(0, episode - 1)
            update = self._update_from_reported_belief(
                agent_name=active_agent,
                reported=terminal_probe,
                episode=replication_episode,
                contact_ids=list(terminal_probe.get("contact_ids", []) or []),
            )
            if hasattr(self.backend, "echo_stage_update"):
                self.backend.echo_stage_update(
                    name=active_agent,
                    episode=replication_episode,
                    update=update,
                )
            result = f"Recorded terminal belief probe for {active_agent}."
            return result

        social_result = self.resolve(active_agent=active_agent, action=action)
        if self._probe_belief:
            episode = int(getattr(getattr(self.backend, "action_logger", None), "episode_idx", 0))
            replication_episode = max(0, episode - 1)
            update = self._agent_reported_update(
                agent_name=active_agent,
                action_text=action_text,
                social_result=social_result,
                episode=replication_episode,
            )
            if hasattr(self.backend, "echo_stage_update"):
                self.backend.echo_stage_update(
                    name=active_agent,
                    episode=replication_episode,
                    update=update,
                )

        result = social_result or f"{active_agent} completed a social-media action."
        return result

    def _update_from_reported_belief(
        self,
        *,
        agent_name: str,
        reported: Any,
        episode: int,
        contact_ids: list[int],
    ) -> dict[str, Any]:
        fallback = _fallback_probe(agent_name, self.backend, episode)
        echo_state = getattr(self.backend, "echo_state", None)
        if echo_state is None:
            return fallback

        previous_belief = int(echo_state.current_beliefs.get(agent_name, 0))
        previous_opinion = str(echo_state.current_opinions.get(agent_name, ""))
        previous_long = str(echo_state.long_memory.get(agent_name, ""))
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
        agent = self._agents_by_name.get(agent_name)
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
        agent_name: str,
        action_text: str,
        social_result: str,
        episode: int,
    ) -> dict[str, Any]:
        fallback = _fallback_probe(agent_name, self.backend, episode)
        echo_state = getattr(self.backend, "echo_state", None)
        agent = self._agents_by_name.get(agent_name)
        if echo_state is None or agent is None:
            return fallback

        previous_belief = int(echo_state.current_beliefs.get(agent_name, 0))
        previous_opinion = str(echo_state.current_opinions.get(agent_name, ""))
        previous_long = str(echo_state.long_memory.get(agent_name, ""))
        prompt = (
            "This is a private measurement probe, not a social-media action.\n"
            f"You are {agent_name}. Report your current belief after your latest "
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
                action_spec=ActionSpec(
                    prompt=prompt,
                    output_type=OutputType.CHOICE,
                    options=self._belief_options,
                    tag=self._belief_probe_tag,
                )
            )
        except Exception:
            return fallback

        update = self._update_from_reported_belief(
            agent_name=agent_name,
            reported=reported,
            episode=episode,
            contact_ids=_extract_contact_ids(action_text),
        )
        return {
            **update,
            "episode": episode,
        }
