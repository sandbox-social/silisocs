import json

from concordia.typing import entity as entity_lib

from mastodon_sim.agents.components.concat_act import SocialConcatActComponent
from mastodon_sim.environments.gm.act import SMAct
from mastodon_sim.environments.gm.components.resolve import ToolCallingResolveComponent


class _FakeModel:
    def sample_tool_call(self, prompt: str, tools: list[dict]):
        del prompt, tools
        return "toot", {"status": "Hello from tool mode"}


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

    assert parsed["tool_call"]["name"] == "toot"
    assert parsed["tool_call"]["arguments"]["status"] == "Hello from tool mode"
    assert parsed["tool_call"]["arguments"]["current_user"] == "Alice"


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
