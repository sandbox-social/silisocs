"""Dry-run utility for validating external scenario and replication configs."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class DryRunTarget:
    """One runnable config target."""

    label: str
    config_path: Path
    world_variant: str
    extra_overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class DryRunResult:
    """Result of one config dry run."""

    target: DryRunTarget
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    command: tuple[str, ...]
    skipped: bool = False
    skip_reason: str = ""


def _discover_config_root_targets(
    *,
    conf_dir: Path,
    label_root: str,
) -> list[DryRunTarget]:
    """Discover dry-run targets from one config root containing world/env groups."""
    candidates: list[DryRunTarget] = []
    if not conf_dir.is_dir():
        return candidates
    for world_file in sorted((conf_dir / "world").glob("*.yaml")):
        candidates.append(
            DryRunTarget(
                label=f"{label_root}/{world_file.stem}",
                config_path=conf_dir,
                world_variant=world_file.stem,
            )
        )
    for env_file in sorted((conf_dir / "env").glob("*.yaml")):
        world_dir = conf_dir / "world"
        default_world = world_dir / "default.yaml"
        matching_world = world_dir / f"{env_file.stem}.yaml"
        if matching_world.is_file():
            world_variant = env_file.stem
        elif default_world.is_file():
            world_variant = "default"
        else:
            continue
        candidates.append(
            DryRunTarget(
                label=f"{label_root}/{world_variant} [env={env_file.stem}]",
                config_path=conf_dir,
                world_variant=world_variant,
                extra_overrides=(f"env={env_file.stem}",),
            )
        )
    return candidates


def _discover_targets(project_root: Path) -> list[DryRunTarget]:
    """Discover dry-run targets under packaged, scenario, and replication configs."""
    candidates: list[DryRunTarget] = []
    candidates.extend(
        _discover_config_root_targets(
            conf_dir=project_root / "src" / "silisocs" / "conf",
            label_root="packaged",
        )
    )
    roots = [
        project_root / "scenarios",
        project_root / "replications",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for world_file in sorted(root.glob("**/conf/world/*.yaml")):
            conf_dir = world_file.parents[1]
            label = str(conf_dir.parent.relative_to(project_root))
            candidates.append(
                DryRunTarget(
                    label=label,
                    config_path=conf_dir,
                    world_variant=world_file.stem,
                )
            )
        for env_file in sorted(root.glob("**/conf/env/*.yaml")):
            conf_dir = env_file.parents[1]
            world_dir = conf_dir / "world"
            default_world = world_dir / "default.yaml"
            matching_world = world_dir / f"{env_file.stem}.yaml"
            if matching_world.is_file():
                world_variant = env_file.stem
            elif default_world.is_file():
                world_variant = "default"
            else:
                continue
            label = str(conf_dir.parent.relative_to(project_root))
            candidates.append(
                DryRunTarget(
                    label=f"{label} [env={env_file.stem}]",
                    config_path=conf_dir,
                    world_variant=world_variant,
                    extra_overrides=(f"env={env_file.stem}",),
                )
            )
    return candidates


def _build_command(
    target: DryRunTarget,
    output_dir: Path,
    hydra_dir: Path,
) -> list[str]:
    """Build the runtime command for one target."""
    command = [
        sys.executable,
        "-m",
        "silisocs.runtime.runner",
        "--config-path",
        str(target.config_path),
        f"world={target.world_variant}",
    ]
    agents_variant_file = target.config_path / "agents" / f"{target.world_variant}.yaml"
    if agents_variant_file.is_file():
        command.append(f"agents={target.world_variant}")
    env_variant_file = target.config_path / "env" / f"{target.world_variant}.yaml"
    if env_variant_file.is_file():
        command.append(f"env={target.world_variant}")
    existing_overrides = set(command)
    command.extend(
        override for override in target.extra_overrides if override not in existing_overrides
    )
    command.extend(
        [
            # `scripted`, never `sim.llm.disabled`: the no-op model cannot answer
            # a tool-call spec, which the config validator now rejects outright
            # under the packaged `sim.tool_calling.mode: single`.
            "++sim.llm.provider=scripted",
            "++sim.llm.name=scripted",
            # `extra_kwargs` are provider-specific (a real HTTP provider's
            # `extra_body`, say); forcing `scripted` would make the scenario's own
            # kwargs invalid constructor params for a provider it never chose.
            "++sim.llm.extra_kwargs=null",
            "++num_steps=0",
            f"++output_dir={output_dir}",
            f"hydra.run.dir={hydra_dir}",
            "hydra.output_subdir=configs",
        ]
    )
    return command


def _run_target(project_root: Path, target: DryRunTarget, run_root: Path) -> DryRunResult:
    """Run one config target and collect outputs."""
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", target.label.replace("/", "__")).strip("_")
    run_dir = run_root / f"{safe_label}__{target.world_variant}"
    output_dir = run_dir / "outputs"
    hydra_dir = run_dir / "hydra"
    output_dir.mkdir(parents=True, exist_ok=True)
    hydra_dir.mkdir(parents=True, exist_ok=True)
    command = _build_command(target, output_dir=output_dir, hydra_dir=hydra_dir)
    result = subprocess.run(
        command,
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    stderr = result.stderr or ""
    stdout = result.stdout or ""
    skip_reason = ""
    if result.returncode != 0:
        combined = f"{stderr}\n{stdout}"
        if "No module named 'datasets'" in combined:
            skip_reason = "missing optional dependency: datasets (hf extra)"
        elif (
            "Install Concordia compatibility" in combined
            or "No module named 'concordia'" in combined
        ):
            skip_reason = "missing optional dependency: concordia extra"
    return DryRunResult(
        target=target,
        ok=result.returncode == 0 or bool(skip_reason),
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
        command=tuple(command),
        skipped=bool(skip_reason),
        skip_reason=skip_reason,
    )


def _single_config_targets(conf_dir: Path) -> list[DryRunTarget]:
    """Discover the dry-run targets of ONE scenario config directory.

    Same shape as the discovery path (`_discover_config_root_targets`), so a
    single scenario is validated by exactly the checks the whole-repo sweep runs.
    """
    return _discover_config_root_targets(conf_dir=conf_dir, label_root=conf_dir.parent.name)


def run_dry_runs(
    project_root: Path,
    *,
    config_path: Path | None = None,
) -> list[DryRunResult]:
    """Run dry-run validation for external configs.

    ``config_path`` restricts the sweep to one scenario config directory (the
    authoring loop); omitted, every scenario and replication under
    ``project_root`` is discovered and checked.
    """
    targets = (
        _single_config_targets(config_path)
        if config_path is not None
        else _discover_targets(project_root)
    )
    if not targets:
        return []
    with TemporaryDirectory(prefix="silisocs_config_dry_run_") as tmpdir:
        run_root = Path(tmpdir)
        return [_run_target(project_root, target, run_root) for target in targets]


def _format_failure(result: DryRunResult, max_lines: int = 40) -> str:
    """Render a concise failure report."""
    output = result.stderr.strip() or result.stdout.strip()
    lines = output.splitlines()[-max_lines:]
    tail = "\n".join(lines)
    return (
        f"[FAIL] {result.target.label} world={result.target.world_variant}\n"
        f"Command: {' '.join(result.command)}\n"
        f"Exit code: {result.returncode}\n"
        f"{tail}"
    )


def _resolve_config_path(raw: str) -> Path:
    """Resolve a ``--config-path`` argument to the directory holding ``world/``.

    Accepts either the config dir itself (``scenarios/election/conf``) or the
    scenario root (``scenarios/election``), because both are what users have in
    hand while authoring.
    """
    directory = Path(raw).resolve()
    if (directory / "world").is_dir():
        return directory
    nested = directory / "conf"
    if (nested / "world").is_dir():
        return nested
    return directory


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for config dry-run checks."""
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run scenario/replication configs through the real runtime runner "
            "(build the runtime, run zero steps) and report failures. Checks one "
            "config directory with --config-path, or every scenario and replication "
            "under a checkout with --project-root."
        )
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root containing scenarios/ and replications/ (default: current dir).",
    )
    parser.add_argument(
        "--config-path",
        default=None,
        help=(
            "Validate exactly ONE scenario config directory (e.g. "
            "scenarios/election/conf, or the scenario root containing it) instead of "
            "discovering every config under --project-root."
        ),
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    config_path = _resolve_config_path(args.config_path) if args.config_path else None
    if config_path is not None and not config_path.is_dir():
        print(
            f"--config-path '{args.config_path}' is not a directory. Point it at a "
            "scenario config directory containing world/*.yaml (e.g. "
            "scenarios/election/conf)."
        )
        return 1

    results = run_dry_runs(project_root, config_path=config_path)
    if not results:
        if config_path is not None:
            print(
                f"No config targets found in {config_path}: a scenario config "
                "directory must contain world/*.yaml (and optionally env/*.yaml). "
                "Point --config-path at <scenario>/conf."
            )
        else:
            print(
                f"No scenario/replication config targets discovered under {project_root}. "
                "Run this from a silisocs checkout, pass --project-root <checkout>, or "
                "validate a single scenario with --config-path <scenario>/conf."
            )
        return 1

    passed = sum(1 for r in results if r.ok and not r.skipped)
    skipped = [r for r in results if r.skipped]
    failed = [r for r in results if not r.ok]
    print(
        f"Dry-run summary: {passed} passed, {len(skipped)} skipped, "
        f"{len(failed)} failed (total={len(results)})"
    )
    if skipped:
        print("\nSkipped targets:")
        for item in skipped:
            print(f"- {item.target.label} world={item.target.world_variant}: {item.skip_reason}")
    if failed:
        print()
        for failure in failed:
            print(_format_failure(failure))
            print()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
