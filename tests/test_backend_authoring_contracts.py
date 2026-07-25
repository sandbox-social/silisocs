"""A custom backend's mistakes surface as errors, not as empty output.

The contracts a backend author must satisfy used to be discoverable only by
noticing that something was missing: a swallowed ``action_logger`` wrote no
events, an empty allow-list exposed no actions, a checkpoint flag with no state
methods restored nothing, and a social component on a generic backend crashed
mid-run. Each of those now fails (or is repaired) where the cause is.
"""
# ruff: noqa: D103

from __future__ import annotations

import logging
from typing import Any

import pytest

from silisocs.environments.backends.base import BackendApp, app_action
from silisocs.environments.backends.factory import create_backend_app
from silisocs.environments.gm.components.factory import build_observe_component
from silisocs.environments.gm.context import GameMasterContext
from silisocs.environments.gm.game_master import _create_backend
from silisocs.evaluations.vocabulary import (
    EventSemantics,
    event_semantics_for,
    register_event_semantics,
)
from silisocs.runtime.io.jsonl import flush_jsonl_writers


class _PlainBackend(BackendApp):
    """A hand-written backend: no dataclass fields, ``action_logger`` unnamed."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

    def name(self) -> str:
        return "plain"

    def description(self) -> str:
        return "plain"

    @app_action(selectable_name="ping", description="Ping.")
    def ping(self, agent_name: str) -> str:
        self._log_action_event(source_user=agent_name, label="ping", data={})
        return "pong"


class _DeclaringBackend(_PlainBackend):
    @app_action(tags=("market.trade",), fields={"market.qty": "n"})
    def ping(self, agent_name: str, n: int = 1) -> str:
        return "pong"


_OWN_LOGGER = object()


class _OwnLoggerBackend(_PlainBackend):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.action_logger = _OWN_LOGGER


class _NoActionsBackend(BackendApp):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

    def name(self) -> str:
        return "empty"

    def description(self) -> str:
        return "empty"


class _ClaimsCheckpointState(_PlainBackend):
    provides_checkpoint_state = True


class _HalfCheckpointState(_ClaimsCheckpointState):
    def get_state(self) -> dict[str, Any]:
        return {"episode": 1}  # set_state still inherited


class _WholeCheckpointState(_ClaimsCheckpointState):
    def get_state(self) -> dict[str, Any]:
        return {"episode": 1}

    def set_state(self, state: dict[str, Any]) -> None:
        pass


def _path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _build(cls: type, tmp_path, **cfg: Any) -> Any:
    return _create_backend(
        {
            "backend_type": "custom_demo",
            "class_path": _path(cls),
            "output_rootname": str(tmp_path),
            **cfg,
        },
        gm_name="gm",
    )


def test_action_logger_is_wired_even_when_the_constructor_ignores_it(tmp_path):
    """The trap: a swallowed kwarg used to mean an empty log and no error."""
    backend = _build(_PlainBackend, tmp_path)
    assert backend.action_logger is not None
    backend.invoke_action_with_kwargs("ping", {"agent_name": "Ada"})
    flush_jsonl_writers()
    rows = (tmp_path / "action_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and '"label": "ping"' in rows[0]


def test_a_constructor_that_installs_its_own_logger_keeps_it(tmp_path):
    assert _build(_OwnLoggerBackend, tmp_path).action_logger is _OWN_LOGGER


def test_empty_allow_list_is_rejected_and_says_why(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        _build(_PlainBackend, tmp_path, enabled_actions=[])
    message = str(excinfo.value)
    assert "enabled_actions: []" in message and "enabled_actions: null" in message
    assert "'ping'" in message  # the declared actions it could have named


def test_excluding_every_action_is_rejected(tmp_path):
    """FINISHED survives every exclusion, but it only ends a turn — it is not an action."""
    with pytest.raises(ValueError, match="no callable actions"):
        _build(_PlainBackend, tmp_path, excluded_actions=["ping"])


def test_an_empty_list_is_rejected_under_open_ended_too(tmp_path):
    """open_ended re-adds FINISHED, which must not disguise an empty catalog."""
    with pytest.raises(ValueError, match="no callable actions"):
        _build(_PlainBackend, tmp_path, enabled_actions=[], turn_policy_built_in="open_ended")


def test_null_means_no_filter(tmp_path):
    backend = _build(_PlainBackend, tmp_path, enabled_actions=None)
    assert "ping" in {action.selectable_name for action in backend.actions()}


def test_a_backend_with_no_actions_warns_rather_than_fails(tmp_path, caplog):
    """An update-only world backend is unusual, not wrong."""
    _build(_NoActionsBackend, tmp_path)
    assert "declares no @app_action methods" in caplog.text


@pytest.mark.parametrize(
    ("cls", "named"),
    [
        (_ClaimsCheckpointState, "get_state() and set_state()"),
        (_HalfCheckpointState, "set_state()"),
    ],
)
def test_claiming_checkpoint_authority_without_the_methods_is_rejected(cls, named):
    with pytest.raises(ValueError) as excinfo:
        create_backend_app("custom_demo", class_path=_path(cls))
    assert named in str(excinfo.value)


def test_overriding_both_state_methods_satisfies_the_flag():
    assert create_backend_app("custom_demo", class_path=_path(_WholeCheckpointState)) is not None


@pytest.mark.parametrize(
    "backend_type",
    [
        "twitter_like",
        "reddit_like",
        "mastodon",
        "resource_market",
        "virtual_space",
        "public_goods",
        "messaging",
    ],
)
def test_shipped_backends_satisfy_the_checkpoint_contract(backend_type, tmp_path):
    from silisocs.environments.backends.factory import (
        _validate_checkpoint_contract,
        resolve_backend_class,
    )

    _validate_checkpoint_contract(resolve_backend_class(backend_type))


def test_class_declared_capabilities_are_live_for_a_custom_backend():
    """A custom type names no importable class, so the registries need the built one."""
    create_backend_app("declaring_demo", class_path=_path(_DeclaringBackend))
    assert event_semantics_for("declaring_demo").labels("market.trade") == frozenset({"ping"})
    assert event_semantics_for("declaring_demo").tags_of("ping") == ("market.trade",)


def test_an_unbuilt_backend_type_still_resolves_to_empty_capabilities():
    assert event_semantics_for("never_built").roles == {}


def test_an_explicit_registration_wins_over_the_class_declaration():
    create_backend_app("override_demo", class_path=_path(_DeclaringBackend))
    register_event_semantics(
        "override_demo",
        EventSemantics(
            roles={"other": frozenset({"alternate"})},
            label_tags={"alternate": ("other",)},
        ),
    )
    assert event_semantics_for("override_demo").tags_of("alternate") == ("other",)


def test_decorated_actions_extend_a_registered_declaration():
    """A registration no longer shadows the decorators — it merges with them.

    Forking a shipped backend and decorating a NEW action must reach the
    analysis surfaces without editing any central table; the declared entry
    still wins for labels it names.
    """
    create_backend_app("merge_demo", class_path=_path(_DeclaringBackend))
    register_event_semantics(
        "merge_demo",
        EventSemantics(
            roles={"other": frozenset({"alternate"})},
            label_tags={"alternate": ("other",)},
        ),
    )
    semantics = event_semantics_for("merge_demo")
    # The registered entry is intact...
    assert semantics.tags_of("alternate") == ("other",)
    # ...and the decorator-declared action is visible alongside it.
    assert semantics.labels("market.trade") == frozenset({"ping"})
    assert semantics.tags_of("ping") == ("market.trade",)
    assert semantics.fields["market.qty"] == ("n",)


def test_malformed_class_declaration_fails_loud():
    """A backend author's broken `event_semantics` raises instead of silently
    degrading to empty semantics (which would just hide every panel).
    """
    from silisocs.evaluations.vocabulary import declared_event_semantics

    class _BrokenDeclaration(BackendApp):
        event_semantics = {"roles": "not-a-mapping"}

        def name(self) -> str:
            return "broken"

        def description(self) -> str:
            return "broken"

    with pytest.raises(ValueError, match="event_semantics is malformed"):
        declared_event_semantics(_BrokenDeclaration)

    class _NotEvenAMapping(BackendApp):
        event_semantics = ["roles"]

        def name(self) -> str:
            return "broken"

        def description(self) -> str:
            return "broken"

    with pytest.raises(TypeError, match="event_semantics must be"):
        declared_event_semantics(_NotEvenAMapping)


def _context(backend: Any) -> GameMasterContext:
    return GameMasterContext(
        gm_name="gm",
        agents=(),
        model=None,
        agent_names=(),
        backend=backend,
        agent_flow_tags={},
    )


def test_a_generic_backend_omitting_observe_gets_the_generic_component(tmp_path):
    backend = _build(_PlainBackend, tmp_path)
    component = build_observe_component(None, context=_context(backend))
    assert type(component).__name__ == "AppObservationComponent"


def test_a_social_backend_omitting_observe_keeps_the_timeline_default(tmp_path):
    backend = create_backend_app("twitter_like", db_path=str(tmp_path / "t.db"))
    component = build_observe_component(None, context=_context(backend))
    assert type(component).__name__ == "TimelineMakeObservation"


class _NarratingBackend(_PlainBackend):
    """Emits human-readable narration but commits nothing analysis can attribute."""

    @app_action(selectable_name="mumble", description="Mumble.")
    def mumble(self, agent_name: str) -> str:
        self._emit_event_log(f"{agent_name} mumbled", event_type="mumble")
        return "ok"


class _GameMaster:
    name = "gm"
    backend_type = "custom_demo"

    def __init__(self, backend: Any) -> None:
        self.backend = backend


def test_a_backend_that_only_narrates_is_reported_at_run_end(tmp_path, caplog):
    """Narration fills the log with rows nothing can group by, which is not committing."""
    from silisocs.runtime.execution.session import _warn_silent_backends

    backend = _build(_NarratingBackend, tmp_path)
    backend.mumble("Ada")
    flush_jsonl_writers()
    assert (tmp_path / "action_events.jsonl").read_text(encoding="utf-8")  # log is not empty

    _warn_silent_backends([_GameMaster(backend)], logging.getLogger("test"))
    assert "committed no structured action events" in caplog.text
    assert "_log_action_event" in caplog.text  # names the fix


def test_a_backend_that_commits_is_not_reported(tmp_path, caplog):
    from silisocs.runtime.execution.session import _warn_silent_backends

    backend = _build(_PlainBackend, tmp_path)
    backend.ping("Ada")

    _warn_silent_backends([_GameMaster(backend)], logging.getLogger("test"))
    assert "committed no structured action events" not in caplog.text
