from typing import Any

import pytest

from silisocs.agents.native import NativeAgent
from silisocs.environments.backends.base import BackendApp, app_action
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


class _FakeApp(BackendApp):
    def name(self) -> str:
        return "fake"

    def description(self) -> str:
        return "Fake backend"

    @app_action(selectable_name="toot", description="Post a toot")
    def toot(self, agent_name: str, status: str) -> str:
        return f"toot:{agent_name}:{status}"

    @app_action(selectable_name="like", description="Like a post")
    def like(self, agent_name: str, post_id: int) -> str:
        return f"like:{agent_name}:{post_id}"


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
                {"status": "Hello from tool mode"},
            )
        ]
    )

    result = resolve.resolve(active_agent="Alice", action=action)
    assert result == "toot:Alice:Hello from tool mode"


def test_tool_calling_resolve_allows_finished_tool_call() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())

    action = entity_lib.ActionOutput.from_tool_calls([entity_lib.ToolCall("FINISHED", {})])

    result = resolve.resolve(active_agent="Alice", action=action)
    assert result == "Finished action episode"


def test_tool_calling_resolve_executes_multi_tool_calls_in_order() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())

    action = entity_lib.ActionOutput.from_tool_calls(
        [
            entity_lib.ToolCall("toot", {"status": "First"}),
            entity_lib.ToolCall("toot", {"status": "Second"}),
        ]
    )

    result = resolve.resolve(active_agent="Alice", action=action)
    assert result == "toot:Alice:First\ntoot:Alice:Second"


def test_tool_calling_resolve_reports_committed_and_attempted_counts() -> None:
    # A mixed batch: one valid call (commits) + one unknown action (fails). The
    # ResolveReport exposes committed=1, attempted=2 for a committed-counting policy.
    resolve = ToolCallingResolveComponent(backend=_FakeApp())
    action = entity_lib.ActionOutput.from_tool_calls(
        [
            entity_lib.ToolCall("toot", {"status": "hi"}),
            entity_lib.ToolCall("no_such_action", {}),
        ]
    )
    result = resolve.resolve(active_agent="Alice", action=action)
    # Still a plain string to every existing consumer.
    assert "toot:Alice:hi" in result
    assert "Unknown action" in result
    # ...but carries the commit accounting.
    assert isinstance(result, entity_lib.ResolveReport)
    assert result.committed == 1
    assert result.attempted == 2


def test_tool_calling_resolve_all_valid_calls_all_committed() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())
    action = entity_lib.ActionOutput.from_tool_calls(
        [
            entity_lib.ToolCall("toot", {"status": "a"}),
            entity_lib.ToolCall("toot", {"status": "b"}),
        ]
    )
    result = resolve.resolve(active_agent="Alice", action=action)
    assert isinstance(result, entity_lib.ResolveReport)
    assert result.committed == 2
    assert result.attempted == 2


def test_tool_calling_resolve_falls_back_when_backend_lacks_detailed_seam() -> None:
    # A duck-typed backend that implements only invoke_action_with_kwargs (not the
    # committed-aware detailed seam) still resolves; committed is None so a
    # count_committed policy falls back to emitted counting.
    class _StringOnlyBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def _all_actions(self) -> list[Any]:
            return []

        def action_aliases(self) -> list[Any]:
            return []

        def _action_has_parameter(self, name: str, param: str) -> bool:
            return False

        def invoke_action_with_kwargs(self, name: str, payload: dict[str, Any]) -> str:
            self.calls.append(name)
            return f"did:{name}"

    backend = _StringOnlyBackend()
    assert not hasattr(backend, "invoke_action_detailed")
    resolve = ToolCallingResolveComponent(backend=backend)
    result = resolve.resolve(
        active_agent="Alice",
        action=entity_lib.ActionOutput.from_tool_calls([entity_lib.ToolCall("toot", {"x": "y"})]),
    )
    assert result == "did:toot"
    assert backend.calls == ["toot"]
    assert isinstance(result, entity_lib.ResolveReport)
    assert result.committed is None  # unknown -> policy falls back to emitted counting
    assert result.attempted == 1


def test_tool_calling_resolve_rejects_agent_authored_actor_identity() -> None:
    resolve = ToolCallingResolveComponent(backend=_FakeApp())

    action = entity_lib.ActionOutput.from_tool_calls(
        [entity_lib.ToolCall("toot", {"agent_name": "Mallory", "status": "Nope"})]
    )

    with pytest.raises(ValueError, match="runtime-owned actor"):
        resolve.resolve(active_agent="Alice", action=action)

    legacy_action = entity_lib.ActionOutput.from_tool_calls(
        [entity_lib.ToolCall("toot", {"current_user": "Mallory", "status": "Nope"})]
    )

    with pytest.raises(ValueError, match="runtime-owned actor"):
        resolve.resolve(active_agent="Alice", action=legacy_action)
