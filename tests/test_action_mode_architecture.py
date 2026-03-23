"""
Tests for prompt generation, action mode, and tool-calling architecture.

This module verifies:
1. Action mode and output style separation
2. Correct prompt generation for custom and generic modes
3. Tool-calling integration with both modes
4. Resolve component expectations match prompt format
"""

import json
import re
from unittest.mock import MagicMock, patch
import pytest
from omegaconf import OmegaConf

from mastodon_sim.environments.gm.act import SMAct
from mastodon_sim.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)
from mastodon_sim.environments.backends.base import PhoneApp
from mastodon_sim.runtime.runner import _build_action_call_to_action


class TestActionCallToActionBuilder:
    """Test the _build_action_call_to_action helper function."""

    def test_custom_mode_with_custom_prompt(self):
        """Custom mode should return action_prompt + output_style."""
        cfg = OmegaConf.create({
            "social_media": {
                "action_prompt": "Please decide what action to take.",
                "output_style": "Format as: ACTION TYPE: ...",
            }
        })
        result = _build_action_call_to_action(cfg, action_mode="custom", enable_tool_calling=False)
        assert "Please decide what action to take" in result
        assert "Format as: ACTION TYPE:" in result

    def test_custom_mode_with_tool_calling(self):
        """Custom mode with tool-calling should replace output_style."""
        cfg = OmegaConf.create({
            "social_media": {
                "action_prompt": "Please decide what action to take.",
                "output_style": "Format as: ACTION TYPE: ...",
            }
        })
        result = _build_action_call_to_action(cfg, action_mode="custom", enable_tool_calling=True)
        assert "Please decide what action to take" in result
        assert "tool_call" in result
        assert "Format as: ACTION TYPE:" not in result

    def test_fallback_to_legacy_prompt(self):
        """Should fallback to legacy action_call_to_action if new fields don't exist."""
        cfg = OmegaConf.create({
            "social_media": {
                "action_call_to_action": "Legacy prompt format",
            }
        })
        result = _build_action_call_to_action(cfg, action_mode="custom", enable_tool_calling=False)
        assert result == "Legacy prompt format"

    def test_generic_mode_override(self):
        """Generic mode means we should not use custom call_to_action."""
        cfg = OmegaConf.create({
            "social_media": {
                "action_prompt": "Custom action prompt",
                "output_style": "Custom output style",
            }
        })
        # When action_mode is "generic", the runner will not pass call_to_action_str,
        # but the helper still works with what it's given
        result = _build_action_call_to_action(cfg, action_mode="generic", enable_tool_calling=False)
        # In generic mode, the backend generates the prompt, not this function
        # But if given config, it should still work
        assert result is not None


class MockBackend(PhoneApp):
    """Mock backend for testing."""

    def name(self) -> str:
        return "test_backend"

    def description(self) -> str:
        return "A test backend"

    def actions(self):
        return []


class TestPromptGeneration:
    """Test SMAct prompt generation logic."""

    def test_generic_mode_without_tool_calling(self):
        """Generic mode without tool-calling should use generate_generic_action_prompt."""
        mock_app = MagicMock(spec=MockBackend)
        mock_app.generate_generic_action_prompt.return_value = "Available actions: post(...), comment(...)"

        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=mock_app,
            action_mode="generic",
            enable_tool_calling=False,
        )

        result = act._next_entity_action_spec({}, MagicMock())

        assert "Available actions:" in result
        mock_app.generate_generic_action_prompt.assert_called_once()
        assert "TOOL_CALLING_MODE" not in result

    def test_generic_mode_with_tool_calling(self):
        """Generic mode with tool-calling should add tool schemas."""
        mock_app = MagicMock(spec=MockBackend)
        mock_app.generate_generic_action_prompt.return_value = "Available actions: post(...)"
        mock_app.generate_tool_schemas.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "post",
                    "description": "Create a post",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=mock_app,
            action_mode="generic",
            enable_tool_calling=True,
        )

        result = act._next_entity_action_spec({}, MagicMock())

        assert "TOOL_CALLING_MODE" in result
        assert "TOOL_SCHEMAS_JSON" in result
        assert "Available actions:" in result
        mock_app.generate_tool_schemas.assert_called_once()

    def test_custom_mode_without_tool_calling(self):
        """Custom mode should use call_to_action_str."""
        mock_app = MagicMock(spec=MockBackend)
        call_to_action = "Determine what action to take.\nFinal decision: ACTION TYPE: ..."

        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=mock_app,
            call_to_action_str=call_to_action,
            action_mode="custom",
            enable_tool_calling=False,
        )

        result = act._next_entity_action_spec({}, MagicMock())

        assert call_to_action in result
        assert "TOOL_CALLING_MODE" not in result

    def test_custom_mode_with_tool_calling(self):
        """Custom mode with tool-calling should add tool schemas to custom prompt."""
        mock_app = MagicMock(spec=MockBackend)
        call_to_action = "Determine what action to take.\nResond with: ACTION TYPE: ..."
        mock_app.generate_tool_schemas.return_value = [
            {
                "type": "function",
                "function": {"name": "post", "description": "Post", "parameters": {}},
            }
        ]

        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=mock_app,
            call_to_action_str=call_to_action,
            action_mode="custom",
            enable_tool_calling=True,
        )

        result = act._next_entity_action_spec({}, MagicMock())

        assert "TOOL_CALLING_MODE" in result
        assert call_to_action in result
        mock_app.generate_tool_schemas.assert_called_once()


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
        result = component.resolve(active_entity="Alice", action_text="ACTION: post\ncontent: Hello")
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
        tool_call_json = json.dumps({
            "tool_call": {
                "name": "post",
                "arguments": {"content": "Hello", "current_user": "Alice"},
            }
        })
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


class TestToolSchemasGeneration:
    """Test that tool schemas are valid and usable."""

    def test_tool_schemas_are_valid_json(self):
        """Generated tool schemas should be valid JSON."""
        mock_app = MagicMock()
        mock_app.generate_tool_schemas.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "post",
                    "description": "Create a post",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Post content"}
                        },
                        "required": ["content"],
                    },
                },
            }
        ]

        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=mock_app,
            action_mode="generic",
            enable_tool_calling=True,
        )

        result = act._next_entity_action_spec({}, MagicMock())

        # Extract JSON from markers
        match = re.search(r"### TOOL_SCHEMAS_JSON ###\n(.*)\n### END_TOOL_SCHEMAS_JSON ###", result, re.DOTALL)
        assert match
        schemas_json = match.group(1)
        schemas = json.loads(schemas_json)
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert "type" in schemas[0]
        assert "function" in schemas[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
