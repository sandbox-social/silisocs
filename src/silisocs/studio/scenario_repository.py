"""Filesystem-backed scenario documents and the config-group file layout."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from silisocs.studio.save_conflicts import check_fingerprint, fingerprint_text

SCENARIO_FILES = (
    "world/default.yaml",
    "agents/default.yaml",
    "sim.yaml",
    "env.yaml",
    "eval.yaml",
)

# Config-group directories a scenario may ship. A scenario can name its group
# files after itself (world/resource_market.yaml, selected with world=...) rather
# than using the default variant above, so listing/editing globs these too — a
# scenario is not only the shape SCENARIO_FILES describes.
_GROUP_DIRS = ("world", "agents", "env", "sim", "eval", "views")

# Hydra package directives each config-group document must open with. Scenario
# world/agents files shadow their base group, so without the directive their
# keys land under the group name instead of the intended package and the run
# silently loses num_agents/num_steps/seed (world) or the whole persona
# pipeline (agents). The flat sim/env/eval files are merged directly and carry
# no directive.
_PACKAGE_DIRECTIVES = {
    "world": "# @package _global_",
    "agents": "# @package agents",
}


def leading_comment_block(text: str) -> str:
    """Return the comment/blank prefix of a YAML document (directives live here)."""
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line.lstrip().startswith("#"):
            break
        kept.append(line)
    return "".join(kept)


def ensure_package_directive(relative: str, text: str) -> str:
    """Prepend the required ``# @package`` directive when a document lacks one."""
    parts = Path(relative).parts
    directive = _PACKAGE_DIRECTIVES.get(parts[0]) if len(parts) == 2 else None
    if directive is None or "# @package" in leading_comment_block(text):
        return text
    return f"{directive}\n{text}"


def scenario_relative_files(conf_dir: Path) -> list[str]:
    files = [relative for relative in SCENARIO_FILES if (conf_dir / relative).is_file()]
    seen = set(files)
    for group in _GROUP_DIRS:
        files.extend(
            relative
            for path in sorted((conf_dir / group).glob("*.yaml"))
            if path.is_file() and (relative := path.relative_to(conf_dir).as_posix()) not in seen
        )
    return files


def supported_scenario_file(relative: str) -> bool:
    path = Path(relative)
    return relative in SCENARIO_FILES or (
        len(path.parts) == 2 and path.parts[0] in _GROUP_DIRS and path.suffix == ".yaml"
    )


class ScenarioRepository:
    """Read and write scenario YAML without discarding unknown fields."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def validate_name(name: str) -> str:
        clean = str(name).strip()
        if not clean or any(part in clean for part in ("/", "\\", "..")):
            raise ValueError("Scenario name must be a safe directory name")
        return clean

    @staticmethod
    def default_files(name: str) -> dict[str, str]:
        """Return the starter YAML file set for a brand-new scenario draft."""
        from silisocs.environments.backends.factory import registered_backend_types

        backend_types = registered_backend_types()
        default_backend = backend_types[0] if backend_types else "custom"
        return {
            "world/default.yaml": "# @package _global_\n"
            + yaml.safe_dump(
                {
                    "scenario_name": name,
                    "num_agents": 4,
                    "num_steps": 5,
                    "seed": 1,
                    "setting": {"name": "", "background": []},
                    "event": {"name": "", "context": ""},
                },
                sort_keys=False,
            ),
            "agents/default.yaml": (
                "# @package agents\nshared_memories: []\npersona_pipeline:\n  classes: {}\n"
            ),
            "sim.yaml": "{}\n",
            "env.yaml": yaml.safe_dump(
                {"gm": {"backend": {"type": default_backend}}}, sort_keys=False
            ),
            "eval.yaml": "probes:\n  enabled: false\n",
        }

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        return [
            {
                "name": path.name,
                "path": str(path),
                "files": scenario_relative_files(path / "conf"),
            }
            for path in sorted(self.root.iterdir())
            if path.is_dir() and (path / "conf").is_dir()
        ]

    def count(self) -> int:
        """Count scenario config roots without loading their documents."""
        if not self.root.is_dir():
            return 0
        return sum(1 for path in self.root.iterdir() if (path / "conf").is_dir())

    def load(self, name: str, *, parse: bool = True) -> dict[str, Any]:
        name = self.validate_name(name)
        scenario_dir = self.root / name
        if not scenario_dir.is_dir():
            raise KeyError(name)
        files: dict[str, Any] = {}
        for relative in scenario_relative_files(scenario_dir / "conf"):
            path = scenario_dir / "conf" / relative
            if not path.is_file():
                continue
            # One read serves text and fingerprint (UTF-8 en/decoding round-trips,
            # so hashing the decoded text equals hashing the raw bytes).
            text = path.read_bytes().decode("utf-8")
            files[relative] = {
                "text": text,
                "data": yaml.safe_load(text) if parse else None,
                "fingerprint": fingerprint_text(text),
            }
        return {
            "name": name,
            "path": str(scenario_dir),
            "files": files,
            # The set an editor sends back on save; see save_conflicts.py.
            "fingerprints": {relative: item["fingerprint"] for relative, item in files.items()},
        }

    def save(
        self,
        name: str,
        files: dict[str, str],
        *,
        fingerprints: dict[str, str] | None = None,
        baselines: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        name = self.validate_name(name)
        unknown = sorted(relative for relative in files if not supported_scenario_file(relative))
        if unknown:
            raise ValueError(f"Unsupported scenario files: {unknown}")
        validated: dict[str, str] = {}
        for relative, text in files.items():
            data = yaml.safe_load(text) if text.strip() else {}
            if not isinstance(data, dict):
                raise ValueError(f"{relative} must contain a YAML mapping")
            # Write the author's text verbatim: re-dumping parsed YAML would
            # strip comments — including the # @package directives Hydra needs.
            validated[relative] = ensure_package_directive(relative, text)
        # Check every document before writing any: a conflicting save leaves the
        # scenario exactly as it was, never half-applied.
        for relative in validated:
            check_fingerprint(
                relative,
                self.root / name / "conf" / relative,
                (fingerprints or {}).get(relative),
                submitted=files[relative],
                baseline=(baselines or {}).get(relative),
            )
        for relative, text in validated.items():
            path = self.root / name / "conf" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return self.load(name)
