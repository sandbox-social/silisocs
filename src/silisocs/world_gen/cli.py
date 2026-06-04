"""cli module. Auto-generated module docstring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from silisocs.world_gen.specs import StudySpec, WorldSpec
from silisocs.world_gen.validator import validate_study, validate_world
from silisocs.world_gen.writer import write_study, write_world

app = typer.Typer(help="World and study generation commands.")


@app.command("new-world")
def cmd_new_world(
    from_spec_json: str = typer.Option(
        ...,
        "--from-spec-json",
        help="JSON string matching WorldSpec schema.",
    ),
    output_dir: str = typer.Option(
        "worlds",
        "--output-dir",
        help="Root directory where worlds/<name>/ will be written.",
    ),
) -> None:
    """Write world config files from a validated WorldSpec JSON blob."""
    try:
        raw = json.loads(from_spec_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"Error: invalid JSON — {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        spec = WorldSpec.model_validate(raw)
    except Exception as exc:
        typer.echo(f"Error: spec validation failed — {exc}", err=True)
        raise typer.Exit(1) from exc

    root = Path(output_dir) / spec.name
    if root.exists():
        typer.echo(
            f"Error: {root} already exists. Remove it first or choose a different name.", err=True
        )
        raise typer.Exit(1)

    typer.echo(f"Writing world files to {root}/")
    write_world(spec, root)
    typer.echo("  conf/world/default.yaml")
    typer.echo("  conf/agents/default.yaml")
    typer.echo("  conf/env.yaml")
    typer.echo("  conf/eval.yaml")
    typer.echo("  conf/sim.yaml")

    typer.echo("\nValidating config (dry run)...")
    result = validate_world(root)
    typer.echo(str(result))
    if not result.ok:
        raise typer.Exit(1)


@app.command("new-study")
def cmd_new_study(
    from_spec_json: str = typer.Option(
        ...,
        "--from-spec-json",
        help="JSON string matching StudySpec schema.",
    ),
    output_dir: str = typer.Option(
        "experiments/studies",
        "--output-dir",
        help="Root directory where <study_id>/ will be written.",
    ),
) -> None:
    """Write study.yaml from a validated StudySpec JSON blob."""
    try:
        raw = json.loads(from_spec_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"Error: invalid JSON — {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        spec = StudySpec.model_validate(raw)
    except Exception as exc:
        typer.echo(f"Error: spec validation failed — {exc}", err=True)
        raise typer.Exit(1) from exc

    root = Path(output_dir) / spec.study_id
    if root.exists():
        typer.echo(
            f"Error: {root} already exists. Remove it first or choose a different name.", err=True
        )
        raise typer.Exit(1)

    typer.echo(f"Writing study files to {root}/")
    write_study(spec, root)
    typer.echo("  study.yaml")

    typer.echo("\nValidating study plan...")
    result = validate_study(root)
    typer.echo(str(result))
    if not result.ok:
        raise typer.Exit(1)


def world_gen_cli() -> None:
    """Entry point dispatched from runner.cli_main when argv[1] is a gen command."""
    # Strip the subcommand name from argv so Typer sees: new-world --from-spec-json ...
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    app()
