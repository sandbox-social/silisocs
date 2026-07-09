"""``silisocs doctor`` — environment health checks before an expensive run.

Reports Python/package versions, which optional extras are importable, which
provider API keys are set (never their values), and output-directory
writability. Missing extras and keys are warnings — only broken required
checks (Python version, writable output) fail the command.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from silisocs.runtime.language_models.factory import OPENAI_COMPATIBLE_PRESETS

_MIN_PYTHON = (3, 11)

# extra name -> (import names to probe, what the extra unlocks)
_EXTRAS = {
    "dashboard": (("streamlit",), "Streamlit scenario builder (silisocs-dashboard)"),
    "analysis": (("dash", "plotly"), "Dash analytics app (silisocs-analysis-dashboard)"),
    "viz": (("fastapi", "uvicorn"), "backend state visualizers"),
    "mastodon": (("mastodon",), "live Mastodon backend"),
    "concordia": (("concordia",), "Concordia compatibility bridge"),
}


def _importable(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _provider_key_envs() -> dict[str, str]:
    """Map each provider to its API-key env var (from the OpenAI-compatible presets)."""
    envs = {"openai": "OPENAI_API_KEY"}
    for provider, (_base, key_env) in sorted(OPENAI_COMPATIBLE_PRESETS.items()):
        if key_env:
            envs[provider] = key_env
    return envs


def run_doctor(output_dir: str | Path | None = None) -> int:
    """Print environment health checks; return 0 unless a required check fails."""
    failures = 0

    def _line(ok: bool | None, text: str) -> None:
        symbol = "✓" if ok else ("!" if ok is None else "✗")
        print(f"  {symbol} {text}")

    print("silisocs doctor\n")

    print("Core:")
    python_ok = sys.version_info >= _MIN_PYTHON
    _line(
        python_ok,
        f"Python {sys.version.split()[0]} (requires >= {'.'.join(map(str, _MIN_PYTHON))})",
    )
    failures += 0 if python_ok else 1
    try:
        version = importlib.metadata.version("silisocs")
        _line(True, f"silisocs {version}")
    except Exception:
        _line(None, "silisocs version unknown (not installed as a package?)")

    print("\nOptional extras:")
    for extra, (modules, unlocks) in _EXTRAS.items():
        installed = all(_importable(module) for module in modules)
        if installed:
            _line(True, f"{extra}: installed — {unlocks}")
        else:
            _line(None, f'{extra}: not installed — {unlocks}. pip install "silisocs[{extra}]"')

    print("\nModel provider API keys (set/unset only; values never shown):")
    any_key = False
    for provider, env_name in _provider_key_envs().items():
        is_set = bool(os.environ.get(env_name))
        any_key = any_key or is_set
        _line(True if is_set else None, f"{provider}: {env_name} {'set' if is_set else 'unset'}")
    if not any_key:
        print(
            "  ! no provider key set — runs need one, or sim.llm.disabled=true / a local api_base"
        )

    print("\nOutput directory:")
    target = Path(output_dir) if output_dir else Path.cwd() / "outputs"
    probe_dir = (
        target if target.is_dir() else target.parent if target.parent.is_dir() else Path.cwd()
    )
    try:
        with tempfile.NamedTemporaryFile(dir=probe_dir, prefix=".silisocs-doctor-"):
            pass
        _line(True, f"writable: {probe_dir}")
    except OSError as error:
        _line(False, f"not writable: {probe_dir} ({error})")
        failures += 1

    print("\nNext steps:")
    print("  - validate a config without running: silisocs-config-dry-run")
    print("  - launch the workbench: silisocs-dashboard")

    if failures:
        print(f"\n{failures} required check(s) failed.")
    else:
        print("\nAll required checks passed.")
    return 1 if failures else 0
