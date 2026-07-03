"""Shared pytest fixtures for the silisocs test suite.

The suite historically hand-rolled all scaffolding per file; this is the home for
genuinely shared, non-divergent fixtures.

Deliberate non-goal: the per-test ``LanguageModel`` doubles are NOT consolidated
here. They are purpose-built recording/routing stubs (e.g. ``_RoutingModel`` in
``test_native_runtime_interfaces``) with test-specific return behavior, not one
reusable model — the generic no-op / scripted cases are already served by
``NoLanguageModel`` / ``ScriptedLanguageModel``. Forcing them together would add
indirection without cutting real duplication.
"""

from __future__ import annotations

import pytest

from silisocs.runtime.checkpointing import replay_mappers
from silisocs.runtime.language_models import NoLanguageModel


@pytest.fixture(autouse=True)
def _isolate_replay_mappers():
    """Snapshot and restore the module-global replay-mapper registry around each test.

    ``register_replay_mapper`` mutates a process-global dict, so a test that
    registers a custom ``backend_type`` would otherwise leak it into later tests.
    Restoring the snapshot can only remove leaked entries — it never changes a
    correct test's behavior — so this is safe to apply suite-wide.
    """
    snapshot = dict(replay_mappers._REPLAY_MAPPERS)
    try:
        yield
    finally:
        replay_mappers._REPLAY_MAPPERS.clear()
        replay_mappers._REPLAY_MAPPERS.update(snapshot)


@pytest.fixture
def no_language_model() -> NoLanguageModel:
    """Return a shared no-op ``LanguageModel`` for tests that only need a non-calling model."""
    return NoLanguageModel()
