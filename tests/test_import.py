"""Test Mastodon Sim."""

import silisocs


def test_import() -> None:
    """Test that the app can be imported."""
    assert isinstance(silisocs.__name__, str)
