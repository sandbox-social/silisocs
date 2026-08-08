"""Shared resolution of a study's run matrix (scenarios x seeds).

Two callers must enumerate the *same* matrix or Studio lies about progress:
:mod:`silisocs.studies.plan` expands a ``study.yaml`` into the runs the runner
actually launches, and
:attr:`silisocs.evaluations.run_artifact.StudyArtifact.progress` projects that
same matrix onto on-disk completion markers for the progress board. This module
is the one implementation both use. Import-linter places ``evaluations`` below
``studies``, so this is the lowest layer they can share.

The callers differ only in failure mode, which is the ``error`` parameter:

* pass an exception factory (the planner passes ``StudyConfigError``) to
  validate strictly — a malformed study must refuse to run;
* omit it to resolve tolerantly (``None`` / empty) — the progress board displays
  studies it does not own and must not crash on one it cannot model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

ErrorFactory = Callable[[str], Exception]


def _unresolvable(error: ErrorFactory | None, message: str) -> list[int] | None:
    """Raise ``error(message)`` in strict mode; report "cannot resolve" otherwise."""
    if error is not None:
        raise error(message)
    return None


def string_list(name: str, value: Any, error: ErrorFactory | None = None) -> list[str]:
    """Normalise ``value`` to ``list[str]`` (``None`` becomes ``[]``).

    Strict mode rejects anything that is not already a list of strings; tolerant
    mode coerces a bare string and stringifies str/int list entries, dropping the
    rest.
    """
    if value is None:
        return []
    if error is not None:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise error(f"{name} must be a list of strings")
        return list(value)
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int)) and str(item)]
    return []


@overload
def resolve_seeds(
    run_defaults: dict[str, Any],
    node: dict[str, Any],
    *,
    where: str = ...,
    error: ErrorFactory,
) -> list[int]: ...


@overload
def resolve_seeds(
    run_defaults: dict[str, Any],
    node: dict[str, Any],
    *,
    where: str = ...,
    error: None = ...,
) -> list[int] | None: ...


def resolve_seeds(  # noqa: C901, PLR0911
    run_defaults: dict[str, Any],
    node: dict[str, Any],
    *,
    where: str = "condition",
    error: ErrorFactory | None = None,
) -> list[int] | None:
    """Resolve one condition's seed list.

    Honors, in order: condition ``seeds``/``seed``, condition-or-run-default
    ``seed_repeats`` (+ ``seed_start``), run-default ``seeds``, run-default
    ``seed``. ``where`` is the condition's config location
    (``hypotheses.<h>.conditions.<c>``), named in every strict-mode error so a bad
    seed in one of dozens of conditions is findable without bisecting the study
    file. Returns ``None`` in tolerant mode when the seeds cannot be resolved.
    """

    def int_list(values: Any, message: str) -> list[int] | None:
        if not isinstance(values, list) or not all(isinstance(item, int) for item in values):
            return _unresolvable(error, message)
        return list(values)

    if "seeds" in node:
        return int_list(node["seeds"], f"{where}.seeds must be a list of ints")

    if "seed" in node:
        if not isinstance(node["seed"], int):
            return _unresolvable(error, f"{where}.seed must be an int")
        return [node["seed"]]

    repeats = node.get("seed_repeats", run_defaults.get("seed_repeats"))
    if repeats is not None:
        if not isinstance(repeats, int) or repeats <= 0:
            return _unresolvable(
                error, f"seed_repeats must be a positive int (resolved for {where})"
            )
        seed_start = node.get(
            "seed_start", run_defaults.get("seed_start", run_defaults.get("seed", 1))
        )
        if not isinstance(seed_start, int):
            return _unresolvable(error, f"seed_start must be an int (resolved for {where})")
        return [seed_start + index for index in range(repeats)]

    if "seeds" in run_defaults:
        return int_list(
            run_defaults["seeds"],
            f"run_defaults.seeds must be a list of ints (resolved for {where})",
        )

    seed = run_defaults.get("seed", 1)
    if not isinstance(seed, int):
        return _unresolvable(error, f"run_defaults.seed must be an int (resolved for {where})")
    return [seed]


def resolve_scenarios(
    study: dict[str, Any],
    run_defaults: dict[str, Any],
    *,
    error: ErrorFactory | None = None,
) -> list[str]:
    """Resolve a study's base scenario list.

    Honors ``study.scenarios``, then ``study.base_scenarios``, then
    ``run_defaults.scenario``. Strict mode raises when none of them yields a
    scenario; tolerant mode returns ``[]`` and leaves the fallback to the caller.
    """
    scenarios = string_list("study.scenarios", study.get("scenarios"), error)
    if not scenarios:
        scenarios = string_list("study.base_scenarios", study.get("base_scenarios"), error)
    if not scenarios:
        scenario = run_defaults.get("scenario")
        if isinstance(scenario, str) and scenario:
            scenarios = [scenario]
    if not scenarios and error is not None:
        raise error("No scenarios found. Set study.scenarios or run_defaults.scenario")
    return scenarios
