# src/sim/config_utils/social_media_functions.py
"""
Social media configuration utility functions.
Updated to work with YAML-based configuration.
"""

import random

try:
    import networkx as nx
except ImportError:
    nx = None

from sim.config_utils.social_media_dataclasses import SimRoleParameters


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

    following: dict[str, list[str]] = {agent: [] for agent in all_agents}

    # NetworkX generators usually emit undirected edges (except specific directed ones)
    for u, v in G.edges():
        name_u = node_mapping[u]
        name_v = node_mapping[v]

        # Add connection (undirected -> bidirectional follow)
        following[name_u].append(name_v)
        following[name_v].append(name_u)

    # Ensure candidates are followed by everyone
    for agent in all_agents:
        for cand in candidates:
            if agent == cand:
                continue
            if cand not in following[agent]:
                following[agent].append(cand)

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
