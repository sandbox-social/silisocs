"""Legacy compact action-spec parser retained for Concordia adapter shims."""

from __future__ import annotations

from silisocs.runtime.types import ActionSpec, OutputType


def action_spec_parser(text: str | ActionSpec) -> ActionSpec:
    """Parse compact legacy action-spec text into native ``ActionSpec``."""
    if isinstance(text, ActionSpec):
        return text

    raw = str(text or "").strip()
    if not raw:
        return ActionSpec(prompt="", output_type=OutputType.TEXT)

    prompt = raw
    output_type = OutputType.TEXT
    options: tuple[str, ...] = ()

    for part in raw.split(";;"):
        key, sep, value = part.partition(":")
        if not sep:
            continue
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if normalized_key == "prompt":
            prompt = normalized_value
        elif normalized_key == "type":
            aliases = {
                "free": OutputType.TEXT,
                "text": OutputType.TEXT,
                "choice": OutputType.CHOICE,
                "float": OutputType.FLOAT,
                "tool_calls": OutputType.TOOL_CALLS,
                "structured": OutputType.STRUCTURED,
                "skip": OutputType.SKIP,
                "skip_this_step": OutputType.SKIP,
            }
            output_type = aliases.get(normalized_value.lower(), OutputType.TEXT)
        elif normalized_key == "options":
            options = tuple(item.strip() for item in normalized_value.split(",") if item.strip())

    return ActionSpec(prompt=prompt, output_type=output_type, options=options)
