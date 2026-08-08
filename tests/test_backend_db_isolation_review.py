"""Regression tests for single-source-of-truth backend db path resolution.

The multi-GM collision guard (``_isolate_backend_paths``) and the actual app
instantiation (``create_backend_app``) must derive the backend db path from the
same helper (``resolve_backend_db_path``) so the guard always checks the exact
path that will be opened — including any configured ``params.db_path`` override —
and never relies on a duplicated filename literal.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from silisocs.environments.backends import factory as backend_factory
from silisocs.environments.backends.base import BackendApp
from silisocs.environments.backends.factory import (
    create_backend_app,
    default_backend_db_filename,
    resolve_backend_db_path,
)
from silisocs.runtime.construction.game_masters import _isolate_backend_paths


class _RecordingApp(BackendApp):
    """Minimal backend that records the db_path it was instantiated with."""

    def __init__(
        self,
        *,
        db_path: str = "default.db",
        action_logger: Any = None,
        app_description: str = "",
        perform_operations: bool = False,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.action_logger = action_logger
        self.app_description = app_description
        self.perform_operations = perform_operations

    def name(self) -> str:
        return "recording_app"

    def description(self) -> str:
        return self.app_description


@pytest.fixture
def register_recording_backend(monkeypatch):
    """Register the recording app under a throwaway backend type."""
    backends = dict(backend_factory._BUILTIN_BACKENDS)
    backends["recording"] = f"{_RecordingApp.__module__}.{_RecordingApp.__qualname__}"
    monkeypatch.setattr(backend_factory, "_BUILTIN_BACKENDS", backends)
    return "recording"


# --------------------------------------------------------------------------- #
# Guard: distinct vs colliding resolved paths
# --------------------------------------------------------------------------- #


def test_distinct_db_paths_do_not_raise_false_collision():
    """Same backend_type GMs that resolve to different db paths must not collide."""
    entries = [
        ("alpha", {"backend_type": "twitter_like", "output_dir": "/tmp/run"}),
        ("beta", {"backend_type": "twitter_like", "output_dir": "/tmp/run"}),
    ]
    # More than one GM => per-GM isolation nests output_dir; no collision.
    _isolate_backend_paths(entries)
    roots = [cfg["output_dir"] for _, cfg in entries]
    assert roots[0] != roots[1]


def test_same_db_path_raises_collision():
    """Two GMs that resolve to the SAME db path must raise the collision error."""
    entries = [
        ("dup", {"backend_type": "twitter_like", "output_dir": "/tmp/a"}),
        ("dup", {"backend_type": "twitter_like", "output_dir": "/tmp/a"}),
    ]
    with pytest.raises(ValueError, match="same backend database path"):
        _isolate_backend_paths(entries)


def test_custom_db_path_override_distinct_does_not_collide():
    """Distinct configured params.db_path values avoid a false collision.

    Both GMs share backend_type and (after isolation) the same root would yield
    the default ``<type>.db``, but distinct ``params.db_path`` overrides resolve
    to different paths, so the guard must not raise.
    """
    entries = [
        (
            "alpha",
            {
                "backend_type": "twitter_like",
                "output_dir": "/tmp/run",
                "params": {"db_path": "alpha_store.db"},
            },
        ),
        (
            "beta",
            {
                "backend_type": "twitter_like",
                "output_dir": "/tmp/run",
                "params": {"db_path": "beta_store.db"},
            },
        ),
    ]
    _isolate_backend_paths(entries)  # must not raise


def test_custom_db_path_override_same_raises_collision():
    """Identical absolute params.db_path overrides collide even across GM names."""
    shared = "/tmp/shared/explicit.db"
    entries = [
        (
            "alpha",
            {
                "backend_type": "twitter_like",
                "output_dir": "/tmp/run",
                "params": {"db_path": shared},
            },
        ),
        (
            "beta",
            {
                "backend_type": "twitter_like",
                "output_dir": "/tmp/run",
                "params": {"db_path": shared},
            },
        ),
    ]
    with pytest.raises(ValueError, match="same backend database path"):
        _isolate_backend_paths(entries)


# --------------------------------------------------------------------------- #
# Single source of truth: guard key == path create_backend_app actually uses
# --------------------------------------------------------------------------- #


def test_guard_key_matches_create_backend_app_default(register_recording_backend, tmp_path):
    """The guard's resolved key equals the db_path the factory actually opens.

    Default convention: no params.db_path => ``<output_dir>/<type>.db``.
    """
    backend_type = register_recording_backend
    output_dir = str(tmp_path)

    guard_path = resolve_backend_db_path(output_dir, backend_type)

    app = create_backend_app(
        backend_type=backend_type,
        db_path=os.path.join(output_dir, default_backend_db_filename(backend_type)),
        params={},
    )
    assert isinstance(app, _RecordingApp)
    assert app.db_path == guard_path
    assert guard_path == os.path.join(output_dir, f"{backend_type}.db")


def test_guard_key_matches_create_backend_app_override(register_recording_backend, tmp_path):
    """With a configured params.db_path the guard key still equals the real path."""
    backend_type = register_recording_backend
    output_dir = str(tmp_path)
    override = "custom_store.db"

    guard_path = resolve_backend_db_path(output_dir, backend_type, db_path=override)

    app = create_backend_app(
        backend_type=backend_type,
        db_path=os.path.join(output_dir, default_backend_db_filename(backend_type)),
        params={"db_path": override},
    )
    assert isinstance(app, _RecordingApp)
    assert app.db_path == guard_path
    # A relative override is nested under the run output dir, not left un-rooted.
    assert guard_path == os.path.join(output_dir, override)


def test_guard_key_matches_create_backend_app_absolute_override(
    register_recording_backend, tmp_path
):
    """An absolute params.db_path override is honored verbatim by both sides."""
    backend_type = register_recording_backend
    output_dir = str(tmp_path)
    override = str(tmp_path / "abs" / "explicit.db")

    guard_path = resolve_backend_db_path(output_dir, backend_type, db_path=override)

    app = create_backend_app(
        backend_type=backend_type,
        db_path=os.path.join(output_dir, default_backend_db_filename(backend_type)),
        params={"db_path": override},
    )
    assert isinstance(app, _RecordingApp)
    assert app.db_path == guard_path == override


def test_no_duplicated_literal_default_filename():
    """Both call sites derive the default filename from the shared helper."""
    assert default_backend_db_filename("twitter_like") == "twitter_like.db"
    # Guard default key is composed from the same helper, not an inline literal.
    assert resolve_backend_db_path("/tmp/run", "twitter_like") == os.path.join(
        "/tmp/run", default_backend_db_filename("twitter_like")
    )
