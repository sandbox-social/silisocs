"""Normalized analysis input contracts for custom Python panels."""

from __future__ import annotations

import builtins
from types import SimpleNamespace
from typing import Any

from silisocs.analysis.inputs import event_frame, probe_frame
from silisocs.evaluations.vocabulary import EventSemantics, register_event_semantics


def _artifact() -> SimpleNamespace:
    register_event_semantics(
        "custom_world",
        EventSemantics(
            roles={
                "world.move": frozenset({"move"}),
                "interaction.directed": frozenset({"signal"}),
            },
            fields={
                "world.location": ("destination",),
                "network.target_actor": ("target",),
            },
            label_tags={
                "move": ("world.move",),
                "signal": ("interaction.directed", "world.signal"),
            },
        ),
    )
    return SimpleNamespace(
        actions=[
            {
                "label": "move",
                "source_user": "Ada",
                "episode": 1,
                "backend_type": "custom_world",
                "gm_name": "world",
                "data": {"destination": "lab"},
            },
            {
                "label": "signal",
                "source_user": "Ben",
                "episode": 2,
                "backend_type": "custom_world",
                "gm_name": "world",
                "data": {"target": "Ada"},
            },
        ],
        probes=[],
        game_masters=[{"name": "world", "backend_type": "custom_world"}],
    )


def test_event_frame_normalizes_tags_fields_and_reuses_cache() -> None:
    artifact = _artifact()
    frame = event_frame(artifact)

    assert event_frame(artifact) is frame
    assert len(frame) == 2
    assert frame[0].actor == "Ada"
    assert frame[0].field("world.location") == "lab"
    assert frame[1].tags == ("interaction.directed", "world.signal")
    assert frame.values("network.target_actor") == [None, "Ada"]


def test_event_frame_filters_and_groups() -> None:
    frame = event_frame(_artifact())

    assert len(frame.where(tag="world.move")) == 1
    assert len(frame.where(actor="Ben", episode=2)) == 1
    assert len(frame.where(label=None, actor=None, episode=None, tag=None)) == 2
    assert len(frame.where(pred=lambda event: event.field("world.location") == "lab")) == 1
    assert list(frame.by_episode()) == [1, 2]
    assert frame.count_by("actor") == {"Ada": 1, "Ben": 1}
    assert list(frame.group_by("backend_type")) == ["custom_world"]


def test_event_frame_does_not_guess_semantics_for_untyped_rows() -> None:
    artifact = SimpleNamespace(
        actions=[{"label": "move", "source_user": "Ada", "episode": 1, "data": {}}],
        probes=[],
        game_masters=[{"name": "world", "backend_type": "custom_world"}],
    )

    (event,) = event_frame(artifact)
    assert event.backend_type == ""
    assert event.tags == ()


def test_probe_frame_reads_the_writer_shape() -> None:
    artifact = SimpleNamespace(
        actions=[],
        game_masters=[],
        probes=[
            {
                "label": "trust",
                "source_user": "Ada",
                "episode": 1,
                "anchor": "post_step",
                "data": {"probe_return": 0.7},
            },
            {
                "label": "mood",
                "source_user": "Ben",
                "episode": 2,
                "anchor": "pre_step",
                "data": {"probe_return": "calm"},
            },
        ],
    )

    events = probe_frame(artifact)
    assert [(event.probe, event.agent, event.episode, event.value) for event in events] == [
        ("trust", "Ada", 1, 0.7),
        ("mood", "Ben", 2, "calm"),
    ]
    assert [event.anchor for event in events] == ["post_step", "pre_step"]
    assert probe_frame(artifact) is events


def test_to_pandas_import_is_lazy(monkeypatch: Any) -> None:
    imported: list[str] = []
    original = builtins.__import__

    def tracking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        imported.append(name)
        return original(name, *args, **kwargs)

    frame = event_frame(_artifact())
    monkeypatch.setattr(builtins, "__import__", tracking_import)
    assert "pandas" not in imported
    result = frame.to_pandas()

    assert "pandas" in imported
    assert list(result["actor"]) == ["Ada", "Ben"]
