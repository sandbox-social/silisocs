"""Smoke tests for Studio's artifact-backed HTTP surface."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.design.tokens import ACCENT
from silisocs.studio.app import create_app
from silisocs.studio.forms import ScenarioRepository
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
    home = client.get("/").text
    assert "Silisocs Studio" in home
    assert "data-observatory" in home
    assert "mobile-dock" in home
    assert 'src="/assets/studio.js"' in home
    assert 'href="/runs/demo/run-1"' in home
    assert client.get("/api/runs").json()["items"][0]["id"] == "demo/run-1"
    assert any(
        panel["name"] == "action_trends" for panel in client.get("/api/panels").json()["items"]
    )
    assert client.get("/api/runs/demo/run-1/views/overview").json()["name"] == "overview"
    page = client.get("/runs/demo/run-1").text
    assert "Run health" in page
    assert ">Overview</a>" in page
    assert "Run: Analyze" in page


def test_scenario_create_save_and_interactive_launch_surface(tmp_path):
    workspace = tmp_path / "workspace"
    client = TestClient(
        create_app(
            tmp_path / "outputs",
            state_dir=tmp_path / "state",
            repo_root=workspace,
        ),
        raise_server_exceptions=False,
    )

    draft = client.get("/scenarios/new?name=audit_case")
    assert draft.status_code == 200
    assert 'id="launch-mode"' in draft.text
    assert '<option value="interactive">Interactive</option>' in draft.text
    assert '<script src="/assets/scenario.js">' in draft.text
    # An interactive launch starts the run paused; the payload lives in the
    # composer module the page above loads.
    assert "interactive,start_paused:interactive" in client.get("/assets/scenario.js").text

    saved = client.post(
        "/api/scenarios",
        json={
            "name": "audit_case",
            "source": "workspace",
            "files": ScenarioRepository.default_files("audit_case"),
        },
    )
    assert saved.status_code == 200
    assert client.get("/scenarios/audit_case").status_code == 200


def test_malformed_scenario_reports_the_parse_error_instead_of_500ing(tmp_path):
    """A scenario whose YAML does not parse EXISTS: every surface that reads it
    must say what is wrong with it (422 + the parser's message, which carries
    the file position), not 500, and not claim the scenario is missing.
    """
    workspace = tmp_path / "workspace"
    conf = workspace / "scenarios" / "broken" / "conf" / "world"
    conf.mkdir(parents=True)
    (conf / "default.yaml").write_text("num_agents: [1, 2\n", encoding="utf-8")
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=workspace),
        raise_server_exceptions=False,
    )

    for url in ("/scenarios/broken", "/api/scenarios/broken"):
        response = client.get(url)
        assert response.status_code == 422, url
        detail = response.json()["detail"]
        assert "broken" in detail
        # PyYAML names the line/column; that is the whole value of surfacing it.
        assert "line" in detail

    preflight = client.post("/api/preflight", json={"scenario": "broken"})
    assert preflight.status_code == 422

    # A scenario that genuinely is not there is still a 404.
    assert client.get("/api/scenarios/absent").status_code == 404


def test_compose_reports_a_scalar_document_instead_of_500ing(tmp_path):
    """The exact shape preflight was hardened against, one endpoint over.

    A whole document typed as a scalar (``sim.yaml`` holding a bare model name)
    is an ordinary YAML typo; composing into it must name the file, not raise a
    TypeError from the middle of the write.
    """
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=tmp_path),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/compose",
        json={
            "files": {"world/default.yaml": "num_steps: 1\n", "sim.yaml": "gpt-4o-mini\n"},
            "updates": {"world.num_steps": 3},
        },
    )

    assert 400 <= response.status_code < 500
    assert "sim.yaml" in response.json()["detail"]


def test_unhandled_error_answers_with_the_error_not_a_blank_wall(tmp_path, caplog):
    """Starlette's default 500 body is the bare string "Internal Server Error".
    Studio answers the same JSON envelope every other error uses, so the client
    can show it — and logs the traceback server-side.

    The body names the exception TYPE only: an unhandled message is arbitrary
    text (here, an absolute host path) and a token-holding reader is not
    necessarily the operator, so the message stays in the log.
    """
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)

    @app.get("/api/_boom")
    def boom():
        raise RuntimeError(f"the recommender fell over reading {tmp_path}/secret")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level("ERROR", logger="silisocs.studio"):
        response = client.get("/api/_boom")
    assert response.status_code == 500
    assert response.json()["detail"].startswith("RuntimeError")
    assert "see the Studio server log" in response.json()["detail"]
    assert str(tmp_path) not in response.text
    # The operator still gets the whole thing, traceback included.
    assert "the recommender fell over" in caplog.text


def test_scenario_save_conflicts_on_a_stale_fingerprint(tmp_path):
    """Two composer tabs no longer clobber each other: the stale save 409s."""
    workspace = tmp_path / "workspace"
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=workspace)
    )
    files = ScenarioRepository.default_files("audit_case")
    body = {"name": "audit_case", "source": "workspace", "files": files}
    assert client.post("/api/scenarios", json=body).status_code == 200

    loaded = client.get("/api/scenarios/audit_case").json()
    fingerprints = loaded["fingerprints"]
    assert set(fingerprints) == set(loaded["files"])

    # Another tab, holding the same fingerprints, saves first.
    ahead = {**files, "sim.yaml": "# another tab wrote this\n{}\n"}
    assert (
        client.post("/api/scenarios", json={**body, "files": ahead, "fingerprints": fingerprints})
    ).status_code == 200

    stale = {**files, "sim.yaml": "max_concurrent_actions: 9\n"}
    conflict = client.post(
        "/api/scenarios",
        json={**body, "files": stale, "fingerprints": fingerprints, "baselines": files},
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["error"] == "conflict"
    assert detail["file"] == "sim.yaml"
    assert "another tab wrote this" in detail["diff"]
    saved_path = workspace / "scenarios" / "audit_case" / "conf" / "sim.yaml"
    assert "another tab wrote this" in saved_path.read_text(encoding="utf-8")

    # "Overwrite" resends with the fingerprint the 409 reported.
    retry = client.post(
        "/api/scenarios",
        json={
            **body,
            "files": stale,
            "fingerprints": {**fingerprints, "sim.yaml": detail["fingerprint"]},
        },
    )
    assert retry.status_code == 200
    assert retry.json()["fingerprints"]["sim.yaml"] != detail["fingerprint"]
    assert saved_path.read_text(encoding="utf-8") == "max_concurrent_actions: 9\n"

    # A payload without fingerprints (old clients, curl) still overwrites.
    assert client.post("/api/scenarios", json={**body, "files": files}).status_code == 200

    # The conflict UX lives in the shared shell, under a stable test id.
    assert '"save-conflict"' in client.get("/assets/studio.js").text


def test_study_save_is_verbatim_and_conflicts_on_a_stale_fingerprint(tmp_path):
    """study.yaml keeps the author's comments, and a stale save 409s."""
    workspace = tmp_path / "workspace"
    _make_study(workspace)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=workspace))

    loaded = client.get("/api/studies/exp1").json()
    authored = "# lab notebook: keep this comment\n" + loaded["yaml"]
    saved = client.post(
        "/api/studies/exp1",
        json={"yaml": authored, "fingerprint": loaded["fingerprint"], "baseline": loaded["yaml"]},
    )
    assert saved.status_code == 200
    definition_path = workspace / "experiments" / "studies" / "exp1" / "study.yaml"
    assert definition_path.read_text(encoding="utf-8") == authored

    conflict = client.post(
        "/api/studies/exp1",
        json={
            "yaml": loaded["yaml"],
            "fingerprint": loaded["fingerprint"],
            "baseline": loaded["yaml"],
        },
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["file"] == "study.yaml"
    assert "keep this comment" in detail["diff"]
    assert definition_path.read_text(encoding="utf-8") == authored

    # No fingerprint keeps the plain overwrite behaviour.
    assert client.post("/api/studies/exp1", json={"yaml": loaded["yaml"]}).status_code == 200
    assert definition_path.read_text(encoding="utf-8") == loaded["yaml"]


def test_completed_watch_ribbon_uses_persisted_artifact_counters(tmp_path):
    run = _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    app.state.studio.jobs.store.insert(
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


def test_study_page_rejects_path_like_view(tmp_path):
    """An unmapped tab must route ?view= through the allowlist, never read a path.

    Without the guard, a path-like ``?view=`` reaches ``build_view`` ->
    ``Path(view).read_text`` + panel ``class_path`` import: arbitrary file read
    and import-time code execution.
    """
    import yaml

    from silisocs.studio.studies import StudyRepository

    workspace = tmp_path / "workspace"
    StudyRepository(workspace / "experiments" / "studies").save(
        "expt",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "study": {
                    "name": "Expt",
                    "id": "expt",
                    "scenarios": ["demo"],
                    "run_defaults": {"seed_start": 1, "seed_repeats": 1},
                },
                "hypotheses": {
                    "h1": {"statement": "x", "conditions": {"control": {"overrides": {}}}}
                },
            }
        ),
    )
    client = TestClient(
        create_app(tmp_path, state_dir=tmp_path / "state", repo_root=workspace),
        raise_server_exceptions=False,
    )

    # A legit built-in study view (via its tab) still renders.
    assert client.get("/studies/expt?tab=board").status_code == 200
    # An unmapped tab falls through to the raw ?view=; a path is rejected, not read.
    assert client.get("/studies/expt?tab=analyze&view=/etc/hostname").status_code == 404
    assert client.get("/studies/expt?tab=analyze&view=../../../etc/passwd").status_code == 404
    assert client.get("/studies/expt?tab=analyze&view=nope").status_code == 404


def test_malformed_scenario_view_does_not_500_the_run_page(tmp_path):
    """A broken scenario views/*.yaml is skipped, not fatal to the whole run page."""
    outputs = tmp_path / "artifacts"
    _make_run(outputs)
    view_dir = tmp_path / "workspace" / "scenarios" / "demo" / "conf" / "views"
    view_dir.mkdir(parents=True)
    (view_dir / "broken.yaml").write_text("view: [unterminated\n", encoding="utf-8")
    (view_dir / "lab.yaml").write_text(
        "view:\n"
        "  name: lab\n"
        "  title: Lab View\n"
        "  scope: run\n"
        "  layout: rows\n"
        "  panels:\n"
        "    - built_in: health_summary\n",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path / "workspace"),
        raise_server_exceptions=False,
    )

    # The malformed view raises YAMLError inside run_view_names; it must be
    # dropped, leaving the page (and the valid sibling view) working.
    assert client.get("/runs/demo/run-1").status_code == 200
    assert client.get("/api/runs/demo/run-1/views/lab").status_code == 200


def test_invalid_shipped_view_is_422_everywhere_and_logged_when_dropped(tmp_path, caplog):
    """A view that EXISTS but does not parse answers 422 on every surface.

    Layout `tabs` was legal before it was rejected, so a scenario in the wild
    ships one. 404 would send the author looking for a missing file and 500
    tells them nothing, so each surface answers the parser's own message — and
    the nav, which drops the view rather than breaking every tab, says so in
    the server log instead of dropping it silently.
    """
    outputs = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    _make_run(outputs)
    _make_study(workspace, scenarios=("demo",))
    view_dir = workspace / "scenarios" / "demo" / "conf" / "views"
    view_dir.mkdir(parents=True)
    (view_dir / "tabbed.yaml").write_text(
        "view:\n  name: tabbed\n  title: Tabbed\n  scope: run\n  layout: tabs\n"
        "  panels:\n    - built_in: health_summary\n",
        encoding="utf-8",
    )
    (view_dir / "tabbed_study.yaml").write_text(
        "view:\n  name: tabbed_study\n  title: Tabbed Study\n  scope: study\n  layout: tabs\n"
        "  panels:\n    - built_in: condition_comparison\n",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(outputs, state_dir=tmp_path / "state", repo_root=workspace),
        raise_server_exceptions=False,
    )

    message = "View layout 'tabs' is not implemented"
    for url in (
        "/runs/demo/run-1?tab=analyze&view=tabbed",
        "/api/runs/demo/run-1/views/tabbed",
        "/api/runs/demo/run-1/report?view=tabbed",
        "/studies/exp1?tab=analyze&view=tabbed_study",
        "/api/studies/exp1/compare?view=tabbed_study",
    ):
        response = client.get(url)
        assert response.status_code == 422, url
        assert message in response.text, url

    # A genuinely absent view is still a 404 on the same surfaces.
    assert client.get("/api/runs/demo/run-1/views/nope").status_code == 404
    assert client.get("/api/studies/exp1/compare?view=nope").status_code == 404

    # The run page still renders without the invalid view — and names it.
    with caplog.at_level("WARNING", logger="silisocs.studio"):
        assert client.get("/runs/demo/run-1?tab=analyze").status_code == 200
    assert "tabbed.yaml" in caplog.text
    assert message in caplog.text


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
    assert body.startswith("@font-face{")
    assert "NaNs" not in body
    assert client.get("/assets/manrope.woff2").headers["content-type"].startswith("font/woff2")


def test_templates_only_load_scripts_studio_serves(tmp_path):
    """Every ``<script src>`` a template names must resolve, and nothing else.

    Studio's client code lives in ``static/*.js`` served through ``/assets``;
    a template carries only its JSON data island plus script tags. A typo'd
    ``src`` is a page that silently loses all of its behaviour (no error, no
    failing assertion anywhere else), and a re-inlined block is code that
    escapes linting and reuse — so both are checked here.
    """
    import re
    from pathlib import Path

    import silisocs.studio

    # Rendered before the workspace index exists, so it deliberately loads
    # nothing at all — it is the one page that cannot depend on an asset.
    standalone = {"warming.html"}
    templates = Path(silisocs.studio.__file__).resolve().parent / "templates"
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    referenced: set[str] = set()
    for path in sorted(templates.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        for attributes, body in re.findall(r"<script([^>]*)>(.*?)</script>", text, re.DOTALL):
            if "src=" in attributes:
                assert not body.strip(), f"{path.name}: a sourced script must have no body"
                continue
            assert path.name in standalone or 'type="application/json"' in attributes, (
                f"{path.name} carries an inline script; it belongs in static/*.js"
            )
        referenced.update(re.findall(r'<script[^>]*\ssrc="([^"]+)"', text))

    assert "/assets/studio.js" in referenced
    assert "/assets/panels.js" in referenced
    for url in sorted(referenced):
        assert url.startswith("/assets/"), f"{url} is not served by Studio"
        assert client.get(url).status_code == 200, f"{url} does not resolve"


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


def test_cross_site_origin_is_rejected(tmp_path):
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    payload = {"files": {"world/default.yaml": "num_agents: 1\nnum_steps: 1\n"}}

    # A forged cross-site browser POST carries a foreign Origin -> 403.
    hostile = client.post("/api/preflight", json=payload, headers={"origin": "http://evil.example"})
    assert hostile.status_code == 403

    # A same-origin Studio fetch (Origin matches Host) is allowed.
    same = client.post(
        "/api/preflight",
        json=payload,
        headers={"origin": "http://testserver", "host": "testserver"},
    )
    assert same.status_code == 200


def test_running_run_is_listed_and_its_pages_load_mid_run(tmp_path):
    """A provisional (status "running") manifest makes a live run watchable.

    The session writes it at launch; the run must appear in the catalog and its
    pages must load while event logs are still growing — including logs the
    launch-time snapshot never saw (discovered live for a running status).
    """
    run = tmp_path / "demo" / "run-live"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "running",
                "scenario": "demo",
                "num_agents": 2,
                "num_steps": 5,
                "game_masters": [{"name": "gm", "backend_type": "twitter_like"}],
                "artifacts": {"action_events": []},  # nothing written yet at launch
            }
        )
    )
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    listed = client.get("/api/runs").json()["items"]
    assert [item["status"] for item in listed] == ["running"]
    run_id = listed[0]["id"]
    assert client.get(f"/runs/{run_id}?tab=watch&view=overview").status_code == 200

    # Events written after launch are visible on the next page load.
    (run / "action_events.jsonl").write_text(
        json.dumps({"source_user": "Alice", "label": "post", "episode": 0, "data": {}}) + "\n"
    )
    page = client.get(f"/runs/{run_id}?tab=analyze&view=overview")
    assert page.status_code == 200
    assert "Alice" in page.text


def test_generic_study_panel_endpoint_serves_any_study_panel(tmp_path):
    """/api/studies/{id}/panels/{name} mirrors the run-panel endpoint at study
    scope, so live surfaces (the Board's SSE refresh) render any study panel
    through the shared machinery instead of hand-building one panel's shape.
    """
    import yaml

    workspace = tmp_path / "workspace"
    study_dir = workspace / "experiments" / "studies" / "exp1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "study": {
                    "name": "Exp",
                    "id": "exp1",
                    "scenarios": ["world_a"],
                    "run_defaults": {"seed_start": 1, "seed_repeats": 1},
                },
                "hypotheses": {"h1": {"statement": "s", "conditions": {"c": {"overrides": {}}}}},
            },
            sort_keys=False,
        )
    )
    organized = study_dir / "generated" / "organized"
    organized.mkdir(parents=True)
    (organized / "study_summary.yaml").write_text(
        json.dumps({"metrics_by_condition": {"h1": {"c": {"metric": 1.0}}}})
    )
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=workspace))

    payload = client.get("/api/studies/exp1/panels/condition_comparison")
    assert payload.status_code == 200
    assert payload.json()["output"]["type"] == "figure"

    # A run-scope panel is not served at study scope.
    assert client.get("/api/studies/exp1/panels/action_trends").status_code == 404

    # The compare endpoint routes its params to any named study panel.
    compared = client.get("/api/studies/exp1/compare", params={"compare": "seed"})
    assert compared.status_code == 200
    assert (
        client.get("/api/studies/exp1/compare", params={"panel": "no_such_panel"}).status_code
        == 200
    )
    # A run-scope or unknown view is a 404, never an unhandled scope error.
    assert client.get("/api/studies/exp1/compare", params={"view": "overview"}).status_code == 404
    assert client.get("/api/studies/exp1/compare", params={"view": "nope"}).status_code == 404


def _make_study(workspace, study_id="exp1", scenarios=("world_a",)):
    import yaml

    study_dir = workspace / "experiments" / "studies" / study_id
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "study": {
                    "name": "Exp",
                    "id": study_id,
                    "scenarios": list(scenarios),
                    "run_defaults": {"seed_start": 1, "seed_repeats": 1},
                },
                "hypotheses": {"h1": {"statement": "s", "conditions": {"c": {"overrides": {}}}}},
            },
            sort_keys=False,
        )
    )
    return study_dir


def test_study_board_links_each_completed_replicate_to_its_run_page(tmp_path):
    """The board's run cells resolve to run pages, and those runs link back.

    Study replicate runs live under the studies root (outside the output root),
    so this also covers the second discovery root: the run must be addressable
    at /runs/studies/... and carry a back-link to its parent study.
    """
    outputs = tmp_path / "outputs"
    workspace = tmp_path / "workspace"
    study_dir = _make_study(workspace)
    replicate = study_dir / "runs" / "h1" / "c" / "world_a" / "seed_1"
    _make_run(replicate, name="run")
    (replicate / "run" / "RUN_COMPLETE.json").write_text("{}")
    client = TestClient(create_app(outputs, state_dir=tmp_path / "state", repo_root=workspace))

    run_href = "/runs/studies/exp1/runs/h1/c/world_a/seed_1/run"
    board = client.get("/studies/exp1?tab=board")
    assert board.status_code == 200
    assert f'href="{run_href}"' in board.text

    # The API shape carries the same link for live board refreshes.
    payload = client.get("/api/studies/exp1/panels/study_progress").json()
    linked = [
        cell
        for row in payload["output"]["rows"]
        for cell in row.values()
        if isinstance(cell, dict) and cell.get("href")
    ]
    assert [cell["href"] for cell in linked] == [run_href]

    # The replicate is a first-class run page, and it knows its parent study.
    run_page = client.get(run_href)
    assert run_page.status_code == 200
    assert 'href="/studies/exp1"' in run_page.text
    assert any(
        item["id"].startswith("studies/") for item in client.get("/api/runs").json()["items"]
    )


def test_unregistered_class_path_panel_is_served_by_name(tmp_path):
    """A one-off view-slot class_path panel answers the by-name panel endpoint.

    Live refresh resolves panels by name; a panel referenced only via
    class_path in a shipped view (never registered) must still refresh instead
    of silently 404-ing after its first render.
    """
    import sys
    import types

    from silisocs.analysis import Markdown, Panel

    module = types.ModuleType("studio_oneoff_panels")

    class OneOffPanel(Panel):
        name = "oneoff_metric"
        title = "One-off metric"
        scope = "run"

        def build(self, artifact, params):
            return Markdown("one-off output")

    module.OneOffPanel = OneOffPanel
    sys.modules["studio_oneoff_panels"] = module
    try:
        outputs = tmp_path / "artifacts"
        _make_run(outputs)
        view_dir = tmp_path / "workspace" / "scenarios" / "demo" / "conf" / "views"
        view_dir.mkdir(parents=True)
        (view_dir / "custom.yaml").write_text(
            "view:\n  name: custom\n  title: Custom\n  scope: run\n  layout: rows\n"
            "  panels:\n    - class_path: studio_oneoff_panels.OneOffPanel\n",
            encoding="utf-8",
        )
        client = TestClient(
            create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path / "workspace")
        )
        payload = client.get("/api/runs/demo/run-1/panels/oneoff_metric")
        assert payload.status_code == 200
        assert payload.json()["output"]["text"] == "one-off output"
        # A name in no registry and no shipped view is still a 404.
        assert client.get("/api/runs/demo/run-1/panels/never_heard_of_it").status_code == 404
    finally:
        sys.modules.pop("studio_oneoff_panels", None)


def test_scenario_shipped_study_view_serves_for_study(tmp_path):
    """A scenario may ship a study-scope view; studies using it can select it."""
    workspace = tmp_path / "workspace"
    _make_study(workspace)
    view_dir = workspace / "scenarios" / "world_a" / "conf" / "views"
    view_dir.mkdir(parents=True)
    (view_dir / "custom_study.yaml").write_text(
        "view:\n  name: custom_study\n  title: Custom Study View\n  scope: study\n"
        "  layout: rows\n  panels:\n    - built_in: per_agent_distributions\n",
        encoding="utf-8",
    )
    (view_dir / "lab.yaml").write_text(
        "view:\n  name: lab\n  title: Lab\n  scope: run\n  layout: rows\n"
        "  panels:\n    - built_in: health_summary\n",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=workspace),
        raise_server_exceptions=False,
    )

    page = client.get("/studies/exp1?tab=analyze&view=custom_study")
    assert page.status_code == 200
    assert 'data-panel="per_agent_distributions"' in page.text
    assert (
        client.get("/api/studies/exp1/compare", params={"view": "custom_study"}).status_code == 200
    )
    # A run-scope view — shipped or built-in — stays a 404 at study scope.
    assert client.get("/studies/exp1?tab=analyze&view=lab").status_code == 404
    assert client.get("/studies/exp1?tab=analyze&view=overview").status_code == 404


def test_study_panels_render_their_controls(tmp_path):
    """Study panels' declared controls render on the study page like run panels'."""
    workspace = tmp_path / "workspace"
    _make_study(workspace)
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=workspace)
    )

    page = client.get("/studies/exp1?tab=compare")
    assert page.status_code == 200
    assert 'data-study-id="exp1"' in page.text
    assert "setPanelParam('condition_comparison','baseline'" in page.text
    assert "setPanelParam('condition_comparison','compare'" in page.text


def test_interactive_watch_tab_carries_run_controls(tmp_path):
    """The Watch tab shows Step/Play/Pause for an interactive running job.

    The live -> watch handoff must not strand a start-paused interactive run on
    a page without its controls.
    """
    run = _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    app.state.studio.jobs.store.insert(
        Job(
            id="interactive-run",
            kind="run",
            status="running",
            pid=None,
            created_at=100.0,
            started_at=101.0,
            ended_at=None,
            exit_code=None,
            scenario="demo",
            config_snapshot_path=None,
            output_dir=str(run),
            log_path=str(tmp_path / "interactive.log"),
            parent_study=None,
            port=None,
            command_json=json.dumps(
                {
                    "argv": [],
                    "cwd": str(tmp_path),
                    "env": {},
                    "control_path": str(run / "run.control"),
                }
            ),
        )
    )

    page = TestClient(app).get("/runs/demo/run-1?tab=watch")
    assert page.status_code == 200
    assert "control-bar" in page.text
    assert "ctlStep()" in page.text
    assert "/control" in page.text


def _finished_interactive_job(tmp_path, run) -> Job:
    return Job(
        id="finished-interactive",
        kind="run",
        status="finished",
        pid=None,
        created_at=100.0,
        started_at=101.0,
        ended_at=200.0,
        exit_code=0,
        scenario="demo",
        config_snapshot_path=None,
        output_dir=str(run),
        log_path=str(tmp_path / "interactive.log"),
        parent_study=None,
        port=None,
        command_json=json.dumps(
            {
                "argv": [],
                "cwd": str(tmp_path),
                "env": {},
                "control_path": str(run / "run.control"),
            }
        ),
    )


def test_finished_interactive_job_renders_no_run_controls(tmp_path):
    """A dead job's control file is inert — the live page must not offer controls."""
    run = _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    app.state.studio.jobs.store.insert(_finished_interactive_job(tmp_path, run))

    page = TestClient(app).get("/live?job=finished-interactive")
    assert page.status_code == 200
    assert "control-bar" not in page.text
    assert "ctlStep()" not in page.text


def test_run_controls_are_rejected_for_a_terminal_job(tmp_path):
    """POSTing controls to a finished job errors loudly instead of a silent 200."""
    run = _make_run(tmp_path)
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    app.state.studio.jobs.store.insert(_finished_interactive_job(tmp_path, run))

    response = TestClient(app).post("/api/jobs/finished-interactive/control", json={"target": 1})
    assert response.status_code == 422
    assert "finished" in response.json()["detail"]
    assert not (run / "run.control").exists()


def test_run_track_reflects_completion_not_step_count(tmp_path):
    """The archive's run track is a completion signal, not a step-count bar."""
    _make_run(tmp_path)  # status success, 1 step
    running = tmp_path / "demo" / "run-live"
    running.mkdir(parents=True)
    (running / "run_manifest.json").write_text(
        json.dumps({"status": "running", "scenario": "demo", "num_steps": 20, "artifacts": {}})
    )
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    page = client.get("/runs").text
    # The 1-step successful run is full; the 20-step running run is not.
    assert "width:100%" in page
    assert "width:0%" in page
    assert "width:10%" not in page


def test_unknown_control_kind_degrades_to_text_input_not_nothing(tmp_path):
    """A custom Control.kind the shell does not know must still render a working
    widget (a text input bound to setPanelParam), never silently vanish.
    """
    from silisocs.analysis import Control, Markdown, Panel, register_panel
    from silisocs.analysis.panel import _PANELS

    @register_panel
    class _ThresholdProbe(Panel):
        name = "test_threshold_probe"
        title = "Threshold probe"
        scope = "run"
        requires = frozenset({"action_events"})
        controls = (Control(kind="threshold_slider", param="threshold", label="Threshold"),)

        def build(self, artifact, params):
            return Markdown(f"threshold={params.get('threshold', 'unset')}")

    try:
        outputs = tmp_path / "artifacts"
        _make_run(outputs)
        view_dir = tmp_path / "workspace" / "scenarios" / "demo" / "conf" / "views"
        view_dir.mkdir(parents=True)
        (view_dir / "custom.yaml").write_text(
            "view:\n  name: custom\n  title: Custom\n  scope: run\n  layout: rows\n"
            "  panels:\n    - built_in: test_threshold_probe\n",
            encoding="utf-8",
        )
        client = TestClient(
            create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path / "workspace")
        )
        page = client.get("/runs/demo/run-1?tab=analyze&view=custom")
        assert page.status_code == 200
        assert "setPanelParam('test_threshold_probe','threshold'" in page.text
        assert 'type="text"' in page.text
    finally:
        _PANELS.pop("test_threshold_probe", None)


def test_unknown_output_type_renders_a_note_not_an_empty_panel(tmp_path):
    """A PanelOutput type a renderer does not know (a custom panel, or one half
    of a lockstep change) must SAY so on both surfaces. Rendering nothing is
    indistinguishable from a panel that had nothing to report.
    """
    from dataclasses import dataclass

    from silisocs.analysis import Panel, register_panel
    from silisocs.analysis.panel import _PANELS

    @dataclass(frozen=True)
    class Sankey:  # a shape neither renderer knows
        flows: tuple[str, ...] = ()

    @register_panel
    class _SankeyProbe(Panel):
        name = "test_sankey_probe"
        title = "Sankey probe"
        scope = "run"
        requires = frozenset({"action_events"})

        def build(self, artifact, params):
            return Sankey(("a->b",))

    try:
        outputs = tmp_path / "artifacts"
        _make_run(outputs)
        view_dir = tmp_path / "workspace" / "scenarios" / "demo" / "conf" / "views"
        view_dir.mkdir(parents=True)
        (view_dir / "custom.yaml").write_text(
            "view:\n  name: custom\n  title: Custom\n  scope: run\n  layout: rows\n"
            "  panels:\n    - built_in: test_sankey_probe\n",
            encoding="utf-8",
        )
        client = TestClient(
            create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path / "workspace")
        )
        page = client.get("/runs/demo/run-1?tab=analyze&view=custom")
        assert page.status_code == 200
        assert 'data-unknown-output="sankey"' in page.text

        # The client renderer repaints the same panel without a navigation, so
        # it carries the same fallback — kept in lockstep by hand.
        assert "unknownOutput" in client.get("/assets/panels.js").text
    finally:
        _PANELS.pop("test_sankey_probe", None)


def test_empty_states_offer_the_action_they_describe(tmp_path):
    """An empty page must name the way out of being empty — and must not blame
    filters the user never set.
    """
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=tmp_path)
    )

    runs = client.get("/runs").text
    assert "No runs yet" in runs
    assert "Adjust the filters" not in runs
    # With runs indexed, a filter that matches nothing DOES say so.
    _make_run(tmp_path / "outputs")
    filtered = client.get("/runs", params={"q": "nothing-matches-this"}).text
    assert "No runs match these filters" in filtered

    scenarios = client.get("/scenarios").text
    assert "No scenarios yet" in scenarios
    assert scenarios.count("New scenario") >= 2  # page action + empty-state CTA
    studies = client.get("/studies").text
    assert "No experiments yet" in studies
    assert studies.count("New study") >= 2


def test_client_failures_are_reported_not_swallowed(tmp_path):
    """The shipped shell scripts must not contain a fetch whose failure ends as
    a button that quietly did nothing. These are the sites that used to.
    """
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))
    shell = client.get("/assets/studio.js").text
    runs_js = client.get("/assets/runs.js").text
    panels_js = client.get("/assets/panels.js").text

    # One shared helper: non-ok -> danger toast carrying the server's `detail`.
    assert "window.apiFetch" in shell and "window.apiError" in shell
    # A danger toast persists until dismissed, so it carries a dismiss control.
    assert "toast-close" in shell
    # Step / Play / Pause / End run, plus both Stop buttons, report rejections.
    assert runs_js.count("apiFetch(") >= 3
    # A panel that cannot refresh says so instead of leaving a stale render.
    assert "panel-error" in panels_js


def test_lab_token_protects_remote_reads(tmp_path, monkeypatch):
    """With a token configured, non-localhost GETs need it too (reads leak
    run configs and logs); ?token= once sets the session cookie.
    """
    monkeypatch.setenv("STUDIO_AUTH_TOKEN", "secret")
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    remote = TestClient(app, client=("203.0.113.9", 40000))

    assert remote.get("/runs").status_code == 401
    opened = remote.get("/runs", params={"token": "secret"})
    # The token is traded for an httponly cookie and stripped from the URL via
    # redirect, so the secret never lands in access logs or history.
    assert opened.status_code == 200
    assert "token" not in str(opened.url)
    assert remote.cookies.get("studio_token") == "secret"
    # The cookie now authorizes plain requests (the client jar persists it).
    assert remote.get("/runs").status_code == 200

    # Localhost reads stay open by design, token or not.
    local = TestClient(app)
    assert local.get("/runs").status_code == 200
