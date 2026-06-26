"""Tests for the unified ``json_safe`` serializer shared by IO + checkpointing.

It previously existed as two near-duplicate functions that diverged on the
fallback for an unserializable object (checkpoint returned it as-is, which could
crash ``json.dump``; telemetry stringified it). They are now one implementation
with a ``str()`` fallback so neither path can crash.
"""

from __future__ import annotations

import json

from omegaconf import OmegaConf

from silisocs.runtime.io.json_safe import json_safe


def test_serializers_are_a_single_implementation() -> None:
    from silisocs.runtime.checkpointing import json_safe as via_pkg
    from silisocs.runtime.checkpointing.serialization import json_safe as via_module

    assert json_safe is via_module is via_pkg


def test_unknown_object_falls_back_to_str_and_never_crashes_json_dump() -> None:
    class Weird:
        def __repr__(self) -> str:
            return "WEIRD"

    safe = json_safe({"x": Weird(), "n": [Weird(), 1]})
    # The whole point: a bare json.dump (no custom encoder) must not raise.
    dumped = json.dumps(safe)
    assert json.loads(dumped) == {"x": "WEIRD", "n": ["WEIRD", 1]}


def test_omegaconf_interpolations_are_resolved() -> None:
    cfg = OmegaConf.create({"a": 1, "b": "${a}", "nested": {"c": "${a}"}})
    assert json_safe(cfg) == {"a": 1, "b": 1, "nested": {"c": 1}}


def test_primitives_enums_and_containers_round_trip() -> None:
    import enum

    class Color(enum.Enum):
        RED = "red"

    out = json_safe({"k": (1, 2), "c": Color.RED, "s": "x", "z": None})
    assert out == {"k": [1, 2], "c": "red", "s": "x", "z": None}
    json.dumps(out)  # serializable
