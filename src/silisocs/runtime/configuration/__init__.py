"""Runtime configuration validation and projection."""

from silisocs.runtime.configuration.external import (
    inject_external_config_path,
    merge_external_group_overrides,
    register_search_path_plugin,
)
from silisocs.runtime.configuration.legacy import (
    reject_legacy_probe_config,
    reject_removed_runtime_keys,
)
from silisocs.runtime.configuration.projection import RuntimeProjection
from silisocs.runtime.configuration.validation import validate_scenario_config

__all__ = [
    "RuntimeProjection",
    "inject_external_config_path",
    "merge_external_group_overrides",
    "register_search_path_plugin",
    "reject_legacy_probe_config",
    "reject_removed_runtime_keys",
    "validate_scenario_config",
]
