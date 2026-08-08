"""Contracts for the shipped Studio front-end assets and their JSON islands.

Two things here are invisible to every other test in the suite:

* the scenario and study editors are one program in two files — the dirty flag,
  the unload guard, the compose POST and the 409-conflict save live once in
  ``composer.js``, and a page that forgets to load it (or a module that grows a
  private copy back) is a silent regression;
* a page's ``#studio-page-data`` island is a serialization budget. Every key put
  there is shipped on every render whether or not any JS reads it, and the
  study page's island once inlined the FULL API projection of every run in the
  workspace (``llm_usage`` and ``health`` dicts included) to power one picker
  that reads three fields.

``tests/studio/test_studio.py`` already proves that every ``<script src>`` in a
shipped template resolves to a served asset; this file does not repeat that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.studio.app import create_app

# The shared editor flow, by the name each page module calls it by.
COMPOSER_EXPORTS = ("composerDirtyState", "composerButton", "composerPost", "composerSave")


def _island(html: str) -> dict:
    """Parse the page's JSON data island."""
    match = re.search(
        r'<script type="application/json" id="studio-page-data">(.*?)</script>', html, re.DOTALL
    )
    assert match, "the page no longer carries a #studio-page-data island"
    return json.loads(match.group(1))


def _make_run(root: Path, name: str = "demo/run-1") -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "seed": 7,
                "num_agents": 1,
                "num_steps": 1,
                "llm_usage": {"totals": {"total_tokens": 12}},
                "health": {"degraded": False},
            }
        )
    )
    return run


def _make_study(workspace: Path, study_id: str = "exp1") -> Path:
    study_dir = workspace / "experiments" / "studies" / study_id
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "study": {
                    "name": "Exp",
                    "id": study_id,
                    "scenarios": ["world_a"],
                    "run_defaults": {"seed_start": 1, "seed_repeats": 1, "overrides": {}},
                },
                "hypotheses": {"h1": {"statement": "s", "conditions": {"c": {"overrides": {}}}}},
            },
            sort_keys=False,
        )
    )
    return study_dir


def test_the_shared_composer_module_is_served_and_owns_the_editor_flow(tmp_path):
    """One copy of the flow both editors share, reachable as an asset."""
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    composer = client.get("/assets/composer.js")
    assert composer.is_success
    for export in COMPOSER_EXPORTS:
        assert f"window.{export}" in composer.text, export

    # Neither page module may grow a private copy back: they call the shared
    # helpers and declare none of them.
    for name in ("scenario.js", "study.js"):
        module = client.get(f"/assets/{name}").text
        for export in COMPOSER_EXPORTS:
            assert f"window.{export}" not in module, f"{name} redeclares {export}"
        assert "composerSave(" in module, name
        assert "beforeunload" not in module, f"{name} keeps its own unload guard"
        assert "showSaveConflict(" not in module, f"{name} keeps its own conflict flow"


def test_both_composer_pages_load_the_shared_module_before_their_own(tmp_path):
    """composer.js defines what scenario.js/study.js call at parse time."""
    workspace = tmp_path / "workspace"
    _make_study(workspace)
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=workspace)
    )

    for url, module in (
        ("/scenarios/new?name=audit_case", "scenario.js"),
        ("/studies/exp1?tab=definition", "study.js"),
    ):
        page = client.get(url)
        assert page.is_success, url
        shared = page.text.index('<script src="/assets/composer.js">')
        assert shared < page.text.index(f'<script src="/assets/{module}">'), url


def test_panel_refresh_keeps_a_409_as_an_awaiting_note_not_an_error(tmp_path):
    """A Watch placeholder must survive a refresh that is merely premature.

    The Watch tab renders "Awaiting <stream>" for a panel skipped only because
    a stream is not recorded yet, and refreshes it when any of its streams
    grows. A panel needing TWO streams then 409s while the second is still
    missing — which is the placeholder's own message, not a failure, so
    ``refreshPanel`` must not replace it with an error note until reload.
    """
    outputs = tmp_path / "outputs"
    _make_run(outputs)
    client = TestClient(create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path))

    # The server's half of the contract: a not-yet-recorded stream is a 409
    # whose detail names what the panel is waiting for.
    premature = client.get("/api/runs/demo/run-1/panels/exposure_funnel")
    assert premature.status_code == 409
    assert "exposure_events" in premature.json()["detail"]

    # The client's half: 409 renders as its own note, never the error note.
    refresh = client.get("/assets/panels.js").text
    body = refresh[refresh.index("window.refreshPanel") :]
    assert "response.status === 409" in body
    assert body.index("panel-awaiting") < body.index("panel-error")
    assert "could not be refreshed" in body


def test_study_island_carries_only_the_keys_its_module_reads(tmp_path):
    """The reuse picker needs three fields per run, not the whole run record."""
    workspace = tmp_path / "workspace"
    _make_study(workspace)
    outputs = tmp_path / "outputs"
    _make_run(outputs)
    client = TestClient(create_app(outputs, state_dir=tmp_path / "state", repo_root=workspace))

    island = _island(client.get("/studies/exp1?tab=definition").text)
    assert set(island) == {
        "id",
        "definition",
        "fingerprint",
        "runChoices",
        "boardStream",
        "paletteCommands",
    }
    assert island["runChoices"], "the workspace run is missing from the reuse picker"
    for run in island["runChoices"]:
        # addExistingRun() reads exactly these; llm_usage/health/num_agents/… are
        # pure page weight here and the API still serves them at /api/runs.
        assert set(run) == {"path", "scenario", "seed"}


def test_scenario_island_carries_only_the_keys_its_module_reads(tmp_path):
    workspace = tmp_path / "workspace"
    client = TestClient(
        create_app(tmp_path / "outputs", state_dir=tmp_path / "state", repo_root=workspace)
    )

    island = _island(client.get("/scenarios/new?name=audit_case").text)
    assert set(island) == {"name", "source", "fingerprints", "values", "paletteCommands"}


def test_the_viewer_startup_poll_lives_once_in_the_shell(tmp_path):
    """The run page and the explorer poll the same way, through one helper."""
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    assert "window.awaitViewerUrl" in client.get("/assets/studio.js").text
    for name in ("runs.js", "explore.js"):
        module = client.get(f"/assets/{name}").text
        assert "awaitViewerUrl(" in module, name
        assert 'state === "ready"' not in module, f"{name} kept its own startup poll"
