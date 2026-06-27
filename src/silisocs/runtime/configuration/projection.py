"""Normalized runtime config projection and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf

_ACTION_MODES = {"custom", "generic"}
_TOOL_CALLING_MODES = {"none", "single", "multi"}


def normalize_action_mode(value: Any) -> str:
    """Normalize an action_mode string. Empty/None -> '' (unset)."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text not in _ACTION_MODES:
        raise ValueError(f"Unsupported sim.action_mode='{text}'. Allowed values: custom, generic.")
    return text


def normalize_tool_calling_mode(value: Any) -> str:
    """Normalize a tool_calling mode string. Empty/None -> '' (unset)."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text not in _TOOL_CALLING_MODES:
        raise ValueError(
            f"Unsupported sim.tool_calling.mode='{text}'. Allowed values: none, single, multi."
        )
    return text


def validate_resolve_tool_calling(
    *,
    tool_calling_mode: str,
    resolve_built_in: str,
    resolve_path: str,
) -> None:
    """Assert tool-calling mode and resolver selection stay aligned (single source)."""
    mode_uses_tool_calling = str(tool_calling_mode).strip().lower() not in {"", "none"}
    resolve_uses_tool_calling = str(resolve_built_in).strip() == "tool_calling"
    if mode_uses_tool_calling != resolve_uses_tool_calling:
        raise ValueError(
            "Tool-calling mode must match resolver selection: "
            f"set {resolve_path}=tool_calling when sim.tool_calling.mode is single/multi, "
            f"or set sim.tool_calling.mode=none when {resolve_path} is not tool_calling."
        )


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
        for path, resolve_built_in in resolve_slots.items():
            validate_resolve_tool_calling(
                tool_calling_mode=tool_calling_mode,
                resolve_built_in=resolve_built_in,
                resolve_path=path,
            )

        engine_step = str(OmegaConf.select(cfg, "sim.engine.step.built_in") or "base")

        return cls(
            action_mode=action_mode,
            tool_calling_mode=tool_calling_mode,
            resolve_built_in=next(iter(resolve_slots.values()), "parsed_action"),
            engine_step=engine_step,
        )


def _gm_has_tool_calling_override(cfg: DictConfig, prefix: str) -> bool:
    """True if a GM declares its own tool_calling_mode/tool_calling at ``prefix``."""
    if str(OmegaConf.select(cfg, f"{prefix}.tool_calling_mode") or "").strip():
        return True
    alias = OmegaConf.select(cfg, f"{prefix}.tool_calling")
    if alias is None:
        return False
    if isinstance(alias, Mapping):
        # A tool_calling mapping is an override attempt regardless of contents; the
        # required 'mode' key is validated per-GM (game_masters._gm_tool_calling_mode),
        # so the actionable "must set 'mode'" error surfaces instead of a generic one.
        return True
    return bool(str(alias).strip())


def _resolve_slot_built_ins(cfg: DictConfig) -> dict[str, str]:
    """Return resolve-slot built-ins governed by the global tool_calling mode.

    GMs that declare their own ``tool_calling``/``tool_calling_mode`` are validated
    per-GM in build_game_masters against their effective mode, so they are skipped
    here to avoid checking them against the (possibly different) global mode.
    """
    gms = OmegaConf.select(cfg, "env.gm_orchestration.gms")
    if isinstance(gms, (list, tuple, ListConfig)) and gms:
        slots: dict[str, str] = {}
        for idx, _gm in enumerate(gms):
            prefix = f"env.gm_orchestration.gms.{idx}"
            if _gm_has_tool_calling_override(cfg, prefix):
                continue
            path = f"{prefix}.components.resolve.built_in"
            slots[path] = str(OmegaConf.select(cfg, path) or "parsed_action").strip()
        return slots
    if _gm_has_tool_calling_override(cfg, "env.gm"):
        return {}
    return {
        "env.gm.components.resolve.built_in": str(
            OmegaConf.select(cfg, "env.gm.components.resolve.built_in") or "parsed_action"
        ).strip()
    }
