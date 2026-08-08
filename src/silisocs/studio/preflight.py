"""Validate composer documents and estimate a run's scale before launching it.

This is the one composer module that imports the engine at module scope: the
scale estimate is read off the REAL turn/participation policies, so it cannot
drift from scheduling behavior. That import is why Studio's warm-up thread
pre-loads the composer instead of paying for it on a page load.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import yaml

from silisocs.simulation_engines.policies.factory import (
    build_participation_policy,
    build_turn_policy,
)
from silisocs.studio.compose import _group_file


def _policy_estimate(
    builder: Callable[[Any], Any],
    slot: Any,
    method_name: str,
    *,
    path: str,
    findings: list[dict[str, str]],
    default: float = 1.0,
) -> float:
    """Build the real runtime policy for a slot and ask it for its estimate.

    Estimate semantics live on the policy classes themselves
    (``expected_active_share`` / ``expected_actions_per_turn``), so preflight
    can never drift from scheduling behavior. A slot that fails to build is an
    error for built-ins (the run would fail at startup the same way) but only
    a warning for a custom ``class_path`` (its import may need the run
    environment); a policy without the estimate hook contributes the
    conservative default — one action by every agent.
    """
    try:
        policy = builder(slot or {})
    except Exception as exc:
        findings.append(
            {
                "severity": "warning" if (slot or {}).get("class_path") else "error",
                "path": path,
                "message": f"Could not build this policy for the estimate: {exc}",
            }
        )
        return default
    method = getattr(policy, method_name, None)
    if not callable(method):
        return default
    try:
        return float(method())
    except Exception as exc:
        # A built-in whose estimate blows up (e.g. count: "abc" — dataclasses
        # don't validate) would crash the run at its first scheduling call the
        # same way, so it is an error; only a custom class_path degrades.
        findings.append(
            {
                "severity": "warning" if (slot or {}).get("class_path") else "error",
                "path": path,
                "message": f"The policy's {method_name}() estimate failed: {exc}",
            }
        )
        return default


def preflight_payload(files: dict[str, str]) -> dict[str, Any]:
    """Validate composer documents and estimate scale without executing a run."""
    findings: list[dict[str, str]] = []
    parsed: dict[str, dict[str, Any]] = {}
    for relative, text in files.items():
        try:
            value = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            findings.append({"severity": "error", "path": relative, "message": str(exc)})
            continue
        if not isinstance(value, dict):
            findings.append(
                {"severity": "error", "path": relative, "message": "Expected a YAML mapping"}
            )
            continue
        parsed[relative] = value

    # Scenarios may ship world/<name>.yaml (resource_market, virtual_space) instead
    # of the default variant, so resolve the actual group files rather than assume
    # the default names — otherwise num_agents/num_steps read 0 and backend checks
    # are skipped for those scenarios.
    names = list(files)
    world = parsed.get(_group_file("world", names) or "world/default.yaml", {})
    sim = parsed.get(_group_file("sim", names) or "sim.yaml", {})
    env = parsed.get(_group_file("env", names) or "env.yaml", {})

    def integer(value: Any, path: str, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            findings.append({"severity": "error", "path": path, "message": "Must be an integer"})
            return default

    def number(value: Any, path: str, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            findings.append({"severity": "error", "path": path, "message": "Must be numeric"})
            return default

    def section(value: Any, path: str) -> dict[str, Any]:
        """Read a config section as a mapping, reporting a scalar instead of crashing.

        A section typed as a scalar (``llm: gpt-4o-mini``) is the most ordinary
        YAML typo there is, and preflight exists to report shape mistakes —
        reading one as an attribute error failed the whole report.
        """
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        findings.append(
            {
                "severity": "error",
                "path": path,
                "message": f"Expected a YAML mapping, got {type(value).__name__}",
            }
        )
        return {}

    agents = integer(world.get("num_agents", 0), "world.num_agents")
    steps = integer(world.get("num_steps", 0), "world.num_steps")
    if agents <= 0:
        findings.append(
            {"severity": "error", "path": "world.num_agents", "message": "Must be positive"}
        )
    if steps <= 0:
        findings.append(
            {"severity": "error", "path": "world.num_steps", "message": "Must be positive"}
        )
    backend = section(section(env.get("gm"), "env.gm").get("backend"), "env.gm.backend")
    if backend:
        from silisocs.environments.backends.factory import resolve_backend_class  # noqa: PLC0415

        try:
            backend_class = resolve_backend_class(
                str(backend.get("type") or ""),
                class_path=str(backend.get("class_path") or "") or None,
            )
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            findings.append({"severity": "error", "path": "env.gm.backend", "message": str(exc)})
        else:
            known_actions = {
                name
                for action in backend_class.declared_action_catalog()
                for name in (action["name"], action["selectable_name"])
            }
            configured_actions = {
                str(name)
                for key in ("enabled_actions", "excluded_actions")
                for name in (backend.get(key) or [])
            }
            unknown_actions = sorted(configured_actions - known_actions)
            if unknown_actions:
                findings.append(
                    {
                        "severity": "error",
                        "path": "env.gm.backend.enabled_actions",
                        "message": f"Unknown backend actions: {', '.join(unknown_actions)}",
                    }
                )

    llm = section(sim.get("llm"), "sim.llm")
    provider = str(llm.get("provider") or "")
    if provider and not llm.get("disabled") and not llm.get("api_key"):
        from silisocs.runtime.language_models.catalog import (  # noqa: PLC0415
            OPENAI_COMPATIBLE_PRESETS,
        )

        key_env = (
            "OPENAI_API_KEY"
            if provider == "openai"
            else OPENAI_COMPATIBLE_PRESETS.get(provider, ("", None))[1]
        )
        if key_env and not os.environ.get(key_env):
            findings.append(
                {
                    "severity": "warning",
                    "path": "sim.llm.provider",
                    "message": f"{key_env} is not set in the Studio process",
                }
            )

    engine = section(sim.get("engine"), "sim.engine")
    actions_per_turn = _policy_estimate(
        build_turn_policy,
        section(engine.get("turn_policy"), "sim.engine.turn_policy"),
        "expected_actions_per_turn",
        path="sim.engine.turn_policy",
        findings=findings,
    )
    active_fraction = _policy_estimate(
        build_participation_policy,
        section(engine.get("participation"), "sim.engine.participation"),
        "expected_active_share",
        path="sim.engine.participation",
        findings=findings,
    )
    active_fraction = min(1.0, max(0.0, active_fraction))
    calls = round(agents * steps * active_fraction * actions_per_turn)
    estimate_cfg = section(sim.get("preflight"), "sim.preflight")
    prompt_tokens = integer(
        estimate_cfg.get("prompt_tokens_per_call", 1200),
        "sim.preflight.prompt_tokens_per_call",
        1200,
    )
    completion_tokens = integer(
        estimate_cfg.get("completion_tokens_per_call", 180),
        "sim.preflight.completion_tokens_per_call",
        180,
    )
    pricing = section(llm.get("pricing"), "sim.llm.pricing")
    input_price = number(pricing.get("input_per_1m", 0), "sim.llm.pricing.input_per_1m", 0.0)
    output_price = number(pricing.get("output_per_1m", 0), "sim.llm.pricing.output_per_1m", 0.0)
    estimated_cost = (
        calls * prompt_tokens / 1_000_000 * input_price
        + calls * completion_tokens / 1_000_000 * output_price
    )
    return {
        "ok": not any(item["severity"] == "error" for item in findings),
        "findings": findings,
        "estimate": {
            "agent_steps": round(agents * steps * active_fraction),
            "actions": calls,
            "llm_calls": calls,
            "prompt_tokens": calls * prompt_tokens,
            "completion_tokens": calls * completion_tokens,
            "cost_usd": round(estimated_cost, 4) if pricing else None,
        },
    }
