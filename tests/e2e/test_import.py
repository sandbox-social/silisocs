"""Test Silisocs package imports."""

import silisocs


def test_import() -> None:
    """Test that the app can be imported."""
    assert isinstance(silisocs.__name__, str)
    assert isinstance(silisocs.__version__, str)
