from typing import Any

from silisocs.agents.native import NativeAgent
from silisocs.environments.gm.components.resolve import ToolCallingResolveComponent
from silisocs.evaluations.probes.types import StructuredProbe
from silisocs.runtime import types as entity_lib
from silisocs.runtime.language_models import LanguageModel


class _FakeModel(LanguageModel):
    def __init__(self, mode: str = "single") -> None:
        self.mode = mode

    def sample_text(self, prompt: str, **kwargs) -> str:
        return ""

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        mode: str = "single",
        **kwargs: Any,
    ) -> list[entity_lib.ToolCall]:
        del kwargs
        del prompt, tools
        if self.mode == "multi":
            return [
                entity_lib.ToolCall("toot", {"status": "Hello from tool mode"}),
                entity_lib.ToolCall("like", {"post_id": "1"}),
            ]
        if self.mode == "multi_single":
            return [entity_lib.ToolCall("toot", {"status": "Only one but list-shaped"})]
        return [entity_lib.ToolCall("toot", {"status": "Hello from tool mode"})]

    def sample_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        del prompt, schema
        return {"belief": 1, "opinion": "I somewhat support this.", "reasoning": "Because."}


class _FakeApp:
    def generate_tool_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "toot",
                    "description": "Post a toot",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string"},
                            "current_user": {"type": "string"},
                        },
                        "required": ["status"],
                    },
                },
            }
        ]

    def invoke_action_with_kwargs(self, name: str, payload: dict) -> str:
        return f"{name}:{payload.get('current_user')}:{payload.get('status')}"


def test_native_agent_returns_typed_tool_calls_from_extra_args() -> None:
    agent = NativeAgent(name="Alice", model=_FakeModel())

    action_spec = entity_lib.ActionSpec(
        prompt="Pick one action for {name}.",
        output_type=entity_lib.OutputType.TOOL_CALLS,
        extra_args={"tools": _FakeApp().generate_tool_schemas(), "tool_mode": "single"},
    )

    result = agent.act(action_spec)

    assert result.output_type == entity_lib.OutputType.TOOL_CALLS
    assert result.tool_calls[0].name == "toot"
    assert result.tool_calls[0].arguments["status"] == "Hello from tool mode"


def test_native_agent_returns_multiple_typed_tool_calls() -> None:
    agent = NativeAgent(name="Alice", model=_FakeModel(mode="multi"))

    action_spec = entity_lib.ActionSpec(
        prompt="Pick actions for {name}.",
        output_type=entity_lib.OutputType.TOOL_CALLS,
        extra_args={"tools": _FakeApp().generate_tool_schemas(), "tool_mode": "multi"},
    )

    result = agent.act(action_spec)

    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].name == "toot"


def test_native_agent_returns_single_item_list_as_typed_tool_call() -> None:
    agent = NativeAgent(name="Alice", model=_FakeModel(mode="multi_single"))

    action_spec = entity_lib.ActionSpec(
        prompt="Pick one action for {name}.",
        output_type=entity_lib.OutputType.TOOL_CALLS,
        extra_args={"tools": _FakeApp().generate_tool_schemas(), "tool_mode": "multi"},
    )

    result = agent.act(action_spec)

    assert result.tool_calls[0].name == "toot"


def test_native_agent_returns_typed_structured_response() -> None:
    agent = NativeAgent(name="Alice", model=_FakeModel())

    schema = {
        "type": "object",
        "properties": {
            "belief": {"type": "integer"},
            "opinion": {"type": "string"},
            "reasoning": {"type": "string"},
        },
        "required": ["belief", "opinion", "reasoning"],
    }
    action_spec = entity_lib.ActionSpec(
        prompt="Update {name}'s stance.",
        output_type=entity_lib.OutputType.STRUCTURED,
        extra_args={"schema": schema},
    )

    result = agent.act(action_spec)

    assert result.output_type == entity_lib.OutputType.STRUCTURED
    assert result.structured is not None
    assert result.structured["belief"] == 1
    assert result.structured["opinion"] == "I somewhat support this."


def test_structured_probe_builds_marker_prompt_and_parses_payload() -> None:
    probe = StructuredProbe(
        {
            "name": "BeliefState",
            "question": "Return belief for {agentname}.",
            "schema": {
                "type": "object",
                "properties": {"belief": {"type": "integer"}},
                "required": ["belief"],
            },
        }
    )
    spec = probe._make_action_spec("Return belief for Alice.")

    assert spec.output_type == entity_lib.OutputType.STRUCTURED
    assert spec.extra_args["schema"]["properties"]["belief"]["type"] == "integer"
    assert probe.parse_answer({"belief": 0}) == {"belief": 0}


def test_tool_calling_resolve_executes_tool_kwargs() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())

    action = entity_lib.ActionOutput.from_tool_calls(
        [
            entity_lib.ToolCall(
                "toot",
                {"current_user": "Alice", "status": "Hello from tool mode"},
            )
        ]
    )

    result = resolve.resolve(active_agent="Alice", action=action)
    assert result == "toot:Alice:Hello from tool mode"


def test_tool_calling_resolve_allows_finished_tool_call() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())

    action = entity_lib.ActionOutput.from_tool_calls([entity_lib.ToolCall("FINISHED", {})])

    result = resolve.resolve(active_agent="Alice", action=action)
    assert result == "FINISHED:None:None"


def test_tool_calling_resolve_executes_multi_tool_calls_in_order() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())

    action = entity_lib.ActionOutput.from_tool_calls(
        [
            entity_lib.ToolCall("toot", {"current_user": "Alice", "status": "First"}),
            entity_lib.ToolCall("toot", {"current_user": "Alice", "status": "Second"}),
        ]
    )

    result = resolve.resolve(active_agent="Alice", action=action)
    assert result == "toot:Alice:First\ntoot:Alice:Second"
