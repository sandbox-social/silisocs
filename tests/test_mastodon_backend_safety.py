"""Safety checks for the Mastodon backend integration."""

from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from silisocs.environments.backends.mastodon import apps as mastodon_apps
from silisocs.environments.backends.mastodon.apps import SocialNetworkApp


def test_mastodon_dry_run_init_does_not_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing the Mastodon backend in dry-run mode must be non-interactive."""
    monkeypatch.setattr(
        mastodon_apps,
        "_load_mastodon_ops",
        lambda: (_ for _ in ()).throw(AssertionError("mastodon ops imported")),
    )
    monkeypatch.setattr(
        builtins,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("input called")),
    )

    app = SocialNetworkApp(perform_operations=False)

    assert app.perform_operations is False


def test_packaged_mastodon_config_defaults_to_dry_run() -> None:
    """Packaged Mastodon config should not mutate a server by default."""
    config_path = Path(__file__).parents[1] / "src" / "silisocs" / "conf" / "env" / "mastodon.yaml"

    cfg = yaml.safe_load(config_path.read_text())
    params = cfg["gm"]["backend"]["params"]

    assert params["perform_operations"] is False
    assert params["reset_server_on_setup"] is False


def test_mastodon_live_mode_imports_backend_ops_module_or_install_hint() -> None:
    """Live mode should import Mastodon ops or fail with an optional-extra hint."""
    try:
        app = SocialNetworkApp(perform_operations=True)
    except RuntimeError as exc:
        assert "silisocs[mastodon]" in str(exc)
    else:
        assert app._mastodon_ops is not None
        assert app._mastodon_ops.__name__.endswith(".mastodon_ops")


def test_mastodon_live_mode_optional_extra_error_is_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing optional dependencies should mention the Mastodon extra."""

    def missing_extra() -> None:
        raise RuntimeError(mastodon_apps._MASTODON_EXTRA_INSTALL_HINT)

    monkeypatch.setattr(mastodon_apps, "_load_mastodon_ops", missing_extra)

    with pytest.raises(RuntimeError, match=r"silisocs\[mastodon\]"):
        SocialNetworkApp(perform_operations=True)


def test_mastodon_user_mapping_does_not_clear_server_without_reset_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mappings should not clear a live Mastodon server without explicit reset opt-in."""
    ops = SimpleNamespace(
        check_env=lambda: (_ for _ in ()).throw(AssertionError("check_env called")),
        clear_mastodon_server=lambda _count: (_ for _ in ()).throw(AssertionError("clear called")),
    )
    app = SocialNetworkApp(perform_operations=False)
    app.perform_operations = True
    app._mastodon_ops = ops

    app.set_user_mapping({"Alice": "user0001"})

    assert app.get_user_mapping() == {"Alice": "user0001"}


def test_mastodon_user_mapping_clears_server_only_with_reset_flag() -> None:
    """The destructive Mastodon reset path should require a separate explicit flag."""
    calls: list[tuple[str, int | None]] = []
    ops = SimpleNamespace(
        check_env=lambda: calls.append(("check_env", None)),
        clear_mastodon_server=lambda count: calls.append(("clear", count)),
    )
    app = SocialNetworkApp(perform_operations=False, reset_server_on_setup=True)
    app.perform_operations = True
    app._mastodon_ops = ops

    app.set_user_mapping({"Alice Smith": "user0001", "Bob Jones": "user0002"})

    assert calls == [("check_env", None), ("clear", 3)]


def test_mastodon_actions_tolerate_missing_action_logger() -> None:
    """The optional Mastodon backend should support dry-run action methods without a logger."""
    app = SocialNetworkApp(perform_operations=False, action_logger=None)
    app.set_user_mapping(
        {
            "AliceSmith": "user0001",
            "BobJones": "user0002",
        }
    )

    result = app.follow_user("Alice Smith", "Bob Jones")

    assert "followed" in result


def test_mastodon_action_logger_accepts_runtime_positional_events() -> None:
    """Mastodon logging should match the shared social backend logger contract."""

    class Logger:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def log(self, event: dict) -> None:
            self.events.append(event)

    logger = Logger()
    app = SocialNetworkApp(perform_operations=False, action_logger=logger)

    app._log_action_event("system", "initialize", {"num_users": 2})

    assert logger.events == [
        {"source_user": "system", "label": "initialize", "data": {"num_users": 2}}
    ]


def test_mastodon_display_name_lookup_supports_single_and_two_token_names() -> None:
    """Mastodon actions should accept full display names and generated single-token names."""
    app = SocialNetworkApp(perform_operations=False, action_logger=None)
    app.set_user_mapping(
        {
            "Alice Smith": "user0001",
            "Bob Jones": "user0002",
            "user_1": "user0003",
        }
    )

    assert app.public_get_username("Alice Smith") == "user0001"
    assert app.public_get_username("AliceSmith") == "user0001"
    assert app.public_get_username("Alice") == "user0001"
    assert app.public_get_username("user_1") == "user0003"
    assert "followed" in app.follow_user("Alice Smith", "Bob Jones")
    assert "posted a toot" in app.post_toot("user_1", "hello")
    assert "liked post" in app.like_toot("user_1", "123")
    assert "boosted post" in app.boost_toot("user_1", "123")


def test_mastodon_already_liked_or_boosted_noop_logs_no_event() -> None:
    """An already-liked/boosted toot is a no-op and must NOT log an action row.

    get_state embeds the logged action history and set_state replays it on restore,
    so a redundant like/boost row would be re-issued — the committed-only contract.
    """

    class Logger:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def log(self, event: dict) -> None:
            self.events.append(event)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real like/boost op must not run on an already-done no-op")

    logger = Logger()
    ops = SimpleNamespace(
        like_check=lambda _u, _t: True,  # already liked
        boost_check=lambda _u, _t: True,  # already boosted
        like_toot=_boom,
        boost_toot=_boom,
    )
    app = SocialNetworkApp(perform_operations=False, action_logger=logger)
    app.set_user_mapping({"user_1": "user0003"})
    app.perform_operations = True
    app._mastodon_ops = ops

    like_msg = app.like_toot("user_1", "123")
    boost_msg = app.boost_toot("user_1", "123")

    assert "previously liked" in like_msg
    assert "previously boosted" in boost_msg
    assert logger.events == []  # neither no-op logged a row
