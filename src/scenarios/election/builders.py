# src/scenarios/election/builders.py
"""
Election-specific agent builders.
Extends base builder with election role logic.
"""

from dataclasses import asdict

from scenarios.election.scenario_dataclasses import (
    NewsAccountParams,
    VoterParams,
)
from sim.config_utils.agent_builders import BaseAgentBuilder
from sim.config_utils.simulation_dataclasses import AgentConfig, SimRole


class ElectionAgentBuilder(BaseAgentBuilder):
    """
    Agent builder for election scenario.
    Handles voters, candidates, and news accounts.
    """

    def build_role_agents(self, role: str, count: int) -> list[AgentConfig]:
        """
        Build agents for a specific role.

        Args:
            role: Role name ('voter', 'candidate', 'news_account')
            count: Number of agents to create

        Returns
        -------
            List of AgentConfig objects
        """
        if count == 0:
            return []

        if role == "voter":
            return self._build_voters(count)
        if role == "candidate":
            return self._build_candidates(count)
        if role == "news_account":
            return self._build_news_account(count)
        raise ValueError(f"Unknown role: {role}")

    def _build_voters(self, count: int) -> list[AgentConfig]:
        """Build voter agents from persona data."""
        configs = []

        # Get role config
        role_cfg = self.get_role_config("voter")

        # Load persona data
        persona_data = self.load_persona_data(self.config.data.persona_file, count=count)

        # Get election policy text
        policy_text = self._get_policy_text()

        # Create sim role
        sim_role = SimRole(name="voter", module_path=role_cfg.module_path)

        for persona in persona_data:
            # Collapse persona fields into context
            collapsed_persona = (
                "\n".join(
                    f"{k}: {v}" for k, v in persona.items() if k not in ["Name", "User_Reference"]
                )
                + "\n"
            )

            agent_config = AgentConfig(
                prefab=sim_role.module_path.split(".")[-1] + "__Entity",
                params=asdict(
                    VoterParams(
                        name=persona["Name"],
                        goal=role_cfg.goal,
                        sim_role=sim_role,
                        election_info=policy_text,
                        seed_post="",
                        bio="",
                        context=collapsed_persona,
                        style=persona["Style"],
                    )
                ),
            )
            configs.append(agent_config)

        return configs

    def _build_candidates(self, count: int) -> list[AgentConfig]:
        """Build candidate agents."""
        configs = []

        # Get role config
        role_cfg = self.get_role_config("candidate")

        # Get election policy text
        policy_text = self._get_policy_text()

        # Create sim role
        sim_role = SimRole(name="candidate", module_path=role_cfg.module_path)

        # Build each candidate
        # We need to respect the count, but candidates are named individuals.
        # We'll take the first `count` candidates defined in config.
        candidate_items = list(self.config.candidates.items())
        if count < len(candidate_items):
            candidate_items = candidate_items[:count]

        for partisan_type, candidate_info in candidate_items:
            agent_config = AgentConfig(
                prefab=sim_role.module_path.split(".")[-1] + "__Entity",
                params=asdict(
                    VoterParams(
                        name=candidate_info.name,
                        seed_post="",
                        sim_role=sim_role,
                        bio="",
                        election_info=policy_text,
                        goal=candidate_info.goal,
                        context=candidate_info.persona,
                        style=candidate_info.style,
                    )
                ),
            )
            configs.append(agent_config)

        return configs

    def _build_news_account(self, count: int) -> list[AgentConfig]:
        """Build news account agent."""
        if count == 0:
            return []

        # Get news configuration
        news_cfg = self.config.news_account

        # Load news data
        include_images = self.config.data.use_news_agent == "with_images"
        news_data = self.load_news_data(self.config.data.news_file)

        # Process news posts
        posts = {
            headline: (content[0] if include_images else "")
            for headline, content in news_data.items()
        }

        print("Headlines loaded:")
        for headline in news_data.keys():
            print(f"  - {headline}")
        print(f"Including images: {include_images}")

        # Get role config
        role_cfg = self.get_role_config("news_account")

        # Create sim role
        sim_role = SimRole(name="news_account", module_path=role_cfg.module_path)

        agent_config = AgentConfig(
            prefab=sim_role.module_path.split(".")[-1] + "__Entity",
            params=asdict(
                NewsAccountParams(
                    name=news_cfg.name,
                    sim_role=sim_role,
                    seed_post=news_cfg.seed_post,
                    bio=news_cfg.bio,
                    posts=posts,
                    context=news_cfg.context,
                    style="",
                    goal=None,
                )
            ),
        )
        return [agent_config]

    def _get_policy_text(self) -> str:
        """Generate policy text from candidate information."""
        policy_lines = []

        for partisan_type, candidate_info in self.config.candidates.items():
            policies = " and ".join(candidate_info.policy_proposals)
            policy_lines.append(f"{candidate_info.name} campaigns on {policies}.")

        return "\n".join(policy_lines)
