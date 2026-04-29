"""Hydra SearchPathPlugin that gives external scenario conf dirs highest priority.

Registered via the ``hydra_plugins`` entry point so Hydra discovers it
automatically. Reads ``MASTODON_SIM_EXTERNAL_CONFIG_DIRS`` (set by
``_inject_external_config_path`` before ``@hydra.main`` runs) and prepends
each dir to the config search path *before* the primary config dir.

Priority order (highest → lowest):
  overlay dirs (last prepended) > primary scenario dir > package conf dir
"""

from __future__ import annotations

import os

from hydra.core.plugins import Plugins
from hydra.plugins.search_path_plugin import SearchPathPlugin


class ScenarioSearchPathPlugin(SearchPathPlugin):
    def manipulate_search_path(self, search_path: Plugins) -> None:  # type: ignore[override]
        paths_csv = os.environ.get("MASTODON_SIM_EXTERNAL_CONFIG_DIRS", "").strip()
        if not paths_csv:
            return
        # Prepend in list order so later entries (overlays) end up with higher priority.
        for raw_dir in [p for p in paths_csv.split(":") if p]:
            search_path.prepend("file", raw_dir)
