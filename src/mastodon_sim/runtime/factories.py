"""Small runtime factories for GM defaults and engine selection."""

from __future__ import annotations

from omegaconf import DictConfig

from mastodon_sim.environments.engines.social_media import (
    BaseSocialMediaEngine,
    FlowSocialMediaEngine,
)


def default_gm_filename(cfg: DictConfig, mode: str) -> str:
    """Resolve default GM prefab filename from preset/mode."""
    sim_cfg = getattr(cfg, "sim", object())
    gm_preset = str(getattr(getattr(sim_cfg, "gm", object()), "preset", "base") or "base")
    if mode == "shared" and gm_preset == "shared_flow":
        return "shared_flow_game_master"
    return str(cfg.social_media.gamemaster.filename)


def default_gm_module_path(cfg: DictConfig, mode: str) -> str:
    """Resolve default GM module path from preset/mode."""
    sim_cfg = getattr(cfg, "sim", object())
    gm_preset = str(getattr(getattr(sim_cfg, "gm", object()), "preset", "base") or "base")
    if mode == "shared" and gm_preset == "shared_flow":
        return "mastodon_sim.environments.gm.shared_flow_game_master"
    return str(cfg.social_media.gamemaster.sim_role.module_path)


def build_engine(cfg: DictConfig):
    """Build runtime engine from configured preset."""
    engine_cfg = getattr(cfg.sim, "engine", object())
    engine_preset = str(getattr(engine_cfg, "preset", "base") or "base")
    if engine_preset == "flow":
        return FlowSocialMediaEngine()
    if engine_preset == "base":
        return BaseSocialMediaEngine()
    raise ValueError(f"Unsupported sim.engine.preset='{engine_preset}'. Use 'base' or 'flow'.")
