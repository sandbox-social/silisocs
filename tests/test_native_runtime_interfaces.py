from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest

from silisocs.agents.base_agent import Agent
from silisocs.agents.native import NativeAgent
from silisocs.runtime.construction.assembly import RuntimeObjects, add_agent
from silisocs.runtime.construction.specs import AgentConfig
from silisocs.runtime.language_models import LanguageModel, NoLanguageModel, ScriptedLanguageModel
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType, ToolCall


class _RoutingModel(LanguageModel):
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append(("text", prompt, kwargs))
        return f"text:{prompt}"

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        **kwargs: Any,
    ) -> tuple[int, str, dict[str, float]]:
        self.calls.append(("choice", prompt, tuple(responses), kwargs))
        return 1, responses[1], {}

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        mode: str = "single",
        **kwargs: Any,
    ) -> list[ToolCall]:
        self.calls.append(("tools", prompt, tools, mode, kwargs))
        return [ToolCall("toot", {"status": prompt})]

    def sample_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("structured", prompt, schema, kwargs))
        return {"answer": prompt}


class _MinimalAgent(Agent):
    def __init__(self, model: LanguageModel | None = None) -> None:
        super().__init__(model or NoLanguageModel())

    @property
    def name(self) -> str:
        return "Alice"

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: Any) -> ActionOutput:
        del action_spec
        return ActionOutput.from_text("")


class _CallingAgent(Agent):
    def __init__(self, model: LanguageModel) -> None:
        super().__init__(model)

    @property
    def name(self) -> str:
        return "Alice"

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        return self._call_model("curated context", action_spec)


def test_action_spec_and_action_output_are_typed_payloads() -> None:
    tool = ToolCall(name="toot", arguments={"status": "hello"})
    spec = ActionSpec(
        prompt="Pick an action for {name}.",
        output_type=OutputType.TOOL_CALLS,
        extra_args={"tools": [{"type": "function"}], "tool_mode": "single"},
    )
    output = ActionOutput.from_tool_calls([tool])

    assert spec.prompt == "Pick an action for {name}."
    assert not hasattr(spec, "call_to_action")
    assert spec.extra_args["tool_mode"] == "single"
    assert output.output_type == OutputType.TOOL_CALLS
    assert output.tool_calls == (tool,)
    assert output.text == ""

    roundtrip = ActionOutput.from_dict(output.to_dict())
    assert roundtrip.output_type == OutputType.TOOL_CALLS
    assert roundtrip.tool_calls == (tool,)


def test_agent_requires_language_model_and_exposes_protected_call_helper() -> None:
    model = _RoutingModel()
    agent = _CallingAgent(model)

    result = agent.act(ActionSpec(prompt="Say hi", output_type=OutputType.TEXT))

    assert result == ActionOutput.from_text("text:curated context\n\nSay hi")
    assert model.calls[0][0] == "text"


def test_agent_call_model_routes_typed_output_modes() -> None:
    model = _RoutingModel()
    agent = _CallingAgent(model)

    choice = agent.act(ActionSpec(prompt="Pick", output_type=OutputType.CHOICE, options=("A", "B")))
    structured = agent.act(
        ActionSpec(
            prompt="Return JSON",
            output_type=OutputType.STRUCTURED,
            extra_args={"schema": {"type": "object"}},
        )
    )
    tools = agent.act(
        ActionSpec(
            prompt="Post",
            output_type=OutputType.TOOL_CALLS,
            extra_args={"tools": [{"type": "function"}], "tool_mode": "multi"},
        )
    )
    skipped = agent.act(ActionSpec(prompt="Skip", output_type=OutputType.SKIP))

    assert choice.choice == "B"
    assert structured.structured == {"answer": "curated context\n\nReturn JSON"}
    assert tools.tool_calls == (ToolCall("toot", {"status": "curated context\n\nPost"}),)
    assert skipped.output_type == OutputType.SKIP
    assert [call[0] for call in model.calls] == ["choice", "structured", "tools"]


def test_agent_call_model_fails_loudly_for_bad_specs() -> None:
    agent = _CallingAgent(NoLanguageModel())

    try:
        agent.act(ActionSpec(prompt="Post", output_type=OutputType.TOOL_CALLS))
    except ValueError as exc:
        assert "extra_args['tools']" in str(exc)
    else:
        raise AssertionError("TOOL_CALLS without tools should fail")


def test_agent_initialize_defaults_to_noop() -> None:
    agent = _MinimalAgent()

    agent.initialize({"memories": ["shared context"]})


def test_native_agent_initialize_observes_memory_strings() -> None:
    entity = NativeAgent(
        name="Alice",
        model=NoLanguageModel(),
    )

    entity.initialize({"memories": ["shared memory", "specific memory"]})

    memories = entity.get_all_memories_as_text()
    assert "shared memory" in memories
    assert "specific memory" in memories


def test_native_agent_caps_memory_text_growth() -> None:
    entity = NativeAgent(
        name="Alice",
        model=NoLanguageModel(),
        memory_history=10,
    )

    for i in range(50):
        entity.observe(f"observation {i}")

    memories = entity.get_all_memories_as_text()
    assert len(memories) == 10
    assert memories[0] == "observation 40"
    assert memories[-1] == "observation 49"


def test_native_agent_memory_history_is_config_settable() -> None:
    entity = NativeAgent(
        name="Alice",
        model=NoLanguageModel(),
        memory_history=1000,
    )

    assert entity._memory_history == 1000
    # default applies when unset
    default_entity = NativeAgent(name="Bob", model=NoLanguageModel())
    assert default_entity._memory_history == 1000


def test_native_agent_set_state_caps_restored_memory() -> None:
    entity = NativeAgent(
        name="Alice",
        model=NoLanguageModel(),
        memory_history=5,
    )

    # Simulate a checkpoint from an older, uncapped run.
    entity.set_state({"memory_text": [f"old {i}" for i in range(100)]})

    memories = entity.get_all_memories_as_text()
    assert len(memories) == 5
    assert memories[0] == "old 95"


def test_native_game_master_contract_uses_plain_actor_names() -> None:
    from silisocs.environments.gm.runtime import GameMasterProtocol

    class _GM:
        name = "gm"

        def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]:
            assert [agent.name for agent in candidate_agents] == ["Alice"]
            return ["Alice"]

        def initialize(self, *, agents: Sequence[Any], context: Any) -> None:
            del agents, context

        def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
            del step, agents, context

        def action_prompt(self, agent_name: str) -> ActionSpec:
            return ActionSpec(f"Do one thing, {agent_name}", OutputType.TEXT)

        def make_observation(self, agent_name: str) -> str:
            return f"obs:{agent_name}"

        def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
            return f"resolved:{agent_name}:{action.text}"

    gm: GameMasterProtocol = _GM()

    assert gm.acting_agents([SimpleNamespace(name="Alice")]) == ["Alice"]
    assert gm.action_prompt("Alice").prompt == "Do one thing, Alice"
    assert gm.make_observation("Alice") == "obs:Alice"
    assert gm.resolve_action("Alice", ActionOutput.from_text("hello")) == "resolved:Alice:hello"


def test_native_game_master_protocol_uses_canonical_methods_only() -> None:
    from silisocs.environments.gm.runtime import GameMasterProtocol

    assert "initialize" in GameMasterProtocol.__dict__
    assert "update" in GameMasterProtocol.__dict__
    assert "action_prompt" in GameMasterProtocol.__dict__
    assert "initialize_backend" not in GameMasterProtocol.__dict__
    assert "post_seed_posts" not in GameMasterProtocol.__dict__


def test_non_native_agent_class_requires_explicit_concordia_compat() -> None:
    instance = AgentConfig(
        class_path="types.SimpleNamespace",
        params={"name": "Alice"},
    )

    try:
        add_agent(
            runtime=RuntimeObjects(),
            spec=instance,
            models={"default": NoLanguageModel()},
            object_to_model={"Alice": "default"},
        )
    except TypeError as exc:
        assert "compat: concordia" in str(exc)
    else:
        raise AssertionError("Non-native agent prefab should require explicit compat.")


def test_agent_runtime_spec_requires_non_empty_name() -> None:
    spec = AgentConfig(
        class_path="silisocs.agents.native.NativeAgent",
        params={"name": " ", "context": "No identity."},
    )

    with pytest.raises(ValueError, match="non-empty `name`"):
        add_agent(
            runtime=RuntimeObjects(),
            spec=spec,
            models={"default": NoLanguageModel()},
            object_to_model={"": "default"},
        )


def test_agent_runtime_spec_rejects_duplicate_name() -> None:
    runtime = RuntimeObjects()
    spec = AgentConfig(
        class_path="silisocs.agents.native.NativeAgent",
        params={"name": "Alice", "context": "One Alice."},
    )

    add_agent(
        runtime=runtime,
        spec=spec,
        models={"default": NoLanguageModel()},
        object_to_model={"Alice": "default"},
    )

    with pytest.raises(ValueError, match="Duplicate agent name: Alice"):
        add_agent(
            runtime=runtime,
            spec=AgentConfig(
                class_path="silisocs.agents.native.NativeAgent",
                params={"name": "Alice", "context": "Another Alice."},
            ),
            models={"default": NoLanguageModel()},
            object_to_model={"Alice": "default"},
        )


def test_concordia_agent_compat_builds_through_adapter() -> None:
    pytest.importorskip("concordia")
    from silisocs.adapters.concordia import ConcordiaAgentAdapter

    spec = AgentConfig(
        class_path="silisocs.agents.concordia.ConcordiaAgent",
        params={
            "name": "Alice",
            "context": "Alice is a compat test participant.",
            "world_context": "A compatibility test is running.",
            "goal": "Act naturally.",
        },
        compat="concordia",
    )

    agent = add_agent(
        runtime=RuntimeObjects(),
        spec=spec,
        models={"default": ScriptedLanguageModel(text_response="scripted compat response")},
        object_to_model={"Alice": "default"},
    )

    assert isinstance(agent, ConcordiaAgentAdapter)
    assert agent.name == "Alice"
    agent.observe("Alice sees one compatibility observation.")
    result = agent.act(ActionSpec(prompt="What should {name} say?", output_type=OutputType.TEXT))
    assert result.output_type == OutputType.TEXT
    assert "scripted compat response" in result.text


def test_concordia_agent_compat_supports_associative_memory() -> None:
    pytest.importorskip("concordia")
    from silisocs.adapters.concordia import ConcordiaAgentAdapter

    spec = AgentConfig(
        class_path="silisocs.agents.concordia.ConcordiaAgent",
        params={
            "name": "Alice",
            "context": "Alice is a compat associative-memory participant.",
            "memory_backend": "associative",
        },
        compat="concordia",
    )

    agent = add_agent(
        runtime=RuntimeObjects(),
        spec=spec,
        models={"default": ScriptedLanguageModel(text_response="associative compat response")},
        object_to_model={"Alice": "default"},
    )

    assert isinstance(agent, ConcordiaAgentAdapter)
    agent.observe("Alice sees one associative-memory observation.")
    result = agent.act(ActionSpec(prompt="What should {name} say?", output_type=OutputType.TEXT))
    assert "associative compat response" in result.text


def test_concordia_agent_compat_rejects_unknown_memory_backend() -> None:
    pytest.importorskip("concordia")
    spec = AgentConfig(
        class_path="silisocs.agents.concordia.ConcordiaAgent",
        params={"name": "Alice", "memory_backend": "mystery"},
        compat="concordia",
    )

    with pytest.raises(ValueError, match="Unsupported Concordia memory backend"):
        add_agent(
            runtime=RuntimeObjects(),
            spec=spec,
            models={"default": NoLanguageModel()},
            object_to_model={"Alice": "default"},
        )


def test_concordia_agent_requires_explicit_compat() -> None:
    pytest.importorskip("concordia")
    spec = AgentConfig(
        class_path="silisocs.agents.concordia.ConcordiaAgent",
        params={"name": "Alice"},
    )

    with pytest.raises(TypeError, match="compat: concordia"):
        add_agent(
            runtime=RuntimeObjects(),
            spec=spec,
            models={"default": NoLanguageModel()},
            object_to_model={"Alice": "default"},
        )


def test_openai_language_model_sample_text_uses_direct_instruct_messages() -> None:
    from silisocs.runtime.language_models import OpenAILanguageModel

    captured: dict[str, Any] = {}

    class _Message:
        content = "answer"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs: Any) -> _Response:
            captured.update(kwargs)
            return _Response()

    model = OpenAILanguageModel.__new__(OpenAILanguageModel)
    model._temperature = 0.1
    model._model_name = "fake"
    model._max_retries = 0
    model._measurements = None
    model.debug = False
    model._extra_kwargs = {}
    cast(Any, model)._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    cast(Any, model)._record_retry_outcome = lambda retries, success: None

    assert model.sample_text("What should Alice do?") == "answer"

    messages = captured["messages"]
    assert messages == [
        {"role": "system", "content": "You are a helpful, instruction-following assistant."},
        {"role": "user", "content": "What should Alice do?"},
    ]
