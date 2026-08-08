"""Studio serves backend platform viewers in-process, same-origin.

A viewer is an ASGI app declared by its backend (``VisualizerSpec.app_factory``)
and mounted inside Studio, so it is ready the moment the page asks for it. The
subprocess launch plan stays for viewers that are not ASGI apps.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform
from silisocs.environments.backends.twitter_like.visualizer.server import create_viewer_app
from silisocs.studio import viewers as viewers_module
from silisocs.studio.app import create_app
from silisocs.studio.viewers import viewer_mount_path


def _seed_twitter_db(path, usernames):
    """Build a real twitter-like database with one user per name."""
    platform = TwitterLikePlatform(str(path), use_queue=False)
    try:
        for username in usernames:
            platform.create_user(username, bio=f"{username} bio")
    finally:
        platform.shutdown()
    return path


def _make_run(root, name="demo/run-1", usernames=("alice",)):
    run = root / name
    run.mkdir(parents=True)
    _seed_twitter_db(run / "twitter_like.db", usernames)
    (run / "run_manifest.json").write_text(
        json.dumps({"status": "success", "scenario": name.split("/")[0], "game_masters": []})
    )
    return run


def test_viewer_factory_isolates_apps_per_database(tmp_path):
    """Each app reads only the database it was built for."""
    first = create_viewer_app(_seed_twitter_db(tmp_path / "a.db", ["alice", "bob"]))
    second = create_viewer_app(_seed_twitter_db(tmp_path / "b.db", ["carol"]))

    with TestClient(first) as client:
        assert client.get("/api/stats").json()["user_count"] == 2
    with TestClient(second) as client:
        names = [user["username"] for user in client.get("/api/users").json()["users"]]
        assert names == ["carol"]


def test_viewer_factory_lifespan_closes_the_database(tmp_path, monkeypatch):
    db = _seed_twitter_db(tmp_path / "a.db", ["alice"])  # seed before spying
    closed = []
    monkeypatch.setattr(TwitterLikePlatform, "shutdown", lambda self: closed.append(True))

    with TestClient(create_viewer_app(db)) as client:
        client.get("/api/stats")
    # The standalone server relies on lifespan to release the connection.
    assert closed == [True]


def test_studio_serves_mounted_viewer_pages_and_apis(tmp_path):
    _make_run(tmp_path, usernames=["alice", "bob"])
    with TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)) as c:
        index = c.get("/viewers/demo/run-1/twitter_like/")
        assert index.status_code == 200
        # Relative asset paths are what let one app serve at / and under a mount.
        assert 'href="assets/design.css"' in index.text

        assert c.get("/viewers/demo/run-1/twitter_like/api/stats").json()["user_count"] == 2
        css = c.get("/viewers/demo/run-1/twitter_like/assets/design.css")
        assert css.status_code == 200
        assert css.headers["cache-control"] == "public, max-age=86400"
        assert "@font-face" in css.text
        assert "NaNs" not in css.text

        script = c.get("/viewers/demo/run-1/twitter_like/assets/viewer.js")
        assert script.status_code == 200
        assert "function viewerIcon" in script.text

        font = c.get("/viewers/demo/run-1/twitter_like/assets/manrope.woff2")
        assert font.status_code == 200
        assert font.headers["content-type"].startswith("font/woff2")


def test_mounted_viewer_redirects_to_trailing_slash_keeping_query(tmp_path):
    _make_run(tmp_path)
    with TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)) as c:
        response = c.get("/viewers/demo/run-1/twitter_like?theme=dark", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/viewers/demo/run-1/twitter_like/?theme=dark"


def test_mounted_viewer_404s_for_unknown_run_or_backend(tmp_path):
    _make_run(tmp_path)
    with TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)) as c:
        assert c.get("/viewers/demo/run-1/reddit_like/").status_code == 404
        assert c.get("/viewers/demo/absent/twitter_like/").status_code == 404


def test_mounted_viewer_resolves_run_named_after_a_backend(tmp_path):
    """The run/backend split is found by resolution, not by naive splitting."""
    _make_run(tmp_path, name="twitter_like/run-1")
    with TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)) as c:
        assert c.get("/viewers/twitter_like/run-1/twitter_like/api/stats").status_code == 200


def test_studio_builds_each_viewer_once_and_closes_it_on_shutdown(tmp_path, monkeypatch):
    _make_run(tmp_path)
    built = []
    resolve_factory = viewers_module.viewer_app_factory  # before patching the name

    def counting_factory(backend_type):
        factory = resolve_factory(backend_type)
        if factory is None or backend_type != "twitter_like":
            return factory

        def build(db_path):
            built.append(db_path)
            return factory(db_path)

        return build

    monkeypatch.setattr(viewers_module, "viewer_app_factory", counting_factory)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    closed = []
    with TestClient(app) as c:
        c.get("/viewers/demo/run-1/twitter_like/api/stats")
        c.get("/viewers/demo/run-1/twitter_like/api/users")
        assert len(built) == 1  # cached across requests
        for viewer in app.state.studio.viewers._apps.values():
            viewer.state.close_viewer = lambda: closed.append(True)
    assert closed == [True]  # Studio's lifespan releases mounted connections


def test_viewer_resolution_is_memoized_across_requests(tmp_path, monkeypatch):
    """Forwarded requests (incl. iframe assets) reuse a resolved viewer.

    Resolving a request runs find_run + find_backend_dbs (manifest reads +
    globs); repeated requests to the same viewer must not re-do that work.
    """
    _make_run(tmp_path, usernames=["alice"])
    calls = []
    real_find_dbs = viewers_module.find_backend_dbs
    monkeypatch.setattr(
        viewers_module,
        "find_backend_dbs",
        lambda run_dir: (calls.append(run_dir), real_find_dbs(run_dir))[1],
    )
    with TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)) as c:
        assert c.get("/viewers/demo/run-1/twitter_like/api/stats").status_code == 200
        after_first = len(calls)
        assert after_first >= 1  # the first resolution paid for the glob
        # A second identical request resolves from the memo, re-globbing nothing.
        assert c.get("/viewers/demo/run-1/twitter_like/api/stats").status_code == 200
        assert len(calls) == after_first


def test_start_viewer_negotiates_embedded_mode_without_a_job(tmp_path):
    _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    with TestClient(app) as c:
        payload = c.post("/api/viewers/demo/run-1/twitter_like").json()
    assert payload == {
        "mode": "embedded",
        "url": viewer_mount_path("demo/run-1", "twitter_like"),
    }
    # An in-process viewer needs no process: nothing was queued.
    assert [job for job in app.state.studio.jobs.store.list() if job.kind == "viewer"] == []


def test_start_viewer_falls_back_to_a_subprocess_for_non_asgi_visualizers(tmp_path):
    """A backend without an app_factory keeps the launch-plan path."""
    run = tmp_path / "demo" / "run-2"
    run.mkdir(parents=True)
    (run / "state.sqlite").write_bytes(b"")
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "game_masters": [
                    {
                        "name": "world",
                        "backend_type": "legacy_world",
                        "database": "state.sqlite",
                        "visualizer": {
                            "env_var": "LEGACY_WORLD_DB",
                            "module": "legacy.viewer",
                            "default_port": 9099,
                        },
                    }
                ],
            }
        )
    )
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    with TestClient(app) as c:
        payload = c.post("/api/viewers/demo/run-2/legacy_world").json()
        assert payload["mode"] == "subprocess"
        assert payload["status_url"] == "/api/viewers/demo/run-2/legacy_world/status"
        status = c.get(payload["status_url"]).json()
        # The process cannot serve (the module is fictional); either it has not
        # bound yet or it already died — never "ready".
        assert status["state"] in {"starting", "failed"}


def test_viewer_status_reports_an_exited_process(tmp_path):
    from silisocs.studio.jobs import Job

    _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    app.state.studio.jobs.store.insert(
        Job(
            id="dead-viewer",
            kind="viewer",
            status="failed",
            pid=None,
            created_at=0.0,
            started_at=0.0,
            ended_at=1.0,
            exit_code=1,
            scenario="demo/run-1:twitter_like",
            config_snapshot_path=None,
            output_dir=str(tmp_path / "demo" / "run-1"),
            log_path=str(tmp_path / "viewer.log"),
            parent_study=None,
            port=59999,
            command_json='["python", "-m", "legacy.viewer"]',
        )
    )
    with TestClient(app) as c:
        status = c.get("/api/viewers/demo/run-1/twitter_like/status").json()
    assert status["state"] == "failed"
    assert "code 1" in status["detail"]
