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

from silisocs.environments.backends.base import SocialMediaApp, app_action
from silisocs.environments.gm.act import SMAct
from silisocs.environments.gm.base_game_master import BaseSocialMediaGameMaster
from silisocs.runtime.action_prompts import (
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


class _GenericBuildApp:
    def generate_generic_action_prompt(self) -> str:
        return (
            "Available actions:\n"
            "  POST(content)\n"
            "  LIKE(post_id)\n\n"
            "[OUTPUT STYLE]\n"
            "Respond with EXACTLY ONE action."
        )


class TestPromptCompilationMatrixIntegration:
    """Test all combinations of prompt compilation modes."""

    def test_custom_mode_no_additions_produces_baseline_prompt(self):
        """Custom mode defaults include guidance and output style in non-tool mode."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Take an action.",
                    "output_style": "Use ACTION TYPE format",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "Take an action." in result
        assert "[ActNum]" in result
        assert "[OUTPUT STYLE]" in result

    def test_custom_mode_with_action_count_guidance_adds_marker_and_guidance(self):
        """With action_count_guidance flag, should add [ActNum] marker and guidance."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Take an action.",
                    "output_style": "Ignored",
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
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result
        assert "Take an action." in result

    def test_custom_mode_includes_output_style_by_default(self):
        """Output-style section should be included by default in non-tool mode."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Take action.",
                    "output_style": "Format: ACTION TYPE: ...",
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
                "env": {
                    "action_prompt": "Take action.",
                    "output_style": "Format: ACTION TYPE: ...",
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
        # Should have action guidance and action prompt
        assert "[ActNum]" in result
        assert "Only take one action" in result
        assert "Take action." in result
        # But NOT output style (tool-calling strips it)
        assert "[OUTPUT STYLE]" not in result
        assert "Format: ACTION TYPE:" not in result

    def test_generic_mode_runner_compiler_ignores_config_prompt_text(self):
        """Runner-side generic compile should not consume user-authored generic prompts."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Ignored",
                    "output_style": "Runner style",
                }
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "[OUTPUT STYLE]" in result

    def test_generic_mode_runner_compiler_can_disable_action_guidance(self):
        """Only action guidance toggle should affect runner generic skeleton output."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "output_style": "Runner style",
                },
                "sim": {"prompt_additions": {"action_count_guidance": False}},
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "[ActNum]" not in result
        assert "[OUTPUT STYLE]" in result

    def test_generic_mode_with_multi_tool_calling_guidance(self):
        """Runner-side generic compile in tool mode only carries action guidance."""
        cfg = OmegaConf.create(
            {
                "env": {"output_style": "Style ignored under tool-calling"},
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
        assert "[OUTPUT STYLE]" not in result

    def test_prompt_structure_with_both_additions(self):
        """Prompt with both action guidance and output style should be ordered correctly."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Base prompt.",
                    "output_style": "Format instructions",
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
        # Should have proper ordering: base -> actnum -> output_style
        base_idx = result.index("Base prompt.")
        actnum_idx = result.index("[ActNum]")
        output_style_idx = result.index("[OUTPUT STYLE]")
        assert base_idx < actnum_idx < output_style_idx

    def test_runner_uses_per_gm_prompt_overrides_when_provided(self):
        """Per-GM prompt config should override environment defaults."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Default base prompt.",
                    "output_style": "Default style",
                },
            }
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg,
            action_mode="custom",
            tool_calling_mode="none",
            gm_prompt_cfg={
                "action_prompt": "GM-specific base prompt.",
                "output_style": "GM-specific style",
            },
        )
        assert "GM-specific base prompt." in result
        assert "GM-specific style" in result
        assert "Default base prompt." not in result

    def test_base_gm_build_generic_prompt_uses_backend_generated_actions(self):
        cfg = OmegaConf.create(
            {
                "env": {
                    "output_style": "FINAL: ACTION + params",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
                    }
                },
            }
        )
        gm = BaseSocialMediaGameMaster()
        prompt = gm.build_generic_prompt(
            cfg=cfg,
            sm_app=_GenericBuildApp(),
            tool_calling_mode="none",
        )
        assert "Available actions:" in prompt
        assert "POST(content)" in prompt
        assert "[ActNum]" in prompt
        assert "[OUTPUT STYLE]" in prompt
        assert "FINAL: ACTION + params" in prompt


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

    def test_smact_does_not_double_add_tool_markers_or_schemas(self):
        """SMAct should preserve existing wrapper blocks without duplication."""
        app = _IntegrationTestApp()
        wrapped_prompt = (
            "### TOOL_CALLING_MODE ###\n"
            "Base prompt\n"
            "### TOOL_SCHEMAS_JSON ###\n"
            "[]\n"
            "### END_TOOL_SCHEMAS_JSON ###"
        )
        act = SMAct(
            model=MagicMock(),
            entity_names=["Alice"],
            sm_app=app,
            call_to_action_str=wrapped_prompt,
            action_mode="custom",
            enable_tool_calling=True,
        )
        result = act._next_entity_action_spec({}, MagicMock())
        assert result.count("### TOOL_CALLING_MODE ###") == 1
        assert result.count("### TOOL_SCHEMAS_JSON ###") == 1
        assert result.count("### END_TOOL_SCHEMAS_JSON ###") == 1

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
                "env": {
                    "action_prompt": "Decide action for {name}.",
                    "output_style": "Format: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
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
                "env": {
                    "action_prompt": "Decide action.",
                    "output_style": "Format: ACTION TYPE: ...",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
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
            additions=PromptAdditions(add_action_count_guidance=False),
        )
        assert result == prompt

    def test_inline_output_style_marker_preserved(self):
        """Inline [OUTPUT STYLE] marker should be preserved."""
        prompt = "Action prompt.\n[OUTPUT STYLE]\nFormat: ACTION"
        result = compile_action_prompt(
            base_prompt=prompt,
            output_style="Config style",
            tool_calling_mode="none",
            additions=PromptAdditions(add_action_count_guidance=True),
        )
        assert "[OUTPUT STYLE]" in result

    def test_marker_ordering_is_stable(self):
        """Marker ordering should be deterministic."""
        cfg = OmegaConf.create(
            {
                "env": {
                    "action_prompt": "Decide action.",
                    "output_style": "Format: ACTION TYPE",
                },
                "sim": {
                    "prompt_additions": {
                        "action_count_guidance": True,
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
