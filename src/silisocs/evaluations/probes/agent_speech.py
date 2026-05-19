"""agent_speech module. Auto-generated module docstring."""

import importlib
import logging
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from silisocs.evaluations.probes.types import PROBE_TYPES
from silisocs.runtime.types import ActionSpec, OutputType

logger = logging.getLogger(__name__)

DEFAULT_CALL_TO_SPEECH = (
    "Given the above, what is {name} likely to say next? Respond in"
    ' the format `{name} -- "..."` For example, '
    'Cristina -- "Hello! Mighty fine weather today, right?", '
    'Ichabod -- "I wonder if the alfalfa is ready to harvest", or '
    'Townsfolk -- "Good morning".\n'
)


def _agent_name(agent) -> str:
    """Return the public agent name without depending on private storage."""
    return str(getattr(agent, "name", getattr(agent, "_agent_name", "unknown")))


def write_seed_toot(agent):
    """write_seed_toot.

    :param agent:
    """
    agent_name = _agent_name(agent)
    call_to_speech = DEFAULT_CALL_TO_SPEECH.format(
        name=agent_name,
    )
    interaction_premise = f"{agent_name} has to make their first post on Social Media\n"
    interrogation = interaction_premise
    interrogation += "Thought on Social Media post: In less than 100 words, write a toot that aligns with your views and background."
    agent_says = agent.act(
        action_spec=ActionSpec(
            prompt="Context: " + interrogation + call_to_speech,
            output_type=OutputType.TEXT,
        ),
    )
    # Some agent names are single tokens; strip available name tokens safely.
    cleaned = str(agent_says).strip()
    for token in agent_name.split():
        if token:
            cleaned = cleaned.strip(token).strip()
    agent_says = cleaned.strip("-").strip().strip('"')
    return agent_says


def _build_questionnaire_prompt(agent, probes):
    """_build_questionnaire_prompt.

    :param agent:
    :param probes:
    """
    lines = [
        "You are completing a survey in character.",
        "Return only answer lines in this exact style and order:",
        "Q0: <answer>",
        "Q1: <answer>",
        "Do not include any other text.",
        "",
        "Questions:",
    ]
    for idx, query in enumerate(probes):
        lines.append(f"Q{idx}: {query.form_question_for_agent(agent)}")
    return "\n".join(lines)


def _parse_questionnaire_answers(raw_response: str, expected_count: int) -> dict[str, str]:
    """_parse_questionnaire_answers.

    :param str raw_response:
    :type raw_response: str
    :param int expected_count:
    :type expected_count: int

    :returns: dict[str, str]
    :rtype: dict[str, str]
    """
    parsed: dict[str, str] = {}
    # Match Q<n> anywhere on a line (not just at line start) to handle cases
    # where the act wrapper prepends the agent name to the first line of output.
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


def _ask_structured_questionnaire(agent, probes) -> dict[str, str]:
    """_ask_structured_questionnaire.

    :param agent:
    :param probes:

    :returns: dict[str, str]
    :rtype: dict[str, str]
    """
    questionnaire_prompt = _build_questionnaire_prompt(agent, probes)
    action_spec = ActionSpec(
        prompt=questionnaire_prompt,
        output_type=OutputType.TEXT,
        tag="probe",
    )
    raw_response = str(agent.act(action_spec=action_spec))
    return _parse_questionnaire_answers(raw_response, expected_count=len(probes))


def _run_single_probes(agent, probes, structured_error: Exception | None = None) -> list[dict]:
    """Fallback to native one-query-per-call probing."""
    agent_results = []
    for probe in probes:
        probe_name = getattr(probe, "probe_name", getattr(probe, "name", type(probe).__name__))
        try:
            agent_probe_return = probe.submit(agent)
            agent_probe_return["probe_mode"] = "single_probe"
            if structured_error is not None:
                agent_probe_return["structured_probe_error"] = str(structured_error)
        except Exception as exc:
            logger.exception(
                "Probe failed for agent=%s probe=%s",
                _agent_name(agent),
                probe_name,
            )
            agent_probe_return = {
                "probe_type": probe_name,
                "probe_return": None,
                "raw_response": None,
                "probe_error": str(exc),
                "probe_mode": "single_probe",
            }
            if structured_error is not None:
                agent_probe_return["structured_probe_error"] = str(structured_error)

        agent_results.append(
            {
                "source_user": _agent_name(agent),
                "label": probe_name,
                "data": agent_probe_return,
            }
        )
    return agent_results


def _run_single_probe_query(
    agent,
    probe,
    *,
    structured_error: Exception | None = None,
    fallback_reason: str | None = None,
) -> dict:
    """Fallback one failed structured question to native single-probe mode."""
    probe_name = getattr(probe, "probe_name", getattr(probe, "name", type(probe).__name__))
    try:
        agent_probe_return = probe.submit(agent)
        agent_probe_return["probe_mode"] = "single_probe_fallback"
        if structured_error is not None:
            agent_probe_return["structured_probe_error"] = str(structured_error)
        if fallback_reason is not None:
            agent_probe_return["structured_fallback_reason"] = fallback_reason
    except Exception as exc:
        logger.exception(
            "Single-probe fallback query failed for agent=%s query=%s",
            _agent_name(agent),
            probe_name,
        )
        agent_probe_return = {
            "probe_type": probe_name,
            "probe_return": None,
            "raw_response": None,
            "probe_error": str(exc),
            "probe_mode": "single_probe_fallback",
        }
        if structured_error is not None:
            agent_probe_return["structured_probe_error"] = str(structured_error)
        if fallback_reason is not None:
            agent_probe_return["structured_fallback_reason"] = fallback_reason

    return {
        "source_user": _agent_name(agent),
        "label": probe_name,
        "data": agent_probe_return,
    }


def deploy_probes_to_agent(agent, probes, probe_event_logger):
    """deploy_probes_to_agent.

    :param agent:
    :param probes:
    :param probe_event_logger:
    """
    if not probes:
        return

    agent_results: list[dict] = []
    try:
        answers_by_id = _ask_structured_questionnaire(agent, probes)
    except Exception as exc:
        logger.exception(
            "Structured probe questionnaire failed for agent=%s. Falling back to single-probe mode.",
            _agent_name(agent),
        )
        agent_results = _run_single_probes(agent, probes, structured_error=exc)
        probe_event_logger.log(agent_results)
        return

    for idx, probe in enumerate(probes):
        probe_name = getattr(probe, "probe_name", getattr(probe, "name", type(probe).__name__))
        key = f"q{idx}"
        raw_answer = answers_by_id.get(key, "")

        if key not in answers_by_id or not str(raw_answer).strip():
            agent_results.append(
                _run_single_probe_query(
                    agent,
                    probe,
                    fallback_reason=f"missing_or_empty_structured_answer:{key}",
                )
            )
            continue

        try:
            agent_probe_return = probe.submit_with_raw_response(raw_answer)
            agent_probe_return["probe_mode"] = "single_structured_lines"
        except Exception as exc:
            logger.exception(
                "Structured probe parse failed for agent=%s probe=%s",
                _agent_name(agent),
                probe_name,
            )
            agent_results.append(
                _run_single_probe_query(
                    agent,
                    probe,
                    structured_error=exc,
                    fallback_reason=f"structured_parse_failed:{key}",
                )
            )
            continue

        agent_results.append(
            {
                "source_user": _agent_name(agent),
                "label": probe_name,
                "data": agent_probe_return,
            }
        )

    probe_event_logger.log(agent_results)


def _resolve_probe_class(probe_type: str, probe_lib_module: str | None) -> type:
    """Resolve a probe class by name: built-in probe types first, then importlib."""
    if probe_type in PROBE_TYPES:
        return PROBE_TYPES[probe_type]
    if probe_lib_module:
        module = importlib.import_module(probe_lib_module)
        cls = getattr(module, probe_type, None)
        if cls is not None:
            return cls
    raise ImportError(
        f"Unknown probe type '{probe_type}'. Built-in types: {list(PROBE_TYPES.keys())}"
    )


def deploy_probes(
    agents,
    probes,
    probe_event_logger,
    worker_limit: int | None = None,
    prebuilt_probes: list | None = None,
):
    """deploy_probes.

    :param agents:
    :param probes:
    :param probe_event_logger:
    :param int | None worker_limit:
    :type worker_limit: int | None
    :param list | None prebuilt_probes:
    :type prebuilt_probes: list | None
    """
    if prebuilt_probes is not None:
        probes = prebuilt_probes
    else:
        probe_lib_module = probes.get("probe_lib_module") if probes else None
        raw_probes = probes.get("probes", {}) if probes else {}
        if isinstance(raw_probes, Mapping):
            probes_config = list(raw_probes.values())
        elif isinstance(raw_probes, Sequence) and not isinstance(raw_probes, (str, bytes)):
            probes_config = list(raw_probes)
        else:
            probes_config = []
        probes = []
        for probe_config in probes_config:
            if not isinstance(probe_config, dict):
                continue
            ProbeClass = _resolve_probe_class(probe_config["probe_type"], probe_lib_module)
            probe_data = probe_config.get("probe_data", {})
            if not isinstance(probe_data, dict):
                probe_data = {}
            probe_obj = ProbeClass(probe_data)
            probe_name = probe_config.get("probe_name") or probe_data.get("name")
            if probe_name:
                probe_obj.probe_name = str(probe_name)
            probes.append(probe_obj)

    max_workers = None if worker_limit is None or worker_limit <= 0 else worker_limit
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(deploy_probes_to_agent, agent, probes, probe_event_logger): agent
            for agent in agents
        }
        for future in as_completed(futures):
            agent = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception(
                    "Probe deployment failed for agent=%s",
                    _agent_name(agent),
                )
