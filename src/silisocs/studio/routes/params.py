"""Reading request inputs: query arguments and JSON payload fields.

Nothing here touches app state — these turn what arrived over HTTP into the
plain Python shapes panels, views, and repositories expect, and raise 422 when
it cannot be done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import Request


def coerce_param(value: str) -> Any:
    """Query-string param -> the plain type panels expect."""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"", "none", "all"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def panel_param_overrides(query_params: Any) -> dict[str, dict[str, Any]]:
    """Parse ``p.<panel>.<param>=value`` query args into build_view overrides."""
    overrides: dict[str, dict[str, Any]] = {}
    for key, value in query_params.items():
        if not key.startswith("p."):
            continue
        parts = key.split(".", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            overrides.setdefault(parts[1], {})[parts[2]] = coerce_param(value)
    return overrides


def panel_params(query_params: Any) -> dict[str, Any]:
    """Read a panel endpoint's own params: every query arg that is not a ``p.`` override."""
    return {
        key: coerce_param(value) for key, value in query_params.items() if not key.startswith("p.")
    }


def compare_run_ids(request: Request) -> list[str]:
    """Parse the ``runs`` query args (repeated or comma-joined) into run ids."""
    ids = list(request.query_params.getlist("runs"))
    if len(ids) == 1 and "," in ids[0]:
        ids = ids[0].split(",")
    return [run_id.strip() for run_id in ids if run_id.strip()]


def require_file_mapping(files: Any) -> dict[str, str]:
    """Return the payload's file mapping, or raise 422 when it is not one."""
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in files.items()
    ):
        raise HTTPException(status_code=422, detail="files must map relative names to YAML text")
    return files
