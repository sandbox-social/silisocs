"""Probe deployment orchestration utilities.

Keeps probe scheduling and agent selection logic out of the simulation engine.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from silisocs.evaluations.probes.agent_speech import _resolve_probe_class, deploy_probes
from silisocs.runtime.io import EventLogger
from silisocs.simulation_engines.policies.factory import build_probe_schedule_policy

logger = logging.getLogger(__name__)

DEFAULT_FLOW_TAG = "default"


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

    @classmethod
    def from_probes_config(cls, probes_config: Mapping[str, Any] | None) -> ProbeDeploymentPolicy:
        """Build a deployment policy from a probes config mapping."""
        deployment_cfg = dict((probes_config or {}).get("deployment", {}) or {})
        every_n_steps = int(deployment_cfg.get("every_n_steps", 1))
        if every_n_steps <= 0:
            raise ValueError("probes.deployment.every_n_steps must be >= 1")

        include_agents = tuple(str(x) for x in deployment_cfg.get("include_agents", []) or [])
        exclude_agents = tuple(str(x) for x in deployment_cfg.get("exclude_agents", []) or [])
        include_classes = tuple(str(x) for x in deployment_cfg.get("include_classes", []) or [])
        exclude_classes = tuple(str(x) for x in deployment_cfg.get("exclude_classes", []) or [])
        include_flows = tuple(str(x) for x in deployment_cfg.get("include_flows", []) or [])
        exclude_flows = tuple(str(x) for x in deployment_cfg.get("exclude_flows", []) or [])

        return cls(
            enabled=bool(deployment_cfg.get("enabled", True)),
            start_step=int(deployment_cfg.get("start_step", 1)),
            every_n_steps=every_n_steps,
            include_classes=include_classes,
            exclude_classes=exclude_classes,
            include_agents=include_agents,
            exclude_agents=exclude_agents,
            include_flows=include_flows,
            exclude_flows=exclude_flows,
        )


class ProbeDeploymentOrchestrator:
    """Coordinates when and for which agents probes are deployed."""

    def __init__(
        self,
        probes_config: Mapping[str, Any] | None,
        probe_event_logger: Any,
        policy: ProbeDeploymentPolicy | None = None,
    ):
        """Initialize the orchestrator with probes config, logger, and policy."""
        self._probes_config = dict(probes_config or {})
        self._probe_event_logger = probe_event_logger
        self._policy = policy or ProbeDeploymentPolicy.from_probes_config(self._probes_config)
        self._cached_probes: list[Any] | None = None

    def _get_probes(self) -> list[Any]:
        """Build and cache probe objects from config."""
        if self._cached_probes is not None:
            return self._cached_probes
        probe_lib_module = self._probes_config.get("probe_lib_module")
        raw_probes = self._probes_config.get("probes", {})
        if isinstance(raw_probes, Mapping):
            probes_config = list(raw_probes.values())
        elif isinstance(raw_probes, Sequence) and not isinstance(raw_probes, (str, bytes)):
            probes_config = list(raw_probes)
        else:
            probes_config = []
        probes = []
        for probe_config in probes_config:
            if not isinstance(probe_config, Mapping):
                continue
            ProbeClass = _resolve_probe_class(probe_config["probe_type"], probe_lib_module)
            probe_data = probe_config.get("probe_data", {})
            if not isinstance(probe_data, Mapping):
                probe_data = {}
            probe_obj = ProbeClass(dict(probe_data))
            probe_name = probe_config.get("probe_name") or probe_data.get("name")
            if probe_name:
                probe_obj.probe_name = str(probe_name)
            probes.append(probe_obj)
        self._cached_probes = probes
        return probes

    def is_configured(self) -> bool:
        """Return whether any probes are configured for deployment."""
        return bool(self._probes_config.get("probes"))

    def should_deploy(self, step: int) -> bool:
        """Return whether probes are due for deployment at the given step."""
        if not self._policy.enabled:
            return False
        if not self.is_configured():
            return False
        if step < self._policy.start_step:
            return False
        return (step - self._policy.start_step) % self._policy.every_n_steps == 0

    def _select_agents(
        self,
        agents: Sequence[Any],
        agent_flows: Mapping[str, str] | None = None,
    ) -> list[Any]:
        """Select probe targets by class, agent name, and flow filters.

        ``agent_flows`` maps agent name -> flow tag (authoritative source is the
        game master's ``agent_flow_tags``). It is required only when flow filters
        are configured; agents missing from the map resolve to the default flow.
        """

        def _agent_name(agent: Any) -> str:
            return str(getattr(agent, "_agent_name", getattr(agent, "name", "")))

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
        if self._policy.include_classes:
            include_classes = set(self._policy.include_classes)
            selected = [agent for agent in selected if _agent_classes(agent) & include_classes]
        if self._policy.exclude_classes:
            exclude_classes = set(self._policy.exclude_classes)
            selected = [
                agent for agent in selected if not (_agent_classes(agent) & exclude_classes)
            ]
        if self._policy.include_agents:
            include = set(self._policy.include_agents)
            selected = [agent for agent in selected if _agent_name(agent) in include]
        if self._policy.exclude_agents:
            exclude = set(self._policy.exclude_agents)
            selected = [agent for agent in selected if _agent_name(agent) not in exclude]
        if self._policy.include_flows:
            include_flows = set(self._policy.include_flows)
            selected = [agent for agent in selected if _agent_flow(agent) in include_flows]
        if self._policy.exclude_flows:
            exclude_flows = set(self._policy.exclude_flows)
            selected = [agent for agent in selected if _agent_flow(agent) not in exclude_flows]
        return selected

    def maybe_deploy(
        self,
        step: int,
        agents: Sequence[Any],
        worker_limit: int | None = None,
        agent_flows: Mapping[str, str] | None = None,
    ) -> tuple[bool, int]:
        """Deploy probes if the configured schedule says this step is due."""
        if not self.should_deploy(step):
            return False, 0

        selected_agents = self._select_agents(agents, agent_flows)
        if not selected_agents:
            logger.warning(
                "Probe deployment skipped at step=%s: no agents matched filters "
                "(include_flows=%s exclude_flows=%s include_classes=%s include_agents=%s). "
                "Check for a typo'd flow/class name.",
                step,
                self._policy.include_flows,
                self._policy.exclude_flows,
                self._policy.include_classes,
                self._policy.include_agents,
            )
            return False, 0

        self._probe_event_logger.episode_idx = step
        deploy_probes(
            selected_agents,
            self._probes_config,
            self._probe_event_logger,
            worker_limit=worker_limit,
            prebuilt_probes=self._get_probes(),
        )
        return True, len(selected_agents)


class DefaultProbeRunner:
    """Evaluation-owned probe runner for in-loop deployment."""

    def __init__(self, probes_config: Mapping[str, Any] | None, output_rootname: str):
        config = dict(probes_config or {})
        self._logger = EventLogger("probe", os.path.join(output_rootname, "probe_events.jsonl"))
        self._orchestrator = ProbeDeploymentOrchestrator(config, self._logger)
        schedule_cfg = dict(config.get("schedule", {}) or {})
        self._schedule_policy = build_probe_schedule_policy(schedule_cfg)

    def maybe_run(
        self,
        *,
        step: int,
        agents: Sequence[Any],
        worker_limit: int | None,
        agent_flows: Mapping[str, str] | None = None,
    ) -> tuple[bool, int]:
        if not self._schedule_policy.should_run_probe_phase(
            step=step, orchestrator=self._orchestrator
        ):
            return False, 0
        return self._orchestrator.maybe_deploy(
            step=step, agents=agents, worker_limit=worker_limit, agent_flows=agent_flows
        )
