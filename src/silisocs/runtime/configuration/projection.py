"""Normalized runtime config projection and validation."""

from __future__ import annotations

from dataclasses import dataclass

from omegaconf import DictConfig, ListConfig, OmegaConf

_ACTION_MODES = {"custom", "generic"}
_TOOL_CALLING_MODES = {"none", "single", "multi"}


@dataclass(frozen=True)
class RuntimeProjection:
    """Normalized runtime settings derived from Hydra config."""

    action_mode: str
    tool_calling_mode: str
    resolve_built_in: str
    engine_step: str

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

        resolve_slots = _resolve_slot_built_ins(cfg)
        mode_uses_tool_calling = tool_calling_mode != "none"
        for path, resolve_built_in in resolve_slots.items():
            resolve_uses_tool_calling = resolve_built_in == "tool_calling"
            if mode_uses_tool_calling != resolve_uses_tool_calling:
                raise ValueError(
                    "Tool-calling mode must match resolver selection: "
                    f"set {path}=tool_calling when sim.tool_calling.mode is single/multi, "
                    f"or set sim.tool_calling.mode=none when {path} is not tool_calling."
                )

        engine_step = str(OmegaConf.select(cfg, "sim.engine.step.built_in") or "base")

        return cls(
            action_mode=action_mode,
            tool_calling_mode=tool_calling_mode,
            resolve_built_in=next(iter(resolve_slots.values()), "parsed_action"),
            engine_step=engine_step,
        )


def _resolve_slot_built_ins(cfg: DictConfig) -> dict[str, str]:
    """Return resolve-slot built-ins for the default GM or each orchestrated GM."""
    gms = OmegaConf.select(cfg, "env.gm_orchestration.gms")
    if isinstance(gms, (list, tuple, ListConfig)) and gms:
        slots: dict[str, str] = {}
        for idx, _gm in enumerate(gms):
            path = f"env.gm_orchestration.gms.{idx}.components.resolve.built_in"
            slots[path] = str(OmegaConf.select(cfg, path) or "parsed_action").strip()
        return slots
    return {
        "env.gm.components.resolve.built_in": str(
            OmegaConf.select(cfg, "env.gm.components.resolve.built_in") or "parsed_action"
        ).strip()
    }
