"""JSON-safe serialization for runtime artifacts.

The implementation lives in :mod:`silisocs.runtime.io.json_safe` (the low-level IO
layer) so checkpointing and telemetry logging share one serializer. This module
re-exports it to keep the historical ``checkpointing.serialization.json_safe`` path.
"""

from silisocs.runtime.io.json_safe import json_safe

__all__ = ["json_safe"]
