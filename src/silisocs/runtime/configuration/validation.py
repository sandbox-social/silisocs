# silisocs/runtime/config.py
"""Configuration helpers and validators for scenarios.

This module provides validation helpers used by the experiment runner to ensure
scenario YAML files conform to expected shapes.

The validation functions raise :class:`ValueError` or :class:`FileNotFoundError`
when checks fail.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, OmegaConf

# ============================================================================
# Base Scenario Schema
# ============================================================================


@dataclass
class BaseScenarioSchema:
    """
    Base schema that all scenarios must conform to.
    Scenario-specific values are defined in scenario YAML files.
    """

    # Scenario identification
    scenario_name: str

    # Setting information
    setting: dict[str, Any] = field(
        default_factory=lambda: {
            "name": "",
            "background": [],
        }
    )

    # Setting information
    event: dict[str, Any] = field(
        default_factory=lambda: {
            "name": "",
            "context": "",
        }
    )

    # Data sources (optional, scenario-specific)
    data: dict[str, Any] = field(default_factory=dict)

    # Agent configuration
    roles: dict[str, int] = field(default_factory=dict)  # role -> count
    role_configs: dict[str, Any] = field(default_factory=dict)  # role -> config

    # Social network configuration
    social_network: dict[str, Any] = field(
        default_factory=lambda: {
            "active_rates": {},
            "fully_connected_targets": [],
            "base_followership_probability": 0.4,
        }
    )

    # Shared memories and observations
    shared_memories: list[str] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)

    # Probes configuration
    probes: dict[str, Any] = field(default_factory=dict)

    # Output configuration
    jobname_format: str = ""


# ============================================================================
# Validation Functions
# ============================================================================


def validate_scenario_structure(cfg: DictConfig) -> None:
    """
    Validate that scenario config has all required fields.

    Args:
        cfg: Scenario configuration to validate

    Raises
    ------
        ValueError: If required fields are missing
    """
    has_class_pipeline = bool(OmegaConf.select(cfg, "persona_pipeline.classes"))
    required_fields = ["scenario_name", "setting"]
    if has_class_pipeline:
        # Modern class-pipeline scenarios can define shared memories at the
        # persona defaults level and do not need legacy initial_observations.
        has_top_level_shared = OmegaConf.select(cfg, "shared_memories") is not None
        has_pipeline_shared = (
            OmegaConf.select(cfg, "persona_pipeline.defaults.shared_memories") is not None
        )
        if not (has_top_level_shared or has_pipeline_shared):
            missing_fields = ["shared_memories or persona_pipeline.defaults.shared_memories"]
            raise ValueError(
                f"Scenario configuration missing required fields: {', '.join(missing_fields)}\n"
                "Please ensure your sim.yaml/agent.yaml include all required fields."
            )
    else:
        required_fields.extend(["roles", "shared_memories", "initial_observations"])

    missing_fields = []
    for field_name in required_fields:
        if not OmegaConf.select(cfg, field_name):
            missing_fields.append(field_name)

    if missing_fields:
        raise ValueError(
            f"Scenario configuration missing required fields: {', '.join(missing_fields)}\n"
            "Please ensure your sim.yaml/agent.yaml include all required fields."
        )

    # Validate nested required fields
    if OmegaConf.select(cfg, "setting"):
        setting_required = ["name", "background"]
        for field_name in setting_required:
            if not OmegaConf.select(cfg, f"setting.{field_name}"):
                missing_fields.append(f"setting.{field_name}")

        # Validate nested required fields
    if OmegaConf.select(cfg, "event"):
        event_required = ["name", "context"]
        for field_name in event_required:
            if not OmegaConf.select(cfg, f"event.{field_name}"):
                missing_fields.append(f"event.{field_name}")

    if missing_fields:
        raise ValueError(
            f"Scenario configuration missing required nested fields: {', '.join(missing_fields)}"
        )

    print("✓ Scenario structure validation passed")


def validate_cross_references(cfg: DictConfig) -> None:
    """
    Validate that cross-references in config are consistent.

    Args:
        cfg: Full experiment configuration

    Raises
    ------
        ValueError: If cross-references are invalid
    """
    errors = []

    # Support both legacy (`cfg.sc`) and refactored scenario-level config.
    scenario_cfg = getattr(cfg, "sc", cfg)

    # Validate that probe candidate references exist
    if hasattr(scenario_cfg, "probes"):
        probes_cfg = scenario_cfg.probes

        # Get candidate names from refactored structure first.
        candidate_names: list[str] = []
        if hasattr(scenario_cfg, "candidates"):
            for _partisan_type, info in scenario_cfg.candidates.items():
                if hasattr(info, "name"):
                    candidate_names.append(str(info.name))
                elif isinstance(info, dict) and "name" in info:
                    candidate_names.append(str(info["name"]))

        # Legacy fallback: nested role_configs.candidate.candidates structure.
        if (
            not candidate_names
            and hasattr(scenario_cfg, "role_configs")
            and "candidate" in scenario_cfg.role_configs
        ):
            candidate_cfg = scenario_cfg.role_configs.candidate
            if hasattr(candidate_cfg, "candidates"):
                for _partisan_type, info in candidate_cfg.candidates.items():
                    if hasattr(info, "name"):
                        candidate_names.append(str(info.name))
                    elif isinstance(info, dict) and "name" in info:
                        candidate_names.append(str(info["name"]))

        # Validate probe references
        probes_to_validate = getattr(probes_cfg, "probes", None)

        if probes_to_validate is not None:
            for probe_num, probe_data in probes_to_validate.items():
                if hasattr(probe_data, "interaction_premise_template"):
                    premise = probe_data.interaction_premise_template
                elif hasattr(probe_data, "probe_data") and hasattr(
                    probe_data.probe_data, "interaction_premise_template"
                ):
                    premise = probe_data.probe_data.interaction_premise_template
                else:
                    continue

                # Check candidate references
                for field in ["candidate", "candidate1", "candidate2"]:
                    if hasattr(premise, field):
                        candidate = getattr(premise, field)
                        if candidate and candidate not in candidate_names:
                            errors.append(
                                f"Probe {probe_num} references unknown candidate: {candidate}"
                            )

    # Validate social network role references
    if hasattr(scenario_cfg, "social_network") and hasattr(scenario_cfg, "roles"):
        roles = set(scenario_cfg.roles.keys())
        social_network_cfg = scenario_cfg.social_network

        # Check social-network activity rate references.
        # Refactor renamed `active_rates` -> `activity_transition_rates`.
        active_rates = None
        if hasattr(social_network_cfg, "activity_transition_rates"):
            active_rates = social_network_cfg.activity_transition_rates
        elif hasattr(social_network_cfg, "active_rates"):
            active_rates = social_network_cfg.active_rates

        if active_rates:
            for role in active_rates.keys():
                if role not in roles:
                    errors.append(f"Social network activity rates reference unknown role: {role}")

        # Check fully_connected_targets references
        if hasattr(social_network_cfg, "fully_connected_targets"):
            for role in social_network_cfg.fully_connected_targets:
                if role not in roles:
                    errors.append(
                        f"Social network fully_connected_targets references unknown role: {role}"
                    )

    # Validate fixed-action set references in class pipeline.
    class_pipeline = OmegaConf.select(scenario_cfg, "persona_pipeline.classes")
    if class_pipeline:
        inline_sets = OmegaConf.select(scenario_cfg, "fixed_action_sets.inline") or {}
        file_sets = OmegaConf.select(scenario_cfg, "fixed_action_sets.file")
        available_set_names = set(inline_sets.keys()) if isinstance(inline_sets, dict) else set()

        # File-based set names are validated at build-time once file is loaded.
        # Here we only assert that refs are not empty and that inline refs exist.
        for class_name, class_cfg in class_pipeline.items():
            class_cfg = class_cfg or {}
            fixed_cfg = class_cfg.get("fixed_action") if isinstance(class_cfg, dict) else None
            if not isinstance(fixed_cfg, dict):
                continue
            if not bool(fixed_cfg.get("enabled", False)):
                continue
            set_ref = str(fixed_cfg.get("action_set_ref", "")).strip()
            if not set_ref:
                errors.append(
                    f"Class `{class_name}` has fixed_action.enabled=true but no action_set_ref"
                )
                continue
            if set_ref not in available_set_names and not file_sets:
                errors.append(
                    f"Class `{class_name}` references unknown fixed_action set `{set_ref}`"
                )

    if errors:
        raise ValueError(
            "Cross-reference validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    print("✓ Cross-reference validation passed")


def validate_data_files(cfg: DictConfig, scenario_path: Path) -> None:
    """
    Validate that referenced data files exist.

    Args:
        cfg: Scenario configuration
        scenario_path: Path to scenario directory

    Raises
    ------
        FileNotFoundError: If required files don't exist
    """
    missing_files = []
    class_pipeline = OmegaConf.select(cfg, "persona_pipeline.classes")

    def _resolve_local_path(raw_path: str) -> Path | None:
        """_resolve_local_path.

        :param str raw_path:
        :type raw_path: str

        :returns: Path | None
        :rtype: Path | None
        """
        if not raw_path:
            return None
        candidate_paths = [
            Path(raw_path),
            scenario_path / raw_path,
            scenario_path / "input" / raw_path,
            scenario_path / "input" / "personas" / raw_path,
            scenario_path / "input" / "news_data" / raw_path,
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                return candidate
        return None

    # Validate persona file
    if not class_pipeline and hasattr(cfg, "data") and hasattr(cfg.data, "persona_file"):
        persona_file = scenario_path / "input" / "personas" / cfg.data.persona_file
        if not persona_file.exists():
            missing_files.append(str(persona_file))

    # Validate news file if news agent is used
    if hasattr(cfg, "data") and hasattr(cfg.data, "use_news_agent"):
        if cfg.data.use_news_agent and cfg.data.use_news_agent != "none":
            if hasattr(cfg.data, "news_file"):
                news_file = scenario_path / "input" / "news_data" / f"{cfg.data.news_file}.json"
                if not news_file.exists():
                    missing_files.append(str(news_file))

    if class_pipeline:
        for class_name, class_cfg in class_pipeline.items():
            class_cfg = class_cfg or {}
            data_cfg = class_cfg.get("data", {})
            data_source = data_cfg.get("source")
            data_path = data_cfg.get("path") or data_cfg.get("dataset")
            if data_source == "local_json":
                resolved_data_path = _resolve_local_path(str(data_path))
                if resolved_data_path is None:
                    missing_files.append(f"class `{class_name}` data path not found: {data_path}")

            shared_memories = class_cfg.get("shared_memories")
            if isinstance(shared_memories, dict):
                shared_path = shared_memories.get("path")
                if shared_path and _resolve_local_path(str(shared_path)) is None:
                    missing_files.append(
                        f"class `{class_name}` shared memories path not found: {shared_path}"
                    )

    fixed_action_file = OmegaConf.select(cfg, "fixed_action_sets.file")
    if fixed_action_file:
        resolved = _resolve_local_path(str(fixed_action_file))
        if resolved is None:
            missing_files.append(f"fixed_action_sets.file path not found: {fixed_action_file}")

    if missing_files:
        raise FileNotFoundError(
            "Data file validation failed. Missing files:\n"
            + "\n".join(f"  - {f}" for f in missing_files)
        )

    print("✓ Data file validation passed")


def validate_scenario_config(cfg: DictConfig, scenario_path: Path | None = None) -> None:
    """
    Run all validation checks on scenario configuration.

    Args:
        cfg: Configuration to validate
        scenario_path: Path to scenario directory (for data file validation)

    Raises
    ------
        Various exceptions if validation fails
    """
    print("\n" + "=" * 60)
    print("VALIDATING SCENARIO CONFIGURATION")
    print("=" * 60)

    # 1. Validate structure
    validate_scenario_structure(cfg)

    # 2. Validate cross-references
    validate_cross_references(cfg)

    # 3. Validate data files (if path provided)
    if scenario_path:
        validate_data_files(cfg, scenario_path)

    print("=" * 60)
    print("✅ ALL VALIDATION CHECKS PASSED")
    print("=" * 60 + "\n")
