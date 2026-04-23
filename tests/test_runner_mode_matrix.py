"""Tests for GM and engine preset matrix behavior."""

from __future__ import annotations

from omegaconf import OmegaConf

from mastodon_sim.environments.engines.social_media import (
    BaseSocialMediaEngine,
    FlowSocialMediaEngine,
)
from mastodon_sim.runtime.factories import build_engine, default_gm_filename


def _cfg(gm_preset: str, engine_preset: str):
    return OmegaConf.create(
        {
            "sim": {
                "gm": {"preset": gm_preset},
                "engine": {"preset": engine_preset},
            },
            "social_media": {
                "gamemaster": {
                    "filename": "social_media_game_master",
                }
            },
        }
    )


def test_base_gm_with_base_engine() -> None:
    """Base GM preset should pair with base engine preset."""
    cfg = _cfg("base", "base")

    gm_filename = default_gm_filename(cfg, mode="shared")
    engine = build_engine(cfg)

    assert gm_filename == "social_media_game_master"
    assert isinstance(engine, BaseSocialMediaEngine)


def test_base_gm_with_flow_engine() -> None:
    """Base GM preset should still work with flow engine preset."""
    cfg = _cfg("base", "flow")

    gm_filename = default_gm_filename(cfg, mode="shared")
    engine = build_engine(cfg)

    assert gm_filename == "social_media_game_master"
    assert isinstance(engine, FlowSocialMediaEngine)


def test_shared_flow_gm_with_base_engine() -> None:
    """Shared-flow GM preset should resolve correct GM prefab filename."""
    cfg = _cfg("shared_flow", "base")

    gm_filename = default_gm_filename(cfg, mode="shared")
    engine = build_engine(cfg)

    assert gm_filename == "shared_flow_game_master"
    assert isinstance(engine, BaseSocialMediaEngine)
