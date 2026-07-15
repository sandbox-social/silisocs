"""Smoke tests for Studio's artifact-backed HTTP surface."""
# ruff: noqa: D103

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.design.tokens import ACCENT
from silisocs.studio.app import create_app
from silisocs.studio.jobs import Job


def _make_run(root, name="demo/run-1"):
    run = root / name
    run.mkdir(parents=True)
    (run / "action_events.jsonl").write_text(
        json.dumps({"source_user": "Alice", "label": "post", "episode": 0, "data": {}}) + "\n"
    )
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "num_agents": 1,
                "num_steps": 1,
                "artifacts": {"action_events": ["action_events.jsonl"]},
            }
        )
    )
    return run


def test_studio_run_browser_and_api(tmp_path):
    _make_run(tmp_path)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    assert "Simulation workbench" in client.get("/").text
    assert client.get("/api/runs").json()["items"][0]["id"] == "demo/run-1"
    assert any(
        panel["name"] == "action_trends" for panel in client.get("/api/panels").json()["items"]
    )
    assert client.get("/api/runs/demo/run-1/views/overview").json()["name"] == "overview"
    page = client.get("/runs/demo/run-1").text
    assert "Run health" in page
    assert ">Overview</a>" in page
    assert "Run: Analyze" in page


def test_completed_watch_ribbon_uses_persisted_artifact_counters(tmp_path):
    run = _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    app.state.jobs.store.insert(
        Job(
            id="completed-run",
            kind="run",
            status="finished",
            pid=None,
            created_at=100.0,
            started_at=101.0,
            ended_at=103.0,
            exit_code=0,
            scenario="demo",
            config_snapshot_path=None,
            output_dir=str(run),
            log_path=str(tmp_path / "completed.log"),
            parent_study=None,
            port=None,
            command_json=json.dumps({"argv": [], "cwd": str(tmp_path), "env": {}}),
        )
    )

    page = TestClient(app).get("/runs/demo/run-1?tab=watch")

    assert page.status_code == 200
    assert '<strong id="watch-status">success</strong>' in page.text
    assert '<span id="watch-step">Episode 1/1 complete</span>' in page.text
    assert '<span id="watch-elapsed">0:02 elapsed</span>' in page.text
    assert '<span id="watch-actions">1 action</span>' in page.text


def test_run_config_diffs_effective_yaml_from_scenario_source(tmp_path):
    outputs = tmp_path / "artifacts"
    run = _make_run(outputs)
    (run / "effective_config.yaml").write_text(
        "scenario_name: demo\nnum_agents: 2\nsim:\n  llm:\n    name: test-model\n",
        encoding="utf-8",
    )
    conf = tmp_path / "scenarios" / "demo" / "conf"
    (conf / "world").mkdir(parents=True)
    (conf / "world" / "default.yaml").write_text(
        "# @package _global_\nscenario_name: demo\nnum_agents: 1\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path))

    page = client.get("/runs/demo/run-1?tab=config")

    assert page.status_code == 200
    assert "Changes from scenario baseline" in page.text
    assert "diff-addition" in page.text
    assert "+num_agents: 2" in page.text
    assert "test-model" in page.text


def test_run_rejects_unknown_tab(tmp_path):
    _make_run(tmp_path)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    assert client.get("/runs/demo/run-1?tab=unknown").status_code == 404


def test_studio_unknown_view_and_run_are_404(tmp_path):
    """Unknown views 404 (never 500) and never reach load_view's YAML-path branch."""
    _make_run(tmp_path)
    client = TestClient(
        create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path),
        raise_server_exceptions=False,
    )
    assert client.get("/runs/demo/run-1?view=nope").status_code == 404
    assert client.get("/api/runs/demo/run-1/views/nope").status_code == 404
    assert client.get("/runs/demo/run-1?view=/etc/hostname").status_code == 404
    assert client.get("/runs/demo/missing").status_code == 404


def test_studio_hides_per_gm_event_shards(tmp_path):
    """A multi-GM run's per-GM log subdirectory is not listed as its own run."""
    run = _make_run(tmp_path)
    gm_dir = run / "social_gm"
    gm_dir.mkdir()
    (gm_dir / "action_events.jsonl").write_text("{}\n")
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    assert [item["id"] for item in client.get("/api/runs").json()["items"]] == ["demo/run-1"]


def test_studio_tokens_css_comes_from_design_package(tmp_path):
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    body = client.get("/assets/tokens.css").text
    assert f"--accent:{ACCENT}" in body


def test_scenario_shipped_view_uses_configured_repository_root(tmp_path):
    outputs = tmp_path / "artifacts"
    _make_run(outputs)
    view_dir = tmp_path / "workspace" / "scenarios" / "demo" / "conf" / "views"
    view_dir.mkdir(parents=True)
    (view_dir / "lab.yaml").write_text(
        """view:
  name: lab
  title: Lab View
  scope: run
  layout: rows
  panels:
    - built_in: health_summary
""",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            outputs,
            state_dir=tmp_path / "state",
            repo_root=tmp_path / "workspace",
        )
    )

    assert client.get("/api/runs/demo/run-1/views/lab").json()["title"] == "Lab View"
    report = client.get("/api/runs/demo/run-1/report?view=lab")
    assert report.status_code == 200
    assert "Lab View" in report.text


def test_lab_token_protects_control_plane(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_AUTH_TOKEN", "secret")
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    payload = {"files": {"world/default.yaml": "num_agents: 1\nnum_steps: 1\n"}}

    assert client.post("/api/preflight", json=payload).status_code == 401
    assert (
        client.post(
            "/api/preflight",
            json=payload,
            headers={"Authorization": "Bearer secret"},
        ).status_code
        == 200
    )
