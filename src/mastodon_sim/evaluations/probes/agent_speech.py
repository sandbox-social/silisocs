import importlib
import logging
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from concordia.typing import entity, entity_component

from mastodon_sim.evaluations.probes.types import PROBE_TYPES
from mastodon_sim.evaluations.probes.types import AgentQuery as _AgentQuery

AgentQuery = _AgentQuery

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


def _build_questionnaire_prompt(agent, queries):
    lines = [
        "You are completing a survey in character.",
        "Return only answer lines in this exact style and order:",
        "Q0: <answer>",
        "Q1: <answer>",
        "Do not include any other text.",
        "",
        "Questions:",
    ]
    for idx, query in enumerate(queries):
        lines.append(f"Q{idx}: {query.form_question_for_agent(agent)}")
    return "\n".join(lines)


def _parse_questionnaire_answers(raw_response: str, expected_count: int) -> dict[str, str]:
    parsed: dict[str, str] = {}
    # Match Q<n> anywhere on a line (not just at line start) to handle cases
    # where Concordia prepends the agent name to the first line of output.
    line_pattern = re.compile(r"(?im)(?:^|\s)q(?P<idx>\d+)\s*[:\-]\s*(?P<answer>.+?)\s*$")
    for match in line_pattern.finditer(raw_response):
        idx = int(match.group("idx"))
        key = f"q{idx}"
        if key not in parsed:
            parsed[key] = match.group("answer").strip()

    # Some small models use Q1..Qn labels even when prompted for Q0..Q(n-1).
    # Normalize one-based indexing if all expected answers are present that way.
    if expected_count > 0 and "q0" not in parsed:
        one_based_keys = [f"q{i}" for i in range(1, expected_count + 1)]
        if all(k in parsed for k in one_based_keys):
            parsed = {f"q{i - 1}": parsed[f"q{i}"] for i in range(1, expected_count + 1)}

    if parsed:
        return parsed

    numbered_pattern = re.compile(r"(?im)^\s*(?P<num>\d+)[\)\.\:\-]\s*(?P<answer>.+?)\s*$")
    for match in numbered_pattern.finditer(raw_response):
        idx = int(match.group("num")) - 1
        if idx >= 0:
            parsed[f"q{idx}"] = match.group("answer").strip()
    if parsed:
        return parsed

    fallback_lines = [line.strip() for line in raw_response.splitlines() if line.strip()]
    for idx, line in enumerate(fallback_lines[:expected_count]):
        parsed[f"q{idx}"] = line
    if parsed:
        return parsed

    raise ValueError("Could not parse questionnaire answers from response.")


def _recover_agent_phase(agent) -> None:
    """Best-effort recovery when probe act fails mid-transition."""
    phase_lock = getattr(agent, "_phase_lock", None)
    try:
        if phase_lock is not None:
            with phase_lock:
                if getattr(agent, "_phase", None) != entity_component.Phase.READY:
                    agent._phase = entity_component.Phase.READY
        elif getattr(agent, "_phase", None) != entity_component.Phase.READY:
            agent._phase = entity_component.Phase.READY
    except Exception:
        logger.exception(
            "Failed to recover phase for agent=%s after probe act failure.",
            getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
        )


def _ask_structured_questionnaire(agent, queries) -> dict[str, str]:
    questionnaire_prompt = _build_questionnaire_prompt(agent, queries)
    try:
        action_spec = entity.ActionSpec(
            call_to_action=questionnaire_prompt,
            output_type=entity.OutputType.FREE,
            tag="query",
        )
    except TypeError:
        action_spec = entity.ActionSpec(
            call_to_action=questionnaire_prompt,
            output_type=entity.OutputType.FREE,
        )
    try:
        raw_response = agent.act(action_spec=action_spec)
    except Exception:
        _recover_agent_phase(agent)
        raise
    return _parse_questionnaire_answers(raw_response, expected_count=len(queries))


def _run_legacy_queries(agent, queries, structured_error: Exception | None = None) -> list[dict]:
    """Fallback to the original one-query-per-call behavior."""
    agent_results = []
    for query in queries:
        query_name = getattr(query, "probe_name", getattr(query, "name", type(query).__name__))
        try:
            _recover_agent_phase(agent)
            agent_query_return = query.submit(agent)
            agent_query_return["query_mode"] = "legacy_per_query"
            if structured_error is not None:
                agent_query_return["structured_query_error"] = str(structured_error)
        except Exception as exc:
            _recover_agent_phase(agent)
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
                "query_mode": "legacy_per_query",
            }
            if structured_error is not None:
                agent_query_return["structured_query_error"] = str(structured_error)

        agent_results.append(
            {
                "source_user": agent._agent_name,
                "label": query_name,
                "data": agent_query_return,
            }
        )
    return agent_results


def _run_single_legacy_query(
    agent,
    query,
    *,
    structured_error: Exception | None = None,
    fallback_reason: str | None = None,
) -> dict:
    """Fallback one failed structured question to legacy mode."""
    query_name = getattr(query, "probe_name", getattr(query, "name", type(query).__name__))
    try:
        _recover_agent_phase(agent)
        agent_query_return = query.submit(agent)
        agent_query_return["query_mode"] = "legacy_per_query_fallback"
        if structured_error is not None:
            agent_query_return["structured_query_error"] = str(structured_error)
        if fallback_reason is not None:
            agent_query_return["structured_fallback_reason"] = fallback_reason
    except Exception as exc:
        _recover_agent_phase(agent)
        logger.exception(
            "Legacy fallback query failed for agent=%s query=%s",
            getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
            query_name,
        )
        agent_query_return = {
            "query_type": query_name,
            "query_return": None,
            "raw_response": None,
            "query_error": str(exc),
            "query_mode": "legacy_per_query_fallback",
        }
        if structured_error is not None:
            agent_query_return["structured_query_error"] = str(structured_error)
        if fallback_reason is not None:
            agent_query_return["structured_fallback_reason"] = fallback_reason

    return {
        "source_user": agent._agent_name,
        "label": query_name,
        "data": agent_query_return,
    }


def deploy_probes_to_agent(agent, queries, probe_event_logger):
    if not queries:
        return

    agent_results: list[dict] = []
    try:
        answers_by_id = _ask_structured_questionnaire(agent, queries)
    except Exception as exc:
        logger.exception(
            "Structured probe questionnaire failed for agent=%s. Falling back to legacy mode.",
            getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
        )
        _recover_agent_phase(agent)
        agent_results = _run_legacy_queries(agent, queries, structured_error=exc)
        probe_event_logger.log(agent_results)
        return

    for idx, query in enumerate(queries):
        query_name = getattr(query, "probe_name", getattr(query, "name", type(query).__name__))
        key = f"q{idx}"
        raw_answer = answers_by_id.get(key, "")

        if key not in answers_by_id or not str(raw_answer).strip():
            agent_results.append(
                _run_single_legacy_query(
                    agent,
                    query,
                    fallback_reason=f"missing_or_empty_structured_answer:{key}",
                )
            )
            continue

        try:
            agent_query_return = query.submit_with_raw_response(raw_answer)
            agent_query_return["query_mode"] = "single_structured_lines"
        except Exception as exc:
            logger.exception(
                "Structured probe parse failed for agent=%s query=%s",
                getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
                query_name,
            )
            agent_results.append(
                _run_single_legacy_query(
                    agent,
                    query,
                    structured_error=exc,
                    fallback_reason=f"structured_parse_failed:{key}",
                )
            )
            continue

        agent_results.append(
            {
                "source_user": agent._agent_name,
                "label": query_name,
                "data": agent_query_return,
            }
        )

    probe_event_logger.log(agent_results)


def _resolve_query_class(query_type: str, query_lib_module: str | None) -> type:
    """Resolve a query class by name: built-in probe types first, then importlib."""
    if query_type in PROBE_TYPES:
        return PROBE_TYPES[query_type]
    if query_lib_module:
        module = importlib.import_module(query_lib_module)
        cls = getattr(module, query_type, None)
        if cls is not None:
            return cls
    raise ImportError(
        f"Unknown probe type '{query_type}'. Built-in types: {list(PROBE_TYPES.keys())}"
    )


def deploy_probes(
    agents,
    probes,
    probe_event_logger,
    worker_limit: int | None = None,
    prebuilt_queries: list | None = None,
):
    if prebuilt_queries is not None:
        queries = prebuilt_queries
    else:
        query_lib_module = probes.get("query_lib_module") if probes else None
        raw_queries = probes.get("queries", {}) if probes else {}
        if isinstance(raw_queries, Mapping):
            queries_config = list(raw_queries.values())
        elif isinstance(raw_queries, Sequence) and not isinstance(raw_queries, (str, bytes)):
            queries_config = list(raw_queries)
        else:
            queries_config = []
        queries = []
        for query_config in queries_config:
            if not isinstance(query_config, dict):
                continue
            QueryClass = _resolve_query_class(query_config["query_type"], query_lib_module)
            query_data = query_config.get("query_data", {})
            if not isinstance(query_data, dict):
                query_data = {}
            query_obj = QueryClass(query_data)
            probe_name = query_config.get("probe_name") or query_data.get("name")
            if probe_name:
                query_obj.probe_name = str(probe_name)
            queries.append(query_obj)

    max_workers = None if worker_limit is None or worker_limit <= 0 else worker_limit
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(deploy_probes_to_agent, agent, queries, probe_event_logger): agent
            for agent in agents
        }
        for future in as_completed(futures):
            agent = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception(
                    "Probe deployment failed for agent=%s",
                    getattr(agent, "_agent_name", getattr(agent, "name", "unknown")),
                )
