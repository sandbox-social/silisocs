"""A deterministic, dependency-free harness adapter.

:class:`FakeHarnessAdapter` runs a scripted mini agentic loop (read the feed, then
post) through the Tool Bridge, mirroring the ``TutorialBehavior`` scripted agent. It
exists for two reasons: (1) it makes the entire harness integration testable with no
external process or API key — the contract tests use it as the reference harness, so
its behavior IS the public spec; (2) users can select it as a ``dry_run`` harness to
validate a scenario's wiring before paying for a real harness.

Behavior is fully determined by construction params, so runs are replay-stable:
``script`` (an explicit ``[{name, arguments}, ...]`` tool sequence) takes precedence;
otherwise the adapter auto-detects a "post" action from the surface and fills its first
required string parameter with ``post_content``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.base import HarnessAgent, compose_persona
from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.types import HarnessTurnResult
from silisocs.runtime.language_models import LanguageModel

# Name fragments that identify a "create a post" style action across social backends.
_POST_NAME_HINTS = ("create_tweet", "create_reddit_post", "create_post", "post", "toot", "submit")
# Read-style actions the fake harness calls first (best-effort; skipped if none match).
_READ_NAME_HINTS = ("get_home_feed", "get_timeline", "home", "feed", "timeline")


class FakeHarnessAdapter:
    """Scripted, deterministic harness adapter used by tests and dry runs."""

    def __init__(
        self,
        *,
        script: Sequence[Mapping[str, Any]] | None = None,
        post_content: str = "Hello from a scripted harness agent.",
        read_first: bool = True,
        final_text: str = "",
    ) -> None:
        self._script = [dict(item) for item in (script or [])]
        self._post_content = str(post_content)
        self._read_first = bool(read_first)
        self._final_text = str(final_text or "")
        self._turns = 0

    # ------------------------------------------------------------------ #
    # Adapter contract
    # ------------------------------------------------------------------ #

    def run_turn(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        self._turns += 1
        surface = request.surface
        if surface is not None:
            for name, arguments in self._planned_calls(surface):
                surface.execute(name, arguments)
        final = self._final_text or f"{request.agent_name} completed a scripted harness turn."
        return HarnessTurnResult(final_text=final, finished=True)

    def run_probe(self, request: HarnessProbeRequest) -> str:
        if request.options:
            return str(request.options[0])
        return "Scripted harness probe answer."

    def snapshot(self) -> dict[str, Any]:
        return {"turns": self._turns}

    def restore(self, state: dict[str, Any]) -> None:
        self._turns = int(state.get("turns", 0) or 0)

    # ------------------------------------------------------------------ #
    # Call planning
    # ------------------------------------------------------------------ #

    def _planned_calls(self, surface: ToolSurface) -> list[tuple[str, dict[str, Any]]]:
        if self._script:
            return [(str(item["name"]), dict(item.get("arguments", {}))) for item in self._script]

        schemas = surface.schemas()
        names = [str(((s or {}).get("function") or {}).get("name") or "").strip() for s in schemas]
        calls: list[tuple[str, dict[str, Any]]] = []

        if self._read_first:
            read = self._find(names, _READ_NAME_HINTS)
            # Only auto-call a read whose required args are all runtime-injected (none
            # agent-visible), so the dry run never fabricates arguments or logs a failure.
            if read is not None and not self._required_args(schemas, read):
                calls.append((read, {}))

        post = self._find(names, _POST_NAME_HINTS)
        if post is not None:
            calls.append((post, self._fill_required_string(schemas, post)))
        return calls

    @staticmethod
    def _required_args(schemas: list[dict[str, Any]], action: str) -> list[str]:
        for schema in schemas:
            function = (schema or {}).get("function") or {}
            if str(function.get("name") or "").strip() == action:
                return list((function.get("parameters") or {}).get("required") or [])
        return []

    @staticmethod
    def _find(names: list[str], hints: tuple[str, ...]) -> str | None:
        lowered = {name.lower(): name for name in names if name}
        for hint in hints:
            for low, original in lowered.items():
                if hint in low:
                    return original
        return None

    def _fill_required_string(self, schemas: list[dict[str, Any]], action: str) -> dict[str, Any]:
        for schema in schemas:
            function = (schema or {}).get("function") or {}
            if str(function.get("name") or "").strip() != action:
                continue
            params = function.get("parameters") or {}
            properties = params.get("properties") or {}
            required = params.get("required") or []
            args: dict[str, Any] = {}
            for field_name in required:
                prop = properties.get(field_name) or {}
                if prop.get("type") in (None, "string"):
                    args[field_name] = self._post_content
                    break
            return args
        return {}


class FakeHarnessAgent(HarnessAgent):
    """Reference concrete harness agent, wired to :class:`FakeHarnessAdapter`.

    Referenceable via ``persona_pipeline.classes.<class>.class_path``:
    ``silisocs.agents.harness.fake.FakeHarnessAgent``. Serves as a dependency-free
    ``dry_run`` harness and as the subject of the harness contract tests.
    """

    def __init__(
        self,
        model: LanguageModel,
        *,
        name: str,
        persona: str = "",
        probe_mode: str = "model",
        script: Sequence[Mapping[str, Any]] | None = None,
        post_content: str = "Hello from a scripted harness agent.",
        read_first: bool = True,
        final_text: str = "",
        **persona_fields: Any,
    ) -> None:
        # Absorb the persona-pipeline param surface (bio/style/goal/context/memories/...)
        # the builder passes to every agent, folding it into the harness persona.
        adapter = FakeHarnessAdapter(
            script=script,
            post_content=post_content,
            read_first=read_first,
            final_text=final_text,
        )
        super().__init__(
            model,
            name=name,
            adapter=adapter,
            persona=compose_persona(persona, **persona_fields),
            probe_mode=probe_mode,
        )
