"""Backend app base class and action infrastructure for simulations.

Provides the shared backend action infrastructure for environment backends.
"""

import abc
import dataclasses
import datetime
import inspect
import logging
import re
import types
import typing
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextvars import ContextVar
from typing import Any, Literal, get_type_hints

import docstring_parser
import termcolor

from silisocs.environments.backends.event_semantics import EventSemantics
from silisocs.exceptions import ActionError, BackendError
from silisocs.runtime.types import RUNTIME_AGENT_PARAM, RUNTIME_OWNED_ACTION_PARAMS

_LOGGER = logging.getLogger(__name__)
_ACTION_LOG_SCOPES: ContextVar[tuple[list[bool], ...]] = ContextVar(
    "silisocs_action_log_scopes", default=()
)


@dataclasses.dataclass(frozen=True)
class VisualizerSpec:
    """How a backend's optional read-only visualizer is served.

    Two delivery paths, in preference order:

    ``app_factory`` (``"module:function"``) names a callable taking a database
    path and returning an ASGI app. A host that already speaks ASGI — Studio —
    mounts it in-process: same origin, no subprocess, no port, no readiness
    race. This is the path every shipped visualizer takes.

    ``module``/``env_var``/``default_port``/``port_env`` describe the standalone
    ``python -m`` server. It backs ``python -m ...visualizer.server`` and remains
    the fallback for visualizers that are not ASGI apps.
    """

    env_var: str
    module: str
    default_port: int
    port_env: str = "SILISOCS_VIEWER_PORT"
    app_factory: str | None = None


@dataclasses.dataclass(frozen=True)
class ActionResult:
    """Result of an action that needs to describe its commit or logged payload."""

    message: str
    committed: bool = True
    data: Mapping[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Constants & Types
# --------------------------------------------------------------------------- #

_DATE_FORMAT = "%Y-%m-%d %H:%M"

# Data keys scanned by iter_committed_events(text_contains_any=...). Backends log the
# text an agent authored under different keys (twitter/mastodon: post_text; reddit:
# content/title; profiles: new_bio; quote/status: status/text), so the text filter
# checks all of them to stay backend-agnostic.
_COMMITTED_TEXT_KEYS: tuple[str, ...] = (
    "post_text",
    "content",
    "title",
    "status",
    "text",
    "new_bio",
)

_ARGUMENT_REGEX = re.compile(r"(?P<param>\w+):\s*(?P<value>[^\n]+)")

ParserFunc = Callable[[str], Any]

_ACTION_PROPERTY = "__app_action__"

COLOR_TYPE = (
    Literal[
        "black",
        "grey",
        "red",
        "green",
        "yellow",
        "blue",
        "magenta",
        "cyan",
        "light_grey",
        "dark_grey",
        "light_red",
        "light_green",
        "light_yellow",
        "light_blue",
        "light_magenta",
        "light_cyan",
        "white",
    ]
    | None
)


# --------------------------------------------------------------------------- #
# Literal / Argument Parsing Helpers
# --------------------------------------------------------------------------- #


def parse_literal(literal_type: type) -> ParserFunc:
    """Parse a literal type."""

    def _parse(value: str) -> Any:
        literal_values = typing.get_args(literal_type)
        if value in literal_values:
            return value
        raise ValueError(f"'{value}' is not a valid literal value for {literal_type}")

    return _parse


_ARGUMENT_PARSERS: dict[str, ParserFunc | type] = {
    "datetime.datetime": lambda date: datetime.datetime.strptime(date, _DATE_FORMAT),  # noqa: DTZ007
    "str": str,
    "int": int,
}


# --------------------------------------------------------------------------- #
# @app_action Decorator
# --------------------------------------------------------------------------- #


def _require_annotated_parameters(fn: Callable[..., Any], signature: inspect.Signature) -> None:
    """Reject an ``@app_action`` parameter with no type annotation.

    ``ActionDescriptor.from_method`` falls back to ``Any`` for an unannotated
    parameter, which silently degrades to a bare "string" in the generated tool
    schema and to an unparseable argument in generic mode. That is a defect in
    the backend, not something a run can legitimately continue past, so it fails
    at class-definition time where the mistake is.
    """
    unannotated = [
        name
        for name, param in signature.parameters.items()
        if name != "self"
        and param.annotation is inspect.Parameter.empty
        and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    if unannotated:
        raise TypeError(
            f"@app_action '{fn.__qualname__}' has unannotated parameter(s): "
            f"{', '.join(unannotated)}. Every action parameter needs a type annotation — "
            "tool schemas and generic-mode argument parsing are derived from it."
        )


def app_action(
    method: Callable[..., Any] | None = None,
    *,
    selectable_name: str | None = None,
    description: str | None = None,
    log: bool = True,
    log_as: str | None = None,
    tags: Sequence[str] = (),
    fields: Mapping[str, str | Sequence[str]] | None = None,
):
    """Mark BackendApp methods as callable actions.

    Decorated methods become discoverable via BackendApp.actions() and can be
    invoked through BackendApp.invoke_action().
    """

    def _decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(fn)
        _require_annotated_parameters(fn, signature)
        required_params = [
            name
            for name, param in signature.parameters.items()
            if param.default == inspect.Parameter.empty and name != "self"
        ]
        fn_any = typing.cast(Any, fn)
        fn_any.__app_action__ = True
        fn_any.__required_params__ = required_params
        fn_any.__action_selectable_name__ = selectable_name
        fn_any.__action_description_override__ = description
        fn_any.__action_log__ = bool(log)
        fn_any.__action_log_as__ = log_as
        fn_any.__action_tags__ = tuple(dict.fromkeys(str(tag) for tag in tags if str(tag)))
        fn_any.__action_fields__ = {
            str(name): (
                (paths,)
                if isinstance(paths, str)
                else tuple(dict.fromkeys(str(path) for path in paths if str(path)))
            )
            for name, paths in dict(fields or {}).items()
            if str(name)
        }
        return fn

    if method is not None:
        return _decorate(method)
    return _decorate


class ActionArgumentError(ActionError):
    """An error that is raised when argument parsing fails."""


def record_unexpected_action_error(action_name: str, exc: Exception) -> None:
    """Log and count a non-ActionError escaping a backend action.

    The counted boundary for ``backend_action_errors``. Backends whose own
    dispatchers (``parse_and_resolve_action``) sit outside the invoke layer call
    this and then RE-RAISE, so an unexpected failure is both counted and left to
    the engine's turn isolation instead of being disguised as a friendly message.
    """
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    _LOGGER.exception("Backend action '%s' raised unexpectedly: %s", action_name, exc)
    SimMetricsCollector.get().increment_counter("backend_action_errors")


def _record_argument_parse_failure(
    action_name: str, parameter_name: str, value: str, exc: Exception
) -> None:
    """Log and count an action argument the declared parameter type could not parse."""
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    _LOGGER.warning(
        "Action '%s' argument '%s'=%r did not parse as its declared type (%s); "
        "passing the raw string through.",
        action_name,
        parameter_name,
        value,
        exc,
    )
    SimMetricsCollector.get().increment_counter("action_parse_failures")


def record_invalid_action_target(action_type: str, target_id: Any) -> str:
    """Count an unparseable target id and return the agent-facing rejection message.

    Shared by the backends' custom-mode dispatchers so a non-numeric TARGET ID is
    reported the same way the resolve component reports it (``action_invalid_targets``),
    rather than surfacing as an ``int()`` ValueError disguised as a backend failure.
    """
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    SimMetricsCollector.get().increment_counter("action_invalid_targets")
    _LOGGER.warning(
        "Action '%s' has invalid TARGET ID %r (numeric id required).", action_type, target_id
    )
    return (
        f"Could not perform '{action_type}': TARGET ID {target_id!r} is not a valid "
        "numeric id. Use an id from the timeline."
    )


def is_runtime_owned_parameter(name: str) -> bool:
    """Return whether a backend action parameter is supplied by the runtime."""
    return name in RUNTIME_OWNED_ACTION_PARAMS


# --------------------------------------------------------------------------- #
# Parameter Dataclass
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Parameter:
    """A parameter for an action."""

    name: str
    kind: Any
    description: str | None
    required: bool

    def value_from_text(self, text: str):
        """Parse a value from a string."""
        if text == "" and not self.required:
            return None
        origin = typing.get_origin(self.kind)
        if origin is None:
            return self._parse_single_argument(text)
        if origin in (typing.Union, types.UnionType):
            args = typing.get_args(self.kind)
            if set(args) == {str, type(None)}:
                return text if text != "" else None
            return self.parse_union_type(text, args)
        if origin is list:
            return self._parse_list_argument(text)
        raise ValueError(
            f"Unsupported parameter type {self.kind!r} for {self.name!r} "
            "(supported: str, int, float, bool, list, and Optional/Union of these)"
        )

    def parse_union_type(self, value: str, types: tuple[type, ...]) -> Any:
        """Parse a value from a string, trying each type in the union."""
        for t in types:
            if t is type(None) and value == "":
                return None
            try:
                if typing.get_origin(t) is Literal:
                    return parse_literal(t)(value)
                parser = _ARGUMENT_PARSERS.get(t.__name__, t)
                return parser(value)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse '{value}' as any of {types}")

    def _parse_single_argument(self, text: str, kind: Any = None):
        kind = kind or self.kind
        if kind is type(None):
            return None
        parser = _ARGUMENT_PARSERS.get(kind, kind)  # type: ignore
        return parser(text)

    def _parse_list_argument(self, text: str):
        arg = typing.get_args(self.kind)
        parser = _ARGUMENT_PARSERS.get(arg, arg)  # type: ignore
        return [parser(e) for e in text.split(",")]


# --------------------------------------------------------------------------- #
# ActionDescriptor Dataclass
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class ActionDescriptor:
    """Represents an action that can be invoked on a backend app."""

    name: str
    selectable_name: str
    description: str
    parameters: Sequence[Parameter]
    log: bool
    log_as: str
    tags: tuple[str, ...]
    fields: Mapping[str, tuple[str, ...]]

    @property
    def agent_visible_parameters(self) -> Sequence[Parameter]:
        """Parameters an agent may provide in a tool call or generic action."""
        return [p for p in self.parameters if not is_runtime_owned_parameter(p.name)]

    @classmethod
    def from_method(cls, method):
        """Create an ActionDescriptor from a method."""
        doc = docstring_parser.parse(method.__doc__)
        description = f"{doc.short_description}\n{doc.long_description or ''}"
        description_override = getattr(method, "__action_description_override__", None)
        if description_override:
            description = str(description_override)
        signature = inspect.signature(method)
        type_hints = get_type_hints(method)

        method_parameters = []
        for name, param in signature.parameters.items():
            if name == "self":
                continue
            param_type = type_hints.get(name, Any)
            required = param.default == inspect.Parameter.empty
            param_description = next(
                (p.description for p in doc.params if p.arg_name == name), None
            )
            method_parameters.append(Parameter(name, param_type, param_description, required))

        return cls(
            name=method.__name__,
            selectable_name=str(
                getattr(method, "__action_selectable_name__", None) or method.__name__
            ),
            description=description,
            parameters=method_parameters,
            log=bool(getattr(method, "__action_log__", True)),
            log_as=str(getattr(method, "__action_log_as__", None) or method.__name__),
            tags=tuple(getattr(method, "__action_tags__", ())),
            fields=dict(getattr(method, "__action_fields__", {})),
        )


# --------------------------------------------------------------------------- #
# BackendApp Base Class
# --------------------------------------------------------------------------- #


class BackendApp(metaclass=abc.ABCMeta):
    """Base class for environment backends that agents can interact with.

    Extend this class and decorate any method that should be callable from the
    simulation with ``@app_action``. The base contract is intentionally
    domain-neutral: apps expose callable actions, can provide observations, and
    may opt into a generic initialization hook. Social backends layer timeline
    behavior on top through :class:`SocialBackendApp`.
    """

    action_logger: Any = None
    # Optional EventLogger for exposure_events.jsonl (what each agent SAW). Wired
    # by _create_backend next to action_logger; None if the run isn't logging
    # exposures. See environments/gm/components/social_media/observe.py.
    exposure_logger: Any = None
    # Optional EventLogger for harness_events.jsonl (per-call detail of harness-agent
    # tool loops). Wired by _create_backend next to action_logger; read by the harness
    # Tool Bridge and the harness resolve component. None for runs without harness agents.
    harness_logger: Any = None
    _log_color: COLOR_TYPE = "blue"

    # Checkpoint capability contract (see docs/configuration.md#checkpointing).
    # ``provides_checkpoint_state``: get_state/set_state round-trip authoritative
    # state, so a checkpoint restores this backend directly from its snapshot.
    # Backends that leave it False (e.g. live external servers) must rely on a
    # configured ``sim.checkpoint.restore`` strategy instead. Action-event replay
    # is one such strategy; it owns its per-backend event->action mapping in a
    # registry (runtime/checkpointing/replay_mappers.py) keyed by backend_type, so
    # backends themselves carry no replay-specific method.
    # Declared on the base (not left to each subclass), so every read is a plain
    # attribute access: `backend.provides_checkpoint_state`, never a getattr default.
    provides_checkpoint_state: typing.ClassVar[bool] = False
    visualizer: typing.ClassVar[VisualizerSpec | None] = None
    # Optional aggregate semantics for shared or externally declared event
    # shapes: an EventSemantics instance or the portable {roles, fields, labels}
    # mapping. Most backends declare tags and fields directly on each
    # @app_action; decorator declarations MERGE with this (declared wins per
    # entry), so a subclass adding a tagged action extends the semantics.
    event_semantics: typing.ClassVar[
        Mapping[str, Mapping[str, Sequence[str]]] | EventSemantics | None
    ] = None

    def __init__(self) -> None:
        self._enabled_actions: set[str] | None = None
        self._excluded_actions: set[str] = set()
        # Config-driven agent-facing action aliases: canonical method name ->
        # [agent-facing names], first is the displayed selectable name. Empty by
        # default (agents see canonical/decorator names). See set_action_aliases.
        self._action_aliases: dict[str, list[str]] = {}

    @abc.abstractmethod
    def name(self) -> str:
        """Return the name of the app."""
        raise NotImplementedError

    @abc.abstractmethod
    def description(self) -> str:
        """Return a description of the app."""
        raise NotImplementedError

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Optional generic app setup hook.

        Game-master-owned backend initializers call this only for backends that
        opt into the generic ``app_initialize`` path. Social backends should
        prefer explicit setup helpers consumed by the social backend
        initializer.
        """
        del agent_names, kwargs

    def update(self, *, step: int, agent_names: Sequence[str], context: Any | None = None) -> None:
        """Optional per-step world update hook for backend-owned state."""
        del step, agent_names, context

    def get_state(self) -> dict[str, Any]:
        """Return serializable backend state for checkpoints."""
        return {}

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore backend state from a checkpoint payload."""
        del state

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Return a domain-specific observation string for an actor."""
        del actor_name, kwargs
        return ""

    def current_episode(self) -> int:
        """The engine's current episode index, as stamped on the action logger.

        0 before the first step (or when no logger is wired). The seam
        turn-structured backends use to key per-round state without holding
        their own step counter.
        """
        return int(getattr(self.action_logger, "episode_idx", 0) or 0)

    def _print(
        self,
        entry: str,
        emoji: str = "",
        color: COLOR_TYPE = None,
    ) -> None:
        formatted_entry = f"{emoji} {entry}" if emoji else entry
        print(termcolor.colored(formatted_entry, color or self._log_color))

    def _emit_event_log(self, message: str, *, event_type: str) -> None:
        """Emit a typed backend event to the action logger, when one is configured.

        Message-shaped and unstructured: use it for narration a human reads. An
        action an agent took is a committed event — log it with
        :meth:`_log_action_event` so analysis can attribute and group it.
        """
        log_fn = getattr(self.action_logger, "log", None)
        if callable(log_fn):
            log_fn({"event_type": event_type, "message": message})

    def _log_action_event(self, source_user: str, label: str, data: dict[str, Any]) -> None:
        """Record a committed action event: mirror it in memory and persist to disk.

        This is the single commit point for the committed-only action log — call it
        only on an action's success path (never after a swallowed error or an
        idempotent no-op). Both the on-disk ``action_events.jsonl`` and the in-memory
        mirror (:meth:`iter_committed_events`) get exactly one record per committed
        action, so the mirror is a lock-free runtime read path over the same events
        scenario code (routers, interventions, committed-counting policies) would
        otherwise have to scrape from the log file.

        ``label`` names the action (match the ``@app_action`` name) and
        ``source_user`` the actor: the structure every generic panel groups and
        attributes by, which is why this lives on the domain-neutral base rather
        than on the social subclass.
        """
        for scope in _ACTION_LOG_SCOPES.get():
            scope[0] = True
        self._committed_event_log().append(
            {
                "label": label,
                "source_user": source_user,
                "episode": getattr(self.action_logger, "episode_idx", None),
                "data": dict(data),
            }
        )
        if self.action_logger:
            self.action_logger.log({"source_user": source_user, "label": label, "data": data})

    def _committed_event_log(self) -> list[dict[str, Any]]:
        """Return the in-memory committed-events mirror, created lazily on first use.

        Lazy so no subclass constructor has to change. The fast path is a plain
        attribute read; first use falls back to ``dict.setdefault``, which is atomic
        under the GIL, so two concurrent first-ever turns share one list instead of
        racing to install (and lose) separate ones. After init, ``list.append`` is
        atomic and readers iterate a snapshot, so the mirror needs no lock.
        """
        log = self.__dict__.get("_committed_events")
        if log is None:
            log = self.__dict__.setdefault("_committed_events", [])
        return log

    def iter_committed_events(
        self,
        *,
        labels: Collection[str] | None = None,
        agent: str | None = None,
        since_episode: int | None = None,
        before_episode: int | None = None,
        text_contains_any: Collection[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate committed action events (commit order) matching every given filter.

        Each record is ``{label, source_user, episode, data}`` — one per committed
        action. The mirror reflects the on-disk log exactly, so system/bookkeeping
        labels (``init_*``, recsys events) appear too; pass ``labels`` to scope to
        agent actions. In multi-GM runs the mirror is per-backend (per game master),
        matching the per-GM ``action_events.jsonl`` isolation.

        Filters are conjunctive: ``labels`` keeps listed labels; ``agent`` keeps one
        ``source_user``; ``since_episode``/``before_episode`` bound the episode
        (inclusive lower, exclusive upper — an unstamped ``episode is None`` event is
        excluded whenever either bound is set); ``text_contains_any`` keeps events
        whose authored text (any of ``_COMMITTED_TEXT_KEYS`` in ``data``) contains a
        needle (case-insensitive). Yielded records are copies (mutating one — including
        its ``data`` — does not touch the mirror). Iteration is commit order, which is
        scheduling-dependent under concurrent turns, so don't rely on it for ordering.
        """
        for event in self._iter_matching_committed_events(
            labels=labels,
            agent=agent,
            since_episode=since_episode,
            before_episode=before_episode,
            text_contains_any=text_contains_any,
        ):
            yield {**event, "data": dict(event.get("data") or {})}

    def _iter_matching_committed_events(
        self,
        *,
        labels: Collection[str] | None,
        agent: str | None,
        since_episode: int | None,
        before_episode: int | None,
        text_contains_any: Collection[str] | None,
    ) -> Iterator[dict[str, Any]]:
        """Yield mirror events (no per-row copy) matching every given filter.

        Shared filter core for :meth:`iter_committed_events` (which copies each
        yielded record) and :meth:`count_committed_events` (which only counts, so it
        skips the copy). Iterates the same lock-free snapshot (``tuple(...)``).
        """
        label_set = None if labels is None else {str(x) for x in labels}
        needles = None if not text_contains_any else [str(x).lower() for x in text_contains_any]
        for event in tuple(self._committed_event_log()):
            if label_set is not None and str(event.get("label", "")) not in label_set:
                continue
            if agent is not None and str(event.get("source_user", "")) != str(agent):
                continue
            episode = event.get("episode")
            if since_episode is not None and (episode is None or episode < since_episode):
                continue
            if before_episode is not None and (episode is None or episode >= before_episode):
                continue
            if needles is not None:
                data = event.get("data") or {}
                text = " ".join(str(data.get(key, "")) for key in _COMMITTED_TEXT_KEYS).lower()
                if not any(needle in text for needle in needles):
                    continue
            yield event

    def count_committed_events(
        self,
        *,
        labels: Collection[str] | None = None,
        agent: str | None = None,
        since_episode: int | None = None,
        before_episode: int | None = None,
        text_contains_any: Collection[str] | None = None,
    ) -> int:
        """Count committed events matching the filters of :meth:`iter_committed_events`.

        With no ``labels`` filter this includes every committed row, including
        control-tagged actions such as ``finish_action_episode``.
        Fast path: shares the filter core but skips the per-row copy
        ``iter_committed_events`` makes, since a count never exposes the records.
        """
        return sum(
            1
            for _ in self._iter_matching_committed_events(
                labels=labels,
                agent=agent,
                since_episode=since_episode,
                before_episode=before_episode,
                text_contains_any=text_contains_any,
            )
        )

    def _committed_events_state(self) -> list[dict[str, Any]]:
        """Snapshot the committed-events mirror for ``get_state`` (checkpoint).

        Round-tripping this through get_state/set_state is the only way to restore an
        EXACT mirror (correct labels + episodes): every shipped snapshot backend does
        so, and Mastodon does too (it cannot snapshot its server but embeds the mirror
        anyway). A backend restored purely by action-event replay instead rebuilds
        mirror *content* as the log path re-fires, but only for replayable labels and
        with episodes reflecting the replay — so prefer the round-trip when the mirror
        must be exact.
        """
        return [
            {**event, "data": dict(event.get("data") or {})}
            for event in self._committed_event_log()
        ]

    def _restore_committed_events(self, events: Any) -> None:
        """Replace the committed-events mirror from a ``set_state`` payload."""
        log = self._committed_event_log()
        log.clear()
        if events:
            log.extend(dict(event) for event in events if isinstance(event, Mapping))

    def actions(self) -> Sequence[ActionDescriptor]:
        """Return this app's callable actions."""
        actions = self._all_actions()
        if self._enabled_actions is not None:
            actions = [
                action
                for action in actions
                if self._action_matches_filter(action, self._enabled_actions)
            ]
        if self._excluded_actions:
            actions = [
                action
                for action in actions
                if not self._action_matches_filter(action, self._excluded_actions)
            ]
        return actions

    @classmethod
    def declared_action_catalog(cls) -> list[dict[str, Any]]:
        """Describe class-declared actions without constructing a backend.

        This is the read-only introspection seam used by config editors and
        documentation generators. Runtime filters and instance-specific aliases
        are intentionally absent because no configured instance exists yet.
        """
        methods = inspect.getmembers(cls, predicate=inspect.isfunction)
        descriptors = [
            ActionDescriptor.from_method(method)
            for _, method in methods
            if hasattr(method, _ACTION_PROPERTY)
        ]
        return [
            {
                "name": action.name,
                "selectable_name": action.selectable_name,
                "description": action.description.strip(),
                "parameters": [
                    {
                        "name": parameter.name,
                        "required": parameter.required,
                        "kind": str(parameter.kind),
                        "description": parameter.description,
                    }
                    for parameter in action.agent_visible_parameters
                ],
                "log": action.log,
                "log_as": action.log_as,
                "tags": list(action.tags),
                "fields": {name: list(paths) for name, paths in action.fields.items()},
            }
            for action in descriptors
        ]

    def _raw_actions(self) -> list[ActionDescriptor]:
        """Return all declared backend actions with their built-in names."""
        methods = inspect.getmembers(self, predicate=inspect.ismethod)
        return [ActionDescriptor.from_method(m) for _, m in methods if hasattr(m, _ACTION_PROPERTY)]

    def _all_actions(self) -> list[ActionDescriptor]:
        """Return declared actions with config aliases applied (before filtering).

        When ``set_action_aliases`` has been configured, an action's displayed
        ``selectable_name`` is replaced by its first configured agent-facing name,
        so prompts/catalogs present the simplified vocabulary. The canonical
        method name and every configured alias still resolve (see
        ``configured_action_aliases`` and the resolve alias index).
        """
        actions = self._raw_actions()
        if not self._action_aliases:
            return actions
        applied: list[ActionDescriptor] = []
        for action in actions:
            names = self._action_aliases.get(action.name)
            applied.append(
                dataclasses.replace(action, selectable_name=names[0])
                if names and names[0] != action.selectable_name
                else action
            )
        return applied

    def set_action_aliases(self, aliases: Mapping[str, Any] | None) -> None:
        """Configure agent-facing names for backend actions (config-driven rename).

        ``aliases`` maps an existing action (referenced by its canonical method
        name or current selectable name) to either a single new agent-facing name
        (rename) or a list of names (the first is shown to agents; all are
        accepted by the parser). This lets scenario authors simplify the agent
        action vocabulary in YAML without editing backend code. Unknown actions,
        empty names, or names that collide with another action fail loudly.
        """
        if not aliases:
            self._action_aliases = {}
            return
        token_to_canonical: dict[str, str] = {}
        for action in self._raw_actions():
            for token in (action.name, action.selectable_name):
                normalized = str(token).strip().lower()
                if normalized:
                    token_to_canonical[normalized] = action.name
        resolved: dict[str, list[str]] = {}
        new_name_owner: dict[str, str] = {}
        for key, value in dict(aliases).items():
            canonical = token_to_canonical.get(str(key).strip().lower())
            if canonical is None:
                known = sorted({action.name for action in self._raw_actions()})
                raise ValueError(
                    f"action_aliases references unknown action {key!r}. Known actions: {known}."
                )
            names = [value] if isinstance(value, str) else list(value)
            names = [str(name).strip() for name in names if str(name).strip()]
            if not names:
                raise ValueError(f"action_aliases for {key!r} must provide at least one name.")
            self._validate_alias_names(names, canonical, token_to_canonical, new_name_owner)
            resolved[canonical] = names
        self._action_aliases = resolved

    @staticmethod
    def _validate_alias_names(
        names: list[str],
        canonical: str,
        token_to_canonical: dict[str, str],
        new_name_owner: dict[str, str],
    ) -> None:
        """Reject alias names that collide with another action; record ownership."""
        for name in names:
            low = name.lower()
            if token_to_canonical.get(low, canonical) != canonical:
                raise ValueError(f"action_aliases name {name!r} collides with another action.")
            if new_name_owner.get(low, canonical) != canonical:
                raise ValueError(f"action_aliases name {name!r} is assigned to multiple actions.")
            new_name_owner[low] = canonical

    def configured_action_aliases(self) -> list[set[str]]:
        """Return config-defined alias groups for the resolve/alias index.

        Each group unions an action's canonical name, its built-in selectable
        name, and every configured agent-facing alias, so the parser accepts any
        of them regardless of which one the prompt displays. Empty when no
        aliases are configured.
        """
        if not self._action_aliases:
            return []
        raw_by_name = {action.name: action for action in self._raw_actions()}
        groups: list[set[str]] = []
        for canonical, names in self._action_aliases.items():
            group = {canonical, *names}
            descriptor = raw_by_name.get(canonical)
            if descriptor is not None:
                group.add(descriptor.selectable_name)
            groups.append(group)
        return groups

    @staticmethod
    def _action_matches_filter(action: ActionDescriptor, names: set[str]) -> bool:
        return action.name in names or action.selectable_name in names

    def action_aliases(self) -> list[set[str]]:
        """Return groups of synonymous action tokens for per-flow filtering.

        Each group lists tokens that name the same logical action across the
        vocabularies an agent may emit — e.g. the custom-mode domain verb
        (``post``) and the canonical backend method (``create_tweet``). Per-flow
        action filters union these groups so a filter written in either
        vocabulary matches regardless of which synonym the agent uses. Backends
        with no synonyms (the default) return an empty list.
        """
        return []

    def action_catalog(self) -> list[dict[str, Any]]:
        """Return a normalized action catalog for prompting/UI/config validation."""
        catalog: list[dict[str, Any]] = []
        for action in self.actions():
            catalog.append(
                {
                    "name": action.name,
                    "selectable_name": action.selectable_name,
                    "description": action.description.strip(),
                    "parameters": [
                        {
                            "name": p.name,
                            "required": p.required,
                            "kind": str(p.kind),
                            "description": p.description,
                        }
                        for p in action.agent_visible_parameters
                    ],
                }
            )
        return catalog

    def set_enabled_actions(self, enabled_actions: Sequence[str] | None) -> None:
        """Restrict actions exposed to prompts/parsers/tool-calling.

        If ``enabled_actions`` is None, all actions are exposed.
        """
        self.set_action_filters(
            enabled_actions=enabled_actions, excluded_actions=self._excluded_actions
        )

    def set_excluded_actions(self, excluded_actions: Sequence[str] | None) -> None:
        """Remove actions from prompts/parsers/tool-calling."""
        self.set_action_filters(
            enabled_actions=self._enabled_actions, excluded_actions=excluded_actions
        )

    def set_action_filters(
        self,
        *,
        enabled_actions: Collection[str] | None,
        excluded_actions: Collection[str] | None = None,
    ) -> None:
        """Configure backend action allow/deny lists.

        Action names may be canonical backend method names or selectable aliases.
        Unknown names and contradictory allow/deny filters fail loudly.
        """
        enabled = self._normalize_action_filter(enabled_actions)
        excluded = self._normalize_action_filter(excluded_actions) or set()

        self._validate_action_filter(enabled, "enabled")
        self._validate_action_filter(excluded, "excluded")

        if enabled is not None and excluded:
            conflicts = [
                action.selectable_name
                for action in self._all_actions()
                if self._action_matches_filter(action, enabled)
                and self._action_matches_filter(action, excluded)
            ]
            if conflicts:
                raise ValueError(
                    "Actions cannot be both enabled and excluded: " + ", ".join(sorted(conflicts))
                )

        self._enabled_actions = enabled
        self._excluded_actions = excluded

    @staticmethod
    def _normalize_action_filter(actions: Collection[str] | None) -> set[str] | None:
        if actions is None:
            return None
        return {str(name).strip() for name in actions if str(name).strip()}

    def _validate_action_filter(self, actions: set[str] | None, label: str) -> None:
        if actions is None:
            return
        all_action_names = {action.name for action in self._all_actions()} | {
            action.selectable_name for action in self._all_actions()
        }
        unknown = sorted(name for name in actions if name not in all_action_names)
        if unknown:
            available = ", ".join(sorted(all_action_names))
            raise ValueError(
                f"Unknown {label} action(s): "
                + ", ".join(unknown)
                + f". Available actions: {available}"
            )

    def _handle_action_invocation_error(
        self, action_name: str, exc: Exception, *, record_unexpected: bool
    ) -> str:
        """Format, print, and (optionally) record an action invocation error."""
        if record_unexpected:
            record_unexpected_action_error(action_name, exc)
        message = f"Error invoking action {action_name}: {exc}"
        self._print(message, color="red")
        return message

    def invoke_action(self, action: ActionDescriptor, args_text: str) -> str | None:
        """Invoke the given action with the given arguments."""
        args = _parse_argument_text(args_text)
        self._print(f"Invoking action {action.name} with arguments {args}", color="yellow")
        expected_params = {p.name: p for p in action.parameters}

        # Check for missing required arguments
        missing_args = [
            name for name, param in expected_params.items() if param.required and name not in args
        ]
        if missing_args:
            raise ActionArgumentError(f"Missing required argument(s): {', '.join(missing_args)}")

        # Check for unexpected arguments
        unexpected_args = set(args) - set(expected_params)
        if unexpected_args:
            raise ActionArgumentError(f"Unexpected argument(s): {', '.join(unexpected_args)}")

        # Process values
        processed_args: dict[str, str | None] = {}
        for name, param in expected_params.items():
            if name in args:
                value = args[name]
                if value == "" and not param.required:
                    processed_args[name] = None
                else:
                    processed_args[name] = param.value_from_text(value)
            elif not param.required:
                processed_args[name] = None

        try:
            return self._dispatch_action(action, processed_args)[1]
        except ActionError as e:
            return self._handle_action_invocation_error(action.name, e, record_unexpected=False)
        except Exception as e:
            return self._handle_action_invocation_error(action.name, e, record_unexpected=True)

    def _action_lookup(self) -> dict[str, ActionDescriptor]:
        lookup: dict[str, ActionDescriptor] = {}
        for action in self.actions():
            # Canonical name, displayed selectable name, and any extra configured
            # agent-facing aliases all dispatch to the same action.
            keys = {action.name, action.selectable_name}
            keys.update(self._action_aliases.get(action.name, ()))
            for key in keys:
                existing = lookup.get(key)
                if existing is not None and existing.name != action.name:
                    raise BackendError(
                        f"Action name collision in {type(self).__name__}: "
                        f"'{key}' maps to both '{existing.name}' and '{action.name}'. "
                        "Action names and selectable_names must be unique per backend."
                    )
                lookup[key] = action
        return lookup

    def canonical_action_name(self, action_name: str) -> str | None:
        """Return the canonical method name for any agent-facing action token.

        Resolves a displayed selectable name or configured alias back to the
        backend method name (used to normalize custom-mode action tokens before
        dispatch). Returns None when the token names no known action.
        """
        descriptor = self._action_lookup().get(str(action_name))
        return descriptor.name if descriptor is not None else None

    def invoke_action_by_name(self, action_name: str, args_text: str) -> str:
        """Find an action by name and invoke it with the given argument text.

        Used by the 'generic' action_mode to dispatch LLM output directly.
        """
        action_map = self._action_lookup()
        if action_name not in action_map:
            available = ", ".join(sorted(action_map.keys()))
            return f"Unknown action '{action_name}'. Available actions: {available}"
        return self.invoke_action(action_map[action_name], args_text) or ""

    def invoke_action_with_kwargs(self, action_name: str, args: dict[str, Any]) -> str:
        """Invoke action by name using structured kwargs payload."""
        return self.invoke_action_detailed(action_name, args)[1]

    def invoke_action_detailed(self, action_name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Invoke an action, returning ``(committed, result_string)``.

        ``ActionResult(committed=False)`` lets a backend report a rejected or
        idempotent call without raising. Plain return values are committed
        successes. Validation failures and raised exceptions return
        ``(False, message)``. ``invoke_action_with_kwargs`` is the string-only view.
        """
        action_map = self._action_lookup()
        if action_name not in action_map:
            available = ", ".join(sorted(action_map.keys()))
            return False, f"Unknown action '{action_name}'. Available actions: {available}"

        action = action_map[action_name]
        expected = {param.name: param for param in action.parameters}
        payload = dict(args or {})

        missing_required = [
            name for name, param in expected.items() if param.required and name not in payload
        ]
        if missing_required:
            return False, f"Missing required argument(s): {', '.join(missing_required)}"

        unexpected = sorted(set(payload.keys()) - set(expected.keys()))
        if unexpected:
            return False, f"Unexpected argument(s): {', '.join(unexpected)}"

        processed: dict[str, Any] = {}
        for name, param in expected.items():
            if name not in payload:
                if not param.required:
                    processed[name] = None
                continue

            value = payload[name]
            if value is None and not param.required:
                processed[name] = None
                continue

            if isinstance(value, str):
                try:
                    processed[name] = param.value_from_text(value)
                except Exception as exc:
                    # The raw string still goes through (an action may accept it),
                    # but a coercion the declared type rejected is a real parse
                    # failure — counted, never silent.
                    _record_argument_parse_failure(action.name, name, value, exc)
                    processed[name] = value
            else:
                processed[name] = value

        try:
            committed, message = self._dispatch_action(action, processed)
            return committed, message or ""
        except ActionError as exc:
            return False, self._handle_action_invocation_error(
                action.name, exc, record_unexpected=False
            )
        except Exception as exc:
            return False, self._handle_action_invocation_error(
                action.name, exc, record_unexpected=True
            )

    def _dispatch_action(
        self,
        descriptor: ActionDescriptor,
        processed: Mapping[str, Any],
    ) -> tuple[bool, str | None]:
        """Call one validated action and auto-log a committed result exactly once."""
        scope = [False]
        token = _ACTION_LOG_SCOPES.set((*_ACTION_LOG_SCOPES.get(), scope))
        try:
            raw = getattr(self, descriptor.name)(**processed)
        finally:
            _ACTION_LOG_SCOPES.reset(token)

        committed: bool
        message: str | None
        extra: Mapping[str, Any] | None
        if isinstance(raw, ActionResult):
            committed, message, extra = raw.committed, raw.message, raw.data
        else:
            committed, message, extra = True, None if raw is None else str(raw), None

        if committed and descriptor.log and not scope[0]:
            data = {
                key: value
                for key, value in processed.items()
                if key != RUNTIME_AGENT_PARAM and value is not None
            }
            data.update(dict(extra or {}))
            if "message" not in data:
                data["message"] = message
            source_user = str(processed.get(RUNTIME_AGENT_PARAM) or "system")
            self._log_action_event(source_user, descriptor.log_as, data)
        return committed, message

    def action_accepts_param(self, action_name: str, parameter_name: str) -> bool:
        """Return whether an action accepts the named parameter.

        Used to decide whether to inject the runtime-owned actor argument
        (``agent_name``) into a call: the backend owns its action metadata, so both the
        resolve components and the harness Tool Bridge ask it rather than re-deriving.
        """
        for action in self.actions():
            if action_name in {action.name, action.selectable_name}:
                return any(param.name == parameter_name for param in action.parameters)
        return False

    def generate_generic_action_prompt(self) -> str:
        """Build a call-to-action prompt auto-generated from @app_action methods.

        Used when action_mode='generic'.  The LLM must respond in the format::

            ACTION: <action_name>
            <param>: <value>
            <param>: <value>
        """
        description = str(self.description() or "").strip()
        lines = [description] if description else []
        lines += ["", "Available actions:"]
        actions = list(self.actions())
        for action in actions:
            params_desc = ", ".join(
                f"{p.name} ({'required' if p.required else 'optional'}, {p.kind})"
                for p in action.agent_visible_parameters
            )
            lines.append(f"  {action.selectable_name}({params_desc})")
            lines.append(f"    {action.description.strip()}")

        lines += [
            "",
            "[OUTPUT STYLE]",
            "Respond with EXACTLY ONE action using this format:",
            "ACTION: <action_name>",
            "<param_name>: <value>",
            "<param_name>: <value>",
        ]
        return "\n".join(lines)

    def generate_tool_schemas(self) -> list[dict]:
        """Return OpenAI-compatible tool schemas for all @app_action methods.

        Used when resolve mode is 'tool_calling'.
        """
        schemas = []
        for action in self.actions():
            properties: dict[str, Any] = {}
            required: list[str] = []
            for param in action.agent_visible_parameters:
                prop = _param_to_json_schema(param)
                if param.description:
                    prop = dict(prop, description=param.description)
                properties[param.name] = prop
                if param.required:
                    required.append(param.name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": action.selectable_name,
                        "description": action.description.strip(),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    @app_action(
        selectable_name="FINISHED",
        description="To be used when desirable actions for current timestep have been conducted.",
        tags=("control",),
    )
    def finish_action_episode(self, agent_name: str = "") -> str:
        """No-op terminal action for open-ended loops and constrained action sets."""
        del agent_name
        return "Finished action episode"


# --------------------------------------------------------------------------- #
# Argument Text Parser
# --------------------------------------------------------------------------- #


def _parse_argument_text(args_text: str) -> dict[str, str]:
    """Parse multiline argument text to a dict.

    'param1: value1\\n param2: value2' -> {'param1': 'value1', 'param2': 'value2'}
    """
    matches = _ARGUMENT_REGEX.finditer(args_text)
    return {m.group("param"): m.group("value").strip() for m in matches if m.group("value").strip()}


def _param_to_json_schema(param: "Parameter") -> dict:
    """Convert a Parameter's type annotation to a JSON Schema property dict."""
    kind = param.kind
    origin = typing.get_origin(kind)

    if kind is str:
        return {"type": "string"}
    if kind is int:
        return {"type": "integer"}
    if kind is bool:
        return {"type": "boolean"}
    if kind is float:
        return {"type": "number"}

    if origin in (typing.Union, types.UnionType):
        non_none = [t for t in typing.get_args(kind) if t is not type(None)]
        if len(non_none) == 1:
            return _param_to_json_schema(
                Parameter(param.name, non_none[0], param.description, param.required)
            )

    if origin is list:
        inner = typing.get_args(kind)
        inner_type = "string"
        if inner and inner[0] is int:
            inner_type = "integer"
        return {"type": "array", "items": {"type": inner_type}}

    if typing.get_origin(kind) is Literal:
        return {"type": "string", "enum": list(typing.get_args(kind))}

    return {"type": "string"}  # fallback


# --------------------------------------------------------------------------- #
# SocialBackendApp - social-specific backend base
# --------------------------------------------------------------------------- #


class SocialBackendApp(BackendApp):
    """Backend capability interface for timeline and recommendation components.

    Backends that use timeline observation, social state setup, parsed social
    actions, or recommendation update components implement this interface on
    top of the domain-neutral :class:`BackendApp`.
    """

    def recsys_active_types(self) -> set[str]:
        """Return recsys algorithm types currently live on the backend.

        The recommendation-update component reconciles configured types against
        this set, so a backend whose in-memory recsys engine was rebuilt empty on
        checkpoint restore reports an empty set and triggers a lazy re-init. The
        default has no live recsys state.
        """
        return set()

    def _resolve_active_user_ids(self, active_agent_names: Sequence[str]) -> list[int]:
        """Translate agent display names to backend user ids (unknowns skipped).

        Used by scoped recommendation updates: the update component knows agents
        by display name while the platform recsys keys on numeric user ids. Names
        with no platform user (e.g. agents owned by another GM's backend) are
        dropped silently — a scoped update simply has nothing to refresh for them.
        Relies on the concrete app's ``_get_username`` mapping and its platform's
        cached ``get_user_id``.
        """
        get_username = getattr(self, "_get_username", None)
        platform = getattr(self, "_platform", None)
        if not callable(get_username) or platform is None:
            return []
        ids: list[int] = []
        seen: set[int] = set()
        for display_name in active_agent_names:
            try:
                username = get_username(str(display_name))
            except ValueError:
                continue
            user_id = platform.get_user_id(username)
            if isinstance(user_id, int) and user_id not in seen:
                seen.add(user_id)
                ids.append(user_id)
        return ids

    def setup_social_state(
        self,
        *,
        agent_names: list[str],
        sim_roles: dict[str, str] | None = None,
        graph_config: dict[str, Any] | None = None,
        following_graph: dict[str, list[str]] | None = None,
        agent_bios: dict[str, str] | None = None,
    ) -> None:
        """Set up social users and relationships for this backend."""
        del agent_names, sim_roles, graph_config, following_graph, agent_bios
        raise NotImplementedError(f"{type(self).__name__} does not implement social state setup.")

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Return raw timeline data for a user."""
        del user_name, limit
        raise NotImplementedError(f"{type(self).__name__} does not implement timelines.")

    def get_timeline_mode(
        self,
        timeline_mode: str,
        user_name: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: dict,
    ) -> list[dict]:
        """Return timeline data for a specific mode."""
        del timeline_mode, user_name, limit, recsys_type, timeline_config
        raise NotImplementedError(f"{type(self).__name__} does not implement timeline modes.")

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        """Convert raw timeline data into text for an agent observation."""
        del timeline
        raise NotImplementedError(f"{type(self).__name__} does not format social timelines.")

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str | ActionResult:
        """Dispatch a parsed social action to the correct backend method.

        A failure/no-op branch may return ``ActionResult(committed=False)``; the
        parsed-action resolve component unwraps it to its message text.
        """
        del user_name, action_data
        raise NotImplementedError(f"{type(self).__name__} does not resolve social actions.")
