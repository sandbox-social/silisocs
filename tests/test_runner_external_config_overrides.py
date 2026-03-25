import logging
import sys

from omegaconf import OmegaConf

from mastodon_sim.runtime.runner import EXTERNAL_CONFIG_DIR_ENV, _apply_external_root_overrides


def _base_cfg():
    return OmegaConf.create(
        {
            "sim": {
                "enabled_actions": None,
                "num_steps": 50,
                "engine": {
                    "action_loop": {
                        "params": {
                            "max_actions": 3,
                        }
                    }
                },
            },
            "social_media": {
                "platform": "twitter_like",
                "gamemaster": {
                    "name": "social-media_game-master",
                },
            },
        }
    )


def test_external_root_sim_override_applies(tmp_path, monkeypatch) -> None:
    (tmp_path / "sim.yaml").write_text(
        "\n".join(
            [
                "# @package sim",
                "enabled_actions:",
                "  - create_tweet",
                "  - reply_to_tweet",
                "  - FINISHED",
                "num_steps: 15",
                "engine:",
                "  action_loop:",
                "    params:",
                "      max_actions: 25",
            ]
        ),
        encoding="utf-8",
    )

    cfg = _base_cfg()
    monkeypatch.setenv(EXTERNAL_CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["runner.py"])

    _apply_external_root_overrides(cfg, logging.getLogger(__name__))

    assert list(cfg.sim.enabled_actions) == ["create_tweet", "reply_to_tweet", "FINISHED"]
    assert cfg.sim.num_steps == 15
    assert cfg.sim.engine.action_loop.params.max_actions == 25


def test_external_sim_override_respects_explicit_cli_paths(tmp_path, monkeypatch) -> None:
    (tmp_path / "sim.yaml").write_text(
        "\n".join(
            [
                "# @package sim",
                "enabled_actions:",
                "  - create_tweet",
                "  - FINISHED",
                "num_steps: 15",
                "engine:",
                "  action_loop:",
                "    params:",
                "      max_actions: 25",
            ]
        ),
        encoding="utf-8",
    )

    cfg = _base_cfg()
    cfg.sim.num_steps = 99
    cfg.sim.engine.action_loop.params.max_actions = 7

    monkeypatch.setenv(EXTERNAL_CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner.py",
            "sim.num_steps=99",
            "sim.engine.action_loop.params.max_actions=7",
        ],
    )

    _apply_external_root_overrides(cfg, logging.getLogger(__name__))

    assert cfg.sim.num_steps == 99
    assert cfg.sim.engine.action_loop.params.max_actions == 7
    assert list(cfg.sim.enabled_actions) == ["create_tweet", "FINISHED"]


def test_external_root_social_media_override_applies(tmp_path, monkeypatch) -> None:
    (tmp_path / "social_media.yaml").write_text(
        "\n".join(
            [
                "social_media:",
                "  platform: reddit_like",
            ]
        ),
        encoding="utf-8",
    )

    cfg = _base_cfg()
    monkeypatch.setenv(EXTERNAL_CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["runner.py"])

    _apply_external_root_overrides(cfg, logging.getLogger(__name__))

    assert cfg.social_media.platform == "reddit_like"
