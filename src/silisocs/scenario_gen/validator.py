from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ValidationResult:
    ok: bool
    stdout: str
    stderr: str

    def __str__(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [f"[{status}]"]
        if self.stdout.strip():
            lines.append(self.stdout.strip())
        if self.stderr.strip():
            lines.append(self.stderr.strip())
        return "\n".join(lines)


def validate_scenario(scenario_root: Path | str) -> ValidationResult:
    """Dry-run a scenario config to confirm it loads and resolves without errors."""
    conf_path = Path(scenario_root) / "conf"
    result = subprocess.run(
        [
            "uv",
            "run",
            "silisocs",
            "--config-path",
            str(conf_path),
            "num_steps=1",
            "sim.llm.disabled=true",
        ],
        capture_output=True,
        text=True,
    )
    return ValidationResult(
        ok=result.returncode == 0,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def validate_study(study_root: Path | str) -> ValidationResult:
    """Expand a study plan to confirm all conditions and overrides resolve."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "experiments.run_study",
            "--study",
            str(study_root),
            "plan",
        ],
        capture_output=True,
        text=True,
    )
    return ValidationResult(
        ok=result.returncode == 0,
        stdout=result.stdout,
        stderr=result.stderr,
    )
