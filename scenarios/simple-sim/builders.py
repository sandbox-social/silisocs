# scenarios/my_scenario/builders.py
from mastodon_sim.agents.builders import BaseAgentBuilder
from mastodon_sim.runtime.dataclasses import AgentConfig

class SimpleAgentBuilder(BaseAgentBuilder):
    def build_role_agents(self, role: str, count: int) -> list[AgentConfig]:
        agents = []
        for i in range(count):
            agents.append(AgentConfig(
                prefab="entity__Entity",
                params={
                    "name": f"Agent_{role}_{i}",
                    "context": f"A {role} in the simulation.",
                    "sim_role": {
                        "name": role,
                        "module_path": "mastodon_sim.agents.entity",
                    },
                    "style": "",
                    "seed_post": "",
                    "bio": "",
                    "goal": None,
                },
            ))
        return agents