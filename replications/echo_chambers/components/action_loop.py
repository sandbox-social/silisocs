"""Action-loop policies for loose Echo Chamber social-media studies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from concordia.typing import entity as entity_lib

from silisocs.agents.components.concat_act import (
    STRUCTURED_RESPONSE_MARKER,
    STRUCTURED_SCHEMA_END,
    STRUCTURED_SCHEMA_START,
    extract_structured_response,
)
from silisocs.simulation_engines.policies.action_chunk import _count_structured_actions


def _extract_contact_ids(action_text: str) -> list[int]:
    return [
        int(match.group(1))
        for match in re.finditer(r'"post_id"\s*:\s*"?(\d+)"?', str(action_text or ""))
    ]


def _sm_app_from_game_master(game_master: Any) -> Any:
    act_component = getattr(game_master, "_act_component", None)
    return getattr(act_component, "sm_app", None)


def _belief_probe_call_to_action(
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
        f"{STRUCTURED_RESPONSE_MARKER}\n"
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
        "- Return a current opinion, belief value, and reasoning.\n"
        f"{STRUCTURED_SCHEMA_START}\n"
        + json.dumps(
            {
                "title": "echo_opinion_belief_update",
                "type": "object",
                "properties": {
                    "opinion": {"type": "string"},
                    "belief": {"type": "integer", "minimum": -2, "maximum": 2},
                    "reasoning": {"type": "string"},
                },
                "required": ["opinion", "belief", "reasoning"],
                "additionalProperties": False,
            },
            ensure_ascii=True,
        )
        + f"\n{STRUCTURED_SCHEMA_END}"
    )


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
        entity: Any,
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
            action_result = engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
                return_raw_action=True,
            )
            if isinstance(action_result, dict):
                raw_action = str(action_result.get("raw", "") or "")
                rendered_action = str(action_result.get("rendered", "") or "")
                resolved = str(action_result.get("resolved", "") or "")
            else:
                raw_action = str(action_result or "")
                rendered_action = raw_action
                resolved = ""

            action = rendered_action or raw_action
            if not action:
                break

            raw_actions.append(raw_action or action)
            last_action = action
            last_result = resolved
            remaining_actions -= max(1, _count_structured_actions(raw_action or action))

        self._run_terminal_belief_probe(
            engine=engine,
            game_master=game_master,
            entity=entity,
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
        entity: Any,
        action_count: int,
        raw_actions: list[str],
        last_action_result: str,
        verbose: bool,
    ) -> None:
        sm_app = _sm_app_from_game_master(game_master)
        call_to_action = _belief_probe_call_to_action(
            entity_name=entity.name,
            sm_app=sm_app,
            action_count=action_count,
            last_action_result=last_action_result,
            include_self_state=bool(self.include_self_state),
        )
        reported = entity.act(
            action_spec=entity_lib.ActionSpec(
                call_to_action=call_to_action,
                output_type=entity_lib.OutputType.FREE,
                tag=str(self.belief_probe_tag),
            )
        )
        contact_ids: list[int] = []
        for raw_action in raw_actions:
            contact_ids.extend(_extract_contact_ids(raw_action))

        structured_report = extract_structured_response(str(reported)) or {
            "belief": str(reported).strip()
        }
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
            result = engine.agent_resolve(
                game_master,
                f"{entity.name}: {terminal_payload}",
                verbose=verbose,
            )
        if verbose:
            print(f"Terminal belief probe recorded for {entity.name}: {result}")
