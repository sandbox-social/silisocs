"""Tests for config-driven agent-facing action aliasing/renaming.

The ``action_aliases`` backend config lets scenario authors give backend actions
simpler/different agent-facing names without editing backend code: the displayed
``selectable_name`` is renamed and every configured alias resolves to the same
backend method (across the descriptor-driven resolve paths and, via canonical
normalization, the custom parser path).
"""

from __future__ import annotations

import pytest

from silisocs.environments.backends.base import BackendApp, app_action


class _Dummy(BackendApp):
    """Minimal backend with two actions for alias testing."""

    def name(self) -> str:
        """Return the backend name."""
        return "dummy"

    def description(self) -> str:
        """Return the backend description."""
        return "dummy backend"

    @app_action
    def create_tweet(self, status: str) -> str:
        """Create a post."""
        return f"tweeted:{status}"

    @app_action
    def like_tweet(self, post_id: str) -> str:
        """Like a post."""
        return f"liked:{post_id}"


def _backend(aliases=None) -> _Dummy:
    backend = _Dummy()
    if aliases is not None:
        backend.set_action_aliases(aliases)
    return backend


def test_rename_changes_displayed_selectable_name():
    """A rename replaces the displayed name while keeping the canonical name."""
    backend = _backend({"create_tweet": "post"})
    selectable = {action.selectable_name for action in backend.actions()}
    assert "post" in selectable
    assert "create_tweet" not in selectable
    assert any(action.name == "create_tweet" for action in backend.actions())


def test_alias_invokes_canonical_action():
    """Every configured alias (and the canonical name) dispatches to the method."""
    backend = _backend({"create_tweet": ["post", "tweet"], "like_tweet": "fav"})
    assert backend.invoke_action_with_kwargs("post", {"status": "hi"}) == "tweeted:hi"
    assert backend.invoke_action_with_kwargs("tweet", {"status": "yo"}) == "tweeted:yo"
    assert backend.invoke_action_with_kwargs("create_tweet", {"status": "x"}) == "tweeted:x"
    assert backend.invoke_action_with_kwargs("fav", {"post_id": "7"}) == "liked:7"


def test_canonical_action_name_resolves_aliases():
    """canonical_action_name maps any agent-facing token back to the method name."""
    backend = _backend({"create_tweet": ["post", "tweet"]})
    assert backend.canonical_action_name("post") == "create_tweet"
    assert backend.canonical_action_name("tweet") == "create_tweet"
    assert backend.canonical_action_name("create_tweet") == "create_tweet"
    assert backend.canonical_action_name("unknown") is None


def test_configured_action_aliases_groups():
    """Alias groups union the canonical name, selectable name, and aliases."""
    backend = _backend({"create_tweet": ["post", "tweet"]})
    groups = [set(group) for group in backend.configured_action_aliases()]
    assert {"create_tweet", "post", "tweet"} in groups


def test_validation_unknown_collision_empty():
    """Unknown actions, cross-action collisions, and empty names fail loudly."""
    backend = _Dummy()
    with pytest.raises(ValueError, match="unknown action"):
        backend.set_action_aliases({"nope": "x"})
    with pytest.raises(ValueError, match="collides"):
        backend.set_action_aliases({"create_tweet": "like_tweet"})
    with pytest.raises(ValueError, match="at least one name"):
        backend.set_action_aliases({"create_tweet": []})


def test_default_off_is_unchanged():
    """With no aliases configured, names and groups are untouched."""
    backend = _Dummy()
    assert backend.configured_action_aliases() == []
    selectable = {action.selectable_name for action in backend.actions()}
    assert {"create_tweet", "like_tweet"} <= selectable


def test_clearing_aliases_restores_defaults():
    """Passing an empty/None mapping clears previously-set aliases."""
    backend = _backend({"create_tweet": "post"})
    backend.set_action_aliases(None)
    selectable = {action.selectable_name for action in backend.actions()}
    assert "create_tweet" in selectable
    assert backend.configured_action_aliases() == []


def test_backend_config_accepts_action_aliases():
    """The backend config schema accepts action_aliases and still rejects unknown keys."""
    from omegaconf import OmegaConf

    from silisocs.runtime.construction.game_masters import _backend_config

    cfg = OmegaConf.create(
        {
            "output_rootname": "/tmp/x",
            "sim": {"engine": {"turn_policy": {"built_in": "single_action"}}},
        }
    )
    out = _backend_config(
        cfg,
        {"type": "twitter_like", "action_aliases": {"create_tweet": "post"}},
        path="env.gm.backend",
    )
    assert out["action_aliases"] == {"create_tweet": "post"}

    with pytest.raises(ValueError, match="Unsupported config key"):
        _backend_config(cfg, {"type": "twitter_like", "bogus": 1}, path="env.gm.backend")
