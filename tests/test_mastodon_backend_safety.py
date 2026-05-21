"""Safety checks for the Mastodon backend integration."""

from __future__ import annotations

import builtins

import pytest

pytest.importorskip("loguru", reason="Mastodon backend tests require mastodon extra")

from silisocs.environments.backends.mastodon import apps as mastodon_apps
from silisocs.environments.backends.mastodon.apps import SocialNetworkApp


def test_mastodon_dry_run_init_does_not_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing the Mastodon backend in dry-run mode must be non-interactive."""
    monkeypatch.setattr(
        builtins,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("input called")),
    )

    app = SocialNetworkApp(perform_operations=False)

    assert app.perform_operations is False


def test_mastodon_live_mode_imports_backend_ops_module() -> None:
    """Live mode should import the packaged Mastodon operations module directly."""
    app = SocialNetworkApp(perform_operations=True)

    assert app._mastodon_ops is not None
    assert app._mastodon_ops.__name__.endswith(".mastodon_ops")


def test_mastodon_dry_run_user_mapping_does_not_touch_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run mappings should not validate env vars or clear a live Mastodon server."""
    monkeypatch.setattr(
        mastodon_apps,
        "check_env",
        lambda: (_ for _ in ()).throw(AssertionError("check_env called")),
    )
    monkeypatch.setattr(
        mastodon_apps,
        "clear_mastodon_server",
        lambda _count: (_ for _ in ()).throw(AssertionError("clear called")),
    )
    app = SocialNetworkApp(perform_operations=False)

    app.set_user_mapping({"Alice": "user0001"})

    assert app.get_user_mapping() == {"Alice": "user0001"}


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
