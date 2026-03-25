from __future__ import annotations

from omegaconf import OmegaConf

from mastodon_sim.environments.engines.social_media import (
    BaseSocialMediaEngine,
    FlowSocialMediaEngine,
)
from mastodon_sim.runtime.runner import _build_engine, _default_gm_filename


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
    cfg = _cfg("base", "base")

    gm_filename = _default_gm_filename(cfg, mode="shared")
    engine = _build_engine(cfg)

    assert gm_filename == "social_media_game_master"
    assert isinstance(engine, BaseSocialMediaEngine)


def test_base_gm_with_flow_engine() -> None:
    cfg = _cfg("base", "flow")

    gm_filename = _default_gm_filename(cfg, mode="shared")
    engine = _build_engine(cfg)

    assert gm_filename == "social_media_game_master"
    assert isinstance(engine, FlowSocialMediaEngine)


def test_shared_flow_gm_with_base_engine() -> None:
    cfg = _cfg("shared_flow", "base")

    gm_filename = _default_gm_filename(cfg, mode="shared")
    engine = _build_engine(cfg)

    assert gm_filename == "shared_flow_game_master"
    assert isinstance(engine, BaseSocialMediaEngine)
