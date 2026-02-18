import importlib
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from concordia.typing import entity

logger = logging.getLogger(__name__)

DEFAULT_CALL_TO_SPEECH = (
    "Given the above, what is {name} likely to say next? Respond in"
    ' the format `{name} -- "..."` For example, '
    'Cristina -- "Hello! Mighty fine weather today, right?", '
    'Ichabod -- "I wonder if the alfalfa is ready to harvest", or '
    'Townsfolk -- "Good morning".\n'
)


def write_seed_toot(agent):
    call_to_speech = DEFAULT_CALL_TO_SPEECH.format(
        name=agent._agent_name,
    )
    interaction_premise = f"{agent._agent_name} has to make their first post on Social Media\n"
    interrogation = interaction_premise
    interrogation += "Thought on Social Media post: In less than 100 words, write a toot that aligns with your views and background."
    agent_says = agent.act(
        action_spec=entity.ActionSpec(
            call_to_action="Context: " + interrogation + call_to_speech,
            output_type=entity.OutputType.FREE,
        ),
    )
    agent_says = (
        agent_says.strip(agent._agent_name.split()[0])
        .strip()
        .strip(agent._agent_name.split()[1])
        .strip()
        .strip("--")
        .strip()
        .strip('"')
    )
    return agent_says


class AgentQuery(ABC):
    """
    A parent class for queries. name and query_text defined in children
    """

    def __init__(self, query_data=None):
        self.question_template = ""
        # form generic query from query components
        for component_name, component in self.query_text.items():
            if "static_labels" in component:
                # print(component)
                premise_actors = [
                    actor
                    for actor in query_data[component_name]
                    if query_data[component_name][actor] is not None
                ]
                assert component["static_labels"] == premise_actors, (
                    "query data doesn't match query"
                )
                self.question_template += component["text"].format(**query_data[component_name])
            else:
                self.question_template += component["text"]
        self.query_data = query_data

    def form_query_for_agent(self, agent):
        call_to_speech = DEFAULT_CALL_TO_SPEECH.format(name=agent._agent_name)
        question = self.question_template.format(agentname=agent._agent_name)
        return "Context: " + question + call_to_speech

    def ask(self, agent):
        agent_question = self.form_query_for_agent(agent)
        try:
            action_spec = entity.ActionSpec(
                call_to_action=agent_question,
                output_type=entity.OutputType.FREE,
                tag="query",
            )
        except TypeError:
            action_spec = entity.ActionSpec(
                call_to_action=agent_question,
                output_type=entity.OutputType.FREE,
            )
        agent_says = agent.act(action_spec=action_spec)
        return agent_says

    @abstractmethod
    def parse_answer(self, agent_says) -> str:
        """Example of a base operation that all children must implement"""

    def submit(self, agent):
        agent_says = self.ask(agent)
        query_return = self.query_data.copy() if self.query_data else {}
        query_return.setdefault("query_type", self.name)
        query_return["raw_response"] = agent_says
        query_return["query_return"] = self.parse_answer(agent_says)
        return query_return


def deploy_probes_to_agent(agent, queries, probe_event_logger):
    agent_results = []
    for query in queries:
        try:
            agent_query_return = query.submit(agent)
        except Exception as exc:
            query_name = getattr(query, "name", type(query).__name__)
            logger.exception(
                "Probe query failed for agent=%s query=%s",
                getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
                query_name,
            )
            agent_query_return = {
                "query_type": query_name,
                "query_return": None,
                "raw_response": None,
                "query_error": str(exc),
            }

        agent_results.append(
            {
                "source_user": agent._agent_name,
                "label": agent_query_return["query_type"],
                "data": agent_query_return,
            }
        )

    probe_event_logger.log(agent_results)


def deploy_probes(agents, probes, probe_event_logger):
    query_lib_module = probes["query_lib_module"]
    queries_config = probes["queries"].values()
    queries = []
    for query_config in queries_config:
        QueryClass = getattr(
            importlib.import_module(query_lib_module), query_config["query_type"]
        )  # "module.submodule"
        queries.append(QueryClass(query_config["query_data"]))

    with ThreadPoolExecutor() as executor:
        # Parallel probing
        futures = {
            executor.submit(deploy_probes_to_agent, agent, queries, probe_event_logger): agent
            for agent in agents
        }
        for future, agent in futures.items():
            try:
                future.result()
            except Exception:
                logger.exception(
                    "Probe deployment failed for agent=%s",
                    getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
                )
