import datetime
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from concordia.associative_memory import (
    associative_memory,
    blank_memories,
)
from concordia.clocks import game_clock

from mastodon_sim.concordia import triggering


class GameMaster:
    """
    Simplified game master to run agents with independent phone scene triggering
    in parallel and ranom.
    """

    def __init__(
        self,
        agents,
        roles,
        clock,
        action_spec,
        phones,
        model,
        memory,
        memory_factory,
        embedder,
        importance_model,
        importance_model_gm,
    ):
        """
        Args:
            agents: Dictionary of agents.
            clock: Game clock to advance time.
            action_spec: Action specifications for the agents.
            phones: Dictionary of phones associated with each agent.
            model: Language model to process events.
            memory: Shared associative memory (could be unique per agent if needed).
            memory_factory: Factory to create memory instances.
        """
        self.agents = {agent._agent_name: agent for agent in agents}
        self.roles = roles
        self.clock = clock
        self.action_spec = action_spec
        self.phones = phones
        self.model = model
        self.memory = memory
        self.memory_factory = memory_factory
        self.embedder = embedder
        self.importance_model = importance_model
        self.importance_model_gm = importance_model_gm
        self.agent_components = self._create_agent_components()
        self.log_data = []

    def _create_agent_components(self):
        """Create a unique SceneTriggeringComponent for each agent."""
        components = {}
        for agent_name, agent in self.agents.items():
            if self.roles[agent_name] != "exogenous":
                memory_p = associative_memory.AssociativeMemory(
                    self.embedder, self.importance_model_gm.importance, clock=self.clock.now
                )
                mem_fact = blank_memories.MemoryFactory(
                    model=self.model,
                    embedder=self.embedder,
                    importance=self.importance_model.importance,
                    clock_now=self.clock.now,
                )
                curr_clock = game_clock.MultiIntervalClock(
                    self.clock.now(),
                    step_sizes=[datetime.timedelta(seconds=1800), datetime.timedelta(seconds=10)],
                )
                curr_model = self.model
                components[agent_name] = triggering.BasicSceneTriggeringComponent(
                    player=agent,
                    phone=self.phones[agent_name],
                    model=curr_model,
                    memory=memory_p,
                    clock=curr_clock,
                    memory_factory=mem_fact,
                )
        return components

    def _step_agent(self, agent):
        """Run a single agent's action and trigger their phone scene."""
        try:
            if self.roles[agent._agent_name] == "exogenous":
                action = agent.post(self.phones[agent._agent_name].apps[0])
                self.log_data.append(
                    {"source_user": agent._agent_name, "label": "episode_plan", "data": action}
                )
            else:
                self.model.meta_data["agent_name"] = agent._agent_name
                action = None

            # Trigger the phone scene for this agent using their unique component
            if self.roles[agent._agent_name] != "exogenous":
                self.agent_components[agent._agent_name].update_after_event(action)

            return action
        except Exception as e:
            # Handle any agent-specific exceptions
            return f"Error for {agent._agent_name}: {e!s}"

    def step(self, active_agents=None, timeout=300):
        """
        Run a step for the specified active agents in parallel.

        Args:
            active_agents: List of agent names to take part in the step. If None, all agents act.
            timeout: Timeout in seconds for each agent's action.
        """
        if active_agents is None:
            active_agents = list(self.agents.keys())

        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(self._step_agent, self.agents[agent_name]): agent_name
                for agent_name in active_agents
            }

            try:
                # Apply an overall timeout for `as_completed`
                for future in as_completed(futures, timeout=timeout * len(active_agents)):
                    agent_name = futures[future]
                    try:
                        # Still applying timeout for each future result individually
                        result = future.result(timeout=timeout * 2)
                        print(f"Result for {agent_name}: {result}")
                    except TimeoutError:
                        print(f"Timeout for {agent_name}. Skipping their turn.")
                    except Exception as e:
                        print(f"Error in thread for {agent_name}: {e!s}")
            except TimeoutError:
                # This handles the overall timeout for the entire `as_completed` call
                print("Overall step timed out before all agents could complete.")

        # Advance the game clock after all agents' actions are complete
        self.clock.advance()

    def run_game(self, steps=10):
        """Run the game for a given number of steps."""
        for _ in range(steps):
            self.step()  # By default, all agents will act unless specified otherwise

    def get_active_agents(self, active_rates):
        # random model of realworld agent lives (so that going online every delta is a poisson point process)
        # (active_rate could be a agent engagement component that could be based on a time-varying rate process updated at each episode according to response about how engaged agent is feeling)
        active_agents = []
        for agent_name, rate in active_rates.items():
            if random.random() < rate:
                active_agents.append(agent_name)
        return active_agents
