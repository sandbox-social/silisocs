"""JSON-safe serialization for runtime artifacts."""

import dataclasses
import enum
from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf


def json_safe(obj: Any) -> Any:
    if isinstance(obj, (DictConfig, ListConfig)):
        return json_safe(OmegaConf.to_container(obj, resolve=True))
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return json_safe(dataclasses.asdict(obj))
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return json_safe(to_dict())
    if isinstance(obj, Mapping):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [json_safe(v) for v in obj]
    return obj
