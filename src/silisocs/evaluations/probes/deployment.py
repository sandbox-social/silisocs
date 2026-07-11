"""Probe deployment orchestration utilities.

Keeps probe scheduling and agent selection logic out of the simulation engine.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from silisocs.evaluations.probes.agent_speech import _resolve_probe_class, deploy_probes
from silisocs.runtime.io import EventLogger
from silisocs.simulation_engines.policies.factory import build_probe_schedule_policy
from silisocs.simulation_engines.runtime_base import ProbeSchedulePolicy

logger = logging.getLogger(__name__)

DEFAULT_FLOW_TAG = "default"

# Loop positions a probe may fire at (see the loop strategy's run_probe_phase):
# before a step, after a step, or once after the whole run. Unset -> pre_step.
PROBE_ANCHORS: tuple[str, ...] = ("pre_step", "post_step", "run_end")

# Keys a deployment block (global or per-probe) may set; anything else is a typo.
# ``hold_last_response`` is read from the GLOBAL block only (see default_evaluators);
# it is accepted here so the global block validates, but has no per-probe effect.
_DEPLOYMENT_KEYS: frozenset[str] = frozenset(
    {
        "enabled",
        "start_step",
        "every_n_steps",
        "include_classes",
        "exclude_classes",
        "include_agents",
        "exclude_agents",
        "include_flows",
        "exclude_flows",
        "sample_k",
        "sample_fraction",
        "at",
        "hold_last_response",
    }
)


def _agent_name(agent: Any) -> str:
    """Return an agent's public name without depending on private storage."""
    return str(getattr(agent, "_agent_name", getattr(agent, "name", "")))


@dataclass(frozen=True)
class ProbeDeploymentPolicy:
    """Configurable policy for probe scheduling and target selection."""

    enabled: bool = True
    start_step: int = 1
    every_n_steps: int = 1
    include_classes: tuple[str, ...] = ()
    exclude_classes: tuple[str, ...] = ()
    include_agents: tuple[str, ...] = ()
    exclude_agents: tuple[str, ...] = ()
    include_flows: tuple[str, ...] = ()
    exclude_flows: tuple[str, ...] = ()
    # Sampling caps applied AFTER the include/exclude filters: deploy probes to at
    # most sample_k agents, or to ceil(sample_fraction * filtered) agents.
    # Selection is seed-derived per (seed, step, agent) — replay/resume stable —
    # so each due step probes a fresh but reproducible subset. None = probe every
    # filtered agent (unchanged default).
    sample_k: int | None = None
    sample_fraction: float | None = None
    # Loop anchor this policy fires at; one of PROBE_ANCHORS. ``run_end`` fires once
    # after the run and ignores start_step/every_n_steps (there is no step cadence).
    at: str = "pre_step"

    @classmethod
    def from_deployment_cfg(
        cls,
        deployment_cfg: Mapping[str, Any] | None,
        *,
        context: str = "probes.deployment",
    ) -> ProbeDeploymentPolicy:
        """Validate and build a deployment policy from a single deployment block.

        ``context`` prefixes error messages so a per-probe block names the probe.
        """
        cfg = dict(deployment_cfg or {})
        unknown = set(cfg) - _DEPLOYMENT_KEYS
        if unknown:
            raise ValueError(
                f"{context}: unknown key(s) {sorted(unknown)}; "
                f"valid keys are {sorted(_DEPLOYMENT_KEYS)}"
            )
        every_n_steps = int(cfg.get("every_n_steps", 1))
        if every_n_steps <= 0:
            raise ValueError(f"{context}.every_n_steps must be >= 1")
        at = str(cfg.get("at", "pre_step"))
        if at not in PROBE_ANCHORS:
            raise ValueError(f"{context}.at must be one of {list(PROBE_ANCHORS)} (got {at!r})")

        raw_sample_k = cfg.get("sample_k")
        sample_k = int(raw_sample_k) if raw_sample_k is not None else None
        if sample_k is not None and sample_k < 1:
            raise ValueError(f"{context}.sample_k must be >= 1 (or null)")
        raw_sample_fraction = cfg.get("sample_fraction")
        sample_fraction = float(raw_sample_fraction) if raw_sample_fraction is not None else None
        if sample_fraction is not None and not 0.0 < sample_fraction <= 1.0:
            raise ValueError(f"{context}.sample_fraction must be in (0, 1] (or null)")
        if sample_k is not None and sample_fraction is not None:
            raise ValueError(f"{context}: set sample_k or sample_fraction, not both.")

        return cls(
            enabled=bool(cfg.get("enabled", True)),
            start_step=int(cfg.get("start_step", 1)),
            every_n_steps=every_n_steps,
            include_classes=tuple(str(x) for x in cfg.get("include_classes", []) or []),
            exclude_classes=tuple(str(x) for x in cfg.get("exclude_classes", []) or []),
            include_agents=tuple(str(x) for x in cfg.get("include_agents", []) or []),
            exclude_agents=tuple(str(x) for x in cfg.get("exclude_agents", []) or []),
            include_flows=tuple(str(x) for x in cfg.get("include_flows", []) or []),
            exclude_flows=tuple(str(x) for x in cfg.get("exclude_flows", []) or []),
            sample_k=sample_k,
            sample_fraction=sample_fraction,
            at=at,
        )

    @classmethod
    def from_probes_config(cls, probes_config: Mapping[str, Any] | None) -> ProbeDeploymentPolicy:
        """Build the global deployment policy from a probes config mapping."""
        return cls.from_deployment_cfg(dict((probes_config or {}).get("deployment", {}) or {}))


@dataclass(frozen=True)
class _ProbeEntry:
    """A built probe paired with its effective deployment policy.

    ``sample_salt`` distinguishes the sampling hash per probe: "" for probes on the
    shared global policy (legacy token, byte-identical replays) and the probe name
    for probes with their own ``deployment:`` block (so two capped probes select
    independent subsets instead of the identical one).
    """

    probe: Any
    policy: ProbeDeploymentPolicy
    sample_salt: str


class ProbeDeploymentOrchestrator:
    """Coordinates when and for which agents probes are deployed."""

    def __init__(
        self,
        probes_config: Mapping[str, Any] | None,
        probe_event_logger: Any,
        policy: ProbeDeploymentPolicy | None = None,
        seed: int = 0,
    ):
        """Initialize the orchestrator with probes config, logger, policy, and seed.

        ``seed`` anchors the sampling caps (``sample_k``/``sample_fraction``):
        selection is derived per (seed, step, agent), so the same run config
        probes the same agents on replay/resume.
        """
        self._probes_config = dict(probes_config or {})
        self._probe_event_logger = probe_event_logger
        self._policy = policy or ProbeDeploymentPolicy.from_probes_config(self._probes_config)
        self._global_deployment_cfg = dict(self._probes_config.get("deployment", {}) or {})
        self._seed = int(seed)
        self._cached_entries: list[_ProbeEntry] | None = None

    def _resolve_entries(self) -> list[_ProbeEntry]:
        """Build and cache each probe paired with its effective deployment policy.

        A probe with its own ``deployment:`` block overlays it on the global block
        (per-field fallback); a probe without one shares the global policy object
        (identity marks the legacy, byte-identical path).
        """
        if self._cached_entries is not None:
            return self._cached_entries
        probe_lib_module = self._probes_config.get("probe_lib_module")
        raw_probes = self._probes_config.get("probes", {})
        if isinstance(raw_probes, Mapping):
            probe_configs = list(raw_probes.values())
        elif isinstance(raw_probes, Sequence) and not isinstance(raw_probes, (str, bytes)):
            probe_configs = list(raw_probes)
        else:
            probe_configs = []
        entries: list[_ProbeEntry] = []
        for probe_config in probe_configs:
            if not isinstance(probe_config, Mapping):
                continue
            probe_data = probe_config.get("probe_data", {})
            if not isinstance(probe_data, Mapping):
                probe_data = {}
            probe_name = probe_config.get("probe_name") or probe_data.get("name")
            # Resolve the effective policy FIRST (config-only, no probe object) so a
            # disabled probe stays dormant: it is never built, so a deliberately
            # disabled probe with an unimportable probe_type/lib does not abort the run.
            label = str(probe_name or probe_config.get("probe_type", "probe"))
            override = probe_config.get("deployment")
            if isinstance(override, Mapping) and override:
                policy = ProbeDeploymentPolicy.from_deployment_cfg(
                    {**self._global_deployment_cfg, **dict(override)},
                    context=f"probes.probes.{label}.deployment",
                )
                salt = label
            else:
                policy = self._policy
                salt = ""
            if not policy.enabled:
                continue
            ProbeClass = _resolve_probe_class(probe_config["probe_type"], probe_lib_module)
            probe_obj = ProbeClass(dict(probe_data))
            if probe_name:
                probe_obj.probe_name = str(probe_name)
            entries.append(_ProbeEntry(probe_obj, policy, salt))
        self._cached_entries = entries
        return entries

    def anchors_in_use(self) -> set[str]:
        """Return the set of loop anchors any configured probe fires at."""
        return {entry.policy.at for entry in self._resolve_entries()}

    def is_configured(self) -> bool:
        """Return whether any probes are configured for deployment."""
        return bool(self._resolve_entries())

    @staticmethod
    def _entry_due(policy: ProbeDeploymentPolicy, step: int) -> bool:
        """Return whether a probe on ``policy`` is due at ``step``.

        ``run_end`` probes are one-shot at loop end, so they ignore the step
        cadence (start_step/every_n_steps) and are due whenever enabled.
        """
        if not policy.enabled:
            return False
        if policy.at == "run_end":
            return True
        if step < policy.start_step:
            return False
        return (step - policy.start_step) % policy.every_n_steps == 0

    def _select_agents(
        self,
        agents: Sequence[Any],
        agent_flows: Mapping[str, str] | None = None,
        *,
        step: int = 0,
        policy: ProbeDeploymentPolicy | None = None,
        sample_salt: str = "",
    ) -> list[Any]:
        """Select probe targets by class, agent name, and flow filters.

        ``agent_flows`` maps agent name -> flow tag (authoritative source is the
        game master's ``agent_flow_tags``). It is required only when flow filters
        are configured; agents missing from the map resolve to the default flow.
        ``step`` anchors the seed-derived sampling cap applied after the filters;
        ``policy`` defaults to the global policy, ``sample_salt`` scopes the cap's
        hash per probe.
        """
        policy = policy or self._policy

        def _agent_flow(agent: Any) -> str:
            return str((agent_flows or {}).get(_agent_name(agent), DEFAULT_FLOW_TAG))

        def _agent_classes(agent: Any) -> set[str]:
            """Return configured class/role labels for an agent."""
            out: set[str] = set()

            for attr in ("sim_role_name", "sim_role", "role", "class_name", "agent_class"):
                value = getattr(agent, attr, None)
                if isinstance(value, str) and value.strip():
                    out.add(value.strip())
                elif isinstance(value, Mapping):
                    role_name = value.get("name")
                    if isinstance(role_name, str) and role_name.strip():
                        out.add(role_name.strip())

            params = getattr(agent, "_params", None)
            if isinstance(params, Mapping):
                sim_role = params.get("sim_role")
                if isinstance(sim_role, Mapping):
                    role_name = sim_role.get("name")
                    if isinstance(role_name, str) and role_name.strip():
                        out.add(role_name.strip())
                elif isinstance(sim_role, str) and sim_role.strip():
                    out.add(sim_role.strip())

                for key in ("role", "class_name", "agent_class"):
                    value = params.get(key)
                    if isinstance(value, str) and value.strip():
                        out.add(value.strip())

            return out

        selected = list(agents)
        if policy.include_classes:
            include_classes = set(policy.include_classes)
            selected = [agent for agent in selected if _agent_classes(agent) & include_classes]
        if policy.exclude_classes:
            exclude_classes = set(policy.exclude_classes)
            selected = [
                agent for agent in selected if not (_agent_classes(agent) & exclude_classes)
            ]
        if policy.include_agents:
            include = set(policy.include_agents)
            selected = [agent for agent in selected if _agent_name(agent) in include]
        if policy.exclude_agents:
            exclude = set(policy.exclude_agents)
            selected = [agent for agent in selected if _agent_name(agent) not in exclude]
        if policy.include_flows:
            include_flows = set(policy.include_flows)
            selected = [agent for agent in selected if _agent_flow(agent) in include_flows]
        if policy.exclude_flows:
            exclude_flows = set(policy.exclude_flows)
            selected = [agent for agent in selected if _agent_flow(agent) not in exclude_flows]
        return self._sample_agents(selected, step=step, policy=policy, sample_salt=sample_salt)

    def _sample_agents(
        self,
        selected: list[Any],
        *,
        step: int,
        policy: ProbeDeploymentPolicy | None = None,
        sample_salt: str = "",
    ) -> list[Any]:
        """Apply the sample_k/sample_fraction cap to the filtered agents.

        Selection is a deterministic ranking: each agent gets a
        sha256(seed:step:[salt:]name) score and the smallest ``target`` scores win.
        Hash-based (not ``random``) so it is independent of roster order, process
        hash randomization, and the shared RNG's consumption — replay/resume
        yield the identical probe subset for a given (seed, step). ``sample_salt``
        (a probe name for per-probe blocks, "" for the global policy) scopes the
        ranking so two capped probes pick independent subsets.
        """
        policy = policy or self._policy
        if policy.sample_k is None and policy.sample_fraction is None:
            return selected
        if policy.sample_k is not None:
            target = policy.sample_k
        else:
            target = math.ceil(float(policy.sample_fraction or 0.0) * len(selected))
        if target >= len(selected):
            return selected

        salt = f"{sample_salt}:" if sample_salt else ""

        def _score(agent: Any) -> str:
            token = f"{self._seed}:{int(step)}:probe_sample:{salt}{_agent_name(agent)}"
            return hashlib.sha256(token.encode("utf-8")).hexdigest()

        return sorted(selected, key=_score)[:target]

    def maybe_deploy(
        self,
        step: int,
        agents: Sequence[Any],
        worker_limit: int | None = None,
        agent_flows: Mapping[str, str] | None = None,
        anchor: str = "pre_step",
    ) -> tuple[bool, int]:
        """Deploy every probe due at ``anchor`` and ``step`` to its target agents.

        Probes whose resolved target set is identical are batched into one
        questionnaire call (preserving one-LLM-call-per-agent efficiency); probes
        with different schedules/targets deploy independently. Returns
        ``(deployed?, distinct agents probed)``.
        """
        due = [
            entry
            for entry in self._resolve_entries()
            if entry.policy.at == anchor and self._entry_due(entry.policy, step)
        ]
        if not due:
            return False, 0

        # group probes by identical resolved target set -> one questionnaire each
        groups: dict[tuple[str, ...], tuple[list[Any], list[Any]]] = {}
        for entry in due:
            targets = self._select_agents(
                agents, agent_flows, step=step, policy=entry.policy, sample_salt=entry.sample_salt
            )
            if not targets:
                # Warn per empty probe (not just when EVERY probe is empty), so a
                # typo'd per-probe filter is surfaced even if another probe matched.
                probe_label = getattr(entry.probe, "probe_name", type(entry.probe).__name__)
                logger.warning(
                    "Probe %r at step=%s anchor=%s: no agents matched filters. "
                    "Check for a typo'd flow/class/agent name.",
                    probe_label,
                    step,
                    anchor,
                )
                continue
            key = tuple(_agent_name(agent) for agent in targets)
            bucket = groups.setdefault(key, (targets, []))
            bucket[1].append(entry.probe)
        if not groups:
            return False, 0

        self._probe_event_logger.episode_idx = step
        probed: set[str] = set()
        for targets, probes in groups.values():
            deploy_probes(
                targets,
                self._probes_config,
                self._probe_event_logger,
                worker_limit=worker_limit,
                prebuilt_probes=probes,
                anchor=anchor,
            )
            probed.update(_agent_name(agent) for agent in targets)
        return True, len(probed)


class DefaultProbeRunner:
    """Evaluation-owned probe runner for in-loop deployment."""

    def __init__(
        self, probes_config: Mapping[str, Any] | None, output_rootname: str, seed: int = 0
    ):
        config = dict(probes_config or {})
        self._logger = EventLogger("probe", os.path.join(output_rootname, "probe_events.jsonl"))
        self._orchestrator = ProbeDeploymentOrchestrator(config, self._logger, seed=seed)
        schedule_cfg = dict(config.get("schedule", {}) or {})
        self._schedule_policy: ProbeSchedulePolicy = build_probe_schedule_policy(schedule_cfg)

    def anchors_in_use(self) -> set[str]:
        """Return the loop anchors any configured probe fires at."""
        return self._orchestrator.anchors_in_use()

    def maybe_run(
        self,
        *,
        step: int,
        agents: Sequence[Any],
        worker_limit: int | None,
        agent_flows: Mapping[str, str] | None = None,
        anchor: str = "pre_step",
    ) -> tuple[bool, int]:
        # The engine-level schedule policy gates the per-STEP probe phase (its whole
        # contract is a step cadence). run_end is a one-shot terminal measurement with
        # no step cadence, so it bypasses the gate; it is still governed by its probe's
        # enabled flag / configuration (set deployment.enabled: false to disable it).
        if anchor != "run_end" and not self._schedule_policy.should_run_probe_phase(
            step=step, orchestrator=self._orchestrator
        ):
            return False, 0
        return self._orchestrator.maybe_deploy(
            step=step,
            agents=agents,
            worker_limit=worker_limit,
            agent_flows=agent_flows,
            anchor=anchor,
        )
