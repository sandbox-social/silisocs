"""Unit tests for Studio's backend-neutral platform-viewer orchestration."""
# ruff: noqa: D103

from __future__ import annotations

import json
import os
import sys
import time

from silisocs.environments.backends.factory import resolve_backend_class
from silisocs.studio.viewers import (
    discover_run_dir,
    find_backend_dbs,
    visualizer_plan,
)


def test_find_backend_dbs_flat_and_per_gm_layouts(tmp_path):
    (tmp_path / "twitter_like.db").write_bytes(b"")
    gm_dir = tmp_path / "social_gm"
    gm_dir.mkdir()
    (gm_dir / "reddit_like.db").write_bytes(b"")
    (tmp_path / "unrelated.db").write_bytes(b"")  # unknown backend -> ignored
    (tmp_path / "notes.txt").write_text("x")

    found = find_backend_dbs(tmp_path)
    assert [(backend, path.name) for backend, path in found] == [
        ("twitter_like", "twitter_like.db"),
        ("reddit_like", "reddit_like.db"),
    ]
    # Missing directory -> empty, never raises.
    assert find_backend_dbs(tmp_path / "nope") == []


def test_visualizer_plan_wires_env_module_and_url(tmp_path):
    db = tmp_path / "twitter_like.db"
    plan = visualizer_plan("twitter_like", db)
    spec = resolve_backend_class("twitter_like").visualizer
    assert plan.cmd == [sys.executable, "-m", spec.module]
    assert plan.env == {spec.env_var: str(db)}
    assert plan.url == f"http://127.0.0.1:{spec.default_port}"


def test_custom_visualizer_is_discovered_from_run_manifest(tmp_path):
    db = tmp_path / "state.sqlite"
    db.write_bytes(b"")
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "game_masters": [
                    {
                        "name": "world",
                        "backend_type": "custom_world",
                        "database": "state.sqlite",
                        "backend_class_path": "custom.backend.World",
                        "visualizer": {
                            "env_var": "CUSTOM_WORLD_DB",
                            "module": "custom.backend.viewer",
                            "default_port": 9012,
                            "port_env": "CUSTOM_WORLD_PORT",
                        },
                    }
                ]
            }
        )
    )

    assert find_backend_dbs(tmp_path) == [("custom_world", db)]
    plan = visualizer_plan("custom_world", db, port=9123)
    assert plan.cmd[-1] == "custom.backend.viewer"
    assert plan.env == {"CUSTOM_WORLD_DB": str(db), "CUSTOM_WORLD_PORT": "9123"}
    assert plan.url == "http://127.0.0.1:9123"


def test_discover_run_dir_descends_to_timestamped_leaf(tmp_path):
    """Standard layout: outputs/<scenario>/<job>/<scenario>_<timestamp>/ holds the artifacts."""
    base = tmp_path / "outputs" / "demo"
    old_job = base / "N3_T2_old"
    old_leaf = old_job / "demo_2026-01-01_00-00-00"
    old_leaf.mkdir(parents=True)
    (old_leaf / "effective_config.yaml").write_text("a: 1")
    past = time.time() - 3600
    for path in (old_job, old_leaf):
        os.utime(path, (past, past))

    new_job = base / "N3_T2_new"
    new_leaf = new_job / "demo_2026-01-02_00-00-00"
    new_leaf.mkdir(parents=True)
    (new_leaf / "effective_config.yaml").write_text("a: 1")
    (new_job / "configs").mkdir()  # config snapshot dir, not a run dir

    found = discover_run_dir("demo", started_after=time.time() - 60, root=tmp_path)
    assert found == new_leaf
    # No matching scenario dir -> None, never raises.
    assert discover_run_dir("missing", started_after=0, root=tmp_path) is None


def test_discover_run_dir_accepts_flat_job_layout(tmp_path):
    """A job dir holding the artifacts directly (no timestamped leaf) is found too."""
    flat_job = tmp_path / "outputs" / "demo" / "N3_T2_flat"
    flat_job.mkdir(parents=True)
    (flat_job / "twitter_like.db").write_bytes(b"")

    found = discover_run_dir("demo", started_after=time.time() - 60, root=tmp_path)
    assert found == flat_job
