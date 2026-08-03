"""The port's action-prompt components: the right upstream question per turn.

Upstream issues its calls to action from three places — the marketplace GM's
``_handle_next_action_spec`` (one CTA per *role*), the reflection driver loops
(five different questions at five moments of a day), and the dialogic GM's
speech spec. The port keeps every one of those texts verbatim in :mod:`prompts`
and uses these two components to issue them:

* :class:`MarketActionPromptComponent` — the market GM's slot. Picks the
  consumer or producer CTA from the backend's role map, then builds the same
  tool-calling spec the default component would.
* :class:`ReflectionActionPromptComponent` — the journal GM's slot. Asks the
  calendar which reflection the current step is and issues that question.

(The date GM keeps the built-in ``default`` component: its CTA is the same for
both partners, so a YAML template suffices; a test pins the YAML to
``prompts.DATE_CALL_TO_ACTION``.)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from replications.signaling.components.next_acting import calendar_from_params, current_step
from replications.signaling.components.prompts import (
    CONSUMER_CALL_TO_ACTION,
    PRODUCER_CALL_TO_ACTION,
    REFLECTION_PROMPTS,
)
from silisocs.environments.gm.components.action_prompt import (
    DefaultActionPromptComponent,
    format_action_prompt,
)
from silisocs.environments.gm.components.base import ActionPromptComponent
from silisocs.runtime.types import ActionSpec, OutputType, skip_this_step_action_spec


class MarketActionPromptComponent(DefaultActionPromptComponent):
    """Issue upstream's per-role marketplace call to action.

    ``marketplace.MarketPlace._handle_next_action_spec`` gives consumers and
    producers different CTAs; the default component has one template for
    everyone, so this subclass selects the verbatim :mod:`prompts` constant per
    acting agent from the backend's role map and otherwise behaves exactly like
    the default (tool schemas, tool mode, harness surface).
    """

    #: role -> verbatim upstream CTA (minus the JSON format clause; see prompts).
    _TEMPLATES: Mapping[str, str] = {
        "consumer": CONSUMER_CALL_TO_ACTION,
        "producer": PRODUCER_CALL_TO_ACTION,
    }

    def __init__(self, *, backend: Any | None = None, context: Any | None = None, **kwargs: Any):
        backend = backend if backend is not None else getattr(context, "backend", None)
        super().__init__(backend=backend, context=context, **kwargs)
        if not callable(getattr(self._backend, "role_of", None)):
            raise ValueError(
                f"{type(self).__name__} needs a backend with role_of(): the CTA is "
                "per-role (consumer bids, producer asks)."
            )

    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return the acting agent's role-specific spec."""
        template = self._TEMPLATES.get(str(self._backend.role_of(agent_name)))
        if template is None:
            # An agent the market does not know cannot be given a decision;
            # skipping beats fabricating a CTA upstream never issues.
            return skip_this_step_action_spec()
        # Let the base assemble the spec (tool schemas, tool mode, harness
        # surface), then swap in the role's verbatim text.
        spec = super().action_prompt(agent_name)
        return ActionSpec(
            prompt=format_action_prompt(template.strip(), agent_name),
            output_type=spec.output_type,
            options=spec.options,
            tag=spec.tag,
            extra_args=spec.extra_args,
        )


class ReflectionActionPromptComponent(ActionPromptComponent):
    """Issue the reflection question the calendar assigns to the current step.

    The partner named by the four post-date prompts comes from the backend's
    ``partner_of(agent, step)`` — the journal backend derives it from the same
    seed-derived dyad schedule the date GM's next-acting builds, so the two
    never disagree.
    """

    def __init__(  # noqa: PLR0913 - the slot's runtime kwargs plus the calendar
        self,
        *,
        backend: Any = None,
        context: Any = None,
        action_prompt_template: str = "",
        enable_tool_calling: bool = False,
        tool_calling_mode: str = "single",
        calendar: Mapping[str, Any] | None = None,
        **_ignored: Any,
    ) -> None:
        super().__init__()
        component = type(self).__name__
        # Tool-calling is meaningless for free prose; the knobs are accepted so the
        # slot's runtime kwargs still bind, and deliberately ignored.
        del enable_tool_calling, tool_calling_mode
        backend = backend if backend is not None else getattr(context, "backend", None)
        if backend is None:
            raise ValueError(
                f"{component} needs the journal backend: it carries the episode index "
                "every calendar decision is keyed by."
            )
        self._backend: Any = backend
        self._calendar = calendar_from_params(calendar, component=component)
        self._action_prompt_template = str(action_prompt_template or "")
        if self._calendar.date_reflections > 0 and not callable(
            getattr(self._backend, "partner_of", None)
        ):
            raise ValueError(
                f"{component} needs a backend with partner_of(): the post-date "
                "reflection prompts name each buyer's partner."
            )

    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return the reflection prompt for one agent on the current step."""
        step = current_step(self._backend)
        kind = self._calendar.reflection_kind(step)
        if not kind:
            # Unreachable in a well-formed run (next_acting gates the journal GM to
            # reflection steps), but a SKIP beats a crash if a config disagrees.
            return skip_this_step_action_spec()
        template = REFLECTION_PROMPTS.get(kind)
        if template is None:
            raise ValueError(
                f"No reflection prompt is defined for calendar kind '{kind}'. "
                f"Known kinds: {', '.join(sorted(REFLECTION_PROMPTS))}."
            )

        partner = ""
        # Only the post-date prompts name a partner; the market reflection does not.
        if "{partner}" in template:
            partner = str(self._backend.partner_of(agent_name, step) or "")
            if not partner:
                # An odd buyer roster leaves someone without a date; asking them to
                # reflect on a partner who does not exist would fabricate content.
                return skip_this_step_action_spec()

        # ``day`` is the calendar's 0-based index, matching upstream's ``day {day}``.
        text = template.format(
            day=self._calendar.phase_of(step).day,
            name=agent_name,
            partner=partner,
        )
        if self._action_prompt_template:
            preamble = format_action_prompt(self._action_prompt_template, agent_name)
            text = f"{preamble}\n\n{text}"
        # The kind rides along so the agent can run the reflection's question
        # chain (consumer chain for the market reflection, convo chain for the
        # post-date ones), matching which upstream entity answers each.
        return ActionSpec(
            prompt=text,
            output_type=OutputType.TEXT,
            extra_args={"reflection_kind": kind},
        )
