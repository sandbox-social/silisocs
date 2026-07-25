"""Panel contract, output types, and discovery registry."""

from __future__ import annotations

import importlib
import importlib.metadata
import warnings
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
    """Pre-escaped HTML, injected verbatim into the page (NOT sandboxed).

    Renderers emit this content unescaped, so a panel MUST ``html.escape`` any
    value derived from run data (post text, agent names, probe responses come
    from model output). See ``docs/analysis_panels.md`` for the contract.
    """

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
    ``agent_select``, ``probe_select``, ``select``, ``text``); ``param`` is the
    ``params`` key the widget writes; ``choices`` (optional) enumerates values
    for selects whose options are static — shells derive dynamic ones (episodes,
    agents, probes) from the artifact. A ``kind`` the shell does not recognize
    renders as a free-text input bound to the same param (``text`` is the
    canonical spelling of that widget), so a custom kind is never silently
    inert.
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


def serialize_controls(panel: type[Panel], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Serialize a panel's controls into the shell API interchange shape.

    Shared by ``analysis.views.build_view`` and the Studio panel endpoint so both
    surfaces describe a panel's controls identically (kind/param/label/choices,
    plus the control's current value read from ``params``).
    """
    return [
        {
            "kind": control.kind,
            "param": control.param,
            "label": control.label or control.param.title(),
            "choices": list(control.choices),
            "value": params.get(control.param),
        }
        for control in panel.controls
    ]


class Panel(ABC):
    """A read-only artifact plus params to portable visualization transform.

    A panel declares what it needs — ``requires`` (all-of: event streams the run
    must have recorded), ``semantics`` (any-of: roles the run's backend must
    declare), ``needs_tags`` — and a run that cannot supply it simply does
    not show the panel. That is how a resource-market run has no follow graph and
    a social run has no market ledger, with neither side naming the other. See
    ``docs/analysis_panels.md``.
    """

    name: ClassVar[str]
    title: ClassVar[str]
    scope: ClassVar[Literal["run", "study"]]
    requires: ClassVar[frozenset[str]] = frozenset()
    # Semantic roles (``EventSemantics.roles``) this panel can read. ANY-of:
    # one is enough. Empty means backend-neutral — applicable to every run.
    semantics: ClassVar[frozenset[str]] = frozenset()
    # Set when the panel needs the backend to classify actions with open tags.
    needs_tags: ClassVar[bool] = False
    controls: ClassVar[tuple[Control, ...]] = ()

    @classmethod
    def applicable(cls, artifact: RunArtifact | StudyArtifact) -> bool:
        """Whether this panel can say anything about the artifact's backend.

        Override for a capability the declarations cannot express — reaching
        into the run's data, say — but prefer declaring, so backends stay the
        ones describing themselves.
        """
        from silisocs.analysis.panels._shared import run_has_tags, run_semantic_roles

        if not isinstance(artifact, RunArtifact):
            return True
        if cls.needs_tags and not run_has_tags(artifact):
            return False
        return not cls.semantics or bool(cls.semantics & run_semantic_roles(artifact))

    @abstractmethod
    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> PanelOutput:
        """Build a portable output without mutating the artifact."""


_PANELS: dict[str, type[Panel]] = {}
_ENTRY_POINTS_LOADED: list[bool] = []


def validate_panel(cls: type[Panel]) -> type[Panel]:
    """Validate a panel class's declarations, raising on contract violations.

    Shared by :func:`register_panel` and the view-slot ``class_path`` loader so
    every panel — registered or one-off — is held to the same contract.
    """
    if not isinstance(cls, type) or not issubclass(cls, Panel):
        raise TypeError("register_panel expects a Panel subclass")
    name = str(getattr(cls, "name", "")).strip()
    if not name:
        raise ValueError("Panel.name must be non-empty")
    if getattr(cls, "scope", None) not in {"run", "study"}:
        raise ValueError(f"Panel {name!r} must declare scope='run' or 'study'")
    # ``requires``/``semantics``/``needs_tags`` gate RUN artifacts only (they
    # read a run's manifest). On a study panel they would be silently ignored —
    # fail loud at declaration time instead of letting the author believe the
    # gate is in force.
    if cls.scope == "study" and (cls.requires or cls.semantics or cls.needs_tags):
        raise ValueError(
            f"Panel {name!r} has scope='study' but declares requires/semantics/"
            "needs_tags — those gates apply to run artifacts only and would be "
            "silently ignored at study scope"
        )
    return cls


def register_panel(cls: type[Panel]) -> type[Panel]:
    """Register a panel class by its declared name."""
    validate_panel(cls)
    _PANELS[str(cls.name).strip()] = cls
    return cls


def _load_entry_points() -> None:
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED.append(True)
    for entry_point in importlib.metadata.entry_points(group="silisocs.panels"):
        # One broken third-party plugin must not take down every analysis
        # surface that lists panels — skip it with a warning instead.
        try:
            register_panel(entry_point.load())
        except Exception as exc:
            warnings.warn(
                f"Skipping panel entry point {entry_point.name!r}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )


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
