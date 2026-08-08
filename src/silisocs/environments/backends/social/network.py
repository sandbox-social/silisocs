"""Social backend graph-generation helpers."""

import json
import random
from pathlib import Path
from typing import Any

import networkx as nx


def _coerce_predefined_node(
    value: Any,
    *,
    agent_names: list[str],
    node_to_agent: dict[Any, str],
) -> str | None:
    """Map a predefined graph node identifier onto a configured agent name."""
    if value in node_to_agent:
        return node_to_agent[value]

    value_str = str(value)
    if value_str in node_to_agent:
        return node_to_agent[value_str]
    if value_str in agent_names:
        return value_str

    try:
        value_int = int(value)
    except (TypeError, ValueError):
        value_int = None

    if value_int is not None:
        if value_int in node_to_agent:
            return node_to_agent[value_int]
        if 0 <= value_int < len(agent_names):
            return agent_names[value_int]

    return None


def _normalize_predefined_graph_payload(
    payload: Any,
    agent_names: list[str],
) -> dict[str, list[str]]:
    """Normalize supported predefined graph JSON shapes to follower -> followees.

    Supported inputs are:
      * ``{"nodes": [...], "edges": [[u, v], ...]}``, as emitted by
        EchoChamberSim. Edges are treated as undirected follow ties.
      * ``{"Agent_0": ["Agent_1"]}`` or ``{"0": [1]}`` adjacency maps.
      * Wrappers such as ``{"adjacency": {...}}`` or ``{"graph": {...}}``.
    """
    following_sets: dict[str, set[str]] = {name: set() for name in agent_names}

    if not isinstance(payload, dict):
        raise ValueError("Predefined social graph payload must be a JSON object.")

    for wrapper_key in ("adjacency", "following", "follow_graph", "graph"):
        wrapped = payload.get(wrapper_key)
        if isinstance(wrapped, dict):
            return _normalize_predefined_graph_payload(wrapped, agent_names)

    node_to_agent: dict[Any, str] = {}
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        for idx, node in enumerate(nodes):
            if idx >= len(agent_names):
                break
            node_to_agent[node] = agent_names[idx]
            node_to_agent[str(node)] = agent_names[idx]

    edges = payload.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, (list, tuple)) or len(edge) < 2:
                raise ValueError(f"Malformed predefined graph edge: {edge!r}")
            source = _coerce_predefined_node(
                edge[0], agent_names=agent_names, node_to_agent=node_to_agent
            )
            target = _coerce_predefined_node(
                edge[1], agent_names=agent_names, node_to_agent=node_to_agent
            )
            if source and target and source != target:
                following_sets[source].add(target)
                following_sets[target].add(source)
        return {name: sorted(following_sets[name]) for name in agent_names}

    adjacency_like = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "nodes",
            "edges",
            "clustering_coefficient",
            "average_path_length",
            "density",
            "diameter",
        }
    }
    for raw_source, raw_targets in adjacency_like.items():
        source = _coerce_predefined_node(
            raw_source, agent_names=agent_names, node_to_agent=node_to_agent
        )
        if not source:
            raise ValueError(f"Unknown predefined graph source node: {raw_source!r}")
        if isinstance(raw_targets, dict):
            target_iterable = list(raw_targets.keys())
        elif isinstance(raw_targets, (list, tuple, set)):
            target_iterable = list(raw_targets)
        else:
            raise ValueError(
                f"Predefined graph adjacency for {raw_source!r} must be a list or mapping."
            )
        for raw_target in target_iterable:
            target = _coerce_predefined_node(
                raw_target, agent_names=agent_names, node_to_agent=node_to_agent
            )
            if target and target != source:
                following_sets[source].add(target)

    return {name: sorted(following_sets[name]) for name in agent_names}


def get_followership_connection_stats(
    roles: list[str],
    fully_connected_targets: list[str],
    base_probability: float,
) -> dict[str, dict[str, float]]:
    """
    Generate followership statistics for social network.

    Args:
        roles: List of all role names
        fully_connected_targets: Roles that everyone follows
        base_probability: Base probability for non-fully-connected connections

    Returns
    -------
        Dictionary mapping from_role -> to_role -> probability
    """
    p_from_to: dict[str, dict[str, float]] = {}

    for role_i in roles:
        p_from_to[role_i] = {}
        for role_j in roles:
            if role_j in fully_connected_targets:
                p_from_to[role_i][role_j] = 1.0
            else:
                p_from_to[role_i][role_j] = base_probability

    return p_from_to


def generate_random_network(
    agents: list[str],
    sim_roles: dict[str, str],
    role_follow_probabilities: dict[str, dict[str, float]],
    ensure_candidate_following: bool = True,
) -> dict[str, list[str]]:
    """
    Generate a random social network based on role probabilities.

    Args:
        agents: List of agent names.
        sim_roles: Agent name -> role name mapping.
        role_follow_probabilities: Role-to-role follow probability matrix.
        ensure_candidate_following: If True, ensures all agents follow candidates.

    Returns
    -------
        Adjacency list mapping follower -> list of followees.
    """
    following_lists: dict[str, list[str]] = {}

    # Identify candidates for constraint enforcement
    candidates = []
    if ensure_candidate_following:
        candidates = [agent for agent, role in sim_roles.items() if role == "candidate"]

    for agent_i, role_i in sim_roles.items():
        if agent_i not in agents:
            continue

        following_lists[agent_i] = []
        for agent_j, role_j in sim_roles.items():
            if agent_i == agent_j:
                continue
            if agent_j not in agents:
                continue

            prob = role_follow_probabilities.get(role_i, {}).get(role_j, 0.0)
            if random.random() < prob:
                following_lists[agent_i].append(agent_j)

        # Enforce candidate following
        if ensure_candidate_following:
            for cand in candidates:
                if agent_i == cand:
                    continue
                if cand not in following_lists[agent_i]:
                    following_lists[agent_i].append(cand)

    return following_lists


def generate_graph_from_networkx(
    agents: list[str],
    candidates: list[str],
    graph_type: str = "barabasi_albert",
    **kwargs,
) -> dict[str, list[str]]:
    """
    Generate a social network using NetworkX generators.
    Automatically enforces that all agents follow candidates.

    Args:
        agents: List of all agent names.
        candidates: List of candidate agent names.
        graph_type: Type of graph to generate ('barabasi_albert', 'lfr_benchmark').
        **kwargs: Arguments passed to the networkx generator function.

    Returns
    -------
        Adjacency list mapping follower -> list of followees.
    """
    all_agents = list(agents)
    for cand in candidates:
        if cand not in all_agents:
            all_agents.append(cand)

    all_agents = list(dict.fromkeys(all_agents))
    n = len(all_agents)

    if graph_type == "barabasi_albert":
        m = kwargs.get("m", 2)
        if m >= n:
            m = n - 1
        m = max(m, 1)
        graph = nx.barabasi_albert_graph(n, m)
    elif graph_type == "lfr_benchmark":
        tau1 = kwargs.get("tau1", 3.0)
        tau2 = kwargs.get("tau2", 1.5)
        mu = kwargs.get("mu", 0.1)
        avg_deg = kwargs.get("average_degree", min(5, n - 1))
        min_comm = kwargs.get("min_community", min(20, n // 2)) if n > 10 else 2
        graph = nx.LFR_benchmark_graph(
            n,
            tau1,
            tau2,
            mu,
            average_degree=avg_deg,
            min_community=min_comm,
            seed=42,
        )
    else:
        raise ValueError(
            f"Unknown social graph_type '{graph_type}'. Available: barabasi_albert, lfr_benchmark."
        )

    # Map integer nodes back to agent names
    # Shuffle agents to avoid bias if agent list is ordered by role
    dataset_agents = list(all_agents)
    random.shuffle(dataset_agents)
    node_mapping = dict(enumerate(dataset_agents))

    # Use sets internally for O(1) membership checks, convert to lists at the end.
    following_sets: dict[str, set[str]] = {agent: set() for agent in all_agents}

    for u, v in graph.edges():
        name_u = node_mapping[u]
        name_v = node_mapping[v]
        following_sets[name_u].add(name_v)
        following_sets[name_v].add(name_u)

    # Ensure candidates are followed by everyone
    for agent in all_agents:
        for cand in candidates:
            if agent != cand:
                following_sets[agent].add(cand)

    following: dict[str, list[str]] = {agent: sorted(s) for agent, s in following_sets.items()}
    return following


def generate_follow_network(
    agent_names: list[str],
    sim_roles: dict[str, str],
    graph_config: dict,
) -> dict[str, list[str]]:
    """Generate a follow network from GM initialize graph config.

    This is the single entry-point that SM app backends call.  It reads
    ``network_type``, ``barabasi_albert_m``, ``fully_connected_targets``,
    and ``base_followership_probability`` from the config dict, determines
    which agents act as "hubs" (fully-connected targets), and delegates to
    the appropriate graph generator.

    Args:
        agent_names: All agent display names.
        sim_roles: Agent name -> role name mapping.
        graph_config: ``env.gm.components.initialize.params.graph``.

    Returns
    -------
        Dict mapping each agent name to a list of agents they follow.
    """
    network_type = graph_config.get("network_type", "barabasi_albert")
    ba_m = int(graph_config.get("barabasi_albert_m", 30))
    fully_connected = graph_config.get("fully_connected_targets", [])
    base_prob = float(graph_config.get("base_followership_probability", 0.4))

    # Identify hub agents (agents whose role is in fully_connected_targets).
    hub_agents = [name for name in agent_names if sim_roles.get(name, "") in fully_connected]

    if network_type == "barabasi_albert":
        return generate_graph_from_networkx(
            agent_names,
            hub_agents,
            graph_type="barabasi_albert",
            m=ba_m,
        )
    if network_type == "lfr_benchmark":
        return generate_graph_from_networkx(
            agent_names,
            hub_agents,
            graph_type="lfr_benchmark",
        )
    if network_type == "random":
        # Build a simple role-probability matrix for random network.
        roles = list({r for r in sim_roles.values() if r})
        follow_prob = get_followership_connection_stats(roles, list(fully_connected), base_prob)
        return generate_random_network(
            agent_names,
            sim_roles,
            follow_prob,
            ensure_candidate_following=bool(hub_agents),
        )
    if network_type == "predefined":
        predefined = graph_config.get("predefined_graph", {})
        predefined_path = str(graph_config.get("predefined_graph_path", "")).strip()
        if predefined_path:
            path = Path(predefined_path)
            if not path.is_file():
                raise FileNotFoundError(f"Predefined social graph file not found: {path}")
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            predefined = _normalize_predefined_graph_payload(payload, agent_names)
        else:
            predefined = _normalize_predefined_graph_payload(predefined, agent_names)
        return {name: list(predefined.get(name, [])) for name in agent_names}

    raise ValueError(
        f"Unknown social network_type '{network_type}'. "
        "Available: barabasi_albert, lfr_benchmark, random, predefined."
    )
