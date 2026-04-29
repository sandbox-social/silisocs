"""Small runtime factories for GM defaults and engine selection."""

from __future__ import annotations

from omegaconf import DictConfig

from mastodon_sim.simulator.engines.base_engines import (
    BaseRuntimeEngine,
    FlowRuntimeEngine,
)


def _env_cfg(cfg: DictConfig):
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


def default_gm_filename(cfg: DictConfig, mode: str) -> str:
    """Resolve default GM prefab filename from preset/mode."""
    gm_preset = str(
        getattr(getattr(_env_cfg(cfg), "gm", object()), "preset", None)
        or getattr(getattr(getattr(cfg, "sim", object()), "gm", object()), "preset", "base")
        or "base"
    )
    if mode == "shared" and gm_preset == "shared_flow":
        return "shared_flow_game_master"
    return str(_env_cfg(cfg).gamemaster.filename)


def default_gm_module_path(cfg: DictConfig, mode: str) -> str:
    """Resolve default GM module path from preset/mode."""
    gm_preset = str(
        getattr(getattr(_env_cfg(cfg), "gm", object()), "preset", None)
        or getattr(getattr(getattr(cfg, "sim", object()), "gm", object()), "preset", "base")
        or "base"
    )
    if mode == "shared" and gm_preset == "shared_flow":
        return "mastodon_sim.environments.gm.shared_flow_game_master"
    return str(_env_cfg(cfg).gamemaster.sim_role.module_path)


def build_engine(cfg: DictConfig):
    """Build runtime engine from configured preset."""
    engine_cfg = getattr(cfg.simulator, "engine", object())
    engine_preset = str(getattr(engine_cfg, "preset", "base") or "base")
    if engine_preset == "flow":
        return FlowRuntimeEngine()
    if engine_preset == "base":
        return BaseRuntimeEngine()
    raise ValueError(f"Unsupported sim.engine.preset='{engine_preset}'. Use 'base' or 'flow'.")
