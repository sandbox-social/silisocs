"""Turn-policy policies for loose Echo Chamber social-media studies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.policies.action_chunk import _count_structured_actions


def _extract_contact_ids(action_text: str) -> list[int]:
    return [
        int(match.group(1))
        for match in re.finditer(r'"post_id"\s*:\s*"?(\d+)"?', str(action_text or ""))
    ]


def _sm_app_from_game_master(game_master: Any) -> Any:
    return getattr(game_master, "sm_app", getattr(game_master, "app", None))


def _belief_probe_prompt(
    *,
    entity_name: str,
    sm_app: Any,
    action_count: int,
    last_action_result: str,
    include_self_state: bool,
) -> str:
    echo_state = getattr(sm_app, "echo_state", None)
    if echo_state is None:
        previous_belief = 0
        previous_opinion = ""
        previous_long = ""
        topic = "Should we use euthanasia?"
    else:
        previous_belief = int(echo_state.current_beliefs.get(entity_name, 0))
        previous_opinion = str(echo_state.current_opinions.get(entity_name, ""))
        previous_long = str(echo_state.long_memory.get(entity_name, ""))
        topic = str(echo_state.topic)

    self_state_text = ""
    if include_self_state:
        self_state_text = (
            f"Previous belief: {previous_belief}\nPrevious opinion: {previous_opinion}\n"
        )

    return (
        "This is a private measurement probe, not a social-media action.\n"
        f"You are {entity_name}. Update your opinion and belief after your latest "
        f"timeline observation and {action_count} social-media action(s).\n"
        f"Topic: {topic}\n"
        f"{self_state_text}"
        f"Previous long-term memory: {previous_long}\n"
        f"Most recent environment result: {last_action_result}\n\n"
        "Belief values: '-2' for strongly oppose, '-1' for somewhat oppose, "
        "'0' for neutral, '1' for somewhat support, '2' for strongly support.\n\n"
        "Task:\n"
        "Reflect on your opinion and belief, considering whether to maintain your stance "
        "or adjust it based on your long-term memory and the observations from "
        "this action window.\n\n"
        "Instructions:\n"
        "- Think like a human: decide whether to hold firm in your own opinion "
        "or adapt based on the influence of the opinions you have heard.\n"
        "- Return a current opinion, belief value, and reasoning."
    )


def _belief_probe_schema() -> dict[str, Any]:
    return {
        "title": "echo_opinion_belief_update",
        "type": "object",
        "properties": {
            "opinion": {"type": "string"},
            "belief": {"type": "integer", "minimum": -2, "maximum": 2},
            "reasoning": {"type": "string"},
        },
        "required": ["opinion", "belief", "reasoning"],
        "additionalProperties": False,
    }


@dataclass
class FixedActionsThenBeliefProbePolicy:
    """Run N social actions, then one terminal agent-level belief probe."""

    count: int = 5
    belief_options: list[str] = field(default_factory=lambda: ["-2", "-1", "0", "1", "2"])
    belief_probe_tag: str = "echo_belief_probe"
    include_self_state: bool = True
    name: str = "echo_fixed_actions_then_belief_probe"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        skip_actions: bool,
        verbose: bool,
    ) -> str:
        """Execute social actions and record exactly one terminal belief event."""
        if skip_actions:
            return ""

        last_action = ""
        last_result = ""
        raw_actions: list[str] = []
        remaining_actions = max(1, int(self.count))

        while remaining_actions > 0:
            action_result = engine.run_agent_step(
                game_master=game_master,
                agent=agent,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
            )
            raw_action = action_result.raw_action
            rendered_action = str(action_result.rendered_action or "")
            resolved = str(action_result.resolved_result or "")

            action = rendered_action or str(raw_action)
            if not action:
                break

            raw_actions.append(str(raw_action or action))
            last_action = action
            last_result = resolved
            remaining_actions -= max(1, _count_structured_actions(raw_action or action))

        self._run_terminal_belief_probe(
            engine=engine,
            game_master=game_master,
            agent=agent,
            action_count=len(raw_actions),
            raw_actions=raw_actions,
            last_action_result=last_result,
            verbose=verbose,
        )
        return last_action

    def _run_terminal_belief_probe(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_count: int,
        raw_actions: list[str],
        last_action_result: str,
        verbose: bool,
    ) -> None:
        sm_app = _sm_app_from_game_master(game_master)
        prompt = _belief_probe_prompt(
            entity_name=agent.name,
            sm_app=sm_app,
            action_count=action_count,
            last_action_result=last_action_result,
            include_self_state=bool(self.include_self_state),
        )
        reported = agent.act(
            action_spec=ActionSpec(
                prompt=prompt,
                output_type=OutputType.STRUCTURED,
                tag=str(self.belief_probe_tag),
                extra_args={"schema": _belief_probe_schema()},
            )
        )
        contact_ids: list[int] = []
        for raw_action in raw_actions:
            contact_ids.extend(_extract_contact_ids(raw_action))

        if not isinstance(reported, ActionOutput) or reported.structured is None:
            raise TypeError("Echo belief probe expected a structured ActionOutput.")
        structured_report = dict(reported.structured)
        terminal_payload = json.dumps(
            {
                "echo_belief_probe_result": {
                    **structured_report,
                    "action_count": int(action_count),
                    "contact_ids": contact_ids,
                }
            },
            ensure_ascii=True,
        )
        with engine._gm_lock(game_master):
            result = game_master.resolve_action(
                agent.name, ActionOutput.from_text(terminal_payload)
            )
        if verbose:
            print(f"Terminal belief probe recorded for {agent.name}: {result}")
