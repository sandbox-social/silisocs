"""Social-media ConcatAct wrapper with optional tool-calling support."""

from __future__ import annotations

import json

from concordia.components.agent import concat_act_component
from concordia.document import interactive_document
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
    tool-calling marker.
    """

    def _extract_tooling(self, call_to_action: str) -> tuple[str, list[dict] | None]:
        """Check if tool-calling is enabled and extract schemas from prompt.

        When tool-calling marker is found, extract embedded schemas (if present)
        and return a clean prompt with markers and schemas removed.

        Returns (clean_prompt, tools) where:
        - tools is None if tool-calling marker not present
        - tools is extracted schema list if marker + schemas found
        - tools is [] if marker present but no schemas (fallback to free text)
        - clean_prompt has all markers and schemas removed
        """
        if _TOOL_CALLING_MARKER not in call_to_action:
            return call_to_action, None

        # Extract schemas if present
        tools = None
        cleaned = call_to_action

        if _TOOL_SCHEMAS_START in call_to_action and _TOOL_SCHEMAS_END in call_to_action:
            before, remainder = call_to_action.split(_TOOL_SCHEMAS_START, 1)
            schemas_json, after = remainder.split(_TOOL_SCHEMAS_END, 1)

            # Try to parse the schemas
            try:
                tools = json.loads(schemas_json.strip())
                if not isinstance(tools, list):
                    tools = None
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

            # Clean prompt: remove markers and schemas
            cleaned = before.strip() + " " + after.strip()
            cleaned = cleaned.replace(_TOOL_CALLING_MARKER, "").strip()
        else:
            # No schemas found, just remove marker
            cleaned = call_to_action.replace(_TOOL_CALLING_MARKER, "").strip()
            tools = []  # Marker present but no schemas

        return cleaned, tools

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
        # Escape braces to prevent format() from interpreting JSON as placeholders
        # e.g., {"success": bool} → {{success}}: bool
        call_to_action = call_to_action.replace("{", "{{").replace("}", "}}")
        call_to_action = call_to_action.replace("{{name}}", "{name}")
        try:
            call_to_action = call_to_action.format(name=self.get_entity().name)
        except (KeyError, ValueError):
            # If formatting still fails, use the escaped version as-is
            call_to_action = call_to_action.replace("{{", "{").replace("}}", "}")

        # DEBUG: Log full prompt for all agents
        import logging

        debug_logger = logging.getLogger("CONCAT_ACT_DEBUG")
        debug_logger.warning(f"\n{'=' * 80}")
        debug_logger.warning(f"AGENT: {self.get_entity().name}")
        debug_logger.warning(f"ACTION_SPEC_TYPE: {action_spec.output_type}")
        debug_logger.warning(f"HAS_TOOLS: {tools is not None}")
        debug_logger.warning(f"CONTEXT_LENGTH: {len(context)} chars")
        debug_logger.warning(f"CONTEXT_PREVIEW: {context[:200].strip()}...")
        debug_logger.warning(f"CALL_TO_ACTION_LENGTH: {len(call_to_action)} chars")
        debug_logger.warning(f"CALL_TO_ACTION: {call_to_action[:300]}")
        debug_logger.warning(f"{'=' * 80}\n")

        if action_spec.output_type in entity_lib.FREE_ACTION_TYPES:
            if tools is not None:
                # Include context with call_to_action for tool-calling
                prompt_with_context = context + "\n" + call_to_action
                debug_logger.warning(
                    f"TOOL_CALLING_PROMPT_LENGTH: {len(prompt_with_context)} chars"
                )
                result = self._sample_tool_call(prompt_with_context, tools)
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
