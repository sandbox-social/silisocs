"""Small helpers shared by agent-builder internals."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf

from silisocs.runtime.construction.specs import AgentConfig

logger = logging.getLogger(__name__)


def to_plain(data: Any) -> Any:
    """Convert OmegaConf containers into ordinary Python values."""
    if isinstance(data, (DictConfig, ListConfig)):
        try:
            return OmegaConf.to_container(data, resolve=True)
        except Exception:
            return OmegaConf.to_container(data, resolve=False)
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
        return "\n".join(str(item).strip() for item in value) if isinstance(value, list) else str(value)

    return re.sub(r"\{([^{}]+)\}", _sub, spec)


def derive_name(context: str, words: int = 2) -> str:
    """Derive a stable fallback name from the first words of context."""
    tokens = re.findall(r"[A-Za-z0-9']+", context or "")
    return " ".join(tokens[: max(1, words)]) if tokens else ""


def safe_path_exists(candidate: Any) -> bool:
    """Check path existence while treating OS path errors as absence."""
    try:
        return bool(getattr(candidate, "exists", lambda: False)())
    except OSError:
        return False


def deduplicate_agent_configs(configs: list[AgentConfig]) -> list[AgentConfig]:
    """Drop duplicate named agents while preserving first occurrence."""
    result: list[AgentConfig] = []
    seen: set[str] = set()
    skipped: list[str] = []
    for cfg in configs:
        name = str((cfg.params or {}).get("name", "")).strip()
        if not name:
            result.append(cfg)
            continue
        if name in seen:
            skipped.append(name)
            continue
        seen.add(name)
        result.append(cfg)
    if skipped:
        unique = sorted(set(skipped))
        preview = ", ".join(unique[:10]) + (", ..." if len(unique) > 10 else "")
        logger.warning(
            "Skipped %d duplicate agent names (%d unique): %s",
            len(skipped),
            len(unique),
            preview,
        )
    return result
