"""Action-prompt compilation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omegaconf import OmegaConf

ACT_NUM_MARKER = "[ActNum]"
OUTPUT_STYLE_MARKER = "[OUTPUT STYLE]"
TOOL_CALLING_MODE_MARKER = "### TOOL_CALLING_MODE ###"
TOOL_SCHEMAS_START = "### TOOL_SCHEMAS_JSON ###"
TOOL_SCHEMAS_END = "### END_TOOL_SCHEMAS_JSON ###"
SINGLE_STEP_PROMPT_LINE = "Only take one action in this step"
MULTI_TOOL_CALLING_PROMPT_LINE = (
    "You are allowed to output multiple tool calls to take a batch of actions "
    "(if/as appropriate). If multiple tool calls, actions will be executed "
    "in sequence of calls."
)


@dataclass(frozen=True)
class PromptAdditions:
    add_action_count_guidance: bool = False
    add_output_style: bool = False
    add_tool_calling_marker: bool = False
    add_tool_schemas: bool = False
    add_social_media_info_generic: bool = False
    add_social_media_info_custom: bool = False


def _cfg_bool(cfg: Any, path: str, default: bool = False) -> bool:
    return bool(OmegaConf.select(cfg, path, default=default))


def prompt_additions_from_cfg(cfg: Any) -> PromptAdditions:
    """Read prompt-addition toggles from config. Defaults are all false."""
    return PromptAdditions(
        add_action_count_guidance=_cfg_bool(
            cfg, "sim.prompt_additions.action_count_guidance.add_to_prompt", False
        ),
        add_output_style=_cfg_bool(cfg, "sim.prompt_additions.output_style.add_to_prompt", False),
        add_tool_calling_marker=_cfg_bool(
            cfg, "sim.prompt_additions.tool_calling_marker.add_to_prompt", False
        ),
        add_tool_schemas=_cfg_bool(cfg, "sim.prompt_additions.tool_schemas.add_to_prompt", False),
        add_social_media_info_generic=_cfg_bool(
            cfg, "sim.prompt_additions.social_media_info.generic.add_to_prompt", False
        ),
        add_social_media_info_custom=_cfg_bool(
            cfg, "sim.prompt_additions.social_media_info.custom.add_to_prompt", False
        ),
    )


def split_output_style_sections(action_prompt: str) -> tuple[str, str, bool]:
    raw = str(action_prompt or "")
    if OUTPUT_STYLE_MARKER not in raw:
        return raw.strip(), "", False
    head, tail = raw.split(OUTPUT_STYLE_MARKER, 1)
    return head.strip(), tail.strip(), True


def action_guidance_line(tool_calling_mode: str) -> str:
    if str(tool_calling_mode or "none").strip().lower() == "multi":
        return MULTI_TOOL_CALLING_PROMPT_LINE
    return SINGLE_STEP_PROMPT_LINE


def compile_action_prompt(
    *,
    base_prompt: str,
    output_style: str,
    tool_calling_mode: str,
    additions: PromptAdditions,
) -> str:
    """Compile prompt fragments into one action prompt.

    Output-style guidance is always stripped when tool-calling is enabled.
    """
    tool_calling_enabled = str(tool_calling_mode or "none").strip().lower() in {"single", "multi"}
    prompt_head, inline_output_style, has_inline_marker = split_output_style_sections(base_prompt)
    sections: list[str] = [prompt_head] if prompt_head else []

    if additions.add_action_count_guidance:
        sections.append(f"{ACT_NUM_MARKER}\n{action_guidance_line(tool_calling_mode)}")

    if additions.add_output_style and not tool_calling_enabled:
        final_output_style = str(output_style or "").strip() or inline_output_style
        if final_output_style:
            sections.append(f"{OUTPUT_STYLE_MARKER}\n{final_output_style}")
        elif has_inline_marker:
            sections.append(OUTPUT_STYLE_MARKER)

    return "\n\n".join(section for section in sections if section)


def build_complete_action_prompt_for_runner(
    *,
    cfg: Any,
    action_mode: str,
    tool_calling_mode: str,
) -> str:
    """Build the complete action prompt at runner startup, before GM instantiation.

    This compiles both custom and generic mode prompts with all config-driven additions.
    Tool-calling markers and schemas are NOT added here (the runner has no app instance).

    Args:
        cfg: Full OmegaConf config
        action_mode: 'custom' or 'generic'
        tool_calling_mode: 'none', 'single', or 'multi'

    Returns
    -------
        Complete action prompt string, ready for GM to pass through to SMAct.
    """
    additions = prompt_additions_from_cfg(cfg)
    normalized_action_mode = str(action_mode or "custom").strip().lower()
    normalized_tool_mode = str(tool_calling_mode or "none").strip().lower()
    output_style = str(getattr(cfg.social_media, "output_style", "") or "")

    if normalized_action_mode == "generic":
        if hasattr(cfg.social_media, "generic_action_prompt"):
            base_prompt = str(cfg.social_media.generic_action_prompt)
        else:
            base_prompt = "(generic prompt generation deferred to GM with sm_app instance)"
    else:
        base_prompt = str(getattr(cfg.social_media, "action_prompt", "") or "").strip()

    final_prompt = compile_action_prompt(
        base_prompt=base_prompt,
        output_style=output_style,
        tool_calling_mode=normalized_tool_mode,
        additions=PromptAdditions(
            add_action_count_guidance=additions.add_action_count_guidance,
            add_output_style=additions.add_output_style,
        ),
    )

    return final_prompt


def apply_tool_calling_additions_for_gm(
    *,
    cfg: Any,
    sm_app: Any,
    base_prompt: str,
    tool_calling_mode: str,
) -> str:
    """Wrap base prompt with tool-calling markers/schemas if configured.

    Called by GM.build() after sm_app is instantiated. Takes the runner's base prompt
    and adds tool-calling wrapper if enabled.

    Args:
        cfg: Full OmegaConf config
        sm_app: Social media app instance
        base_prompt: Pre-compiled base prompt from runner
        tool_calling_mode: 'none', 'single', or 'multi'

    Returns
    -------
        Final prompt with optional tool-calling markers and schemas.
    """
    additions = prompt_additions_from_cfg(cfg)
    normalized_tool_mode = str(tool_calling_mode or "none").strip().lower()
    tool_calling_enabled = normalized_tool_mode in {"single", "multi"}

    if not tool_calling_enabled or not additions.add_tool_calling_marker:
        return base_prompt

    import json

    wrapped = f"{TOOL_CALLING_MODE_MARKER}\n{base_prompt}"
    if additions.add_tool_schemas and hasattr(sm_app, "generate_tool_schemas"):
        tool_schemas = list(sm_app.generate_tool_schemas() or [])
        if tool_schemas:
            wrapped += f"\n{TOOL_SCHEMAS_START}\n{json.dumps(tool_schemas)}\n{TOOL_SCHEMAS_END}"
    return wrapped


def build_action_prompt_with_app_instance(
    *,
    cfg: Any,
    action_mode: str,
    tool_calling_mode: str,
    sm_app: Any = None,
) -> str:
    """Build final action prompt with app instance available for tool schemas.

    If sm_app is not provided, creates a minimal instance for prompt compilation only.
    """
    if sm_app is None:
        from mastodon_sim.environments.backends.factory import create_social_media_app

        platform_type = str(
            getattr(cfg.social_media, "platform_type", "twitter_like") or "twitter_like"
        )
        sm_app = create_social_media_app(
            platform_type=platform_type,
            action_logger=None,
            app_description=str(getattr(cfg.social_media, "usage_instructions", "") or ""),
            db_path=":memory:",
        )

    base_prompt = build_complete_action_prompt_for_runner(
        cfg=cfg,
        action_mode=action_mode,
        tool_calling_mode=tool_calling_mode,
    )
    return apply_tool_calling_additions_for_gm(
        cfg=cfg,
        sm_app=sm_app,
        base_prompt=base_prompt,
        tool_calling_mode=tool_calling_mode,
    )


def _inject_action_count_guidance(base_prompt: str, guidance: str) -> str:
    """Inject action-count guidance before [OUTPUT STYLE] marker."""
    prompt = str(base_prompt or "").strip()
    line = str(guidance or "").strip()
    if not line:
        return prompt

    if OUTPUT_STYLE_MARKER not in prompt:
        return f"{prompt}\n\n{line}" if prompt else line

    head, tail = prompt.split(OUTPUT_STYLE_MARKER, 1)
    head = head.strip()
    tail = tail.strip()
    merged_head = f"{head}\n\n{line}" if head else line
    if tail:
        return f"{merged_head}\n\n{OUTPUT_STYLE_MARKER}\n{tail}"
    return f"{merged_head}\n\n{OUTPUT_STYLE_MARKER}"
