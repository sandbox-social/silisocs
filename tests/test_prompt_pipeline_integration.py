"""Integration tests for centralized action prompt pipeline.

Verifies that:
1. Prompt compilation at runner time produces expected output
2. Native GM action prompts expose typed ActionSpec metadata
3. Tool-calling schemas are carried in ActionSpec.extra_args
4. All prompt states match expected format
5. Prompt modifications maintain structure
"""

import pytest
from omegaconf import OmegaConf

from silisocs.environments.backends.base import SocialBackendApp, app_action
from silisocs.environments.gm.components.action_prompt import DefaultActionPromptComponent
from silisocs.environments.gm.components.base import (
    NextActingComponent,
    NoOpUpdateComponent,
    ObservationComponent,
    ResolveComponent,
)
from silisocs.environments.gm.game_master import (
    ComponentGameMaster,
    GameMasterComponentSlots,
    build_generic_action_prompt,
)
from silisocs.initialization.game_masters import NoOpGameMasterInitializer
from silisocs.runtime.language_models import NoLanguageModel
from silisocs.runtime.prompts.action_prompts import (
    PromptAdditions,
    build_complete_action_prompt_for_runner,
    compile_action_prompt,
)
from silisocs.runtime.types import ActionOutput


class _IntegrationTestApp(SocialBackendApp):
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
    def create_post(self, agent_name: str, content: str) -> str:
        del agent_name, content
        return "ok"

    @app_action(selectable_name="LIKE", description="Like a post")
    def like_post(self, agent_name: str, post_id: str) -> str:
        del agent_name, post_id
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


class _NoOpNextActing(NextActingComponent):
    def acting_agent_names(self) -> list[str]:
        return []


class _NoOpObservation(ObservationComponent):
    def make_observation(self, agent_name: str) -> str:
        del agent_name
        return ""


class _NoOpResolve(ResolveComponent):
    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        del agent_name, action
        return ""


def _prompt_cfg(
    action_prompt: str | None = None,
    output_style: str | None = None,
    sim: dict | None = None,
):
    params = {}
    if action_prompt is not None:
        params["action_prompt"] = action_prompt
    if output_style is not None:
        params["output_style"] = output_style
    data = {
        "env": {
            "gm": {
                "components": {
                    "action_prompt": {
                        "params": params,
                    }
                }
            }
        }
    }
    if sim:
        data["sim"] = sim
    return OmegaConf.create(data)


class TestPromptCompilationMatrixIntegration:
    """Test all combinations of prompt compilation modes."""

    def test_custom_mode_no_additions_produces_baseline_prompt(self):
        """Custom mode defaults include guidance and output style in non-tool mode."""
        cfg = _prompt_cfg(
            action_prompt="Take an action.",
            output_style="Use ACTION TYPE format",
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "Take an action." in result
        assert "[ActNum]" in result
        assert "[OUTPUT STYLE]" in result

    def test_custom_mode_with_action_count_guidance_adds_marker_and_guidance(self):
        """With action_count_guidance flag, should add [ActNum] marker and guidance."""
        cfg = _prompt_cfg(
            action_prompt="Take an action.",
            output_style="Ignored",
            sim={"prompt_additions": {"action_count_guidance": True}},
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "[ActNum]" in result
        assert "Only take one action in this step" in result
        assert "Take an action." in result

    def test_custom_mode_includes_output_style_by_default(self):
        """Output-style section should be included by default in non-tool mode."""
        cfg = _prompt_cfg(
            action_prompt="Take action.",
            output_style="Format: ACTION TYPE: ...",
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "[OUTPUT STYLE]" in result
        assert "Format: ACTION TYPE:" in result
        assert "Take action." in result

    def test_custom_mode_tool_calling_strips_output_style(self):
        """Tool-calling mode should strip output style even if flag is set."""
        cfg = _prompt_cfg(
            action_prompt="Take action.",
            output_style="Format: ACTION TYPE: ...",
            sim={"prompt_additions": {"action_count_guidance": True}},
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
        cfg = _prompt_cfg(action_prompt="Ignored", output_style="Runner style")
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "[OUTPUT STYLE]" in result

    def test_generic_mode_runner_compiler_can_disable_action_guidance(self):
        """Only action guidance toggle should affect runner generic skeleton output."""
        cfg = _prompt_cfg(
            output_style="Runner style",
            sim={"prompt_additions": {"action_count_guidance": False}},
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="none"
        )
        assert "[ActNum]" not in result
        assert "[OUTPUT STYLE]" in result

    def test_generic_mode_with_multi_tool_calling_guidance(self):
        """Runner-side generic compile in tool mode only carries action guidance."""
        cfg = _prompt_cfg(
            output_style="Style ignored under tool-calling",
            sim={"prompt_additions": {"action_count_guidance": True}},
        )
        result = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="generic", tool_calling_mode="multi"
        )
        assert "[ActNum]" in result
        assert "You are allowed to output multiple tool calls" in result
        assert "[OUTPUT STYLE]" not in result

    def test_prompt_structure_with_both_additions(self):
        """Prompt with both action guidance and output style should be ordered correctly."""
        cfg = _prompt_cfg(
            action_prompt="Base prompt.",
            output_style="Format instructions",
            sim={"prompt_additions": {"action_count_guidance": True}},
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
        cfg = _prompt_cfg(
            action_prompt="Default base prompt.",
            output_style="Default style",
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
                    "gm": {
                        "components": {
                            "action_prompt": {
                                "params": {
                                    "output_style": "FINAL: ACTION + params",
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
        prompt = build_generic_action_prompt(
            backend=_GenericBuildApp(),
            tool_calling_mode="none",
            output_style=str(cfg.env.gm.components.action_prompt.params.output_style),
            add_action_count_guidance=True,
        )
        assert "Available actions:" in prompt
        assert "POST(content)" in prompt
        assert "[ActNum]" in prompt
        assert "[OUTPUT STYLE]" in prompt
        assert "FINAL: ACTION + params" in prompt


def _gm_for_prompt(
    *,
    backend: SocialBackendApp,
    prompt: str,
    enable_tool_calling: bool,
) -> ComponentGameMaster:
    component_slots = GameMasterComponentSlots(
        initialize=NoOpGameMasterInitializer(),
        next_acting=_NoOpNextActing(),
        action_prompt=DefaultActionPromptComponent(
            backend=backend,
            action_prompt_template=prompt,
            enable_tool_calling=enable_tool_calling,
        ),
        observe=_NoOpObservation(),
        resolve=_NoOpResolve(),
        update=NoOpUpdateComponent(),
    )
    return ComponentGameMaster(
        name="gm",
        model=NoLanguageModel(),
        backend=backend,
        backend_type="integration_test",
        component_slots=component_slots,
        action_prompt_template=prompt,
        agent_flow_tags={},
        tool_calling_mode="single" if enable_tool_calling else "none",
    )


class TestNativeGMActionPromptBehavior:
    """Test that native GMs return typed action prompts directly."""

    def test_gm_returns_text_action_spec(self):
        """GM should return a typed text ActionSpec without string wrappers."""
        app = _IntegrationTestApp()
        gm = _gm_for_prompt(
            backend=app,
            prompt="Test prompt here",
            enable_tool_calling=False,
        )
        spec = gm.action_prompt("Alice")
        assert spec.prompt == "Test prompt here"
        assert spec.output_type.value == "text"
        assert spec.extra_args == {}

    def test_gm_handles_multiline_prompt(self):
        """GM should preserve multiline prompts."""
        multiline_prompt = "Line 1\nLine 2\nLine 3"
        app = _IntegrationTestApp()
        gm = _gm_for_prompt(
            backend=app,
            prompt=multiline_prompt,
            enable_tool_calling=False,
        )
        assert gm.action_prompt("Alice").prompt == multiline_prompt

    def test_gm_with_tool_calling_uses_extra_args(self):
        """Tool-calling GMs should carry schemas in extra_args."""
        app = _IntegrationTestApp()
        gm = _gm_for_prompt(
            backend=app,
            prompt="Base prompt",
            enable_tool_calling=True,
        )
        spec = gm.action_prompt("Alice")
        assert spec.prompt == "Base prompt"
        assert spec.output_type.value == "tool_calls"
        assert spec.extra_args["tool_mode"] == "single"
        schemas = spec.extra_args["tools"]
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert all("function" in schema for schema in schemas)

    def test_gm_does_not_parse_legacy_tool_markers(self):
        """Legacy markers are treated as prompt text, not as transport metadata."""
        app = _IntegrationTestApp()
        wrapped_prompt = (
            "### LEGACY_TOOL_MODE ###\n"
            "Base prompt\n"
            "### TOOL_SCHEMAS_JSON ###\n"
            "[]\n"
            "### END_TOOL_SCHEMAS_JSON ###"
        )
        gm = _gm_for_prompt(
            backend=app,
            prompt=wrapped_prompt,
            enable_tool_calling=True,
        )
        spec = gm.action_prompt("Alice")
        assert spec.prompt == wrapped_prompt
        assert spec.extra_args["tools"]

    def test_action_prompt_component_without_backend_returns_text_spec(self):
        """Component without a backend should return a plain text spec."""
        component = DefaultActionPromptComponent(
            backend=None,
            action_prompt_template="Base prompt",
            enable_tool_calling=True,
        )
        spec = component.action_prompt("Alice")
        assert spec.prompt == "Base prompt"
        assert spec.output_type.value == "text"
        assert spec.extra_args == {}


class TestPromptPipelineEndToEnd:
    """Test complete flow from config to native GM action prompt."""

    def test_runner_compile_then_gm_action_prompt(self):
        """Test complete flow: runner compiles prompt, GM emits typed spec."""
        cfg = _prompt_cfg(
            action_prompt="Decide action for {name}.",
            output_style="Format: ACTION TYPE: ...",
            sim={"prompt_additions": {"action_count_guidance": True}},
        )

        # Step 1: Runner builds prompt
        compiled_prompt = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="none"
        )
        assert "[ActNum]" in compiled_prompt
        assert "[OUTPUT STYLE]" in compiled_prompt
        assert "Only take one action" in compiled_prompt

        # Step 2: GM uses compiled prompt
        app = _IntegrationTestApp()
        gm = _gm_for_prompt(
            backend=app,
            prompt=compiled_prompt,
            enable_tool_calling=False,
        )
        spec = gm.action_prompt("Alice")

        # Step 3: GM output should contain everything from runner as plain prompt text
        assert "[ActNum]" in spec.prompt
        assert "[OUTPUT STYLE]" in spec.prompt
        assert "Only take one action" in spec.prompt
        assert spec.output_type.value == "text"

    def test_runner_with_tool_calling_uses_typed_metadata(self):
        """Test flow with tool-calling: runner builds base, GM adds typed tool metadata."""
        cfg = _prompt_cfg(
            action_prompt="Decide action.",
            output_style="Format: ACTION TYPE: ...",
            sim={"prompt_additions": {"action_count_guidance": True}},
        )

        # Step 1: Runner builds prompt (note: output style stripped for tool-calling)
        compiled_prompt = build_complete_action_prompt_for_runner(
            cfg=cfg, action_mode="custom", tool_calling_mode="single"
        )
        assert "[ActNum]" in compiled_prompt
        assert "[OUTPUT STYLE]" not in compiled_prompt  # Stripped by compile

        # Step 2: GM attaches tool schemas via extra_args
        app = _IntegrationTestApp()
        gm = _gm_for_prompt(
            backend=app,
            prompt=compiled_prompt,
            enable_tool_calling=True,
        )
        spec = gm.action_prompt("Alice")

        # Step 3: Final output should have prompt text plus typed metadata
        assert spec.output_type.value == "tool_calls"
        assert spec.extra_args["tools"]
        assert "[ActNum]" in spec.prompt
        assert "Decide action." in spec.prompt


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
        cfg = _prompt_cfg(
            action_prompt="Decide action.",
            output_style="Format: ACTION TYPE",
            sim={"prompt_additions": {"action_count_guidance": True}},
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
