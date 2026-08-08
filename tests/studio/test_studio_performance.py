"""Studio does not re-do work a browser or a tab does not need.

Between them these are what make navigating Studio quick: the browser stops
re-fetching megabytes of vendored JS, and the server stops rebuilding panels for
tabs that never show them.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.design.assets import CachedAsset
from silisocs.evaluations.run_artifact import load_run
from silisocs.studio.app import create_app


def _make_run(root, name="demo/run-1", events=3):
    run = root / name
    run.mkdir(parents=True)
    (run / "action_events.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "source_user": f"agent-{index % 2}",
                    "label": "post",
                    "episode": index,
                    "backend_type": "twitter_like",
                    "data": {"post_id": index, "post_text": "hello"},
                }
            )
            + "\n"
            for index in range(events)
        )
    )
    (run / "effective_config.yaml").write_text("world:\n  seed: 1\n")
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "game_masters": [{"name": "gm", "backend_type": "twitter_like"}],
                "artifacts": {"action_events": ["action_events.jsonl"]},
            }
        )
    )
    return run


@pytest.mark.parametrize(
    "name",
    ["plotly.js", "cytoscape.js", "tokens.css", "studio.js", "manrope.woff2"],
)
def test_assets_are_revalidated_not_redownloaded(tmp_path, name):
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    first = client.get(f"/assets/{name}")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=86400"
    etag = first.headers["etag"]

    # The bundles are large and unchanged between releases:
    # a browser holding one must never be made to download it again.
    again = client.get(f"/assets/{name}", headers={"if-none-match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_unknown_asset_is_a_404(tmp_path):
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    assert client.get("/assets/nope.js").status_code == 404


def test_chart_runtimes_are_loaded_only_when_a_page_needs_them(tmp_path):
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    page = client.get("/")

    assert page.status_code == 200
    assert '<script src="/assets/plotly.js">' not in page.text
    assert '<script src="/assets/cytoscape.js">' not in page.text
    # The lazy loaders live in the shared panel module, not inline in the page.
    panels = client.get("/assets/panels.js").text
    assert 'loadStudioAsset("plotly.js", "Plotly")' in panels
    assert 'loadStudioAsset("cytoscape.js", "cytoscape")' in panels


def test_plotly_bundle_contains_only_the_cartesian_surface(tmp_path):
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    bundle = client.get("/assets/plotly.js")

    assert bundle.status_code == 200
    assert b"plotly.js (cartesian - minified)" in bundle.content[:300]
    assert len(bundle.content) < 1_500_000


def test_cached_asset_matches_only_its_own_etag():
    asset = CachedAsset.build("body", "text/css")
    other = CachedAsset.build("different", "text/css")
    assert asset.matches(asset.etag)
    assert asset.matches(f"W/{asset.etag}")
    assert asset.matches(f'"stale", {asset.etag}')
    assert asset.matches("*")
    assert not asset.matches(other.etag)
    assert not asset.matches(None)
    assert not asset.matches("")


def test_only_the_active_tab_reads_the_runs_events(tmp_path, monkeypatch):
    _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    client = TestClient(app)

    reads = []
    from silisocs.evaluations import run_artifact as artifact_module

    real_iter = artifact_module.iter_jsonl
    monkeypatch.setattr(
        artifact_module,
        "iter_jsonl",
        lambda files: (reads.append(tuple(files)), real_iter(files))[1],
    )

    for tab in ("config", "logs", "platform", "overview"):
        reads.clear()
        client.get(f"/runs/demo/run-1?tab={tab}")
        assert reads == [], f"{tab} tab parsed event logs it never renders"

    reads.clear()
    assert client.get("/runs/demo/run-1?tab=analyze").status_code == 200
    assert reads, "the analyze tab must read the events it charts"


def test_a_runs_events_are_parsed_once_per_request(tmp_path, monkeypatch):
    """The overview view builds five panels; they share one parse, not five."""
    _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    client = TestClient(app)

    from silisocs.evaluations import run_artifact as artifact_module

    parses = []
    real_iter = artifact_module.iter_jsonl
    monkeypatch.setattr(
        artifact_module,
        "iter_jsonl",
        lambda files: (parses.append(tuple(files)), real_iter(files))[1],
    )
    assert client.get("/runs/demo/run-1?tab=analyze&view=overview").status_code == 200
    action_parses = [files for files in parses if files and "action" in files[0].name]
    assert len(action_parses) == 1


def test_analysis_view_links_preserve_the_active_tab(tmp_path):
    _make_run(tmp_path)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    analyze = client.get("/runs/demo/run-1?tab=analyze&view=overview")
    watch = client.get("/runs/demo/run-1?tab=watch&view=overview")

    assert 'href="?tab=analyze&view=overview"' in analyze.text
    assert 'href="?tab=watch&view=overview"' in watch.text


def test_cached_event_lists_agree_with_the_streaming_api(tmp_path):
    run = _make_run(tmp_path, name="demo/run-2", events=4)
    artifact = load_run(run)
    assert artifact.actions == list(artifact.iter_actions())
    assert artifact.actions is artifact.actions  # cached per instance
    assert artifact.exposures == []
    assert artifact.probes == []


def test_find_run_reuses_one_artifact_until_events_change(tmp_path):
    """Repeated requests for an unchanged run share one parsed-once artifact."""
    from silisocs.studio.catalog import clear_discovery_cache, find_run

    clear_discovery_cache()
    run = _make_run(tmp_path, name="demo/run-cache", events=2)

    first = find_run(tmp_path, "demo/run-cache")
    second = find_run(tmp_path, "demo/run-cache")
    # The same RunArtifact instance is returned, so its cached_property event
    # views are reused instead of re-parsed on every panel/view request.
    assert second.artifact is first.artifact
    assert len(first.artifact.actions) == 2

    # A live run growing on disk changes the action-event size/mtime, which
    # invalidates the cache: a fresh artifact is built that sees the new event.
    with (run / "action_events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"source_user": "x", "label": "post", "episode": 9, "data": {}}) + "\n"
        )
    third = find_run(tmp_path, "demo/run-cache")
    assert third.artifact is not first.artifact
    assert len(third.artifact.actions) == 3

    # Opting out of the cache always rebuilds.
    assert find_run(tmp_path, "demo/run-cache", use_cache=False).artifact is not third.artifact


def test_find_run_sees_the_final_manifest_of_a_completed_run(tmp_path):
    """The manifest replacing its provisional copy invalidates the cache.

    A finishing run's last event row usually lands BEFORE the final manifest
    write, so an event-only fingerprint would serve the stale ``running``
    record forever.
    """
    import os

    from silisocs.studio.catalog import clear_discovery_cache, find_run

    clear_discovery_cache()
    run = _make_run(tmp_path, name="demo/run-finishing", events=1)
    manifest = run / "run_manifest.json"
    provisional = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**provisional, "status": "running"}), encoding="utf-8")

    assert find_run(tmp_path, "demo/run-finishing").artifact.status == "running"

    manifest.write_text(json.dumps({**provisional, "status": "success"}), encoding="utf-8")
    stat = manifest.stat()  # force a distinct mtime even on coarse filesystems
    os.utime(manifest, (stat.st_atime, stat.st_mtime + 1))

    assert find_run(tmp_path, "demo/run-finishing").artifact.status == "success"


def test_discovery_is_memoized_briefly_then_refreshed(tmp_path):
    from silisocs.studio.catalog import clear_discovery_cache, discover_runs

    _make_run(tmp_path, name="demo/run-1")
    assert [record.id for record in discover_runs(tmp_path)] == ["demo/run-1"]

    _make_run(tmp_path, name="demo/run-2")
    # Within the window the walk is skipped, so the new run is not seen yet.
    assert [record.id for record in discover_runs(tmp_path)] == ["demo/run-1"]
    # An uncached read — and any read past the TTL — sees it.
    assert len(discover_runs(tmp_path, use_cache=False)) == 2
    clear_discovery_cache()
    assert len(discover_runs(tmp_path)) == 2


def test_scenario_page_defers_and_scopes_run_history(tmp_path, monkeypatch):
    from silisocs.studio import catalog

    _make_run(tmp_path, name="demo/run-1")
    _make_run(tmp_path, name="other/run-1")
    workspace = tmp_path / "workspace"
    conf = workspace / "scenarios" / "demo" / "conf"
    (conf / "world").mkdir(parents=True)
    (conf / "world" / "default.yaml").write_text(
        "# @package _global_\nscenario_name: demo\nnum_agents: 1\nnum_steps: 1\n"
    )
    (conf / "agents").mkdir()
    (conf / "agents" / "default.yaml").write_text(
        "# @package agents\npersona_pipeline:\n  classes: {}\n"
    )
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=workspace))
    walked = []
    real_walk = catalog.os.walk
    monkeypatch.setattr(
        catalog.os,
        "walk",
        lambda path: (walked.append(path), real_walk(path))[1],
    )

    page = client.get("/scenarios/demo")
    assert page.status_code == 200
    assert walked == []
    # The page loads the composer module, which fetches the history separately.
    assert '<script src="/assets/scenario.js">' in page.text
    assert "loadScenarioHistory()" in client.get("/assets/scenario.js").text

    history = client.get("/api/scenarios/demo/runs")
    assert history.status_code == 200
    assert [item["id"] for item in history.json()["items"]] == ["demo/run-1"]
    assert walked == [(tmp_path / "demo").resolve()]


def test_event_growth_reads_a_prediscovered_file_list_incrementally(tmp_path):
    """The SSE loop resolves event files occasionally, then reads them by offset.

    ``_read_event_growth`` no longer discovers files itself: it takes the
    already-resolved path list and only reads the bytes appended since the
    previous call, so discovery does not run on every 0.4s poll tick.
    Discovery itself defers to the canonical resolver in
    ``evaluations.action_events`` (flat run-root log for a single-GM run, per-GM
    shards otherwise), so the watch ribbon and the run artifact never disagree
    about which logs a run has.
    """
    from silisocs.studio.jobs import _event_stream_files, _read_event_growth

    root = tmp_path / "run"
    root.mkdir(parents=True)
    flat = root / "action_events.jsonl"
    flat.write_text(json.dumps({"episode": 0}) + "\n")

    files = _event_stream_files(root, "action")
    assert files == [flat]

    positions: dict = {}
    # Growth is keyed by source: "" for the flat root log, gm name per shard.
    assert _read_event_growth(files, positions, root) == ({"": 1}, 0)
    # A second read over the same list without new bytes returns nothing.
    assert _read_event_growth(files, positions, root) == ({}, -1)
    # Appending is picked up on the next read, without re-discovering.
    with flat.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"episode": 5}) + "\n")
    assert _read_event_growth(files, positions, root) == ({"": 1}, 5)

    # A multi-GM run has no flat log; every per-GM shard is its own source.
    multi = tmp_path / "multi"
    for gm, episode in (("social_gm", 1), ("market_gm", 2)):
        (multi / gm).mkdir(parents=True)
        (multi / gm / "action_events.jsonl").write_text(json.dumps({"episode": episode}) + "\n")
    shards = _event_stream_files(multi, "action")
    assert {path.parent.name for path in shards} == {"social_gm", "market_gm"}
    assert _read_event_growth(shards, {}, multi) == ({"social_gm": 1, "market_gm": 1}, 2)


def test_discovery_finds_runs_in_one_walk_and_skips_per_gm_shards(tmp_path):
    run = _make_run(tmp_path, name="demo/multi")
    shard = run / "social_gm"
    shard.mkdir()
    (shard / "action_events.jsonl").write_text("")

    from silisocs.studio.catalog import discover_runs

    assert [record.id for record in discover_runs(tmp_path, use_cache=False)] == ["demo/multi"]


def _slow_workspace_scan(monkeypatch):
    """Hold the warm-up open until the returned event is set."""
    import threading

    from silisocs.studio.workspace import WorkspaceCatalog

    release = threading.Event()
    real = WorkspaceCatalog.extension_catalog

    def scan(self):
        release.wait(10)
        return real(self)

    monkeypatch.setattr(WorkspaceCatalog, "extension_catalog", scan)
    return release


def test_ready_reports_the_warmup_phase_and_then_ready(tmp_path, monkeypatch):
    release = _slow_workspace_scan(monkeypatch)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    warming = client.get("/api/ready").json()
    assert warming == {"ready": False, "phase": warming["phase"]}
    assert warming["phase"]

    release.set()
    assert client.app.state.studio.warmup.wait(10)
    assert client.get("/api/ready").json() == {"ready": True, "phase": "Ready"}


def test_page_loads_get_the_warming_screen_only_while_the_workspace_is_indexed(
    tmp_path, monkeypatch
):
    """A slow first index answers browsers immediately instead of stalling them."""
    release = _slow_workspace_scan(monkeypatch)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    browser = {"accept": "text/html,application/xhtml+xml"}

    warming = client.get("/", headers=browser)
    assert warming.status_code == 503
    assert 'data-testid="studio-warming"' in warming.text
    # Self-contained: the screen cannot depend on a bundle that is still loading.
    assert "studio.js" not in warming.text
    # APIs are never held artificially — only browser navigations are.
    assert client.get("/api/scenarios").status_code == 200

    release.set()
    assert client.app.state.studio.warmup.wait(10)
    served = client.get("/", headers=browser)
    assert served.status_code == 200
    assert 'data-testid="studio-warming"' not in served.text


def test_the_warming_screen_stays_behind_the_auth_middleware(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_AUTH_TOKEN", "secret")
    release = _slow_workspace_scan(monkeypatch)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    remote = TestClient(app, client=("203.0.113.9", 40000))

    assert remote.get("/", headers={"accept": "text/html"}).status_code == 401
    assert remote.get("/api/ready").status_code == 401
    release.set()


def test_binding_studio_does_not_import_the_composer_engine_tree(tmp_path):
    """Registering the routers must not drag in omegaconf/antlr4 (~110 modules).

    ``silisocs.studio.preflight`` reads its scale estimate off the real
    turn/participation policies, so it imports the engine — the single largest
    block of module loads at startup, and the one that matters most exactly
    where startup is slow (a networked filesystem). It is warmed on a thread
    instead (through the ``silisocs.studio.forms`` aggregate), so this asserts
    the bind path itself stays clear of it — in a subprocess, because the rest
    of the suite has long since imported everything.

    The rest of the composer (``form_schema``/``form_providers``/``compose``) is
    deliberately NOT listed: it costs nothing to import, which is why the
    routers import it directly instead of inside their handlers.
    """
    import subprocess
    import sys

    program = f"""
import sys
from silisocs.studio.app import StudioWarmup, create_app

StudioWarmup.start = lambda self: None  # measure the bind path, not the warm-up
create_app({str(tmp_path)!r}, state_dir={str(tmp_path / "state")!r}, repo_root={str(tmp_path)!r})
engine_tree = ("silisocs.studio.forms", "silisocs.studio.preflight", "omegaconf", "antlr4")
deferred = [name for name in engine_tree if name in sys.modules]
assert not deferred, deferred
"""
    subprocess.run([sys.executable, "-c", program], check=True)
