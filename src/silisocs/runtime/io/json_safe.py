"""JSON-safe serialization shared by runtime IO logging and checkpointing.

Converts arbitrary runtime objects into structures that ``json.dump``/``json.dumps``
can serialize. It lives in the low-level ``io`` package so both telemetry logging
(``io.jsonl``) and checkpoint serialization (``checkpointing.serialization``) reuse a
single implementation without a layering inversion.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf


def json_safe(obj: Any) -> Any:
    """Return a JSON-serializable view of ``obj``.

    OmegaConf containers are resolved to plain Python (interpolations included);
    enums become their value; dataclasses and objects exposing ``to_dict()`` are
    recursed; mappings and sequences are recursed element-wise. Anything else falls
    back to ``str(obj)`` so serialization never raises on an unexpected type (and a
    bare ``json.dump`` without a custom encoder cannot crash on checkpoint data).
    """
    if isinstance(obj, (DictConfig, ListConfig)):
        return json_safe(OmegaConf.to_container(obj, resolve=True))
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    # An explicit ``to_dict()`` hook wins over generic ``dataclasses.asdict`` so
    # objects that expose derived fields (e.g. a post's score) serialize as intended.
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return json_safe(to_dict())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return json_safe(dataclasses.asdict(obj))
    if isinstance(obj, Mapping):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [json_safe(v) for v in obj]
    return str(obj)
