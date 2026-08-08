"""Small helpers shared by agent-builder internals."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import InterpolationResolutionError

from silisocs.runtime.construction.specs import AgentConfig


def to_plain(data: Any, *, where: str = "the agents config") -> Any:
    """Convert OmegaConf containers into ordinary Python values, resolving refs.

    An interpolation that cannot resolve raises, naming ``where`` and the missing
    key. It must never fall back to ``resolve=False``: persona text routinely
    interpolates world values (``context: ${event.context}``), so an unresolved
    reference silently becomes the LITERAL string ``${event.context}`` in every
    affected agent's prompt and the run completes looking healthy.
    """
    if isinstance(data, (DictConfig, ListConfig)):
        try:
            return OmegaConf.to_container(data, resolve=True)
        except InterpolationResolutionError as exc:
            # OmegaConf's message already names BOTH the unresolved reference and
            # the config key that carries it (its ``full_key``); flatten it onto
            # one line rather than re-deriving either.
            detail = " ".join(str(exc).split())
            raise ValueError(
                f"Could not resolve an interpolation while reading {where}: {detail}. "
                "Define the referenced key in the composed config (the scenario's "
                "world/agents groups) or remove the reference."
            ) from exc
    return data


def normalize_memories(memories: Any) -> list[str]:
    """Normalize memory input into non-empty strings."""
    if memories is None:
        return []
    if isinstance(memories, str):
        lines = [line.strip() for line in memories.splitlines() if line.strip()]
        return lines or ([memories.strip()] if memories.strip() else [])
    if isinstance(memories, list):
        return [str(item).strip() for item in memories if str(item).strip()]
    return [str(memories).strip()] if str(memories).strip() else []


def coerce_text(value: Any, *, joiner: str = "\n") -> str:
    """Coerce mapped record values into prompt-ready text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return joiner.join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def derive_name(context: str, *, words: int = 2) -> str:
    """Derive a compact display name from persona text."""
    tokens = re.findall(r"[A-Za-z0-9']+", context or "")
    return " ".join(tokens[: max(1, words)]).strip()


def extract_path(record: Any, dotted_path: Any) -> Any:
    """Read a dotted path from a mapping/list record."""
    if dotted_path is None:
        return None
    if not isinstance(dotted_path, str):
        return dotted_path
    current = record
    for chunk in dotted_path.split("."):
        if isinstance(current, Mapping):
            if chunk in current:
                current = current[chunk]
            else:
                lowered = {str(k).lower(): k for k in current if isinstance(k, str)}
                key = lowered.get(chunk.lower())
                current = current[key] if key is not None else None
        elif isinstance(current, list) and chunk.isdigit():
            idx = int(chunk)
            current = current[idx] if 0 <= idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def resolve_source(record: Any, spec: Any) -> Any:
    """Resolve a field-map source as a dotted path or ``{field}`` template."""
    if not isinstance(spec, str) or "{" not in spec:
        return extract_path(record, spec)

    def _sub(match: re.Match[str]) -> str:
        value = extract_path(record, match.group(1).strip())
        if value is None:
            return ""
        return (
            "\n".join(str(item).strip() for item in value)
            if isinstance(value, list)
            else str(value)
        )

    return re.sub(r"\{([^{}]+)\}", _sub, spec)


def safe_path_exists(candidate: Any) -> bool:
    """Check path existence while treating OS path errors as absence."""
    try:
        return bool(getattr(candidate, "exists", lambda: False)())
    except OSError:
        return False


def validate_unique_agent_names(configs: list[AgentConfig]) -> list[AgentConfig]:
    """Validate that every agent spec has a unique, explicit name."""
    seen: set[str] = set()
    duplicates: list[str] = []
    missing: list[int] = []
    for idx, cfg in enumerate(configs, start=1):
        name = str((cfg.params or {}).get("name", "")).strip()
        if not name:
            missing.append(idx)
            continue
        if name in seen:
            duplicates.append(name)
            continue
        seen.add(name)

    errors: list[str] = []
    if missing:
        errors.append(
            "missing names at spec index "
            + ", ".join(str(index) for index in missing[:10])
            + (", ..." if len(missing) > 10 else "")
        )
    if duplicates:
        unique = sorted(set(duplicates))
        errors.append(
            "duplicate names: " + ", ".join(unique[:10]) + (", ..." if len(unique) > 10 else "")
        )
    if errors:
        raise ValueError("Agent names must be explicit and unique: " + "; ".join(errors))
    return configs
