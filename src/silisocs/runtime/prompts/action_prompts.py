"""Action-prompt compilation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from omegaconf import OmegaConf

ACT_NUM_MARKER = "[ActNum]"
OUTPUT_STYLE_MARKER = "[OUTPUT STYLE]"
SINGLE_STEP_PROMPT_LINE = "Only take one action in this step"
MULTI_TOOL_CALLING_PROMPT_LINE = (
    "You are allowed to output multiple tool calls to take a batch of actions "
    "(if/as appropriate). If multiple tool calls, actions will be executed "
    "in sequence of calls."
)


@dataclass(frozen=True)
class PromptAdditions:
    """PromptAdditions."""

    add_action_count_guidance: bool = True


def _cfg_bool(cfg: Any, path: str, default: bool = False) -> bool:
    """_cfg_bool.

    :param Any cfg:
    :type cfg: Any
    :param str path:
    :type path: str
    :param bool default:
    :type default: bool

    :returns: bool
    :rtype: bool
    """
    return bool(OmegaConf.select(cfg, path, default=default))


def prompt_additions_from_cfg(cfg: Any) -> PromptAdditions:
    """Read prompt-addition toggles from config.

    Only action-count guidance remains configurable. The rest of prompt assembly
    is mode-driven:
    - output style is included for non-tool-calling modes
    - tool schemas are attached to ActionSpec.extra_args by the GM when needed
    """
    value = OmegaConf.select(cfg, "sim.prompt_additions.action_count_guidance", default=True)
    if not isinstance(value, bool):
        raise ValueError("sim.prompt_additions.action_count_guidance must be a boolean.")
    return PromptAdditions(add_action_count_guidance=value)


def split_output_style_sections(action_prompt: str) -> tuple[str, str, bool]:
    """split_output_style_sections.

    :param str action_prompt:
    :type action_prompt: str

    :returns: tuple[str, str, bool]
    :rtype: tuple[str, str, bool]
    """
    raw = str(action_prompt or "")
    if OUTPUT_STYLE_MARKER not in raw:
        return raw.strip(), "", False
    head, tail = raw.split(OUTPUT_STYLE_MARKER, 1)
    return head.strip(), tail.strip(), True


def action_guidance_line(tool_calling_mode: str) -> str:
    """action_guidance_line.

    :param str tool_calling_mode:
    :type tool_calling_mode: str

    :returns: str
    :rtype: str
    """
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

    # Output-style guidance is automatic in non-tool-calling mode.
    if not tool_calling_enabled:
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
    gm_prompt_cfg: Mapping[str, Any] | None = None,
) -> str:
    """Build the complete action prompt at runner startup, before GM instantiation.

    This compiles custom-mode prompts with config-driven additions.
    Tool schemas are NOT added here (the runner has no app instance).

    Args:
        cfg: Full OmegaConf config
        action_mode: 'custom' or 'generic'
        tool_calling_mode: 'none', 'single', or 'multi'

    Returns
    -------
        Complete action prompt string, ready for native GM action_prompt().
    """
    additions = prompt_additions_from_cfg(cfg)
    normalized_action_mode = str(action_mode or "custom").strip().lower()
    normalized_tool_mode = str(tool_calling_mode or "none").strip().lower()
    if gm_prompt_cfg is None:
        selected = OmegaConf.select(cfg, "env.gm.components.action_prompt.params", default={})
        if selected is None:
            gm_prompt_cfg = {}
        elif OmegaConf.is_config(selected):
            selected_container = OmegaConf.to_container(selected, resolve=True)
            if not isinstance(selected_container, Mapping):
                raise TypeError("env.gm.components.action_prompt.params must be a mapping.")
            gm_prompt_cfg = {str(key): value for key, value in selected_container.items()}
        else:
            if not isinstance(selected, Mapping):
                raise TypeError("env.gm.components.action_prompt.params must be a mapping.")
            gm_prompt_cfg = {str(key): value for key, value in selected.items()}
    gm_prompt_cfg = dict(gm_prompt_cfg or {})
    output_style = str(gm_prompt_cfg.get("output_style", "") or "").strip()

    if normalized_action_mode == "generic":
        # Generic prompts are generated by Base GM using backend action catalogs.
        base_prompt = ""
    else:
        custom_prompt = str(gm_prompt_cfg.get("action_prompt", "") or "").strip()
        base_prompt = custom_prompt

    final_prompt = compile_action_prompt(
        base_prompt=base_prompt,
        output_style=output_style,
        tool_calling_mode=normalized_tool_mode,
        additions=PromptAdditions(add_action_count_guidance=additions.add_action_count_guidance),
    )

    return final_prompt


def build_action_prompt_with_app_instance(
    *,
    cfg: Any,
    action_mode: str,
    tool_calling_mode: str,
    backend: Any = None,
    gm_prompt_cfg: Mapping[str, Any] | None = None,
) -> str:
    """Build action prompt for runner startup.

    Tool schemas are attached later by the GM action_prompt method with the real app instance.
    """
    del backend
    return build_complete_action_prompt_for_runner(
        cfg=cfg,
        action_mode=action_mode,
        tool_calling_mode=tool_calling_mode,
        gm_prompt_cfg=gm_prompt_cfg,
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
