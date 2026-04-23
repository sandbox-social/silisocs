"""Integration tests for centralized action prompt pipeline.

Verifies that:
1. Prompt compilation at runner time produces expected output
2. SMAct pass-through behavior is correct
3. Tool-calling additions work at runtime
4. All prompt states match expected format
5. Prompt modifications maintain structure (markers, sections)
"""

import json
from unittest.mock import MagicMock

import pytest
from omegaconf import OmegaConf

from mastodon_sim.environments.backends.base import SocialMediaApp, app_action
from mastodon_sim.environments.gm.act import SMAct
from mastodon_sim.runtime.action_prompts import (
    PromptAdditions,
    build_complete_action_prompt_for_runner,
    compile_action_prompt,
)


class _IntegrationTestApp(SocialMediaApp):
    """Minimal test app for integration testing."""

    def name(self) -> str:
        return "integration_test_app"

    def description(self) -> str:
        return "Test app for prompt pipeline"

    def initialize(self, agent_names: list[str], **kwargs):
        del agent_names, kwargs

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        del user_name, action_data
        return "ok"

    @app_action(selectable_name="POST", description="Create a post")
    def create_post(self, current_user: str, content: str) -> str:
        del current_user, content
        return "ok"

    @app_action(selectable_name="LIKE", description="Like a post")
    def like_post(self, current_user: str, post_id: str) -> str:
        del current_user, post_id
        return "ok"


class TestPromptCompilationMatrixIntegration:
    """Test all combinations of prompt compilation modes."""

    def test_custom_mode_no_additions_produces_baseline_prompt(self):
        """Custom mode without additions should output just the base prompt."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Take an action.",
                    "output_style": "Ignored in custom no-additions mode",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert result == "Take an action."

    def test_custom_mode_with_action_count_guidance_adds_marker_and_guidance(self):
        """With action_count_guidance flag, should add [ActNum] marker and guidance."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Take an action.",
                    "output_style": "Ignored",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result
        assert "Take an action." in result

    def test_custom_mode_with_output_style_flag_adds_marker_section(self):
        """With output_style flag, should add [OUTPUT STYLE] marker and content."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Take action.",
                    "output_style": "Format: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "output_style": {"add_to_prompt": True},
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "[OUTPUT STYLE]" in result
        assert "Format: ACTION TYPE:" in result
        assert "Take action." in result

    def test_custom_mode_tool_calling_strips_output_style(self):
        """Tool-calling mode should strip output style even if flag is set."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Take action.",
                    "output_style": "Format: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                        "output_style": {"add_to_prompt": True},
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="single"
        )
        # Should have action guidance and action prompt
        assert "[ActNum]" in result
        assert "Only take one action" in result
        assert "Take action." in result
        # But NOT output style (tool-calling strips it)
        assert "[OUTPUT STYLE]" not in result
        assert "Format: ACTION TYPE:" not in result

    def test_generic_mode_with_deferred_prompt(self):
        """Generic mode without generic_action_prompt uses deferred text."""
        cfg = OmegaConf.create(
            {"social_media": {"action_prompt": "Ignored", "output_style": "Ignored"}}
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "generic prompt generation deferred" in result

    def test_generic_mode_with_provided_prompt(self):
        """Generic mode with provided generic_action_prompt uses it."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "generic_action_prompt": "Available actions: POST, LIKE",
                    "output_style": "Ignored",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "Available actions: POST, LIKE" in result

    def test_generic_mode_with_multi_tool_calling_guidance(self):
        """Generic mode with multi tool-calling should use multi guidance."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "generic_action_prompt": "Actions: POST, LIKE",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="multi"
        )
        assert "[ActNum]" in result
        assert "You are allowed to output multiple tool calls" in result
        assert "Actions: POST, LIKE" in result

    def test_prompt_structure_with_both_additions(self):
        """Prompt with both action guidance and output style should be ordered correctly."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Base prompt.",
                    "output_style": "Format instructions",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                        "output_style": {"add_to_prompt": True},
                    }
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        # Should have proper ordering: base -> actnum -> output_style
        base_idx = result.index("Base prompt.")
        actnum_idx = result.index("[ActNum]")
        output_style_idx = result.index("[OUTPUT STYLE]")
        assert base_idx < actnum_idx < output_style_idx


class TestSMActPassThroughBehavior:
    """Test that SMAct correctly returns pre-compiled prompts."""

    def test_smact_returns_prompt_with_correct_format(self):
        """SMAct should return prompt in 'prompt: ... ;;type: free' format."""
        app = _IntegrationTestApp()
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str="Test prompt here",
            action_mode="custom",
            enable_tool_calling=False,
        )
        result = act._next_entity_action_spec({}, MagicMock())
        assert result == "prompt: Test prompt here ;;type: free"

    def test_smact_with_multiline_prompt(self):
        """SMAct should handle multiline prompts correctly."""
        multiline_prompt = "Line 1\nLine 2\nLine 3"
        app = _IntegrationTestApp()
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str=multiline_prompt,
            action_mode="custom",
            enable_tool_calling=False,
        )
        result = act._next_entity_action_spec({}, MagicMock())
        assert "Line 1\nLine 2\nLine 3" in result
        assert ";;type: free" in result

    def test_smact_with_tool_calling_adds_markers(self):
        """With tool_calling enabled, SMAct should add markers and schemas."""
        app = _IntegrationTestApp()
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str="Base prompt",
            action_mode="custom",
            enable_tool_calling=True,
        )
        result = act._next_entity_action_spec({}, MagicMock())
        assert "### TOOL_CALLING_MODE ###" in result
        assert "### TOOL_SCHEMAS_JSON ###" in result
        assert "### END_TOOL_SCHEMAS_JSON ###" in result
        assert "Base prompt" in result

    def test_smact_tool_schemas_are_valid_json(self):
        """Tool schemas in SMAct output should be valid JSON."""
        app = _IntegrationTestApp()
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str="Base prompt",
            action_mode="custom",
            enable_tool_calling=True,
        )
        result = act._next_entity_action_spec({}, MagicMock())
        # Extract JSON between markers
        start = result.index("### TOOL_SCHEMAS_JSON ###") + len("### TOOL_SCHEMAS_JSON ###")
        end = result.index("### END_TOOL_SCHEMAS_JSON ###")
        json_str = result[start:end].strip()
        schemas = json.loads(json_str)
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert all("function" in schema for schema in schemas)

    def test_smact_without_app_instance_no_tool_calling(self):
        """SMAct without app instance should not add tool-calling additions."""
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=None,
            call_to_action_str="Base prompt",
            action_mode="custom",
            enable_tool_calling=True,
        )
        result = act._next_entity_action_spec({}, MagicMock())
        # Should just return the prompt without tool-calling additions
        assert "Base prompt" in result
        assert "### TOOL_CALLING_MODE ###" not in result


class TestPromptPipelineEndToEnd:
    """Test complete flow from config to SMAct output."""

    def test_runner_compile_then_smact_passthrough(self):
        """Test complete flow: runner compiles prompt, SMAct passes through."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Decide action for {name}.",
                    "output_style": "Format: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                        "output_style": {"add_to_prompt": True},
                    }
                },
            }
        )

        # Step 1: Runner builds prompt
        compiled_prompt = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "[ActNum]" in compiled_prompt
        assert "[OUTPUT STYLE]" in compiled_prompt
        assert "Only take one action" in compiled_prompt

        # Step 2: SMAct uses compiled prompt
        app = _IntegrationTestApp()
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str=compiled_prompt,
            action_mode="custom",
            enable_tool_calling=False,
        )
        result = act._next_entity_action_spec({}, MagicMock())

        # Step 3: SMAct output should contain everything from runner
        assert "[ActNum]" in result
        assert "[OUTPUT STYLE]" in result
        assert "Only take one action" in result
        assert ";;type: free" in result

    def test_runner_with_tool_calling_and_smact_wrapping(self):
        """Test flow with tool-calling: runner builds base, SMAct adds tool markers."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Decide action.",
                    "output_style": "Format: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                        "output_style": {"add_to_prompt": True},
                    }
                },
            }
        )

        # Step 1: Runner builds prompt (note: output style stripped for tool-calling)
        compiled_prompt = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="single"
        )
        assert "[ActNum]" in compiled_prompt
        assert "[OUTPUT STYLE]" not in compiled_prompt  # Stripped by compile

        # Step 2: SMAct wraps with tool-calling
        app = _IntegrationTestApp()
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str=compiled_prompt,
            action_mode="custom",
            enable_tool_calling=True,
        )
        result = act._next_entity_action_spec({}, MagicMock())

        # Step 3: Final output should have markers from both stages
        assert "### TOOL_CALLING_MODE ###" in result
        assert "### TOOL_SCHEMAS_JSON ###" in result
        assert "[ActNum]" in result
        assert "Decide action." in result


class TestPromptStateTransitions:
    """Test that prompt state transitions are handled correctly."""

    def test_no_markers_mode_works(self):
        """Prompt without any special markers should work."""
        prompt = "Please take an action now."
        result = compile_action_prompt(
            base_prompt=prompt,
            output_style="",
            tool_calling_mode="none",
            additions=PromptAdditions(),
        )
        assert result == prompt

    def test_inline_output_style_marker_preserved(self):
        """Inline [OUTPUT STYLE] marker should be preserved."""
        prompt = "Action prompt.\n[OUTPUT STYLE]\nFormat: ACTION"
        result = compile_action_prompt(
            base_prompt=prompt,
            output_style="Config style",
            tool_calling_mode="none",
            additions=PromptAdditions(add_output_style=True),
        )
        assert "[OUTPUT STYLE]" in result

    def test_marker_ordering_is_stable(self):
        """Marker ordering should be deterministic."""
        cfg = OmegaConf.create(
            {
                "social_media": {
                    "action_prompt": "Decide action.",
                    "output_style": "Format: ACTION TYPE",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": {"add_to_prompt": True},
                        "output_style": {"add_to_prompt": True},
                    }
                },
            }
        )

        # Run multiple times
        results = [
            build_complete_action_prompt_for_runner(
                cfg=cfg, action_mode="custom", tool_calling_mode="none"
            )
            for _ in range(5)
        ]

        # All should be identical
        assert all(r == results[0] for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
