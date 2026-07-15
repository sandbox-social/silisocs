"""Panel contract, output types, and discovery registry."""

from __future__ import annotations

import importlib
import importlib.metadata
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Literal, TypeAlias

from silisocs.design.plotly import apply_template
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact


@dataclass(frozen=True)
class Figure:
    """Portable Plotly figure JSON."""

    figure: dict[str, Any]


@dataclass(frozen=True)
class Table:
    """Tabular rows with ordered column descriptors."""

    columns: list[dict[str, Any] | str]
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class Markdown:
    """Trusted Markdown content."""

    text: str


@dataclass(frozen=True)
class Html:
    """Trusted HTML content for a sandboxed renderer."""

    html: str


@dataclass(frozen=True)
class Grid:
    """Responsive collection of nested outputs."""

    items: list[PanelOutput]


PanelOutput: TypeAlias = Figure | Table | Markdown | Html | Grid


@dataclass(frozen=True)
class Control:
    """A shell-rendered input that re-requests the panel with a new param.

    Panels stay pure functions of ``(artifact, params)``; interactivity is a
    shell concern. ``kind`` names the widget (``episode_slider``,
    ``agent_select``, ``toggle``, ``select``); ``param`` is the ``params`` key
    the widget writes; ``choices`` (optional) enumerates values for selects
    whose options are static — shells derive dynamic ones (episodes, agents)
    from the artifact.
    """

    kind: str
    param: str
    label: str = ""
    choices: tuple[str, ...] = ()


def output_to_dict(output: PanelOutput) -> dict[str, Any]:
    """Serialize a panel output into the API interchange shape.

    Figures leave here fully templated (``design.plotly``) so every renderer —
    Studio, static reports, notebooks — draws the same brand-styled chart.
    """
    kind = output.__class__.__name__.lower()
    value = asdict(output)
    if isinstance(output, Grid):
        value = {"items": [output_to_dict(item) for item in output.items]}
    if isinstance(output, Figure):
        value = {"figure": apply_template(output.figure)}
    return {"type": kind, **value}


class Panel(ABC):
    """A read-only artifact plus params to portable visualization transform."""

    name: ClassVar[str]
    title: ClassVar[str]
    scope: ClassVar[Literal["run", "study"]]
    requires: ClassVar[frozenset[str]] = frozenset()
    controls: ClassVar[tuple[Control, ...]] = ()

    @abstractmethod
    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> PanelOutput:
        """Build a portable output without mutating the artifact."""


_PANELS: dict[str, type[Panel]] = {}
_ENTRY_POINTS_LOADED: list[bool] = []


def register_panel(cls: type[Panel]) -> type[Panel]:
    """Register a panel class by its declared name."""
    if not isinstance(cls, type) or not issubclass(cls, Panel):
        raise TypeError("register_panel expects a Panel subclass")
    name = str(getattr(cls, "name", "")).strip()
    if not name:
        raise ValueError("Panel.name must be non-empty")
    if getattr(cls, "scope", None) not in {"run", "study"}:
        raise ValueError(f"Panel {name!r} must declare scope='run' or 'study'")
    _PANELS[name] = cls
    return cls


def _load_entry_points() -> None:
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED.append(True)
    for entry_point in importlib.metadata.entry_points(group="silisocs.panels"):
        candidate = entry_point.load()
        register_panel(candidate)


def _load_builtins() -> None:
    importlib.import_module("silisocs.analysis.panels")
    _load_entry_points()


def get_panel(name: str) -> type[Panel]:
    """Return a registered panel class."""
    _load_builtins()
    try:
        return _PANELS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown panel {name!r}; available: {', '.join(sorted(_PANELS))}") from exc


def list_panels() -> list[type[Panel]]:
    """Return every discovered panel class in name order."""
    _load_builtins()
    return [_PANELS[name] for name in sorted(_PANELS)]
