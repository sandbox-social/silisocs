"""Release-facing packaging metadata checks."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())


def test_package_data_includes_runtime_templates() -> None:
    """Visualizer templates are loaded from package-relative paths at runtime."""
    data = _pyproject()
    package_data = data["tool"]["setuptools"]["package-data"]["silisocs"]

    assert "environments/backends/**/templates/*.html" in package_data


def test_public_package_excludes_live_mastodon_probe_module() -> None:
    """Ad hoc live-service scripts should not ship inside the import package."""
    assert not (PROJECT_ROOT / "src" / "silisocs" / "test_mastodon.py").exists()


def test_project_declares_mit_license() -> None:
    """The public package should have concrete license metadata."""
    data = _pyproject()

    assert data["project"]["license"] == "MIT"
    assert "MIT License" in (PROJECT_ROOT / "LICENSE").read_text()


def test_default_config_does_not_require_optional_hf_dependency() -> None:
    """The lean install quickstart should not require Hugging Face datasets."""
    agents_cfg = yaml.safe_load(
        (PROJECT_ROOT / "src" / "silisocs" / "conf" / "agents" / "default.yaml").read_text()
    )
    user_data = agents_cfg["persona_pipeline"]["classes"]["user"]["data"]

    assert user_data["source"] == "inline"


def test_concordia_is_optional_extra_not_default_dependency() -> None:
    """Native Silisocs installs should not pull Concordia by default."""
    data = _pyproject()
    dependencies = data["project"]["dependencies"]
    optional = data["project"]["optional-dependencies"]

    assert not any("concordia" in dependency.lower() for dependency in dependencies)
    assert optional["concordia"] == ["gdm-concordia>=2.1.0,<2.2.0"]
