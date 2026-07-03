"""Branch routers: pick which GM a flow's agent routes to at a branch node.

A flow chain (``env.gm_orchestration.flow_bindings.flow_to_gms``) may contain a
``{branch: {router, choices}}`` node. At that stage each of the flow's agents is
routed by a :class:`Router` to exactly one of the branch's ``choices`` (real GMs).

A router is a pure function of its :class:`RouteContext` in v1 — it reads only the
context fields, takes no agent turn, and returns one of ``ctx.choices``. The two
capability flags on :class:`Router` are the extension seam: a future router that
needs live GM/backend state or an agent's own choice flips them, and the engine
routes it through an execution-time path instead of materialization. Today such a
router is rejected with a clear "not yet supported" message, so the contract stays
honest.
"""

from __future__ import annotations

import hashlib
import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RouteContext:
    """Inputs a router may read to choose a GM.

    v1 carries the pure fields below. Live-GM views and an ``ask_agent`` capability
    get added here later as new fields with defaults, so existing routers keep working
    unchanged.
    """

    agent_name: str
    flow_name: str
    step_index: int
    seed: int
    choices: tuple[str, ...]


class Router(ABC):
    """Selects one GM from a branch's choices for a single agent."""

    # Capability flags drive WHEN the engine may run the router. v1 only supports the
    # pure default (resolved at materialization). A router that needs live state or an
    # agent turn flips these and is (for now) rejected at build time.
    reads_live_state: bool = False
    drives_agent: bool = False
    name: str = "router"

    @abstractmethod
    def route(self, ctx: RouteContext) -> str:
        """Return the chosen GM name; must be one of ``ctx.choices``."""


@dataclass
class RandomChoiceRouter(Router):
    """Weighted random GM pick.

    ``weights`` maps a GM name to a relative weight; any choice absent from the map
    weighs ``1.0``. The pick is deterministic per ``(seed, flow, step, agent)`` — so a
    run reproduces and replays identically — and uses a local RNG (never the global
    one), so concurrent routing never perturbs other RNG consumers.
    """

    weights: Mapping[str, float] = field(default_factory=dict)
    name: str = "random"

    def route(self, ctx: RouteContext) -> str:
        weights = [max(0.0, float(self.weights.get(choice, 1.0))) for choice in ctx.choices]
        if sum(weights) <= 0.0:
            weights = [1.0] * len(ctx.choices)
        key = f"{ctx.seed}|{ctx.flow_name}|{ctx.step_index}|{ctx.agent_name}"
        seed_int = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
        return random.Random(seed_int).choices(list(ctx.choices), weights=weights, k=1)[0]


@dataclass(frozen=True)
class BranchSpec:
    """A resolved ``{branch: ...}`` chain node: one chain stage where each of a flow's
    agents is routed (by ``router``) to exactly one of ``choices``.

    Config resolution produces it with ``router=None`` (only ``router_slot`` is known
    then); the engine builds the router and fills it in before the strategy runs.
    Consumers that only need the candidate GMs — per-GM owned-flow derivation and the
    restore chain view — read ``choices`` and ignore the router.
    """

    choices: tuple[str, ...]
    router_slot: Mapping[str, Any] = field(default_factory=dict)
    router: Router | None = None
