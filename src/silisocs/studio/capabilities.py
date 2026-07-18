"""Which panels a backend can feed, answered before any run exists.

The run surfaces gate on what a *finished run* recorded (its manifest); the
composer has no run yet, only a chosen backend. Both questions reduce to the
same comparison — the roles a backend declares against the roles a panel reads —
so the pre-run half lives here and the post-run half in ``analysis.views``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from silisocs.analysis.panel import Panel
from silisocs.evaluations.vocabulary import (
    derive_event_semantics,
    event_semantics_for,
    parse_event_semantics,
)

if TYPE_CHECKING:  # Type-only: the backend layer stays a lazy import at runtime.
    from silisocs.environments.backends.base import BackendApp


def backend_semantic_roles(
    backend_type: str, backend_class: type[BackendApp] | None = None
) -> frozenset[str]:
    """Semantic roles a backend can populate, registered or class-declared.

    ``backend_type`` covers registered shapes (the social family registers
    centrally); ``backend_class`` covers a backend that declares on itself,
    including a custom one reached only by ``class_path``.
    """
    semantics = event_semantics_for(backend_type) if backend_type else None
    if semantics is None or not semantics.roles:
        semantics = parse_event_semantics(getattr(backend_class, "event_semantics", None))
    if not semantics.roles and backend_class is not None:
        semantics = derive_event_semantics(backend_class)
    return frozenset(role for role, labels in semantics.roles.items() if labels)


def panel_supports(panel: type[Panel], roles: frozenset[str]) -> bool:
    """Whether a panel can say something about a backend with these capabilities.

    The pre-run mirror of ``Panel.applicable``, which needs an artifact. It reads
    the same declarations, so a third-party panel is judged like a built-in. A
    panel whose own ``applicable`` reaches into run *data* is offered here
    regardless — the composer cannot know what a run will record.
    """
    if panel.needs_tags and not roles:
        return False
    return not panel.semantics or bool(panel.semantics & roles)


def backend_panel_names(
    backend_type: str,
    backend_class: type[BackendApp] | None,
    panels: Iterable[type[Panel]],
) -> Sequence[str]:
    """Names of the panels a backend can feed, keeping the given order."""
    roles = backend_semantic_roles(backend_type, backend_class)
    return tuple(panel.name for panel in panels if panel_supports(panel, roles))
