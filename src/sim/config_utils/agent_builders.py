# src/sim/config_utils/agent_builders.py
"""
Base agent builder classes for constructing agent configurations.
Scenarios extend these to implement role-specific logic.
"""

from abc import ABC, abstractmethod
from typing import Any

from sim.config_utils.simulation_dataclasses import AgentConfig


class BaseAgentBuilder(ABC):
    """
    Base class for building agent configurations.

    Handles iteration over roles and counts, delegates role-specific
    logic to derived classes.
    """

    def __init__(self, scenario_config: Any):
        """
        Initialize builder with scenario configuration.

        Args:
            scenario_config: Scenario-specific configuration object
        """
        self.config = scenario_config

    def build_agents(self, roles: dict[str, int]) -> list[AgentConfig]:
        """
        Build all agent configurations based on role counts.

        Args:
            roles: Dictionary mapping role names to counts

        Returns
        -------
            List of AgentConfig objects
        """
        all_agents = []

        for role, count in roles.items():
            agents = self.build_role_agents(role, count)
            all_agents.extend(agents)

        return all_agents

    @abstractmethod
    def build_role_agents(self, role: str, count: int) -> list[AgentConfig]:
        """
        Build agents for a specific role.

        Args:
            role: Role name (e.g., 'voter', 'candidate')
            count: Number of agents to create for this role

        Returns
        -------
            List of AgentConfig objects for this role
        """

    def get_role_config(self, role: str) -> Any:
        """
        Get configuration for a specific role.

        Args:
            role: Role name

        Returns
        -------
            Role-specific configuration
        """
        if hasattr(self.config, "role_configs") and role in self.config.role_configs:
            return self.config.role_configs[role]
        return None

    def load_persona_data(self, persona_file: str, count: int | None = None) -> list[dict]:
        """
        Load persona data from JSON file.

        Args:
            persona_file: Path to persona JSON file
            count: Number of personas to load (None = all)

        Returns
        -------
            List of persona dictionaries
        """
        import json
        from pathlib import Path

        # Construct path to persona file
        persona_path = (
            Path("src/scenarios") / self.config.scenario_name / "input" / "personas" / persona_file
        )

        with open(persona_path) as f:
            personas = json.load(f)

        if count is not None:
            personas = personas[:count]

        return personas

    def load_news_data(self, news_file: str) -> dict[str, Any]:
        """
        Load news headlines and images from JSON file.

        Args:
            news_file: Name of news file (without .json extension)

        Returns
        -------
            Dictionary of news data
        """
        import json
        from pathlib import Path

        news_path = (
            Path("src/scenarios")
            / self.config.scenario_name
            / "input"
            / "news_data"
            / f"{news_file}.json"
        )

        with open(news_path) as f:
            news_data = json.load(f)

        return news_data
