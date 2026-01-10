# src/sim/config_utils/scenario_schema.py
"""
Base schema and validation for all scenarios.
Defines the common structure that all scenarios must conform to.
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
            "description": "",
            "background": [],
        }
    )

    # Data sources
    data: dict[str, Any] = field(
        default_factory=lambda: {
            "persona_file": "",
            "persona_type": "",
            "news_file": "",
            "use_news_agent": "",
        }
    )

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
    required_fields = [
        "scenario_name",
        "setting",
        "data",
        "roles",
        "shared_memories",
        "initial_observations",
    ]

    missing_fields = []
    for field_name in required_fields:
        if not OmegaConf.select(cfg, field_name):
            missing_fields.append(field_name)

    if missing_fields:
        raise ValueError(
            f"Scenario configuration missing required fields: {', '.join(missing_fields)}\n"
            f"Please ensure your scenario.yaml includes all required fields."
        )

    # Validate nested required fields
    if OmegaConf.select(cfg, "setting"):
        setting_required = ["name", "description"]
        for field_name in setting_required:
            if not OmegaConf.select(cfg, f"setting.{field_name}"):
                missing_fields.append(f"setting.{field_name}")

    if OmegaConf.select(cfg, "data"):
        data_required = ["persona_file", "persona_type"]
        for field_name in data_required:
            if not OmegaConf.select(cfg, f"data.{field_name}"):
                missing_fields.append(f"data.{field_name}")

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

    # Validate that probe candidate references exist
    if hasattr(cfg, "sc") and hasattr(cfg.sc, "probes"):
        probes_cfg = cfg.sc.probes

        # Get candidate names from role_configs if available
        candidate_names = []
        if hasattr(cfg.sc, "role_configs") and "candidate" in cfg.sc.role_configs:
            candidate_cfg = cfg.sc.role_configs.candidate
            if hasattr(candidate_cfg, "candidates"):
                for partisan_type, info in candidate_cfg.candidates.items():
                    if "name" in info:
                        candidate_names.append(info["name"])

        # Validate probe references
        if hasattr(probes_cfg, "queries_data"):
            for query_num, query_data in probes_cfg.queries_data.items():
                if hasattr(query_data, "interaction_premise_template"):
                    premise = query_data.interaction_premise_template

                    # Check candidate references
                    for field in ["candidate", "candidate1", "candidate2"]:
                        if hasattr(premise, field):
                            candidate = getattr(premise, field)
                            if candidate and candidate not in candidate_names:
                                errors.append(
                                    f"Probe {query_num} references unknown candidate: {candidate}"
                                )

    # Validate social network role references
    if hasattr(cfg, "sc"):
        sc = cfg.sc
        if hasattr(sc, "social_network") and hasattr(sc, "roles"):
            roles = set(sc.roles.keys())

            # Check active_rates references
            if hasattr(sc.social_network, "active_rates"):
                for role in sc.social_network.active_rates.keys():
                    if role not in roles:
                        errors.append(
                            f"Social network active_rates references unknown role: {role}"
                        )

            # Check fully_connected_targets references
            if hasattr(sc.social_network, "fully_connected_targets"):
                for role in sc.social_network.fully_connected_targets:
                    if role not in roles:
                        errors.append(
                            f"Social network fully_connected_targets references unknown role: {role}"
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

    # Validate persona file
    if hasattr(cfg, "data") and hasattr(cfg.data, "persona_file"):
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
