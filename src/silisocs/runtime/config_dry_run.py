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
    scenario_variant: str
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


def _discover_targets(project_root: Path) -> list[DryRunTarget]:
    """Discover dry-run targets under `scenarios/` and `replications/`."""
    candidates: list[DryRunTarget] = []
    roots = [
        project_root / "scenarios",
        project_root / "replications",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for scenario_file in sorted(root.glob("**/conf/scenario/*.yaml")):
            conf_dir = scenario_file.parents[1]
            label = str(conf_dir.parent.relative_to(project_root))
            candidates.append(
                DryRunTarget(
                    label=label,
                    config_path=conf_dir,
                    scenario_variant=scenario_file.stem,
                )
            )
        for env_file in sorted(root.glob("**/conf/env/*.yaml")):
            conf_dir = env_file.parents[1]
            scenario_dir = conf_dir / "scenario"
            default_scenario = scenario_dir / "default.yaml"
            matching_scenario = scenario_dir / f"{env_file.stem}.yaml"
            if matching_scenario.is_file():
                scenario_variant = env_file.stem
            elif default_scenario.is_file():
                scenario_variant = "default"
            else:
                continue
            label = str(conf_dir.parent.relative_to(project_root))
            candidates.append(
                DryRunTarget(
                    label=f"{label} [env={env_file.stem}]",
                    config_path=conf_dir,
                    scenario_variant=scenario_variant,
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
        f"scenario={target.scenario_variant}",
    ]
    agents_variant_file = target.config_path / "agents" / f"{target.scenario_variant}.yaml"
    if agents_variant_file.is_file():
        command.append(f"agents={target.scenario_variant}")
    env_variant_file = target.config_path / "env" / f"{target.scenario_variant}.yaml"
    if env_variant_file.is_file():
        command.append(f"env={target.scenario_variant}")
    existing_overrides = set(command)
    command.extend(
        override for override in target.extra_overrides if override not in existing_overrides
    )
    command.extend(
        [
            "++sim.llm.provider=scripted",
            "++sim.llm.name=scripted",
            "++num_steps=0",
            f"++output_rootname={output_dir}",
            f"hydra.run.dir={hydra_dir}",
            "hydra.output_subdir=configs",
        ]
    )
    return command


def _run_target(project_root: Path, target: DryRunTarget, run_root: Path) -> DryRunResult:
    """Run one config target and collect outputs."""
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", target.label.replace("/", "__")).strip("_")
    run_dir = run_root / f"{safe_label}__{target.scenario_variant}"
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


def run_dry_runs(project_root: Path) -> list[DryRunResult]:
    """Run dry-run validation for all discovered external configs."""
    targets = _discover_targets(project_root)
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
        f"[FAIL] {result.target.label} scenario={result.target.scenario_variant}\n"
        f"Command: {' '.join(result.command)}\n"
        f"Exit code: {result.returncode}\n"
        f"{tail}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for config dry-run checks."""
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run all shipped scenario/replication configs through the real runtime "
            "runner and report failures."
        )
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root containing scenarios/ and replications/ (default: current dir).",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    results = run_dry_runs(project_root)
    if not results:
        print("No scenario/replication config targets discovered.")
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
            print(
                f"- {item.target.label} scenario={item.target.scenario_variant}: {item.skip_reason}"
            )
    if failed:
        print()
        for failure in failed:
            print(_format_failure(failure))
            print()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
