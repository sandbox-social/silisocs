import json

from concordia.typing import entity as entity_lib

from silisocs.agents.components.concat_act import (
    STRUCTURED_RESPONSE_MARKER,
    STRUCTURED_SCHEMA_END,
    STRUCTURED_SCHEMA_START,
    SocialConcatActComponent,
    extract_structured_response,
)
from silisocs.environments.gm.act import SMAct
from silisocs.environments.gm.components.resolve import ToolCallingResolveComponent
from silisocs.evaluations.probes.types import StructuredProbe


class _FakeModel:
    def __init__(self, mode: str = "single") -> None:
        self.mode = mode

    def sample_tool_call(self, prompt: str, tools: list[dict]):
        del prompt, tools
        if self.mode == "multi":
            return [
                ("toot", {"status": "Hello from tool mode"}),
                ("like", {"post_id": "1"}),
            ]
        if self.mode == "multi_single":
            return [("toot", {"status": "Only one but list-shaped"})]
        return [("toot", {"status": "Hello from tool mode"})]

    def sample_structured_response(self, prompt: str, schema: dict):
        del prompt, schema
        return {"belief": 1, "opinion": "I somewhat support this.", "reasoning": "Because."}


class _FakeEntity:
    name = "Alice"


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


def test_social_concat_act_emits_tool_call_json_when_marker_present() -> None:
    component = SocialConcatActComponent(model=_FakeModel(), randomize_choices=False)
    component.set_entity(_FakeEntity())

    tools = json.dumps(_FakeApp().generate_tool_schemas())
    action_spec = entity_lib.ActionSpec(
        call_to_action=(
            "### TOOL_CALLING_MODE ###\n"
            "Pick one action for {name}.\n"
            "### TOOL_SCHEMAS_JSON ###\n"
            f"{tools}\n"
            "### END_TOOL_SCHEMAS_JSON ###"
        ),
        output_type=entity_lib.OutputType.FREE,
    )

    result = component.get_action_attempt({"Observation": "timeline"}, action_spec)
    parsed = json.loads(result)

    assert parsed["tool_calls"][0]["name"] == "toot"
    assert parsed["tool_calls"][0]["arguments"]["status"] == "Hello from tool mode"
    assert parsed["tool_calls"][0]["arguments"]["current_user"] == "Alice"


def test_smact_embeds_tool_schemas_for_custom_prompt_when_enabled() -> None:
    act = SMAct(
        model=_FakeModel(),
        entity_names=["Alice"],
        sm_app=_FakeApp(),
        call_to_action_str="Custom action prompt for {name}",
        action_mode="custom",
        enable_tool_calling=True,
    )

    result = act._next_entity_action_spec(
        contexts={},
        action_spec=entity_lib.ActionSpec(
            call_to_action="", output_type=entity_lib.OutputType.FREE
        ),
    )

    assert "### TOOL_CALLING_MODE ###" in result
    assert "### TOOL_SCHEMAS_JSON ###" in result
    assert "Custom action prompt for {name}" in result


def test_social_concat_act_emits_multi_tool_call_json_when_model_returns_multiple() -> None:
    component = SocialConcatActComponent(model=_FakeModel(mode="multi"), randomize_choices=False)
    component.set_entity(_FakeEntity())

    tools = json.dumps(_FakeApp().generate_tool_schemas())
    action_spec = entity_lib.ActionSpec(
        call_to_action=(
            "### TOOL_CALLING_MODE ###\n"
            "Pick one action for {name}.\n"
            "### TOOL_SCHEMAS_JSON ###\n"
            f"{tools}\n"
            "### END_TOOL_SCHEMAS_JSON ###"
        ),
        output_type=entity_lib.OutputType.FREE,
    )

    result = component.get_action_attempt({"Observation": "timeline"}, action_spec)
    parsed = json.loads(result)

    assert len(parsed["tool_calls"]) == 2
    assert parsed["tool_calls"][0]["name"] == "toot"
    assert parsed["tool_calls"][0]["arguments"]["current_user"] == "Alice"


def test_social_concat_act_emits_tool_calls_when_model_returns_single_item_list() -> None:
    component = SocialConcatActComponent(
        model=_FakeModel(mode="multi_single"), randomize_choices=False
    )
    component.set_entity(_FakeEntity())

    tools = json.dumps(_FakeApp().generate_tool_schemas())
    action_spec = entity_lib.ActionSpec(
        call_to_action=(
            "### TOOL_CALLING_MODE ###\n"
            "Pick one action for {name}.\n"
            "### TOOL_SCHEMAS_JSON ###\n"
            f"{tools}\n"
            "### END_TOOL_SCHEMAS_JSON ###"
        ),
        output_type=entity_lib.OutputType.FREE,
    )

    result = component.get_action_attempt({"Observation": "timeline"}, action_spec)
    parsed = json.loads(result)

    assert "tool_calls" in parsed
    assert parsed["tool_calls"][0]["name"] == "toot"


def test_social_concat_act_emits_structured_response_json() -> None:
    component = SocialConcatActComponent(model=_FakeModel(), randomize_choices=False)
    component.set_entity(_FakeEntity())

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
        call_to_action=(
            f"{STRUCTURED_RESPONSE_MARKER}\n"
            "Update {name}'s stance.\n"
            f"{STRUCTURED_SCHEMA_START}\n"
            f"{json.dumps(schema)}\n"
            f"{STRUCTURED_SCHEMA_END}"
        ),
        output_type=entity_lib.OutputType.FREE,
    )

    result = component.get_action_attempt({"Memory": "prior context"}, action_spec)
    parsed = json.loads(result)

    assert parsed["structured_response"]["belief"] == 1
    assert parsed["structured_response"]["opinion"] == "I somewhat support this."


def test_extract_structured_response_accepts_wrapped_and_plain_json() -> None:
    wrapped = json.dumps({"structured_response": {"belief": -1}})
    plain = json.dumps({"belief": 2})

    assert extract_structured_response(wrapped) == {"belief": -1}
    assert extract_structured_response(plain) == {"belief": 2}


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

    assert STRUCTURED_RESPONSE_MARKER in spec.call_to_action
    assert probe.parse_answer(json.dumps({"structured_response": {"belief": 0}})) == {"belief": 0}


def test_tool_calling_resolve_executes_tool_kwargs() -> None:
    resolve = ToolCallingResolveComponent(sm_app=_FakeApp())

    action_text = json.dumps(
        {
            "tool_call": {
                "name": "toot",
                "arguments": {
                    "current_user": "Alice",
                    "status": "Hello from tool mode",
                },
            }
        }
    )

    result = resolve.resolve(active_entity="Alice", action_text=action_text)
    assert result == "toot:Alice:Hello from tool mode"


def test_tool_calling_resolve_executes_function_style_tool_call() -> None:
    resolve = ToolCallingResolveComponent(sm_app=_FakeApp())

    action_text = 'tool_call:toot({"current_user": "Alice", "status": "Function format"})'

    result = resolve.resolve(active_entity="Alice", action_text=action_text)
    assert result == "toot:Alice:Function format"


def test_tool_calling_resolve_executes_escaped_brace_tool_call() -> None:
    resolve = ToolCallingResolveComponent(sm_app=_FakeApp())

    action_text = "tool_call:toot({{'current_user': 'Alice', 'status': 'Escaped format'}})"

    result = resolve.resolve(active_entity="Alice", action_text=action_text)
    assert result == "toot:Alice:Escaped format"


def test_tool_calling_resolve_allows_finished_tool_call() -> None:
    resolve = ToolCallingResolveComponent(sm_app=_FakeApp())

    action_text = json.dumps(
        {
            "tool_call": {
                "name": "FINISHED",
                "arguments": {},
            }
        }
    )

    result = resolve.resolve(active_entity="Alice", action_text=action_text)
    assert result == "FINISHED:None:None"


def test_tool_calling_resolve_executes_multi_tool_calls_in_order() -> None:
    resolve = ToolCallingResolveComponent(sm_app=_FakeApp())

    action_text = json.dumps(
        {
            "tool_calls": [
                {
                    "name": "toot",
                    "arguments": {
                        "current_user": "Alice",
                        "status": "First",
                    },
                },
                {
                    "name": "toot",
                    "arguments": {
                        "current_user": "Alice",
                        "status": "Second",
                    },
                },
            ]
        }
    )

    result = resolve.resolve(active_entity="Alice", action_text=action_text)
    assert result == "toot:Alice:First\ntoot:Alice:Second"
