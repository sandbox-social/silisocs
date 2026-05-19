"""Native action-prompt components for game masters."""

from __future__ import annotations

from typing import Any

from silisocs.environments.gm.components.base import ActionPromptComponent
from silisocs.runtime.types import ActionSpec, OutputType


class DefaultActionPromptComponent(ActionPromptComponent):
    """Build typed action specs from a prompt template and backend tool schemas."""

    def __init__(
        self,
        *,
        app: Any | None = None,
        action_prompt_template: str = "",
        enable_tool_calling: bool = False,
    ) -> None:
        super().__init__()
        self._app = app
        self._action_prompt_template = str(action_prompt_template or "")
        self._enable_tool_calling = bool(enable_tool_calling)

    @staticmethod
    def _format_prompt(prompt: str, agent_name: str) -> str:
        escaped = str(prompt or "").replace("{", "{{").replace("}", "}}")
        escaped = escaped.replace("{{name}}", "{name}")
        try:
            return escaped.format(name=agent_name)
        except (KeyError, ValueError):
            return escaped.replace("{{", "{").replace("}}", "}")

    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return one agent's action spec."""
        extra_args: dict[str, Any] = {}
        if (
            self._enable_tool_calling
            and self._app is not None
            and hasattr(self._app, "generate_tool_schemas")
        ):
            tool_schemas = list(self._app.generate_tool_schemas() or [])
            if tool_schemas:
                extra_args["tools"] = tool_schemas
                extra_args["tool_mode"] = "multi"
        return ActionSpec(
            prompt=self._format_prompt(self._action_prompt_template, agent_name),
            output_type=OutputType.TOOL_CALLS if extra_args.get("tools") else OutputType.TEXT,
            extra_args=extra_args,
        )
