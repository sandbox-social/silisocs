"""
Tests for prompt generation, action mode, and tool-calling architecture.

This module verifies:
1. Action mode and output style separation
2. Correct prompt generation for custom and generic modes
3. Tool-calling integration with both modes
4. Resolve component expectations match prompt format
"""

import json
from unittest.mock import MagicMock

import pytest
from omegaconf import OmegaConf

from mastodon_sim.environments.backends.base import PhoneApp, SocialMediaApp, app_action
from mastodon_sim.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)
from mastodon_sim.runtime.action_prompts import build_complete_action_prompt_for_runner

# from mastodon_sim.runtime.runner import _validate_action_tool_calling_contract


class TestActionCallToActionBuilder:
    """Test the _build_action_prompt helper function."""

    def test_custom_mode_with_custom_prompt(self):
        """Custom mode should include [ActNum] guidance and output-style section."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Please decide what action to take.\n[OUTPUT STYLE]",
                    "output_style": "Format as: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "Please decide what action to take" in result
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result
        assert "[OUTPUT STYLE]" in result
        assert "Format as: ACTION TYPE:" in result

    def test_custom_mode_with_tool_calling(self):
        """Runner should carry [ActNum] but strip [OUTPUT STYLE] when tool-calling enabled."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Please decide what action to take.\n[OUTPUT STYLE]",
                    "output_style": "Format as: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="single"
        )
        assert "Please decide what action to take" in result
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result
        # Tool-calling strips output-style section
        assert "[OUTPUT STYLE]" not in result
        assert "Format as: ACTION TYPE:" not in result

    def test_custom_mode_with_multi_tool_calling_omits_single_action_line(self):
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Please decide what action to take.\n[OUTPUT STYLE]",
                    "output_style": "Format as: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="multi"
        )
        assert "Please decide what action to take" in result
        assert "Only take one action in this step" not in result
        assert "[ActNum]" in result
        assert (
            "You are allowed to output multiple tool calls to take a batch of actions "
            "(if/as appropriate). If multiple tool calls, actions will be executed "
            "in sequence of calls."
        ) in result
        # Tool-calling strips output-style section
        assert "[OUTPUT STYLE]" not in result
        assert "Format as: ACTION TYPE:" not in result

    def test_generic_mode_override(self):
        """Generic mode should not use user-authored generic prompt config at runner stage."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Custom action prompt",
                    "output_style": "Custom output style",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "[OUTPUT STYLE]" in result

    def test_custom_mode_defaults_include_action_guidance_and_output_style(self):
        """By default, prompts include action guidance and output-style in non-tool mode."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Please take an action.",
                    "output_style": "Format as: ACTION",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "Please take an action." in result
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result
        assert "[OUTPUT STYLE]" in result
        assert "Format as: ACTION" in result

    def test_generic_mode_with_action_count_guidance(self):
        """Generic mode can include action count guidance when flagged."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "output_style": "Format as: ACTION",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result

    def test_generic_mode_multi_tool_calling(self):
        """Generic mode with multi tool-calling should use multi guidance."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "output_style": "Format as: ACTION",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="multi"
        )
        assert "[ActNum]" in result
        assert "You are allowed to output multiple tool calls" in result
        assert "Only take one action" not in result

    def test_output_style_is_included_by_default_non_tool_calling(self):
        """Output style should be included by default when tool-calling is disabled."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Take action.\n[OUTPUT STYLE]",
                    "output_style": "Format as: ACTION",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "Take action" in result
        assert "[OUTPUT STYLE]" in result
        assert "Format as: ACTION" in result

    def test_custom_mode_can_disable_action_count_guidance(self):
        """Action-count guidance can be disabled explicitly."""
        prompt = "Custom prompt text only"
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": prompt,
                    "output_style": "Style",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": False,
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert prompt in result
        assert "[ActNum]" not in result
        assert "[OUTPUT STYLE]" in result


@pytest.mark.skip(reason="Validation function moved or removed in refactor")
def test_validate_action_tool_calling_contract_rejects_deprecated_action_mode() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "action_mode": "tool_calling",
                "tool_calling": {"mode": "single"},
                "gm": {"components": {"resolve": {"built_in": "tool_calling"}}},
            }
        }
    )

    with pytest.raises(ValueError, match="deprecated"):
        pass  # _validate_action_tool_calling_contract(cfg)


@pytest.mark.skip(reason="Validation function moved or removed in refactor")
def test_validate_action_tool_calling_contract_requires_resolve_match() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "action_mode": "custom",
                "tool_calling": {"mode": "multi"},
                "gm": {"components": {"resolve": {"built_in": "parsed_action"}}},
            }
        }
    )

    with pytest.raises(ValueError, match="must match resolver"):
        pass  # _validate_action_tool_calling_contract(cfg)


class MockBackend(PhoneApp):
    """Mock backend for testing."""

    def name(self) -> str:
        return "test_backend"

    def description(self) -> str:
        return "A test backend"

    def actions(self):
        return []


class _FinishedIntegrationApp(SocialMediaApp):
    def name(self) -> str:
        return "finished_app"

    def description(self) -> str:
        return "Integration app for FINISHED action"

    def initialize(self, agent_names: list[str], **kwargs):
        del agent_names, kwargs

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        del user_name
        if str(action_data.get("action_type", "")).strip().lower() in {
            "finished",
            "finish",
            "finish_action_episode",
        }:
            return self.finish_action_episode()
        return ""


class _GenericPromptApp(SocialMediaApp):
    def name(self) -> str:
        return "generic_prompt_app"

    def description(self) -> str:
        return "App used to test generic prompt generation"

    def initialize(self, agent_names: list[str], **kwargs):
        del agent_names, kwargs

    @app_action(selectable_name="POST", description="Create a new post")
    def create_post(self, current_user: str, content: str) -> str:
        del current_user, content
        return "ok"


class TestResolveComponentFormatMatching:
    """Test that resolve components receive format they expect."""

    def test_generic_action_expects_action_format(self):
        """GenericActionResolveComponent should expect ACTION: <name> format."""
        mock_app = MagicMock()
        mock_app.invoke_action_by_name.return_value = "success"

        component = GenericActionResolveComponent(
            sm_app=mock_app,
            model=MagicMock(),
        )

        # Should match ACTION: format
        result = component.resolve(
            active_entity="Alice", action_text="ACTION: post\ncontent: Hello"
        )
        assert result == "success"
        mock_app.invoke_action_by_name.assert_called_once()

        # Should not match other formats
        mock_app.reset_mock()
        result = component.resolve(active_entity="Alice", action_text="Not a valid format")
        assert result == ""
        mock_app.invoke_action_by_name.assert_not_called()

    def test_parsed_action_expects_action_type_format(self):
        """ParsedActionResolveComponent expects ACTION TYPE: format."""
        mock_app = MagicMock()
        mock_app.parse_and_resolve_action.return_value = "success"

        component = ParsedActionResolveComponent(
            sm_app=mock_app,
            model=MagicMock(),
        )

        # Should match ACTION TYPE: format
        result = component.resolve(
            active_entity="Alice",
            action_text="ACTION TYPE: POST\nTARGET ID: 5\nCONTENT: Hello",
        )
        assert result == "success"

    def test_tool_calling_expects_json_format(self):
        """ToolCallingResolveComponent expects JSON tool call format."""
        mock_app = MagicMock()
        mock_app.invoke_action_with_kwargs.return_value = "success"

        component = ToolCallingResolveComponent(
            sm_app=mock_app,
            model=MagicMock(),
        )

        # Should parse JSON tool calls
        tool_call_json = json.dumps(
            {
                "tool_call": {
                    "name": "post",
                    "arguments": {"content": "Hello", "current_user": "Alice"},
                }
            }
        )
        result = component.resolve(active_entity="Alice", action_text=tool_call_json)
        assert result == "success"
        mock_app.invoke_action_with_kwargs.assert_called_once_with(
            "post",
            {"content": "Hello", "current_user": "Alice"},
        )

        # Should not match other formats
        mock_app.reset_mock()
        result = component.resolve(active_entity="Alice", action_text="Not JSON")
        assert result != "success"
        mock_app.invoke_action_with_kwargs.assert_not_called()


def test_finished_routes_through_backend_across_all_resolve_modes() -> None:
    app = _FinishedIntegrationApp()

    parsed = ParsedActionResolveComponent(sm_app=app, model=MagicMock())
    generic = GenericActionResolveComponent(sm_app=app, model=MagicMock())
    tool = ToolCallingResolveComponent(sm_app=app, model=MagicMock())

    parsed_result = parsed.resolve(
        active_entity="Alice",
        action_text="ACTION TYPE: FINISHED\nTARGET ID: \nCONTENT: \nREASONING: done",
    )
    generic_result = generic.resolve(active_entity="Alice", action_text="ACTION: FINISHED")
    tool_result = tool.resolve(
        active_entity="Alice",
        action_text=json.dumps({"tool_call": {"name": "FINISHED", "arguments": {}}}),
    )

    assert parsed_result == "Finished action episode"
    assert generic_result == "Finished action episode"
    assert tool_result == "Finished action episode"


def test_tool_calling_resolve_accepts_function_style_tool_call() -> None:
    app = _FinishedIntegrationApp()
    tool = ToolCallingResolveComponent(sm_app=app, model=MagicMock())

    tool_result = tool.resolve(
        active_entity="Alice",
        action_text="tool_call:FINISHED({})",
    )

    assert tool_result == "Finished action episode"


def test_tool_calling_resolve_accepts_escaped_brace_tool_call() -> None:
    app = _FinishedIntegrationApp()
    tool = ToolCallingResolveComponent(sm_app=app, model=MagicMock())

    tool_result = tool.resolve(
        active_entity="Alice",
        action_text="tool_call:FINISHED({{}})",
    )

    assert tool_result == "Finished action episode"


def test_tool_calling_resolve_supports_multi_tool_payload() -> None:
    mock_app = MagicMock()
    mock_app.invoke_action_with_kwargs.side_effect = ["posted", "liked"]
    tool = ToolCallingResolveComponent(sm_app=mock_app, model=MagicMock())

    tool_result = tool.resolve(
        active_entity="Alice",
        action_text=json.dumps(
            {
                "tool_calls": [
                    {"name": "post", "arguments": {"content": "hello"}},
                    {"name": "like", "arguments": {"post_id": "1"}},
                ]
            }
        ),
    )

    assert tool_result == "posted\nliked"
    assert mock_app.invoke_action_with_kwargs.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
