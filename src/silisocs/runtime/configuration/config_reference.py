"""Generate ``docs/config_reference.md`` from the packaged default configs.

The reference is derived from the actual YAML defaults shipped in
``silisocs/conf``, so it cannot drift silently: ``tests/test_config_reference.py``
regenerates it and fails when the checked-in file no longer matches. Regenerate
with ``python -m silisocs.runtime.configuration.config_reference``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

# (section title, packaged file, dotted prefix for keys). The world group
# composes at the config root (@package _global_), so it has no prefix.
_GROUPS = (
    ("world", "world/default.yaml", ""),
    ("agents", "agents/default.yaml", "agents."),
    ("sim", "sim/base.yaml", "sim."),
    ("env (twitter_like)", "env/twitter_like.yaml", "env."),
    ("eval", "eval/base.yaml", "eval."),
)

_HEADER = """\
# Configuration Reference (Generated)

Default values for every key in the packaged base config groups, generated
from the YAML files under `src/silisocs/conf/`. Do not edit by hand:
regenerate with `python -m silisocs.runtime.configuration.config_reference`;
`tests/test_config_reference.py` fails when this file drifts from the
packaged defaults.

For what the knobs mean, see [configuration.md](configuration.md). Scenario
files override these defaults via Hydra composition (see `AGENTS.md`).
"""


def _conf_dir() -> Path:
    import silisocs

    return Path(silisocs.__file__).resolve().parent / "conf"


def _flatten(node: Any, prefix: str, rows: list[tuple[str, Any]]) -> None:
    if isinstance(node, dict) and node:
        for key, value in node.items():
            _flatten(value, f"{prefix}{key}.", rows)
    else:
        rows.append((prefix.rstrip("."), node))


def _render_default(value: Any) -> str:
    if value is None:
        rendered = "null"
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, str):
        rendered = json.dumps(value.replace("\n", " ").strip()) if value else '""'
    else:
        rendered = json.dumps(value)
    if len(rendered) > 80:
        rendered = rendered[:77] + "..."
    return f"`{rendered}`"


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    return "dict"


def generate_config_reference() -> str:
    """Render the full generated reference document as markdown."""
    sections = [_HEADER]
    for title, relative_path, prefix in _GROUPS:
        payload = OmegaConf.to_container(OmegaConf.load(_conf_dir() / relative_path), resolve=False)
        rows: list[tuple[str, Any]] = []
        _flatten(payload if isinstance(payload, dict) else {}, prefix, rows)
        lines = [
            f"## {title} (`{relative_path}`)",
            "",
            "| Key | Default | Type |",
            "|-----|---------|------|",
        ]
        lines.extend(
            f"| `{key}` | {_render_default(value)} | {_type_name(value)} |" for key, value in rows
        )
        sections.append("\n".join(lines) + "\n")
    return "\n".join(sections)


def reference_path(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[4]
    return root / "docs" / "config_reference.md"


def main() -> int:
    """Write the generated reference to docs/config_reference.md."""
    target = reference_path()
    target.write_text(generate_config_reference(), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
