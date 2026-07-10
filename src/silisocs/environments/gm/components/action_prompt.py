"""Native action-prompt components for game masters."""

from __future__ import annotations

from typing import Any

from silisocs.agents.harness.bridge import ToolSurface
from silisocs.environments.gm.components.base import ActionPromptComponent
from silisocs.runtime.types import ActionSpec, OutputType


def format_action_prompt(prompt: str, agent_name: str) -> str:
    """Substitute ``{name}`` in a prompt template with the acting agent's name."""
    escaped = str(prompt or "").replace("{", "{{").replace("}", "}}")
    escaped = escaped.replace("{{name}}", "{name}")
    try:
        return escaped.format(name=agent_name)
    except (KeyError, ValueError):
        return escaped.replace("{{", "{").replace("}}", "}")


class DefaultActionPromptComponent(ActionPromptComponent):
    """Build typed action specs from a prompt template and backend tool schemas.

    Handles native agents (a TEXT prompt, or a TOOL_CALLS spec carrying tool schemas when
    tool-calling is enabled) AND harness agents transparently: an acting agent that
    declares ``wants_tool_surface`` (duck-typed) gets the per-turn Tool Bridge bound into
    ``extra_args['tool_surface']`` instead, so a harness agent acts on any backend with no
    special GM component or config. Non-harness agents never see the surface, so existing
    scenarios are unchanged.
    """

    def __init__(
        self,
        *,
        backend: Any | None = None,
        context: Any | None = None,
        action_prompt_template: str = "",
        action_prompt: str | None = None,
        output_style: str | None = None,
        enable_tool_calling: bool = False,
        tool_calling_mode: str = "single",
    ) -> None:
        super().__init__()
        self._backend = backend
        if not action_prompt_template and action_prompt:
            action_prompt_template = str(action_prompt)
            if output_style:
                action_prompt_template = f"{action_prompt_template}\n\n{output_style}"
        self._action_prompt_template = str(action_prompt_template or "")
        self._enable_tool_calling = bool(enable_tool_calling)
        self._tool_calling_mode = str(tool_calling_mode or "single").strip()
        # Agent objects (from the GM context) to look up whether an acting agent wants a
        # harness Tool Bridge bound this turn.
        self._agents_by_name = (
            {agent.name: agent for agent in getattr(context, "agents", [])}
            if context is not None
            else {}
        )

    def _tool_surface_spec(self, agent_name: str) -> ActionSpec:
        surface = ToolSurface(
            backend=self._backend,
            agent_name=agent_name,
            harness_logger=getattr(self._backend, "harness_logger", None),
        )
        return ActionSpec(
            prompt=format_action_prompt(self._action_prompt_template, agent_name),
            output_type=OutputType.TEXT,
            extra_args={"tool_surface": surface},
        )

    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return one agent's action spec."""
        agent = self._agents_by_name.get(agent_name)
        if (
            agent is not None
            and getattr(agent, "wants_tool_surface", False)
            and self._backend is not None
        ):
            return self._tool_surface_spec(agent_name)

        extra_args: dict[str, Any] = {}
        if (
            self._enable_tool_calling
            and self._backend is not None
            and hasattr(self._backend, "generate_tool_schemas")
        ):
            tool_schemas = list(self._backend.generate_tool_schemas() or [])
            if tool_schemas:
                extra_args["tools"] = tool_schemas
                extra_args["tool_mode"] = self._tool_calling_mode
        return ActionSpec(
            prompt=format_action_prompt(self._action_prompt_template, agent_name),
            output_type=OutputType.TOOL_CALLS if extra_args.get("tools") else OutputType.TEXT,
            extra_args=extra_args,
        )
