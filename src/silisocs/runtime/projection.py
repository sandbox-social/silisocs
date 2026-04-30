"""Normalized runtime config projection and validation."""

from __future__ import annotations

from dataclasses import dataclass

from omegaconf import DictConfig, OmegaConf

_ACTION_MODES = {"custom", "generic"}
_TOOL_CALLING_MODES = {"none", "single", "multi"}


@dataclass(frozen=True)
class RuntimeProjection:
    """Normalized runtime settings derived from Hydra config."""

    action_mode: str
    tool_calling_mode: str
    resolve_built_in: str
    gm_preset: str
    engine_preset: str

    @classmethod
    def from_cfg(cls, cfg: DictConfig) -> RuntimeProjection:
        """Build a validated runtime projection from composed config."""
        action_mode = str(
            getattr(getattr(cfg, "sim", object()), "action_mode", "custom") or "custom"
        )
        action_mode = action_mode.strip().lower()
        if action_mode not in _ACTION_MODES:
            raise ValueError(
                f"Unsupported sim.action_mode='{action_mode}'. Allowed values: custom, generic."
            )

        tool_calling_mode = (
            str(OmegaConf.select(cfg, "sim.tool_calling.mode", default="none") or "none")
            .strip()
            .lower()
        )
        if tool_calling_mode not in _TOOL_CALLING_MODES:
            raise ValueError(
                f"Unsupported sim.tool_calling.mode='{tool_calling_mode}'. "
                "Allowed values: none, single, multi."
            )

        resolve_built_in = str(
            OmegaConf.select(cfg, "env.gm.components.resolve.built_in") or "parsed_action"
        ).strip()
        resolve_uses_tool_calling = resolve_built_in == "tool_calling"
        mode_uses_tool_calling = tool_calling_mode != "none"
        if mode_uses_tool_calling != resolve_uses_tool_calling:
            raise ValueError(
                "Tool-calling mode must match resolver selection: "
                "set env.gm.components.resolve.built_in=tool_calling when "
                "sim.tool_calling.mode is single/multi, or set "
                "sim.tool_calling.mode=none when resolver is not tool_calling."
            )

        gm_preset = str(OmegaConf.select(cfg, "env.gm.preset") or "base")
        engine_preset = str(
            getattr(getattr(getattr(cfg, "sim", object()), "engine", object()), "preset", "base")
            or "base"
        )

        return cls(
            action_mode=action_mode,
            tool_calling_mode=tool_calling_mode,
            resolve_built_in=resolve_built_in,
            gm_preset=gm_preset,
            engine_preset=engine_preset,
        )
