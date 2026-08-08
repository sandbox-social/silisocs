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

import os

# Cap implicit BLAS/OpenMP pools BEFORE anything imports numpy: on a many-core
# host each Python process otherwise spawns one BLAS thread per core, and the
# subprocess-driven e2e tests multiply that past per-user task limits (cgroup
# TasksMax / RLIMIT_NPROC), failing random thread-heavy tests with "can't start
# new thread". Nothing in the suite needs BLAS parallelism; `setdefault` keeps
# an explicit environment override in charge. Inherited by runner subprocesses,
# which copy `os.environ`.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "2")

import pytest  # noqa: E402

from silisocs.environments.backends import event_semantics  # noqa: E402
from silisocs.environments.backends import factory as backend_factory  # noqa: E402
from silisocs.runtime.checkpointing import replay_mappers  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_run_discovery():
    """Drop Studio's memoized run discovery around each test.

    Discovery is memoized for a few seconds so clicking between list pages does
    not re-walk the output tree. Tests write runs and read them back instantly —
    far inside that window — so they get a clean catalog each time.
    """
    from silisocs.studio.catalog import clear_discovery_cache

    clear_discovery_cache()
    try:
        yield
    finally:
        clear_discovery_cache()


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


@pytest.fixture(autouse=True)
def _isolate_backend_capability_registries():
    """Restore the capability registries a built backend seeds, around each test.

    Building a backend records its class so registries keyed by a custom
    ``backend_type`` can reach class-level declarations; ``register_*`` writes to
    the same process-global maps. Both would otherwise leak a test's throwaway
    backend type into later tests.
    """
    snapshots = [
        (backend_factory._RUNTIME_BACKEND_CLASSES, dict(backend_factory._RUNTIME_BACKEND_CLASSES)),
        (event_semantics._EVENT_SEMANTICS, dict(event_semantics._EVENT_SEMANTICS)),
        (
            event_semantics._RESOLVED_EVENT_SEMANTICS,
            dict(event_semantics._RESOLVED_EVENT_SEMANTICS),
        ),
    ]
    try:
        yield
    finally:
        for registry, snapshot in snapshots:
            registry.clear()
            registry.update(snapshot)
