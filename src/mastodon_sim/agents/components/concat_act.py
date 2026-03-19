"""Social-media ConcatAct wrapper with optional tool-calling support."""

from __future__ import annotations

import json
from collections.abc import Sequence

from concordia.components.agent import concat_act_component
from concordia.document import interactive_document
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

_TOOL_CALLING_MARKER = "### TOOL_CALLING_MODE ###"
_TOOL_SCHEMAS_START = "### TOOL_SCHEMAS_JSON ###"
_TOOL_SCHEMAS_END = "### END_TOOL_SCHEMAS_JSON ###"


class SocialConcatActComponent(concat_act_component.ConcatActComponent):
    """ConcatAct wrapper that adds tool-calling handling for FREE outputs.

    This preserves Concordia's default behavior for CHOICE/FLOAT outputs and
    only intercepts FREE outputs when the call_to_action includes the
    tool-calling marker and embedded tool schemas.
    """

    def __init__(
        self,
        model: language_model.LanguageModel,
        component_order: Sequence[str] | None = None,
        prefix_entity_name: bool = True,
        randomize_choices: bool = True,
    ):
        super().__init__(
            model=model,
            component_order=component_order,
            prefix_entity_name=prefix_entity_name,
            randomize_choices=randomize_choices,
        )

    def _extract_tooling(self, call_to_action: str) -> tuple[str, list[dict] | None]:
        """Extract tool schemas from call_to_action marker payload.

        Returns (clean_prompt, tools) where tools is None when tool-calling is
        not requested, [] when marker exists but payload is invalid, or a parsed
        tool schema list otherwise.
        """
        if _TOOL_CALLING_MARKER not in call_to_action:
            return call_to_action, None

        cleaned = call_to_action.replace(_TOOL_CALLING_MARKER, "").strip()
        if _TOOL_SCHEMAS_START not in cleaned or _TOOL_SCHEMAS_END not in cleaned:
            return cleaned, []

        before, remainder = cleaned.split(_TOOL_SCHEMAS_START, 1)
        tools_json, after = remainder.split(_TOOL_SCHEMAS_END, 1)
        prompt = "\n".join(part for part in (before.strip(), after.strip()) if part).strip()

        try:
            parsed = json.loads(tools_json.strip())
            if isinstance(parsed, list):
                return prompt, parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return prompt, []

    def _sample_tool_call(self, prompt_text: str, tools: list[dict]) -> str:
        """Run model tool-calling and format output for resolve parsing."""
        if not hasattr(self._model, "sample_tool_call"):
            return ""

        tool_name, args = self._model.sample_tool_call(prompt_text, tools)
        if not tool_name:
            return ""

        payload = dict(args or {})
        payload.setdefault("current_user", self.get_entity().name)
        return json.dumps(
            {
                "tool_call": {
                    "name": tool_name,
                    "arguments": payload,
                }
            }
        )

    @override
    def get_action_attempt(
        self,
        contexts: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        prompt = interactive_document.InteractiveDocument(self._model)
        context = self._context_for_action(contexts)
        prompt.statement(context + "\n")

        call_to_action, tools = self._extract_tooling(action_spec.call_to_action)
        call_to_action = call_to_action.format(name=self.get_entity().name)

        if action_spec.output_type in entity_lib.FREE_ACTION_TYPES:
            if tools is not None:
                result = self._sample_tool_call(call_to_action, tools)
                if result:
                    self._log(result, prompt)
                    return result

            output = ""
            if self._prefix_entity_name:
                output = self.get_entity().name + " "
            output += prompt.open_question(
                call_to_action,
                max_tokens=2200,
                answer_prefix=output,
                terminators=(),
                question_label="Exercise",
            )
            self._log(output, prompt)
            return output

        if action_spec.output_type in entity_lib.CHOICE_ACTION_TYPES:
            idx = prompt.multiple_choice_question(
                question=call_to_action,
                answers=action_spec.options,
                randomize_choices=self._randomize_choices,
            )
            output = action_spec.options[idx]
            self._log(output, prompt)
            return output

        if action_spec.output_type == entity_lib.OutputType.FLOAT:
            prefix = self.get_entity().name + " " if self._prefix_entity_name else ""
            sampled_text = prompt.open_question(
                call_to_action,
                max_tokens=2200,
                answer_prefix=prefix,
            )
            self._log(sampled_text, prompt)
            try:
                return str(float(sampled_text))
            except ValueError:
                return "0.0"

        raise NotImplementedError(
            f"Unsupported output type: {action_spec.output_type}. "
            "Supported output types are: FREE, CHOICE, and FLOAT."
        )
