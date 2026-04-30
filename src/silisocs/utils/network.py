# src/sim/config_utils/social_media_functions.py
"""
Social media configuration utility functions.
Updated to work with YAML-based configuration.
"""

import json
import random
from pathlib import Path

try:
    import networkx as nx
except ImportError:
    nx = None

from silisocs.utils.social_media_dataclasses import SimRoleParameters


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
    user_data: dict,
    ensure_candidate_following: bool = True,
) -> dict[str, list[str]]:
    """
    Generate a random social network based on role probabilities.
    Restores original logic but with optional candidate constraint enforcement.

    Args:
        agents: List of agent names.
        user_data: Dictionary containing 'sim_roles' and 'sim_role_parameters'.
        ensure_candidate_following: If True, ensures all agents follow candidates.

    Returns
    -------
        Adjacency list mapping follower -> list of followees.
    """
    role_prob_matrix = user_data["sim_role_parameters"]["initial_follow_prob"]
    following_lists: dict[str, list[str]] = {}

    # Identify candidates for constraint enforcement
    candidates = []
    if ensure_candidate_following:
        candidates = [
            agent for agent, role in user_data["sim_roles"].items() if role == "candidate"
        ]

    for agent_i, role_i in user_data["sim_roles"].items():
        if agent_i not in agents:
            continue

        following_lists[agent_i] = []
        for agent_j, role_j in user_data["sim_roles"].items():
            if agent_i == agent_j:
                continue
            if agent_j not in agents:
                continue

            prob = role_prob_matrix.get(role_i, {}).get(role_j, 0.0)
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
    if nx is None:
        print("Warning: NetworkX not installed. Returning empty graph (candidates only).")
        # Fallback simplistic
        fallback_following: dict[str, list[str]] = {agent: [] for agent in agents}
        for agent in agents:
            for cand in candidates:
                if agent != cand:
                    fallback_following[agent].append(cand)
        return fallback_following

    all_agents = list(agents)
    for cand in candidates:
        if cand not in all_agents:
            all_agents.append(cand)

    all_agents = list(dict.fromkeys(all_agents))
    n = len(all_agents)

    G = None

    try:
        if graph_type == "barabasi_albert":
            # Default m=2 if not provided
            m = kwargs.get("m", 2)
            if m >= n:
                m = n - 1
            m = max(m, 1)
            G = nx.barabasi_albert_graph(n, m)

        elif graph_type == "lfr_benchmark":
            # LFR requires specific params. Setting safe defaults if not provided.
            # These defaults are arbitrary to ensure it runs for small N.
            tau1 = kwargs.get("tau1", 3.0)
            tau2 = kwargs.get("tau2", 1.5)
            mu = kwargs.get("mu", 0.1)
            # average_degree must be < n
            avg_deg = kwargs.get("average_degree", min(5, n - 1))
            min_comm = kwargs.get("min_community", min(20, n // 2)) if n > 10 else 2

            # LFR often fails with small N or strict constraints, so we wrap it
            try:
                G = nx.LFR_benchmark_graph(
                    n, tau1, tau2, mu, average_degree=avg_deg, min_community=min_comm, seed=42
                )
            except Exception as e:
                print(f"LFR generation failed ({e}), falling back to Barabási–Albert.")
                m = 2
                if m >= n:
                    m = n - 1
                G = nx.barabasi_albert_graph(n, m)

        else:
            print(f"Unknown graph type '{graph_type}', using Barabási–Albert.")
            m = 2
            if m >= n:
                m = n - 1
            G = nx.barabasi_albert_graph(n, m)

    except Exception as e:
        print(f"Graph generation failed ({e}). Returning candidate connections only.")
        G = nx.empty_graph(n)

    # Map integer nodes back to agent names
    # Shuffle agents to avoid bias if agent list is ordered by role
    dataset_agents = list(all_agents)
    random.shuffle(dataset_agents)
    node_mapping = {i: name for i, name in enumerate(dataset_agents)}

    # Use sets internally for O(1) membership checks, convert to lists at the end.
    following_sets: dict[str, set[str]] = {agent: set() for agent in all_agents}

    for u, v in G.edges():
        name_u = node_mapping[u]
        name_v = node_mapping[v]
        following_sets[name_u].add(name_v)
        following_sets[name_v].add(name_u)

    # Ensure candidates are followed by everyone
    for agent in all_agents:
        for cand in candidates:
            if agent != cand:
                following_sets[agent].add(cand)

    following: dict[str, list[str]] = {agent: list(s) for agent, s in following_sets.items()}
    return following


def get_simrole_parameters(
    activity_transition_rates: dict[str, dict[str, int]],
    roles: list[str],
    fully_connected_targets: list[str],
    base_probability: float,
) -> SimRoleParameters:
    """
    Generate SimRoleParameters from configuration.

    Args:
        active_rates: Activity rates per episode for each role
        roles: List of all role names
        fully_connected_targets: Roles that everyone follows
        base_probability: Base followership probability

    Returns
    -------
        SimRoleParameters instance
    """
    simrole_parameters = SimRoleParameters(
        activity_transition_rates=activity_transition_rates,
        initial_follow_prob=get_followership_connection_stats(
            roles, fully_connected_targets, base_probability
        ),
    )

    return simrole_parameters


def generate_follow_network(
    agent_names: list[str],
    sim_roles: dict[str, str],
    social_network_cfg: dict,
) -> dict[str, list[str]]:
    """Generate a follow network from scenario ``social_network`` config.

    This is the single entry-point that SM app backends call.  It reads
    ``network_type``, ``barabasi_albert_m``, ``fully_connected_targets``,
    and ``base_followership_probability`` from the config dict, determines
    which agents act as "hubs" (fully-connected targets), and delegates to
    the appropriate graph generator.

    Args:
        agent_names: All agent display names.
        sim_roles: Agent name -> role name mapping.
        social_network_cfg: The ``social_network`` section from scenario YAML.

    Returns
    -------
        Dict mapping each agent name to a list of agents they follow.
    """
    network_type = social_network_cfg.get("network_type", "barabasi_albert")
    ba_m = int(social_network_cfg.get("barabasi_albert_m", 30))
    fully_connected = social_network_cfg.get("fully_connected_targets", [])
    base_prob = float(social_network_cfg.get("base_followership_probability", 0.4))

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
        user_data = {
            "sim_roles": sim_roles,
            "sim_role_parameters": {"initial_follow_prob": follow_prob},
        }
        return generate_random_network(
            agent_names, user_data, ensure_candidate_following=bool(hub_agents)
        )
    if network_type == "predefined":
        predefined = social_network_cfg.get("predefined_graph", {})
        predefined_path = str(social_network_cfg.get("predefined_graph_path", "")).strip()
        if predefined_path:
            loaded: dict[str, list[str]] = {}
            try:
                with Path(predefined_path).open(encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    loaded = {str(k): list(v or []) for k, v in payload.items()}
            except Exception as e:
                print(f"Failed to load predefined graph from '{predefined_path}': {e}")
            if loaded:
                predefined = loaded
        return {name: list(predefined.get(name, [])) for name in agent_names}

    print(f"Unknown network type '{network_type}', falling back to barabasi_albert.")
    return generate_graph_from_networkx(
        agent_names,
        hub_agents,
        graph_type="barabasi_albert",
        m=ba_m,
    )
