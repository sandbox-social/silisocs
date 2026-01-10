# src/sim/config_utils/social_media_functions.py
"""
Social media configuration utility functions.
Updated to work with YAML-based configuration.
"""

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


def get_simrole_parameters(
    active_rates: dict[str, float],
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
        active_rates_per_episode=active_rates,
        initial_follow_prob=get_followership_connection_stats(
            roles, fully_connected_targets, base_probability
        ),
    )

    return simrole_parameters
