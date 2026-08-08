"""The typed object every Studio route reads its collaborators off.

``create_app`` resolves each of these once and stores the frozen record as
``app.state.studio``; routers reach it through :func:`studio_state`, which is
what makes a mistyped attribute a type error instead of an ``AttributeError`` at
request time. Nothing here is a module-level singleton, so one process can host
several Studio apps with different roots.

Annotations are strings (``from __future__ import annotations``) and every
collaborator type is imported under ``TYPE_CHECKING``: this module must stay
importable without paying for FastAPI or the job/viewer layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from fastapi import Request
    from fastapi.templating import Jinja2Templates

    from silisocs.design.assets import CachedAsset
    from silisocs.studio.app import StudioWarmup
    from silisocs.studio.jobs import JobManager
    from silisocs.studio.plugins import StudioPage
    from silisocs.studio.scenario_repository import ScenarioRepository
    from silisocs.studio.studies import StudyRepository
    from silisocs.studio.viewers import ViewerDispatch
    from silisocs.studio.workspace import WorkspaceCatalog


@dataclass(frozen=True, slots=True)
class StudioState:
    """Everything the routers need, resolved once when the app is created."""

    output_root: Path
    repo_root: Path
    studio_state: Path
    jobs: JobManager
    workspace: WorkspaceCatalog
    scenarios: ScenarioRepository
    studies: StudyRepository
    plugin_pages: list[StudioPage]
    templates: Jinja2Templates
    warmup: StudioWarmup
    assets: dict[str, CachedAsset]
    viewers: ViewerDispatch


def studio_state(request: Request) -> StudioState:
    """The typed state of the Studio app serving this request."""
    # Starlette's State returns Any from __getattr__, so the cast is where the
    # untyped attribute bag ends and every downstream use becomes checkable.
    return cast("StudioState", request.app.state.studio)
