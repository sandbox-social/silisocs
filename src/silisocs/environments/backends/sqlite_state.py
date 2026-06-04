"""SQLite checkpoint state helpers for local backends."""

from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
from pathlib import Path


def snapshot_sqlite_database(db_path: str) -> str:
    """Return a base64 SQLite backup for an existing database path."""
    source_path = Path(db_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite backend database not found: {source_path}")

    fd, tmp_name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with sqlite3.connect(str(source_path)) as source, sqlite3.connect(tmp_name) as dest:
            source.execute("PRAGMA wal_checkpoint(FULL)")
            source.backup(dest)
        return base64.b64encode(Path(tmp_name).read_bytes()).decode("ascii")
    finally:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass


def restore_sqlite_database(db_path: str, snapshot_b64: str) -> None:
    """Replace a SQLite database file with a base64 checkpoint snapshot."""
    if not isinstance(snapshot_b64, str) or not snapshot_b64:
        raise ValueError("SQLite checkpoint state requires a non-empty snapshot string.")
    path = Path(db_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = base64.b64decode(snapshot_b64.encode("ascii"))
    except Exception as exc:
        raise ValueError("Invalid SQLite checkpoint snapshot encoding.") from exc
    _remove_sqlite_sidecars(path)
    path.write_bytes(data)


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            pass
