from typing import Any

from scenarios.election.scenario_constants import (
    BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY,
)


def get_followership_connection_stats(
    roles: list, fully_connected_targets: list
) -> dict[Any, dict[Any, Any]]:
    """Generate followership statistics."""
    p_from_to: dict[str, dict[str, float]] = {}
    for role_i in roles:
        p_from_to[role_i] = {}
        for role_j in roles:
            if role_j in fully_connected_targets:
                p_from_to[role_i][role_j] = 1.0
            else:
                p_from_to[role_i][role_j] = BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY
    return p_from_to
