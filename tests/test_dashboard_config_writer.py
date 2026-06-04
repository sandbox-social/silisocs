from __future__ import annotations

from pathlib import Path

import yaml

from silisocs.dashboard.config_writer import save_world


def _base_world() -> dict:
    return {
        "world_name": "dashboard_config",
        "setting": {"name": "Dashboard Config", "background": []},
        "event": {"name": "Config", "context": "Check dashboard config writing."},
        "persona_pipeline": {
            "classes": {
                "user": {
                    "count": 1,
                    "class_path": "silisocs.agents.native.NativeAgent",
                    "data": {"source": "inline", "records": [{"name": "Alice"}]},
                }
            }
        },
        "probes": {"deployment": {"enabled": False}, "probes": {}},
    }


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_dashboard_writer_uses_social_defaults_for_social_backend(tmp_path: Path) -> None:
    conf_dir = save_world(
        "social",
        _base_world(),
        {"llm.provider": "scripted"},
        {},
        "twitter_like",
        tmp_path,
    )

    env_cfg = _load_yaml(conf_dir / "env.yaml")

    assert env_cfg["gm"]["backend"]["type"] == "twitter_like"
    assert env_cfg["gm"]["components"]["initialize"]["built_in"] == "social_media"
    assert env_cfg["gm"]["components"]["observe"]["built_in"] == "timeline_every_turn"
    assert env_cfg["gm"]["components"]["update"]["built_in"] == "social_recommendation"


def test_dashboard_writer_uses_packaged_defaults_for_reference_backend(tmp_path: Path) -> None:
    conf_dir = save_world(
        "market",
        _base_world(),
        {"llm.provider": "scripted"},
        {},
        "resource_market",
        tmp_path,
    )

    env_cfg = _load_yaml(conf_dir / "env.yaml")

    assert env_cfg["gm"]["backend"]["type"] == "resource_market"
    assert env_cfg["gm"]["components"]["initialize"]["built_in"] == "app_initialize"
    assert env_cfg["gm"]["components"]["next_acting"]["built_in"] == "fixed_order"
    assert env_cfg["gm"]["components"]["observe"]["built_in"] == "app_observation"
    assert env_cfg["gm"]["components"]["update"]["built_in"] == "app_update"
