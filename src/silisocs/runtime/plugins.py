"""Plugin loading: make user registrations reachable from YAML-only runs.

Every registry in the framework (``register_llm_provider``,
``register_replay_mapper``, ``register_event_semantics``,
``register_health_counter``, ``register_panel``, ...) requires the user's
module to be imported before the run starts. This module is the mechanism:

- A top-level ``plugins:`` config list names modules to import
  (``plugins: [mypkg.silisocs_setup]``). These are load-bearing config — a
  module that fails to import fails the run with the reason.
- Installed packages may declare a ``silisocs.plugins`` entry point whose
  module is imported automatically on every run. These are ambient — one
  broken installed package must not take down unrelated runs, so failures
  warn and skip (the same policy as ``silisocs.panels``).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import warnings
from collections.abc import Sequence

ENTRY_POINT_GROUP = "silisocs.plugins"


def load_plugins(modules: Sequence[str] | None) -> None:
    """Import plugin modules so their registrations run before validation."""
    for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            entry_point.load()
        except Exception as exc:
            warnings.warn(
                f"Skipping {ENTRY_POINT_GROUP} entry point {entry_point.name!r}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
    for name in modules or ():
        module = str(name).strip()
        if not module:
            continue
        try:
            importlib.import_module(module)
        except Exception as exc:
            raise ValueError(
                f"plugins: could not import {module!r} ({exc}). Each entry must "
                "be an importable module whose import registers your providers/"
                "mappers/panels (see docs/configuration.md -> Plugins)."
            ) from exc
