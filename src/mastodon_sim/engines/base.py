"""Base classes for environment engines."""

from __future__ import annotations


class BaseEnvironmentEngine:
    """Marker base class for environment engines.

    Concrete runtime engines live in `runtime.py`:

    - `BaseRuntimeEngine` for simple one-GM execution.
    - `FlowRuntimeEngine` for flow/multi-GM orchestration.

    This marker remains a stable extension location for future engine families.
    """
