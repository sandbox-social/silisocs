#!/usr/bin/env python3
"""Thin wrapper translating study-runner CLI conventions to this codebase's runner.

run_study.py calls this as:
    uv run python run_experiment.py scenario=NAME model=gpt4omini simulation.execution.max_steps=10

This translates to:
    python -m silisocs.runtime.runner \\
        --config-path scenarios/NAME/conf \\
        scenario=NAME \\
        sim.llm.name=gpt-4o-mini \\
        num_steps=10 \\
        sim.checkpoint.explicit_steps=[10] \\
        hydra.run.dir=outputs/NAME_experiment/TIMESTAMP

The output dir is redirected to outputs/{scenario}_experiment/{timestamp}/ so that
run_study.py's find_latest_output_dir() can locate completed runs.

Checkpoints are enabled at the final step so eval.py can find
checkpoints/step_N_checkpoint.json.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

# Map study-convention model names to current sim.llm.name values.
MODEL_MAP: dict[str, str] = {
    "gpt4omini": "gpt-4o-mini",
    "gpt4o": "gpt-4o",
    "gpt4": "gpt-4",
    "gpt4turbo": "gpt-4-turbo",
    "claude": "claude-sonnet-4-6",
    "claude3": "claude-3-5-sonnet-20241022",
    "o1mini": "o1-mini",
    "o1": "o1",
}


def main() -> None:
    raw_args = sys.argv[1:]

    scenario: str | None = None
    llm_name: str | None = None
    disable_lm: bool = False
    num_steps: str | None = None
    temperature: str | None = None
    passthrough: list[str] = []

    for arg in raw_args:
        if arg.startswith("scenario="):
            scenario = arg.split("=", 1)[1]
        elif arg.startswith("model="):
            model_key = arg.split("=", 1)[1]
            if model_key == "mock":
                disable_lm = True
            else:
                llm_name = MODEL_MAP.get(model_key, model_key)
        elif arg.startswith("simulation.execution.max_steps="):
            num_steps = arg.split("=", 1)[1]
        elif arg.startswith("temperature="):
            temperature = arg.split("=", 1)[1]
        else:
            # Forward any unrecognised args (e.g. sim.seed=42) directly to the runner.
            passthrough.append(arg)

    if not scenario:
        print("Error: scenario=NAME is required", file=sys.stderr)
        sys.exit(1)

    config_path = PROJECT_ROOT / "scenarios" / scenario / "conf"
    if not config_path.is_dir():
        print(f"Error: scenario config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = f"outputs/{scenario}_experiment/{timestamp}"

    cmd: list[str] = [
        "python",
        "-m",
        "silisocs.runtime.runner",
        "--config-path",
        str(config_path),
        f"scenario={scenario}",
        # Redirect Hydra output so run_study.py's find_latest_output_dir() can find it.
        f"hydra.run.dir={output_dir}",
        "hydra.output_subdir=.hydra",
    ]

    if llm_name:
        cmd.append(f"sim.llm.name={llm_name}")
    if disable_lm:
        cmd.append("sim.llm.disabled=true")
    if num_steps:
        cmd.append(f"num_steps={num_steps}")
        cmd.append(f"sim.checkpoint.explicit_steps=[{num_steps}]")
    if temperature:
        cmd.append(f"sim.llm.temperature={temperature}")

    cmd.extend(passthrough)

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
