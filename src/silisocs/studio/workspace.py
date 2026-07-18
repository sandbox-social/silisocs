"""Trusted scenario repositories and declarative extension discovery for Studio."""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from silisocs.studio.forms import ScenarioRepository

_MANIFEST_NAMES = ("silisocs-studio.yaml", "silisocs-studio.yml")
_MAX_NICKNAME_LENGTH = 64
_FIRST_PRINTABLE_CODEPOINT = 32
_EXTENSION_KEYS = frozenset(
    {
        "agent.builders",
        "agent.classes",
        "analysis.panels",
        "analysis.scenes",
        "backend.classes",
        "component.action_prompt",
        "component.initialize",
        "component.next_acting",
        "component.observe",
        "component.resolve",
        "component.update",
        "evaluation.presets",
        "game_master.classes",
        "policy.loop",
        "policy.participation",
        "policy.probe",
        "policy.router",
        "policy.step",
        "policy.turn",
    }
)
_SKIPPED_MODULE_PARTS = frozenset(
    {".git", ".venv", "__pycache__", "build", "dist", "node_modules", "scenarios", "tests"}
)
_BASE_CATALOGS = {
    "ActionPromptComponent": "component.action_prompt",
    "Agent": "agent.classes",
    "AgentBuilder": "agent.builders",
    "BackendApp": "backend.classes",
    "BaseGameMaster": "game_master.classes",
    "ExplorationScene": "analysis.scenes",
    "InitializeComponent": "component.initialize",
    "NextActingComponent": "component.next_acting",
    "ObservationComponent": "component.observe",
    "Panel": "analysis.panels",
    "ParticipationPolicy": "policy.participation",
    "ResolveComponent": "component.resolve",
    "SocialBackendApp": "backend.classes",
    "UpdateComponent": "component.update",
}


def _source_id(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:12]


def _scenario_root(path: Path) -> Path:
    nested = path / "scenarios"
    if nested.is_dir():
        return nested
    if path.is_dir() and any((child / "conf").is_dir() for child in path.iterdir()):
        return path
    raise ValueError(f"{path} does not contain a scenarios directory or scenario config roots")


@dataclass(frozen=True)
class RepositorySource:
    """One explicit local source of scenario documents and Python extensions."""

    id: str
    path: Path
    scenario_root: Path
    label: str
    primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe source metadata."""
        return {
            "id": self.id,
            "path": str(self.path),
            "scenario_root": str(self.scenario_root),
            "label": self.label,
            "nickname": self.label,
            "primary": self.primary,
        }


def _manifest_extensions(source: RepositorySource) -> dict[str, set[str]]:
    manifest = next(
        (source.path / name for name in _MANIFEST_NAMES if (source.path / name).is_file()), None
    )
    if manifest is None:
        return {}
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError(f"{manifest} must contain a mapping")
    raw = payload.get("extensions") or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{manifest}: extensions must be a mapping")
    result: dict[str, set[str]] = {}
    for raw_key, raw_values in raw.items():
        key = str(raw_key)
        if key not in _EXTENSION_KEYS:
            raise ValueError(f"{manifest}: unknown extension catalog {key!r}")
        values = [raw_values] if isinstance(raw_values, str) else raw_values
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"{manifest}: extensions.{key} must be a string or list of strings")
        result[key] = {item.strip() for item in values if item.strip()}
    return result


def discover_extensions(source: RepositorySource) -> dict[str, set[str]]:
    """Read optional non-class extension metadata from a project manifest."""
    return _manifest_extensions(source)


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _method_parameters(node: ast.ClassDef, name: str) -> set[str]:
    method = next(
        (
            item
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
        ),
        None,
    )
    if method is None:
        return set()
    arguments = method.args
    return {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    }


def _is_abstract(node: ast.ClassDef) -> bool:
    return any(
        _base_name(decorator) == "abstractmethod"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in item.decorator_list
    )


def _class_catalog_from_shape(node: ast.ClassDef) -> str | None:  # noqa: PLR0911
    for base in node.bases:
        if catalog := _BASE_CATALOGS.get(_base_name(base)):
            return catalog
    if _method_parameters(node, "should_run_probe_phase"):
        return "policy.probe"
    run_parameters = _method_parameters(node, "run")
    if {"engine", "agent", "action_spec"}.issubset(run_parameters):
        return "policy.turn"
    if {"engine", "step_index", "game_masters", "agents"}.issubset(run_parameters):
        return "policy.step"
    if {"engine", "game_masters", "agents", "max_steps"}.issubset(run_parameters):
        return "policy.loop"
    if {"agents", "gms", "ctx"}.issubset(_method_parameters(node, "__call__")):
        return "policy.router"
    return None


def _class_catalogs(module_name: str, source: str) -> dict[str, set[str]]:
    """Classify module classes from bases and structural method signatures."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    catalogs = {
        name: catalog
        for name, node in classes.items()
        if name not in _BASE_CATALOGS
        and not _is_abstract(node)
        and (catalog := _class_catalog_from_shape(node))
    }
    changed = True
    while changed:
        changed = False
        for name, node in classes.items():
            if name in catalogs or name in _BASE_CATALOGS or _is_abstract(node):
                continue
            inherited = next(
                (
                    catalogs[base_name]
                    for base in node.bases
                    if (base_name := _base_name(base)) in catalogs
                ),
                None,
            )
            if inherited:
                catalogs[name] = inherited
                changed = True
    found: dict[str, set[str]] = defaultdict(set)
    for name, catalog in catalogs.items():
        found[catalog].add(f"{module_name}.{name}")
    return dict(found)


def _project_module_files(  # noqa: C901
    source: RepositorySource,
) -> Iterable[tuple[str, Path]]:
    roots = [source.path / "src", source.path]
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates = [
            path for path in root.iterdir() if path.is_dir() and (path / "__init__.py").is_file()
        ]
        candidates.extend(path for path in root.glob("*.py") if path.name != "setup.py")
        for candidate in candidates:
            paths = candidate.rglob("*.py") if candidate.is_dir() else (candidate,)
            for path in paths:
                resolved = path.resolve()
                if resolved in seen or _SKIPPED_MODULE_PARTS.intersection(path.parts):
                    continue
                seen.add(resolved)
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if source.primary and "silisocs" in path.parts and not _class_catalogs("", text):
                    continue
                # Avoid parsing data-only and command modules further.
                if "class " not in text or not any(
                    marker in text
                    for marker in (
                        "Agent",
                        "Backend",
                        "Component",
                        "GameMaster",
                        "Panel",
                        "Policy",
                        "Scene",
                        "Strategy",
                    )
                ):
                    continue
                relative = path.relative_to(root).with_suffix("")
                parts = list(relative.parts)
                if parts[-1] == "__init__":
                    parts.pop()
                if parts:
                    yield ".".join(parts), path


def discover_project_classes(
    source: RepositorySource,
) -> tuple[dict[str, set[str]], list[str]]:
    """Classify project implementations without importing project modules."""
    found: dict[str, set[str]] = defaultdict(set)
    errors: list[str] = []
    for module_name, path in _project_module_files(source):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{module_name}: {type(exc).__name__}: {exc}")
            continue
        for catalog, values in _class_catalogs(module_name, text).items():
            found[catalog].update(values)
    return dict(found), errors


class WorkspaceCatalog:
    """Workspace and external repositories available to one Studio instance."""

    def __init__(
        self,
        repository_root: str | Path,
        state_dir: str | Path,
        *,
        additional: Iterable[str | Path] = (),
    ) -> None:
        self.repository_root = Path(repository_root).expanduser().resolve()
        self.state_path = Path(state_dir).expanduser().resolve() / "repositories.yaml"
        self._sources: dict[str, RepositorySource] = {}
        self._extension_cache: dict[str, dict[str, set[str]]] = {}
        self._extension_lock = threading.RLock()
        self.discovery_errors: dict[str, list[str]] = {}
        self._add_source(self.repository_root, primary=True)
        for stored_path, nickname in self._stored_sources():
            try:
                self._add_source(Path(stored_path), nickname=nickname)
            except (OSError, ValueError):
                continue
        for additional_path in additional:
            try:
                self._add_source(Path(additional_path))
            except (OSError, ValueError):
                continue

    def _stored_sources(self) -> tuple[tuple[str, str], ...]:
        if not self.state_path.is_file():
            return ()
        payload = yaml.safe_load(self.state_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            raise ValueError(f"{self.state_path}: expected repository config version 1")
        entries = payload.get("repositories") if isinstance(payload, Mapping) else None
        if not isinstance(entries, list):
            raise ValueError(f"{self.state_path}: repositories must be a list")
        if any(not isinstance(item, Mapping) for item in entries):
            raise ValueError(f"{self.state_path}: each repository must contain path and nickname")
        stored = tuple(
            (str(item.get("path") or "").strip(), str(item.get("nickname") or "").strip())
            for item in entries
        )
        if any(not path or not nickname for path, nickname in stored):
            raise ValueError(f"{self.state_path}: repository path and nickname must be non-empty")
        return stored

    def _nickname(self, value: str, path: Path, *, source_id: str = "") -> str:
        nickname = str(value).strip() or path.name
        if (
            not nickname
            or len(nickname) > _MAX_NICKNAME_LENGTH
            or any(ord(char) < _FIRST_PRINTABLE_CODEPOINT for char in nickname)
        ):
            raise ValueError("Repository nickname must be 1-64 printable characters")
        duplicate = next(
            (
                source
                for source in self._sources.values()
                if source.id != source_id and source.label.casefold() == nickname.casefold()
            ),
            None,
        )
        if duplicate:
            raise ValueError(
                f"Repository nickname {nickname!r} is already used by {duplicate.path}"
            )
        return nickname

    def _add_source(
        self,
        path: Path,
        *,
        nickname: str = "",
        primary: bool = False,
    ) -> RepositorySource:
        resolved = path.expanduser().resolve()
        source_id = "workspace" if primary else _source_id(resolved)
        source = RepositorySource(
            id=source_id,
            path=resolved,
            scenario_root=resolved / "scenarios" if primary else _scenario_root(resolved),
            label=(
                "Workspace" if primary else self._nickname(nickname, resolved, source_id=source_id)
            ),
            primary=primary,
        )
        self._sources[source.id] = source
        # The path was explicitly trusted. Make its packages available only when
        # an existing class-path seam later imports a selected implementation.
        for import_root in (resolved / "src", resolved):
            if import_root.is_dir() and str(import_root) not in sys.path:
                sys.path.insert(0, str(import_root))
        return source

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"path": str(item.path), "nickname": item.label}
            for item in self.sources
            if not item.primary
        ]
        temporary = self.state_path.with_name(f".{self.state_path.name}.tmp")
        temporary.write_text(
            yaml.safe_dump({"version": 1, "repositories": entries}, sort_keys=False),
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)

    @property
    def sources(self) -> tuple[RepositorySource, ...]:
        """Return configured sources with the primary workspace first."""
        return tuple(
            sorted(self._sources.values(), key=lambda item: (not item.primary, item.label))
        )

    def source(self, source_id: str | None = None) -> RepositorySource:
        """Resolve one source id, defaulting to the primary workspace."""
        key = str(source_id or "workspace")
        try:
            return self._sources[key]
        except KeyError as exc:
            raise KeyError(f"Unknown repository source {key!r}") from exc

    def add(self, path: str | Path, *, nickname: str = "") -> RepositorySource:
        """Trust and persist an additional project repository."""
        source = self._add_source(Path(path), nickname=nickname)
        self._extension_cache.pop(source.id, None)
        self._persist()
        return source

    def rename(self, source_id: str, nickname: str) -> RepositorySource:
        """Rename a connected repository without changing its runtime identity."""
        source = self.source(source_id)
        if source.primary:
            raise ValueError("The primary workspace repository cannot be renamed")
        renamed = RepositorySource(
            id=source.id,
            path=source.path,
            scenario_root=source.scenario_root,
            label=self._nickname(nickname, source.path, source_id=source.id),
        )
        self._sources[source.id] = renamed
        self._persist()
        return renamed

    def remove(self, source_id: str) -> None:
        """Disconnect a non-primary repository."""
        source = self.source(source_id)
        if source.primary:
            raise ValueError("The primary workspace repository cannot be removed")
        del self._sources[source.id]
        self._extension_cache.pop(source.id, None)
        self.discovery_errors.pop(source.id, None)
        self._persist()

    def scenario_repository(self, source_id: str | None = None) -> ScenarioRepository:
        """Return the scenario document store for one source."""
        return ScenarioRepository(self.source(source_id).scenario_root)

    def scenarios(self) -> list[dict[str, Any]]:
        """List scenarios across all connected sources."""
        items: list[dict[str, Any]] = []
        for source in self.sources:
            items.extend(
                {
                    **item,
                    "source": source.id,
                    "source_label": source.label,
                    "source_path": str(source.path),
                    "config_pattern": str(source.scenario_root / "{scenario}" / "conf"),
                }
                for item in self.scenario_repository(source.id).list()
            )
        return sorted(items, key=lambda item: (item["name"], item["source_label"]))

    def extension_catalog(self) -> dict[str, list[dict[str, str]]]:
        """Return discovered extension values grouped by runtime contract."""
        with self._extension_lock:
            catalog: dict[str, list[dict[str, str]]] = defaultdict(list)
            for source in self.sources:
                values_by_kind = self._extension_cache.get(source.id)
                if values_by_kind is None:
                    errors: list[str] = []
                    try:
                        values_by_kind = {
                            key: set(values) for key, values in discover_extensions(source).items()
                        }
                    except (OSError, ValueError, yaml.YAMLError) as exc:
                        values_by_kind = {}
                        errors.append(f"manifest: {type(exc).__name__}: {exc}")
                    classes, class_errors = discover_project_classes(source)
                    errors.extend(class_errors)
                    for key, values in classes.items():
                        values_by_kind.setdefault(key, set()).update(values)
                    self._extension_cache[source.id] = values_by_kind
                    self.discovery_errors[source.id] = errors
                for kind, values in values_by_kind.items():
                    catalog[kind].extend(
                        {"value": value, "origin": source.label, "source": source.id}
                        for value in sorted(values)
                    )
            return dict(catalog)

    def refresh_extensions(self) -> None:
        """Discard discovery caches so changed project code is scanned again."""
        with self._extension_lock:
            self._extension_cache.clear()
            self.discovery_errors.clear()

    def extension_options(
        self,
        kind: str,
        *,
        preferred_source: str = "workspace",
    ) -> tuple[dict[str, str], ...]:
        """Return raw extension values with source-aware presentation labels."""
        entries = sorted(
            self.extension_catalog().get(kind, ()),
            key=lambda item: (item["source"] != preferred_source, item["origin"], item["value"]),
        )
        unique: dict[str, dict[str, str]] = {}
        for item in entries:
            unique.setdefault(
                item["value"],
                {
                    "value": item["value"],
                    "label": f"{item['origin']} -> {item['value'].rsplit('.', 1)[-1]}",
                },
            )
        return tuple(unique.values())
