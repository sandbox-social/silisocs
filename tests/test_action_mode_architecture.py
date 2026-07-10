"""
Tests for prompt generation, action mode, and tool-calling architecture.

This module verifies:
1. Action mode and output style separation
2. Correct prompt generation for custom and generic modes
3. Tool-calling integration with both modes
4. Resolve component expectations match prompt format
"""

from unittest.mock import MagicMock

import pytest
from omegaconf import OmegaConf

from silisocs.environments.backends.base import BackendApp, SocialBackendApp, app_action
from silisocs.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)
from silisocs.runtime.prompts.action_prompts import build_complete_action_prompt_for_runner
from silisocs.runtime.types import ActionOutput, ResolveReport, ToolCall

# from silisocs.runtime.runner import _validate_action_tool_calling_contract


class TestActionCallToActionBuilder:
    """Test the _build_action_prompt helper function."""

    def test_custom_mode_with_custom_prompt(self):
        """Custom mode should include [ActNum] guidance and output-style section."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": "Please decide what action to take.\n[OUTPUT STYLE]",
                                    "output_style": "Format as: ACTION TYPE: ...",
                                }
                            }
                        }
                    },
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": "Please decide what action to take.\n[OUTPUT STYLE]",
                                    "output_style": "Format as: ACTION TYPE: ...",
                                }
                            }
                        }
                    },
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": "Please decide what action to take.\n[OUTPUT STYLE]",
                                    "output_style": "Format as: ACTION TYPE: ...",
                                }
                            }
                        }
                    },
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": "Custom action prompt",
                                    "output_style": "Custom output style",
                                }
                            }
                        }
                    }
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": "Please take an action.",
                                    "output_style": "Format as: ACTION",
                                }
                            }
                        }
                    }
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
                    "gm": {
                        "components": {
                            "action_prompt": {"params": {"output_style": "Format as: ACTION"}}
                        }
                    },
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
                    "gm": {
                        "components": {
                            "action_prompt": {"params": {"output_style": "Format as: ACTION"}}
                        }
                    },
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": "Take action.\n[OUTPUT STYLE]",
                                    "output_style": "Format as: ACTION",
                                }
                            }
                        }
                    }
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "action_prompt": prompt,
                                    "output_style": "Style",
                                }
                            }
                        }
                    },
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


class MockBackend(BackendApp):
    """Mock backend for testing."""

    def name(self) -> str:
        return "test_backend"

    def description(self) -> str:
        return "A test backend"

    def actions(self):
        return []


class _FinishedIntegrationApp(SocialBackendApp):
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


class _GenericPromptApp(SocialBackendApp):
    def name(self) -> str:
        return "generic_prompt_app"

    def description(self) -> str:
        return "App used to test generic prompt generation"

    def initialize(self, agent_names: list[str], **kwargs):
        del agent_names, kwargs

    @app_action(selectable_name="POST", description="Create a new post")
    def create_post(self, agent_name: str, content: str) -> str:
        del agent_name, content
        return "ok"


class TestResolveComponentFormatMatching:
    """Test that resolve components receive format they expect."""

    def test_generic_action_expects_action_format(self):
        """GenericActionResolveComponent should expect ACTION: <name> format."""
        mock_app = MagicMock()
        mock_app.invoke_action_by_name.return_value = "success"

        component = GenericActionResolveComponent(
            backend=mock_app,
            model=MagicMock(),
        )

        # Should match ACTION: format
        result = component.resolve(
            active_agent="Alice", action=ActionOutput.from_text("ACTION: post\ncontent: Hello")
        )
        assert result == "success"
        mock_app.invoke_action_by_name.assert_called_once()

        # Should not match other formats
        mock_app.reset_mock()
        result = component.resolve(
            active_agent="Alice", action=ActionOutput.from_text("Not a valid format")
        )
        assert result == ""
        mock_app.invoke_action_by_name.assert_not_called()

    def test_parsed_action_expects_action_type_format(self):
        """ParsedActionResolveComponent expects ACTION TYPE: format."""
        mock_app = MagicMock()
        mock_app.parse_and_resolve_action.return_value = "success"

        component = ParsedActionResolveComponent(
            backend=mock_app,
            model=MagicMock(),
        )

        # Should match ACTION TYPE: format
        result = component.resolve(
            active_agent="Alice",
            action=ActionOutput.from_text("ACTION TYPE: POST\nTARGET ID: 5\nCONTENT: Hello"),
        )
        assert result == "success"

    def test_tool_calling_expects_typed_tool_calls(self):
        """ToolCallingResolveComponent expects typed tool calls."""
        app = _GenericPromptApp()

        component = ToolCallingResolveComponent(
            backend=app,
            model=MagicMock(),
        )

        result = component.resolve(
            active_agent="Alice",
            action=ActionOutput.from_tool_calls([ToolCall("POST", {"content": "Hello"})]),
        )
        assert result == "ok"

        # Should fail loudly on other formats
        with pytest.raises(TypeError, match="ActionOutput.TOOL_CALLS"):
            component.resolve(active_agent="Alice", action=ActionOutput.from_text("Not JSON"))

        with pytest.raises(ValueError, match="runtime-owned actor"):
            component.resolve(
                active_agent="Alice",
                action=ActionOutput.from_tool_calls(
                    [ToolCall("POST", {"content": "Hello", "agent_name": "Alice"})]
                ),
            )


def test_finished_routes_through_backend_across_all_resolve_modes() -> None:
    app = _FinishedIntegrationApp()

    parsed = ParsedActionResolveComponent(backend=app, model=MagicMock())
    generic = GenericActionResolveComponent(backend=app, model=MagicMock())
    tool = ToolCallingResolveComponent(backend=app, model=MagicMock())

    parsed_result = parsed.resolve(
        active_agent="Alice",
        action=ActionOutput.from_text(
            "ACTION TYPE: FINISHED\nTARGET ID: \nCONTENT: \nREASONING: done"
        ),
    )
    generic_result = generic.resolve(
        active_agent="Alice", action=ActionOutput.from_text("ACTION: FINISHED")
    )
    tool_result = tool.resolve(
        active_agent="Alice",
        action=ActionOutput.from_tool_calls([ToolCall("FINISHED", {})]),
    )

    assert parsed_result == "Finished action episode"
    assert generic_result == "Finished action episode"
    assert tool_result == "Finished action episode"


def test_tool_calling_resolve_supports_multi_tool_payload() -> None:
    mock_app = MagicMock()
    # Resolve dispatches through the committed-aware seam: (committed, result).
    mock_app.invoke_action_detailed.side_effect = [(True, "posted"), (True, "liked")]
    tool = ToolCallingResolveComponent(backend=mock_app, model=MagicMock())

    tool_result = tool.resolve(
        active_agent="Alice",
        action=ActionOutput.from_tool_calls(
            [
                ToolCall("post", {"content": "hello"}),
                ToolCall("like", {"post_id": "1"}),
            ]
        ),
    )

    assert tool_result == "posted\nliked"
    assert mock_app.invoke_action_detailed.call_count == 2
    assert isinstance(tool_result, ResolveReport)
    assert tool_result.committed == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
