"""Checkpoint save policy."""

from typing import Any

CHECKPOINT_SCHEMA_VERSION = 4


def should_save_checkpoint(step: int, checkpoint_cfg: Any | None = None) -> bool:
    """Return whether runtime config requests a checkpoint at this step."""
    if checkpoint_cfg is None:
        return False
    explicit_steps = _parse_int_set(getattr(checkpoint_cfg, "explicit_steps", []) or [])
    every_n = _parse_positive_int(getattr(checkpoint_cfg, "every_n_steps", None))
    if every_n is None and not explicit_steps:
        return False
    return step in explicit_steps or (every_n is not None and step % every_n == 0)


def _parse_int_set(values: Any) -> set[int]:
    parsed: set[int] = set()
    for value in values:
        try:
            parsed.add(int(value))
        except (TypeError, ValueError):
            continue
    return parsed


def _parse_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
