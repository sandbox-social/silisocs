"""Coverage for probe-metrics accumulation (no matplotlib/analysis extra needed).

``_build_probe_metrics_with_context`` and its extracted ``_accumulate_probe_rows``
pass are the core of the probe-metrics evaluator, but the only existing test
(``test_default_evaluators``) is gated behind the matplotlib ``analysis`` extra and
so is skipped in the default suite. These exercise the pure accumulation path
directly (matplotlib is only imported lazily for plotting).
"""

from __future__ import annotations

import pathlib
import tempfile

from silisocs.evaluations.default_evaluators import (
    _accumulate_probe_rows,
    _build_probe_metrics_with_context,
)

_EVENTS = [
    {
        "event_type": "probe",
        "label": "mood",
        "episode": 0,
        "source_user": "alice",
        "data": {"probe_return": "0.8", "probe_mode": "binary"},
    },
    {
        "event_type": "probe",
        "label": "mood",
        "episode": 1,
        "source_user": "bob",
        "data": {"probe_return": "", "probe_mode": "binary"},  # missing response
    },
    {
        "event_type": "probe",
        "label": "note",
        "episode": 0,
        "source_user": "alice",
        "data": {"probe_return": "hello world foo", "probe_mode": "free"},
    },
    {"event_type": "action", "label": "x", "episode": 0, "source_user": "alice", "data": {}},
]


def _run_dir():
    return pathlib.Path(tempfile.mkdtemp())


def test_accumulate_probe_rows_counts_by_dimension() -> None:
    probe_rows = [e for e in _EVENTS if e["event_type"] == "probe"]
    acc = _accumulate_probe_rows(probe_rows, type_map={}, probe_type_filter=None)
    assert acc.kept_rows == 3
    assert acc.dropped_rows == 0
    assert dict(acc.per_episode_counts) == {0: 2, 1: 1}
    assert dict(acc.per_agent_counts) == {"alice": 2, "bob": 1}
    assert set(acc.per_label_counts) == {"mood", "note"}
    # 'mood' has one present + one missing response.
    assert acc.per_label_counts["mood"]["total_events"] == 2
    assert acc.per_label_counts["mood"]["responses_present"] == 1
    assert acc.per_label_counts["mood"]["responses_missing"] == 1


def test_accumulate_probe_rows_type_filter_drops_non_matching() -> None:
    probe_rows = [e for e in _EVENTS if e["event_type"] == "probe"]
    # Force every mood row to a known type via the type_map, then filter it out.
    type_map = {"mood": "BinaryProbe", "note": "FreeTextProbe"}
    acc = _accumulate_probe_rows(probe_rows, type_map=type_map, probe_type_filter="FreeTextProbe")
    assert acc.kept_rows == 1  # only the 'note' row survives
    assert acc.dropped_rows == 2
    assert set(acc.per_label_counts) == {"note"}


def test_build_probe_metrics_with_context_assembles_full_payload() -> None:
    out = _build_probe_metrics_with_context(_EVENTS, _run_dir())
    assert out["summary_type"] == "probe_metrics"
    assert out["total_events"] == 4
    assert out["total_probe_events"] == 3
    assert out["filtered_probe_events"] == 3
    assert out["filtered_out_probe_events"] == 0
    assert out["per_episode"] == {"0": 2, "1": 1}
    assert out["per_agent"] == {"alice": 2, "bob": 1}
    assert out["per_agent_per_episode"]["alice"] == {"0": 2}
    assert set(out["per_label"]) == {"mood", "note"}
