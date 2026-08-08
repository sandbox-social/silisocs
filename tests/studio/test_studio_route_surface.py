"""The HTTP surface Studio serves, pinned.

Studio's routers are sliced by domain, and which module a handler lives in is an
internal detail — but WHICH paths exist, and in WHICH order they are matched, is
the contract clients and bookmarks depend on. Moving a handler between routers
must therefore change neither, which is what these tests make provable.
"""
# ruff: noqa: D103

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from silisocs.studio.app import create_app

# Every (method, path) a built Studio app serves, sorted. Adding a route is a
# deliberate change to this list; a refactor that moves handlers around is not.
ROUTE_SURFACE = [
    ("DELETE", "/api/repositories/{source_id}"),
    ("GET", "/"),
    ("GET", "/api/docs"),
    ("GET", "/api/explore/compare"),
    ("GET", "/api/explore/runs/{run_id:path}/capabilities"),
    ("GET", "/api/explore/runs/{run_id:path}/entities"),
    ("GET", "/api/explore/runs/{run_id:path}/events"),
    ("GET", "/api/explore/runs/{run_id:path}/evidence"),
    ("GET", "/api/explore/runs/{run_id:path}/relationships"),
    ("GET", "/api/explore/runs/{run_id:path}/series"),
    ("GET", "/api/explore/runs/{run_id:path}/story"),
    ("GET", "/api/explore/studies/{study_id}/capabilities"),
    ("GET", "/api/forms"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/jobs/{job_id}/stream"),
    ("GET", "/api/panels"),
    ("GET", "/api/ready"),
    ("GET", "/api/repositories"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/{run_id:path}"),
    ("GET", "/api/runs/{run_id:path}/events/{stream}"),
    ("GET", "/api/runs/{run_id:path}/panels/{panel_name}"),
    ("GET", "/api/runs/{run_id:path}/report"),
    ("GET", "/api/runs/{run_id:path}/views/{view_name}"),
    ("GET", "/api/scenarios"),
    ("GET", "/api/scenarios/{scenario_name}"),
    ("GET", "/api/scenarios/{scenario_name}/runs"),
    ("GET", "/api/studies"),
    ("GET", "/api/studies/{study_id}"),
    ("GET", "/api/studies/{study_id}/compare"),
    ("GET", "/api/studies/{study_id}/notebook"),
    ("GET", "/api/studies/{study_id}/panels/{panel_name}"),
    ("GET", "/api/viewers/{run_id:path}/{backend_type}/status"),
    ("GET", "/api/views"),
    ("GET", "/assets/{name}"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/explore/compare"),
    ("GET", "/explore/run/{run_id:path}"),
    ("GET", "/explore/study/{study_id}"),
    ("GET", "/live"),
    ("GET", "/openapi.json"),
    ("GET", "/runs"),
    ("GET", "/runs/{run_id:path}"),
    ("GET", "/scenarios"),
    ("GET", "/scenarios/new"),
    ("GET", "/scenarios/{scenario_name}"),
    ("GET", "/settings"),
    ("GET", "/studies"),
    ("GET", "/studies/new"),
    ("GET", "/studies/{study_id}"),
    ("HEAD", "/api/docs"),
    ("HEAD", "/docs/oauth2-redirect"),
    ("HEAD", "/openapi.json"),
    ("MOUNT", "/viewers"),
    ("PATCH", "/api/repositories/{source_id}"),
    ("POST", "/api/compose"),
    ("POST", "/api/form-choices"),
    ("POST", "/api/form-preview"),
    ("POST", "/api/jobs/{job_id}/control"),
    ("POST", "/api/jobs/{job_id}/stop"),
    ("POST", "/api/launch"),
    ("POST", "/api/preflight"),
    ("POST", "/api/repositories"),
    ("POST", "/api/repositories/refresh"),
    ("POST", "/api/scenarios"),
    ("POST", "/api/studies/{study_id}"),
    ("POST", "/api/studies/{study_id}/compose"),
    ("POST", "/api/studies/{study_id}/launch"),
    ("POST", "/api/viewers/{run_id:path}/{backend_type}"),
]


def _routes(app) -> list[tuple[str, str]]:
    """Every (method, path) the app serves; a mount reports the pseudo-method MOUNT."""
    return [
        (method, route.path)
        for route in app.routes
        for method in sorted(getattr(route, "methods", None) or ["MOUNT"])
    ]


def test_route_surface_is_exactly_what_studio_publishes(tmp_path):
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    assert sorted(set(_routes(app))) == ROUTE_SURFACE


def test_no_route_is_registered_twice(tmp_path):
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    routes = _routes(app)
    assert len(routes) == len(set(routes))


@pytest.mark.parametrize(
    ("greedy", "shadowed"),
    [
        # A `{run_id:path}` param matches slashes, so each catch-all must be
        # registered after every longer route that shares its prefix — this is
        # the precedence that makes /api/runs/x/views/overview reachable at all.
        (("GET", "/api/runs/{run_id:path}"), ("GET", "/api/runs/{run_id:path}/views/{view_name}")),
        (
            ("GET", "/api/runs/{run_id:path}"),
            ("GET", "/api/runs/{run_id:path}/panels/{panel_name}"),
        ),
        (("GET", "/api/runs/{run_id:path}"), ("GET", "/api/runs/{run_id:path}/report")),
        (("GET", "/api/runs/{run_id:path}"), ("GET", "/api/runs/{run_id:path}/events/{stream}")),
        (("GET", "/runs/{run_id:path}"), ("GET", "/runs")),
        # The /viewers mount matches by prefix, so it must come after everything.
        (("MOUNT", "/viewers"), ("POST", "/api/viewers/{run_id:path}/{backend_type}")),
    ],
)
def test_catch_all_routes_stay_behind_the_routes_they_would_shadow(tmp_path, greedy, shadowed):
    app = create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path)
    routes = _routes(app)
    assert routes.index(shadowed) < routes.index(greedy)
