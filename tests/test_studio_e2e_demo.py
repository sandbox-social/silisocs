"""End-to-end Studio demo over HTTP: preflight, launch, stream, analyze.

This is the executable version of the bundled walkthrough
(docs/tutorials/studio_demo.md): it drives the same control-plane surface the
browser uses — scenario discovery, preflight, ``POST /api/launch``, the SSE job
stream, the run page, and the scenario-bundled ``spread`` analysis view —
against the real ``scenarios/misinformation`` config with the offline
``scripted`` model provider, so it stays fast, deterministic, and CI-safe.
"""
# ruff: noqa: D103

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.studio.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """One launched-and-finished scripted run shared by the assertions below."""
    tmp_path = tmp_path_factory.mktemp("studio_e2e")
    client = TestClient(
        create_app(
            tmp_path / "outputs",
            state_dir=tmp_path / "state",
            repo_root=REPO_ROOT,
        )
    )

    job = client.post(
        "/api/launch",
        json={
            "scenario": "misinformation",
            "overrides": ["sim.llm.provider=scripted", "num_steps=2"],
        },
    ).json()
    assert job.get("id"), f"launch failed: {job}"

    deadline = time.time() + 180
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job['id']}").json()
        if state["status"] in {"finished", "failed", "killed"}:
            break
        time.sleep(0.25)
    log = Path(state["log_path"]).read_text(encoding="utf-8")
    assert state["status"] == "finished", f"run did not finish: {state}\n{log[-2000:]}"

    run_id = str(Path(state["output_dir"]).relative_to(tmp_path / "outputs"))
    return client, job["id"], run_id


def test_scenario_is_discovered_and_preflight_estimates(demo):
    client, _job_id, _run_id = demo
    names = {item["name"] for item in client.get("/api/scenarios").json()["items"]}
    assert "misinformation" in names

    result = client.post(
        "/api/preflight", json={"scenario": "misinformation", "source": "workspace"}
    ).json()
    assert result["ok"] is True, result["findings"]
    estimate = result["estimate"]
    # 6 agents x 10 steps, gated by the markov activity steady state 0.8/0.9.
    assert estimate["agent_steps"] == round(6 * 10 * (0.8 / 0.9))
    assert estimate["llm_calls"] > 0


def test_job_stream_replays_steps_and_done(demo):
    client, job_id, _run_id = demo
    stream = client.get(f"/api/jobs/{job_id}/stream").text
    assert "event: step_finished" in stream
    assert "event: artifact_grown" in stream
    assert "event: done" in stream


def test_run_page_and_manifest_health(demo):
    client, _job_id, run_id = demo
    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "Run health" in page.text

    record = next(item for item in client.get("/api/runs").json()["items"] if item["id"] == run_id)
    assert record["status"] == "success"


def test_bundled_spread_view_renders_every_panel(demo):
    client, _job_id, run_id = demo
    view = client.get(f"/api/runs/{run_id}/views/spread").json()
    assert view["name"] == "spread"
    assert view["title"] == "Misinformation Spread"
    built = {panel["name"] for panel in view["panels"]}
    # The scripted run records every stream the view's panels declare, so
    # nothing is skipped: the bundled view exercises the full analysis surface.
    assert built == {
        "health_summary",
        "interaction_network",
        "content_feed",
        "probe_trends",
        "probe_distribution",
        "behavior_breakdown",
        "exposure_funnel",
    }
    assert view["skipped"] == []


def test_platform_viewer_mounts_in_process(demo):
    client, _job_id, run_id = demo
    started = client.post(f"/api/viewers/{run_id}/twitter_like")
    assert started.status_code == 200
