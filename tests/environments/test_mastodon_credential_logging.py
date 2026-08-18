"""Mastodon setup must persist credentials without emitting their values."""

from __future__ import annotations

import ast
from pathlib import Path


def test_create_env_helper_never_passes_credentials_to_logger() -> None:
    """Reject logger calls that interpolate either generated credential."""
    source = (
        Path(__file__).resolve().parents[2]
        / "src/silisocs/environments/backends/mastodon/mastodon_ops/create_env_file.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
            "debug",
            "info",
            "warning",
            "error",
            "exception",
        }:
            continue
        names = {
            node.id for arg in call.args for node in ast.walk(arg) if isinstance(node, ast.Name)
        }
        assert names.isdisjoint({"client_id", "client_secret"})
