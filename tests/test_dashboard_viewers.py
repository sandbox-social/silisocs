"""Unit tests for the companion-viewer launch helpers (dashboard/viewers.py).

The module is pure (no Streamlit import), so the DB discovery, launch-plan, and
live-tail logic is covered here with plain temp directories.
"""

from __future__ import annotations

import json
import os
import sys
import time

from silisocs.dashboard.viewers import (
    VISUALIZER_BACKENDS,
    analysis_plan,
    discover_run_dir,
    find_backend_dbs,
    tail_action_events,
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
    env_var, module, port = VISUALIZER_BACKENDS["twitter_like"]
    assert plan.cmd == [sys.executable, "-m", module]
    assert plan.env == {env_var: str(db)}
    assert plan.url == f"http://localhost:{port}"


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
    assert plan.url == "http://localhost:9123"


def test_analysis_plan_targets_run_dir(tmp_path):
    plan = analysis_plan(tmp_path)
    assert "--output_dir" in plan.cmd
    assert plan.cmd[plan.cmd.index("--output_dir") + 1] == str(tmp_path)
    assert plan.env == {}
    assert plan.url.startswith("http://localhost:")


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


def test_tail_action_events_summarizes_and_tolerates_partial_lines(tmp_path):
    rows = [
        {"source_user": "Alice", "label": "post", "episode": 0, "data": {}},
        {"source_user": "Bob", "label": "like", "episode": 1, "data": {}},
        {"source_user": "Alice", "label": "post", "episode": 2, "data": {}},
    ]
    lines = "\n".join(json.dumps(row) for row in rows)
    # A partially-written trailing line (live run mid-write) must be skipped.
    (tmp_path / "action_events.jsonl").write_text(lines + '\n{"source_user": "Ca', "utf-8")

    summary = tail_action_events(tmp_path)
    assert summary["total"] == 3
    assert summary["episodes"] == 3  # max episode 2 -> 3 episodes
    assert summary["agents"] == 2
    assert summary["label_counts"] == {"post": 2, "like": 1}
    assert [row["label"] for row in summary["recent"]] == ["post", "like", "post"]

    # A directory with no event logs summarizes to zeros.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert tail_action_events(empty)["total"] == 0
