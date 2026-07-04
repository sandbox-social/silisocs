"""Base-config defaults surfaced to the dashboard from the packaged ``conf/`` tree.

The dashboard used to re-type base-config values (``jobname_format``, the
persona-pipeline ``defaults.params``, ``initial_observations``) as Python literals,
which silently drifts when the YAML changes. Instead, read them once from the packaged
base config so the dashboard and ``conf/`` cannot disagree. Values are loaded raw:
templates like ``${event.context}`` are kept unresolved because the dashboard emits
them verbatim into the scenario it writes.
"""

from __future__ import annotations

import functools
from importlib import resources
from typing import Any

import yaml


@functools.cache
def _load_conf(group: str, name: str) -> dict[str, Any]:
    """Load a packaged ``conf/<group>/<name>.yaml`` as a raw dict (no interpolation)."""
    ref = resources.files("silisocs").joinpath("conf", group, f"{name}.yaml")
    data = yaml.safe_load(ref.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def default_jobname_format() -> str:
    """The base ``jobname_format`` template from ``conf/world/default.yaml``."""
    return str(_load_conf("world", "default").get("jobname_format", ""))


def default_persona_defaults() -> dict[str, Any]:
    """The base ``persona_pipeline.defaults.params`` from ``conf/agents/default.yaml``."""
    agents = _load_conf("agents", "default")
    defaults = (agents.get("persona_pipeline") or {}).get("defaults") or {}
    return dict(defaults.get("params") or {})


def default_initial_observations() -> list[Any]:
    """The base ``initial_observations`` list from ``conf/agents/default.yaml``."""
    return list(_load_conf("agents", "default").get("initial_observations") or [])
