"""Adapter to integrate AgentQuery system with Interviewer GameMaster."""

import importlib
from typing import Any

from concordia.contrib.data.questionnaires import base_questionnaire
from concordia.contrib.data.questionnaires.base_questionnaire import Question


class AgentQueryQuestionnaire(base_questionnaire.QuestionnaireBase):
    """Questionnaire wrapper for AgentQuery instances."""

    def __init__(
        self,
        queries: list,  # [AgentQuery],
        name: str = "AgentProbes",
        description: str = "Agent query probes",
    ):
        """Initialize questionnaire from AgentQuery instances.

        Args:
            queries: List of AgentQuery instances to convert
            name: Name for this questionnaire
            description: Description for this questionnaire
        """
        self.queries = queries
        self.query_by_id = {}  # Maps question_id to AgentQuery instance

        questions = []
        dimensions = set()

        for idx, query in enumerate(queries):
            q_id = f"query_{idx}"
            self.query_by_id[q_id] = query

            # Extract dimension from query_data
            dimension = query.query_data.get("query_type", f"dimension_{idx}")
            dimensions.add(dimension)

            # Create Question object
            question = base_questionnaire.Question(
                statement=query.question_template,
                dimension=dimension,
                preprompt="",
                choices=None,  # Free-form response
                ascending_scale=True,
            )
            questions.append(question)

        super().__init__(
            name=name,
            description=description,
            questionnaire_type="open-ended",
            observation_preprompt="Please answer the following question:",
            questions=questions,
            preprompt="",
            dimensions=list(dimensions),
            context="",
        )

    def process_answer(
        self, player_name: str, answer_text: str, question: Question
    ) -> tuple[str, Any]:
        """Process answer using the original AgentQuery's parse_answer method.

        Args:
            player_name: Name of the player/agent
            answer_text: Raw answer text from the agent
            question: The Question object

        Returns
        -------
            Tuple of (dimension, parsed_answer_value)
        """
        # Find the corresponding AgentQuery for this question
        query = None
        for q_id, q in self.query_by_id.items():
            if q.question_template == question.statement:
                query = q
                break

        if query is None:
            return question.dimension, answer_text

        # Use the query's parse_answer method
        try:
            parsed_value = query.parse_answer(answer_text)
            return question.dimension, parsed_value
        except Exception as e:
            print(f"Error parsing answer for {player_name}: {e}")
            return question.dimension, answer_text

    def aggregate_results(self, player_answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Aggregate results by dimension.

        Args:
            player_answers: Dict of {question_id: answer_data}

        Returns
        -------
            Dict of aggregated results
        """
        # Group by dimension and collect values
        dimension_results: dict[str, list] = {}

        for question_id, answer_data in player_answers.items():
            dimension = answer_data["dimension"]
            value = answer_data["value"]

            if dimension not in dimension_results:
                dimension_results[dimension] = []

            dimension_results[dimension].append(
                {
                    "statement": answer_data["statement"],
                    "text": answer_data["text"],
                    "value": value,
                }
            )

        # For queries, we typically want individual results, not aggregation
        # So we return all values per dimension
        aggregated = {}
        for dim, values in dimension_results.items():
            if len(values) == 1:
                aggregated[dim] = values[0]["value"]
            else:
                aggregated[dim] = [v["value"] for v in values]

        return aggregated

    def plot_results(
        self,
        results_df,
        label_column: str | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Plot results - implement based on your visualization needs."""
        print(f"Plotting not implemented for {self.name}")

    def get_dimension_ranges(self) -> dict[str, tuple[float, float]]:
        """Return dimension ranges if applicable."""
        return {}


def convert_queries_to_questionnaire(
    queries: list,  # [AgentQuery],
    name: str = "AgentProbes",
) -> base_questionnaire.QuestionnaireBase:
    """Convert a list of AgentQuery instances to a QuestionnaireBase.

    Args:
        queries: List of AgentQuery instances
        name: Name for the questionnaire

    Returns
    -------
        QuestionnaireBase instance compatible with Interviewer GM
    """
    return AgentQueryQuestionnaire(queries=queries, name=name)


def load_queries_from_config(
    query_lib_module: str,
    queries_data: dict[str, Any],
) -> list:  # [AgentQuery]:
    """Load AgentQuery instances from configuration.

    Args:
        query_lib_module: Module path for query classes
        queries_data: Dict of query configurations

    Returns
    -------
        List of instantiated AgentQuery objects
    """
    query_lib_module = "example_sim_pkg." + query_lib_module
    queries = []

    for query_data in queries_data.values():
        QueryClass = getattr(importlib.import_module(query_lib_module), query_data["query_type"])
        queries.append(QueryClass(query_data))

    return queries


def extract_results_for_logger(
    gm_answers: dict[str, dict[str, Any]],
    questionnaire: AgentQueryQuestionnaire,
) -> list[dict[str, Any]]:
    """Extract results from GM format to your original logger format.

    Args:
        gm_answers: Answers dict from questionnaire.get_answers()
        questionnaire: The AgentQueryQuestionnaire instance

    Returns
    -------
        List of results in your original format for logging
    """
    agent_results = []

    for agent_name, questionnaire_answers in gm_answers.items():
        if questionnaire.name in questionnaire_answers:
            q_answers = questionnaire_answers[questionnaire.name]

            for question_id, answer_data in q_answers.items():
                result = {
                    "source_user": agent_name,
                    "label": answer_data["dimension"],
                    "data": {
                        "query_type": answer_data["dimension"],
                        "query_return": answer_data["value"],
                        "statement": answer_data["statement"],
                        "raw_text": answer_data["text"],
                    },
                }
                agent_results.append(result)

    return agent_results


# Example usage function
def create_interviewer_gm_with_queries(
    probes_config: dict[str, Any],
    player_names: list[str],
) -> tuple[list[base_questionnaire.QuestionnaireBase], "AgentQueryQuestionnaire"]:
    """Create questionnaires for Interviewer GM from your probes config.

    Args:
        probes_config: Your probes configuration dict
        player_names: List of player/agent names

    Returns
    -------
        Tuple of (questionnaires_list, query_questionnaire) for GM initialization
    """
    # Load queries from config
    queries = load_queries_from_config(
        probes_config["query_lib_module"], probes_config["queries_data"]
    )

    # Convert to questionnaire
    query_questionnaire = convert_queries_to_questionnaire(queries=queries, name="AgentProbes")

    return [query_questionnaire], query_questionnaire


# Integration example
def deploy_probes_via_gm(
    game_master,  # The Interviewer GM instance
    probe_event_logger,
    questionnaire: AgentQueryQuestionnaire,
):
    """Deploy probes via GameMaster and log results.

    Args:
        game_master: The Interviewer GM entity
        probe_event_logger: Your existing logger
        questionnaire: The AgentQueryQuestionnaire instance
    """
    # The GM will handle the questionnaire administration automatically
    # through its act cycle. After completion, extract results:

    # Get the questionnaire component from GM
    questionnaire_component = game_master._context_components.get("questionnaire")

    if questionnaire_component and questionnaire_component.is_done():
        # Extract answers
        gm_answers = questionnaire_component.get_answers()

        # Convert to your logger format
        agent_results = extract_results_for_logger(gm_answers, questionnaire)

        # Log results
        probe_event_logger.log(agent_results)

        return agent_results
    print("Questionnaire not yet complete")
    return None
