"""Agent base classes and interfaces.

This module declares the abstract :class:`silisocs.agents.base_agent.Agent` interface used by the
simulation runtime as well as helper classes and lightweight concrete
implementations used by the project.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Agent(ABC):
    """Abstract base class for social-media simulation agents.

    All agents, whether Concordia-based entities or custom implementations,
    must implement these two core methods.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the agent's display name.

        Returns
        -------
        str
            The agent's name used in logs and output.
        """

    @abstractmethod
    def observe(self, observation: str) -> None:
        """Process an observation from the environment.

        Called at the start of each action cycle to inform the agent about the
        current state of the social-media timeline or environment.

        Parameters
        ----------
        observation : str
            A text observation (timeline, episode number, etc.)
        """

    @abstractmethod
    def act(self, action_spec: Any) -> str:
        """Generate the next action based on the current action specification.

        Called to request an action from the agent. The action_spec encodes the
        current context and desired behavior for this action cycle.

        Parameters
        ----------
        action_spec : Any
            ActionSpec object providing context, prompts, and constraints.

        Returns
        -------
        str
            An action response. The format and interpretation are determined by
            the resolve component and its configuration (not by the agent).
        """


# Type alias for flexibility
AgentLike = Agent
