"""Release-facing packaging metadata checks."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())


def test_package_data_includes_runtime_templates() -> None:
    """Visualizer templates are loaded from package-relative paths at runtime."""
    data = _pyproject()
    package_data = data["tool"]["setuptools"]["package-data"]["silisocs"]

    assert "environments/backends/**/templates/*.html" in package_data


def test_package_data_includes_studio_assets() -> None:
    """Studio templates and zero-build static assets ship package-relative."""
    data = _pyproject()
    package_data = data["tool"]["setuptools"]["package-data"]["silisocs"]

    assert "studio/templates/*.html" in package_data
    assert "studio/static/*.css" in package_data
    assert "studio/static/*.js" in package_data


def test_public_package_excludes_live_mastodon_probe_module() -> None:
    """Ad hoc live-service scripts should not ship inside the import package."""
    assert not (PROJECT_ROOT / "src" / "silisocs" / "test_mastodon.py").exists()


def test_project_declares_mit_license() -> None:
    """The public package should have concrete license metadata."""
    data = _pyproject()

    assert data["project"]["license"] == "MIT"
    assert data["project"]["license-files"] == ["LICENSE", "NOTICE"]
    assert "MIT License" in (PROJECT_ROOT / "LICENSE").read_text()


def test_project_urls_match_public_docs_site() -> None:
    """PyPI metadata should point at the same public project home as the docs."""
    urls = _pyproject()["project"]["urls"]

    assert urls["Documentation"] == "https://sandbox-social.github.io/silisocs"
    assert urls["Repository"] == "https://github.com/sandbox-social/silisocs"


def test_notice_covers_optional_concordia_bridge() -> None:
    """The optional Concordia bridge should have explicit attribution notes."""
    notice = (PROJECT_ROOT / "NOTICE").read_text()

    assert "gdm-concordia" in notice
    assert "Apache License 2.0" in notice


def test_default_config_does_not_require_optional_hf_dependency() -> None:
    """The lean install quickstart should not require Hugging Face datasets."""
    agents_cfg = yaml.safe_load(
        (PROJECT_ROOT / "src" / "silisocs" / "conf" / "agents" / "default.yaml").read_text()
    )
    user_data = agents_cfg["persona_pipeline"]["classes"]["user"]["data"]

    assert user_data["source"] == "inline"


def test_packaged_env_presets_do_not_duplicate_social_backends() -> None:
    """Twitter/Reddit each ship as one canonical env preset."""
    env_files = {
        path.name for path in (PROJECT_ROOT / "src" / "silisocs" / "conf" / "env").glob("*.yaml")
    }

    assert "twitter_like.yaml" in env_files
    assert "reddit_like.yaml" in env_files
    assert not any("extended" in name for name in env_files)
    assert not any("oasis" in name.lower() for name in env_files)


def test_concordia_is_optional_extra_not_default_dependency() -> None:
    """Native Silisocs installs should not pull Concordia by default."""
    data = _pyproject()
    dependencies = data["project"]["dependencies"]
    optional = data["project"]["optional-dependencies"]

    assert not any("concordia" in dependency.lower() for dependency in dependencies)
    # numpy/pandas live here too: adapters/concordia/memory.py is their only
    # importer in src/, so a native install carries neither.
    assert optional["concordia"] == [
        "gdm-concordia>=2.1.0,<2.2.0",
        "numpy>=1.26,<3",
        "pandas>=2.2",
    ]
    assert not any(dep.startswith(("numpy", "pandas")) for dep in dependencies)


# --------------------------------------------------------------------------- #
# Console scripts must be importable from the extras they advertise
# --------------------------------------------------------------------------- #

SRC_ROOT = PROJECT_ROOT / "src"

# Distribution name -> the module name it actually provides, for the few where
# they differ. Everything else imports under its own (normalized) name.
_IMPORT_NAMES = {
    "docstring-parser": "docstring_parser",
    "hydra-core": "hydra",
    "python-dotenv": "dotenv",
    "python-louvain": "community",
    "pyyaml": "yaml",
    "scikit-learn": "sklearn",
    "sentence-transformers": "sentence_transformers",
    "mastodon-py": "mastodon",
}


def _import_names(requirements: list[str]) -> set[str]:
    """Map a dependency list to the top-level module names it makes importable."""
    names = set()
    for requirement in requirements:
        dist = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip().lower()
        names.add(_IMPORT_NAMES.get(dist, dist.replace("-", "_")))
    return names


def _module_file(module: str) -> Path | None:
    relative = Path(*module.split("."))
    for candidate in (
        SRC_ROOT / relative.with_suffix(".py"),
        SRC_ROOT / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _module_scope_imports(path: Path) -> set[str]:
    """Return module names imported at import time (no functions, TYPE_CHECKING, try)."""
    found: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return  # lazy imports inside functions are not import-time costs

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_Try(self, node: ast.Try) -> None:
            return  # a guarded optional import is allowed to be missing

        def visit_If(self, node: ast.If) -> None:
            if "TYPE_CHECKING" not in ast.unparse(node.test):
                self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            found.update(alias.name for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level == 0 and node.module:
                found.add(node.module)

    _Visitor().visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return found


def _third_party_import_closure(entry_module: str) -> set[str]:
    """Third-party top-level modules an entrypoint pulls in at import time."""
    seen: set[str] = set()
    third_party: set[str] = set()
    stack = [entry_module]
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_file(module)
        if path is None:
            continue
        for imported in _module_scope_imports(path):
            parts = imported.split(".")
            if parts[0] == "silisocs":
                # The module and every package on the way to it run at import.
                stack.extend(".".join(parts[:i]) for i in range(2, len(parts) + 1))
            elif parts[0] not in sys.stdlib_module_names:
                third_party.add(parts[0])
    return third_party


def test_report_entrypoint_is_importable_from_the_analysis_extra() -> None:
    """`silisocs-report` must run on `silisocs[analysis]`, not need `studio` too.

    `analysis/report.py` imports jinja2 at module scope (it renders the export
    through Studio's shared `_output.html` macro), but jinja2 shipped only in the
    `studio` extra, so the console script died on ImportError.
    """
    data = _pyproject()
    available = _import_names(data["project"]["dependencies"]) | _import_names(
        data["project"]["optional-dependencies"]["analysis"]
    )

    missing = sorted(_third_party_import_closure("silisocs.analysis.report") - available)

    assert missing == []
    assert "jinja2" in available


def test_core_entrypoints_need_no_optional_extra() -> None:
    """`silisocs` and `silisocs-study` must import on a bare install."""
    base = _import_names(_pyproject()["project"]["dependencies"])

    for entry_module in ("silisocs.runtime.runner", "silisocs.studies.run_study"):
        assert sorted(_third_party_import_closure(entry_module) - base) == []
