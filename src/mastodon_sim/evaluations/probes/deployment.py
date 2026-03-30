"""Probe deployment orchestration utilities.

Keeps probe scheduling and agent selection logic out of the simulation engine.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mastodon_sim.evaluations.probes.agent_speech import _resolve_query_class, deploy_probes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbeDeploymentPolicy:
    """Configurable policy for probe scheduling and target selection."""

    enabled: bool = True
    start_step: int = 1
    every_n_steps: int = 1
    include_classes: tuple[str, ...] = ()
    exclude_classes: tuple[str, ...] = ()
    include_entities: tuple[str, ...] = ()
    exclude_entities: tuple[str, ...] = ()

    @classmethod
    def from_probes_config(cls, probes_config: Mapping[str, Any] | None) -> ProbeDeploymentPolicy:
        deployment_cfg = dict((probes_config or {}).get("deployment", {}) or {})
        every_n_steps = int(deployment_cfg.get("every_n_steps", 1))
        if every_n_steps <= 0:
            raise ValueError("probes.deployment.every_n_steps must be >= 1")

        include_entities = tuple(str(x) for x in deployment_cfg.get("include_entities", []) or [])
        exclude_entities = tuple(str(x) for x in deployment_cfg.get("exclude_entities", []) or [])
        include_classes = tuple(str(x) for x in deployment_cfg.get("include_classes", []) or [])
        exclude_classes = tuple(str(x) for x in deployment_cfg.get("exclude_classes", []) or [])

        return cls(
            enabled=bool(deployment_cfg.get("enabled", True)),
            start_step=int(deployment_cfg.get("start_step", 1)),
            every_n_steps=every_n_steps,
            include_classes=include_classes,
            exclude_classes=exclude_classes,
            include_entities=include_entities,
            exclude_entities=exclude_entities,
        )


class ProbeDeploymentOrchestrator:
    """Coordinates when and for which agents probes are deployed."""

    def __init__(
        self,
        probes_config: Mapping[str, Any] | None,
        probe_event_logger: Any,
        policy: ProbeDeploymentPolicy | None = None,
    ):
        self._probes_config = dict(probes_config or {})
        self._probe_event_logger = probe_event_logger
        self._policy = policy or ProbeDeploymentPolicy.from_probes_config(self._probes_config)
        self._cached_queries: list[Any] | None = None

    def _get_queries(self) -> list[Any]:
        """Build and cache query objects from config (avoids rebuild every episode)."""
        if self._cached_queries is not None:
            return self._cached_queries
        query_lib_module = self._probes_config.get("query_lib_module")
        raw_queries = self._probes_config.get("queries", {})
        if isinstance(raw_queries, Mapping):
            queries_config = list(raw_queries.values())
        elif isinstance(raw_queries, Sequence) and not isinstance(raw_queries, (str, bytes)):
            queries_config = list(raw_queries)
        else:
            queries_config = []
        queries = []
        for query_config in queries_config:
            if not isinstance(query_config, Mapping):
                continue
            QueryClass = _resolve_query_class(query_config["query_type"], query_lib_module)
            query_data = query_config.get("query_data", {})
            if not isinstance(query_data, Mapping):
                query_data = {}
            query_obj = QueryClass(dict(query_data))
            probe_name = query_config.get("probe_name") or query_data.get("name")
            if probe_name:
                query_obj.probe_name = str(probe_name)
            queries.append(query_obj)
        self._cached_queries = queries
        return queries

    def is_configured(self) -> bool:
        return bool(self._probes_config.get("queries"))

    def should_deploy(self, step: int) -> bool:
        if not self._policy.enabled:
            return False
        if not self.is_configured():
            return False
        if step < self._policy.start_step:
            return False
        return (step - self._policy.start_step) % self._policy.every_n_steps == 0

    def _select_agents(self, agents: Sequence[Any]) -> list[Any]:
        def _entity_classes(agent: Any) -> set[str]:
            out: set[str] = set()

            for attr in ("sim_role_name", "sim_role", "role", "class_name", "entity_class"):
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

                for key in ("role", "class_name", "entity_class"):
                    value = params.get(key)
                    if isinstance(value, str) and value.strip():
                        out.add(value.strip())

            return out

        selected = list(agents)
        if self._policy.include_classes:
            include_classes = set(self._policy.include_classes)
            selected = [agent for agent in selected if _entity_classes(agent) & include_classes]
        if self._policy.exclude_classes:
            exclude_classes = set(self._policy.exclude_classes)
            selected = [
                agent for agent in selected if not (_entity_classes(agent) & exclude_classes)
            ]
        if self._policy.include_entities:
            include = set(self._policy.include_entities)
            selected = [
                agent
                for agent in selected
                if getattr(agent, "_agent_name", getattr(agent, "name", "")) in include
            ]
        if self._policy.exclude_entities:
            exclude = set(self._policy.exclude_entities)
            selected = [
                agent
                for agent in selected
                if getattr(agent, "_agent_name", getattr(agent, "name", "")) not in exclude
            ]
        return selected

    def maybe_deploy(
        self,
        step: int,
        agents: Sequence[Any],
        worker_limit: int | None = None,
    ) -> tuple[bool, int]:
        """Deploy probes if the configured schedule says this step is due."""
        if not self.should_deploy(step):
            return False, 0

        selected_agents = self._select_agents(agents)
        if not selected_agents:
            logger.info(
                "Probe deployment skipped at step=%s because no agents matched filters.", step
            )
            return False, 0

        self._probe_event_logger.episode_idx = step
        deploy_probes(
            selected_agents,
            self._probes_config,
            self._probe_event_logger,
            worker_limit=worker_limit,
            prebuilt_queries=self._get_queries(),
        )
        return True, len(selected_agents)
