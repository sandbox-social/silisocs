"""The signaling marketplace: a faithful-mechanism port of Concordia's ``MarketPlace``.

The market is the *measured* half of the signaling experiment — what an agent
can afford, what it buys, and therefore what it can wear on the evening's date
all fall out of this clearing rule. So the rule is transcribed, not
reimplemented: :meth:`MarketplaceApp._clear_auction` and
:meth:`MarketplaceApp._clear_at_fixed_prices` are line-for-line ports of
``concordia/contrib/components/game_master/marketplace.py`` (pinned commit
``7779a4c``), quirks included — the ``bids[i].qty = 0; i += 1`` cash-short
branch, the ``successful_traders`` set doubling as a failure-message dedup, the
VWAP trade rows, and the NaN-clearing-price fallback to the last known price.
A differential test compares this module's clearing against that exact upstream
function, so *transcribe first, refactor never*.

What the port supplies around that verbatim core is silisocs machinery, not new
mechanism:

* **Sealed simultaneous orders.** Upstream collects orders by regexing one
  concatenated putative-event string, then clears. Here each order is a typed
  ``BID``/``ASK`` tool call buffered by
  :class:`~silisocs.environments.backends.round_game.SimultaneousRoundGame`,
  which already hides choices until the round boundary and round-trips a
  half-filled round through a checkpoint. One upstream ``_resolve`` = one
  :meth:`MarketplaceApp.resolve_round`.
* **A calendar, not a round counter.** Upstream's ``_state['round']`` advances
  once per clearing; here the engine step is authoritative and
  :class:`~replications.signaling.components.calendar.DayCalendar` says which
  steps are market rounds. :meth:`MarketplaceApp.update` therefore overrides the
  base sweep: only market steps clear, every other step is marked resolved
  silently (the day's dates and reflections carry no orders).
* **Seeded randomness.** Upstream draws from the global ``random``/``numpy``
  state (starting cash, starting outfit, the fixed-price rationing shuffle, the
  daily eat/wear draws). Every such draw here is seeded from ``seed`` (plus the
  day and agent for the per-day draws), so a resumed or replayed run makes the
  same draws. Order books are likewise built in roster order rather than in
  turn-completion order, matching upstream's ``acting_player_names`` iteration.
* **Daily seller regeneration.** Upstream rebuilds the marketplace config every
  morning, recreating each seller with full stock and starting cash;
  :meth:`MarketplaceApp.restock_sellers` is that reset, invoked by the
  day-boundary update component at each day start (which also restores the
  seller agents' pristine memory).
* **Loud on a mis-wired roster, survivable mid-run.** An agent that is neither a
  known seller nor a known persona fails :meth:`MarketplaceApp.initialize`;
  a buyer with nothing wearable on date night only increments the
  ``signaling_missing_wearable`` health counter (upstream raises, which would
  kill an otherwise valid run at day N).
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from replications.signaling.components import dyads, prompts
from replications.signaling.components import goods as goods_lib
from replications.signaling.components import marketplace_clearing as clearing
from replications.signaling.components.calendar import PHASE_MARKET, DayCalendar
from silisocs.environments.backends.base import ActionResult, app_action
from silisocs.environments.backends.round_game import RoundResult, SimultaneousRoundGame
from silisocs.runtime.telemetry.collector import SimMetricsCollector

#: Upstream market types this port implements. The two auction placeholders in
#: ``marketplace.py`` return NaN and no trades, so they are not ported.
MARKET_TYPES: tuple[str, ...] = ("clearing_house", "fixed_prices")

ROLE_CONSUMER = "consumer"
ROLE_PRODUCER = "producer"


@dataclass
class MarketplaceApp(SimultaneousRoundGame):
    """Simultaneous-order marketplace with Concordia's clearing rule."""

    app_description: str = (
        "A marketplace that buys and sells goods for food, clothing, and gadgets of low, "
        "mid, and high quality. Buyers submit sealed bids and sellers sealed asks; every "
        "order for a round is revealed and cleared together."
    )
    choice_verb = "ordered"
    goods_path: str = ""
    sellers_path: str = ""
    personas_path: str = ""
    market_type: str = "clearing_house"
    show_advert: bool = True
    cash_distribution: str = "mixed"
    seed: int = 42
    #: The run's single day layout (``calendar: ${calendar}``), replaced in
    #: ``__post_init__`` by the :class:`DayCalendar` it describes.
    calendar: Any = None

    def __post_init__(self) -> None:
        """Validate the market type and set up empty per-agent ledgers."""
        super().__post_init__()
        if self.market_type not in MARKET_TYPES:
            raise ValueError(
                f"MarketplaceApp.market_type must be one of {list(MARKET_TYPES)}; "
                f"got {self.market_type!r}."
            )
        self._calendar = DayCalendar.from_config(self.calendar, component=type(self).__name__)
        self.calendar = self._calendar
        self._goods_table: goods_lib.GoodsTable | None = None
        self._goods: dict[str, goods_lib.Good] = {}
        self._seller_records: dict[str, Any] = {}
        self._buyers: list[str] = []
        self._sellers: list[str] = []
        self._roles: dict[str, str] = {}
        self._cash: dict[str, float] = {}
        self._inventory: dict[str, dict[str, int]] = {}
        self._queues: dict[str, list[str]] = {}
        # Clearing prices per resolved market round; a NaN clearing price is
        # stored as None so the history stays JSON-safe for checkpoints.
        self._price_history: list[dict[str, float | None]] = []
        self._trade_history: list[dict[str, Any]] = []
        # Remaining market stock per good, used by the fixed_prices market
        # (upstream mutates ``Good.inventory`` in place; our Good is frozen).
        self._goods_inventory: dict[str, int] = {}

    # ---- identity -------------------------------------------------------

    def name(self) -> str:
        """Return the backend type name used in logs and the run manifest."""
        return "signaling_marketplace"

    def description(self) -> str:
        """Return the agent-facing description of the marketplace."""
        return self.app_description

    def opening_message(self) -> str:
        """Return the event recorded when the marketplace opens."""
        return (
            f"The marketplace opened for {len(self._buyers)} buyers and "
            f"{len(self._sellers)} sellers trading {len(self._goods)} goods "
            f"({self.market_type})."
        )

    # ---- public read API (consumed by the GM components and the DIAL handler) --

    @property
    def buyer_names(self) -> list[str]:
        """Consumers, in roster order."""
        return list(self._buyers)

    @property
    def seller_names(self) -> list[str]:
        """Producers, in roster order."""
        return list(self._sellers)

    @property
    def price_history(self) -> list[dict[str, float | None]]:
        """Clearing prices per resolved market round (``None`` = no price)."""
        return [dict(prices) for prices in self._price_history]

    @property
    def trade_history(self) -> list[dict[str, Any]]:
        """Upstream ``_logtrade_history`` rows, each also carrying its ``day``."""
        return [dict(row) for row in self._trade_history]

    def role_of(self, name: str) -> str:
        """``"consumer"``/``"producer"``, or ``""`` for a non-participant."""
        return self._roles.get(str(name), "")

    def cash_of(self, name: str) -> float:
        """Return the current cash balance."""
        return float(self._cash.get(str(name), 0.0))

    def inventory_of(self, name: str) -> dict[str, int]:
        """Copy of the agent's ``item -> count`` inventory."""
        return dict(self._inventory.get(str(name), {}))

    # ---- lifecycle ------------------------------------------------------

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Seat the roster: sellers from the goods table, buyers from the persona file."""
        super().initialize(agent_names, **kwargs)
        table = self._table()
        names = [str(name) for name in agent_names]

        seller_records = {
            str(record["name"]): record for record in _load_json_list(self.sellers_path)
        }
        # Kept for the daily restock: upstream regenerates every seller fresh
        # each morning, so the records are the sellers' pristine state.
        self._seller_records = seller_records
        self._sellers = [name for name in names if name in seller_records]
        self._buyers = dyads.buyer_roster(names, self.personas_path)
        unknown = [
            name for name in names if name not in seller_records and name not in set(self._buyers)
        ]
        if unknown:
            raise ValueError(
                f"MarketplaceApp roster contains agent(s) that are neither a seller in "
                f"{self.sellers_path!r} nor a persona in {self.personas_path!r}: "
                f"{', '.join(unknown)}."
            )

        self._roles = {}
        self._cash = {}
        self._inventory = {}
        self._queues = {name: [] for name in names}
        for name in self._sellers:
            self._roles[name] = ROLE_PRODUCER
            self._reset_seller(name)

        # Verbatim from personas.load_personas: one random low-tier Clothing item
        # per buyer, then the cash draw over the whole buyer set.
        outfit_rng = random.Random(int(self.seed))
        low_clothing = list(table.get("Clothing", {}).get("Low", {}).keys())
        if self._buyers and not low_clothing:
            raise ValueError(
                f"{self.goods_path!r} has no Clothing/Low items to draw a starting outfit from."
            )
        for name in self._buyers:
            self._roles[name] = ROLE_CONSUMER
            self._inventory[name] = {outfit_rng.choice(low_clothing): 1}
        cash_values = self._generate_cash_values(self.cash_distribution, len(self._buyers))
        for name, cash in zip(self._buyers, cash_values, strict=True):
            self._cash[name] = cash

        self._goods_inventory = {
            good_id: int(good.inventory)
            for good_id, good in self._goods.items()
            if good.inventory is not None
        }
        self._price_history = []
        self._trade_history = []
        # Re-stamp the opening event now that the roster is known (the base
        # recorded it before the seats were assigned).
        self._events = [self.opening_message()]

    def update(self, *, step: int, agent_names: Sequence[str], context: Any | None = None) -> None:
        """Clear every finished *market* round; mark every other step resolved silently.

        The base sweeps rounds ``0..step-1`` and resolves each one, but only a
        market step ever carries orders — a date turn or a reflection step would
        otherwise clear an empty book, append a NaN price row, and log a
        ``round_resolved`` event for a round the market never opened.
        """
        del agent_names, context
        for rnd in range(int(step)):
            if rnd in self._resolved:
                continue
            if self._calendar.is_market_step(rnd):
                self._resolve_round(rnd)
            else:
                self._resolved.add(rnd)

    def _reset_seller(self, name: str) -> None:
        """Give one seller its pristine ledger: full stock, starting cash, no queue.

        Verbatim from ``personas.make_agents``: a producer's inventory is
        ``{good_to_sell: inventory}`` and its cash defaults to 100.0 (the record
        carries no ``cash`` key).
        """
        record = self._seller_records[name]
        self._cash[name] = 100.0
        self._inventory[name] = {str(record["good"]): int(record["stock"])}
        self._queues[name] = []

    def restock_sellers(self) -> None:
        """Reset every seller's ledger for a new day.

        Upstream rebuilds the whole marketplace config each morning and, with
        ``add_sellers=True``, regenerates every seller as a *fresh* agent —
        full inventory, starting cash, empty queue — while buyers carry their
        cash, inventory, and queued outcomes over (``simulation.run_experiment``
        passes only the buyer states into the next day's config). The caller
        (the day-boundary update component) is responsible for the matching
        seller *memory* reset, which is agent state, not backend state.
        """
        for name in self._sellers:
            self._reset_seller(name)
        if self.market_type != "clearing_house":
            # Upstream rebuilds the goods list from its spec every morning, so
            # fixed-price market stock also replenishes daily.
            self._goods_inventory = {
                good_id: int(good.inventory)
                for good_id, good in self._goods.items()
                if good.inventory is not None
            }

    # ---- actions --------------------------------------------------------

    @app_action(
        selectable_name="BID",
        description=(
            "Submit a sealed bid to BUY a good this round: the good's id, the maximum price "
            "per unit you will pay, and how many units you want. Ensure price*qty <= cash."
        ),
        tags=("market.order",),
        fields={
            "market.side": "side",
            "market.good": "good",
            "market.price": "price",
            "market.quantity": "qty",
            "market.round": "round",
        },
    )
    def bid(
        self, agent_name: str, good: str = "", price: float = 0.0, qty: int = 1
    ) -> ActionResult:
        """Buffer this round's sealed bid (revealed and cleared at the round boundary)."""
        return self._submit(agent_name, "bid", good, price, qty)

    @app_action(
        selectable_name="ASK",
        description=(
            "Submit a sealed ask to SELL some of your stock this round: the good's id, the "
            "minimum price per unit you will accept, and how many units you offer "
            "(1 up to the units in your stock)."
        ),
        tags=("market.order",),
        fields={
            "market.side": "side",
            "market.good": "good",
            "market.price": "price",
            "market.quantity": "qty",
            "market.round": "round",
        },
    )
    def ask(
        self, agent_name: str, good: str = "", price: float = 0.0, qty: int = 1
    ) -> ActionResult:
        """Buffer this round's sealed ask (revealed and cleared at the round boundary)."""
        return self._submit(agent_name, "ask", good, price, qty)

    def _submit(  # noqa: PLR0911 - one early return per distinct rejection reason
        self, agent_name: str, side: str, good: str, price: Any, qty: Any
    ) -> ActionResult:
        """Validate one order and buffer it as the agent's hidden choice for the round.

        Each rejection returns its own message: an agent that gets a specific
        reason back can retry correctly, where a single generic error cannot be
        acted on.
        """
        step = self.current_episode()
        phase = self._calendar.phase_of(step)
        if phase.phase != PHASE_MARKET:
            return ActionResult(
                "The marketplace is closed right now; orders are only accepted during the "
                f"day's {self._calendar.rounds_per_day} market rounds.",
                committed=False,
            )
        role = self.role_of(agent_name)
        if not role:
            return ActionResult(f"Unknown marketplace participant: {agent_name}", committed=False)
        expected = "bid" if role == ROLE_CONSUMER else "ask"
        if side != expected:
            verb = "BID to buy" if role == ROLE_CONSUMER else "ASK to sell"
            return ActionResult(
                f"{agent_name} is a {role} in this marketplace and may only {verb}.",
                committed=False,
            )
        good_id = str(good)
        if good_id not in self._goods_map():
            return ActionResult(
                f"Unknown good: {good_id!r}. Order the exact id of a good on offer.",
                committed=False,
            )
        try:
            order_price = float(price)
            order_qty = int(qty)
        except (TypeError, ValueError):
            return ActionResult(
                f"Price must be a number and quantity a whole number; got {price!r} and {qty!r}.",
                committed=False,
            )
        if not order_price > 0.0 or not math.isfinite(order_price):
            return ActionResult(
                f"Price must be a positive number of dollars; got {price!r}.", committed=False
            )
        if order_qty < 1:
            return ActionResult(f"Quantity must be at least 1 unit; got {qty!r}.", committed=False)
        error = self.record_choice(
            agent_name,
            {"side": side, "good": good_id, "price": order_price, "qty": order_qty},
        )
        if error:
            return ActionResult(error, committed=False)
        verb = "bid" if side == "bid" else "ask"
        return ActionResult(
            f"You submitted a sealed {verb} for {order_qty} {good_id} at ${order_price:.2f} each. "
            "Orders are revealed and cleared once every participant has ordered.",
            data={
                "round": int(step),
                "day": int(phase.day),
                "side": side,
                "good": good_id,
                "price": order_price,
                "qty": order_qty,
            },
        )

    # ---- clearing -------------------------------------------------------

    def _clearing_context(self) -> clearing.ClearingContext:
        """Bundle the ledgers the clearing rules mutate, plus the two callbacks.

        Built per round rather than cached: ``set_state`` rebinds the ledger
        dicts on restore, and a stale context would then mutate orphaned ones.
        """
        return clearing.ClearingContext(
            cash=self._cash,
            inventory=self._inventory,
            goods_inventory=self._goods_inventory,
            trade_history=self._trade_history,
            queue=self._queue,
            day_of=self._calendar.day_of,
            seed=int(self.seed),
        )

    def resolve_round(self, rnd: int, choices: Mapping[str, Any]) -> RoundResult:
        """Reveal the round's sealed orders and clear the market (upstream ``_resolve``)."""
        phase = self._calendar.phase_of(rnd)
        goods_map = self._goods_map()
        orderbooks: dict[str, list[clearing.Order]] = {good_id: [] for good_id in goods_map}
        num_orders = 0
        # Books are built in roster order, not in the order concurrent turns
        # happened to commit: upstream iterates ``acting_player_names``, and the
        # clearing loop's stable sort breaks price ties by book position, so the
        # book order is part of the mechanism (and of replay stability).
        roster_index = {name: index for index, name in enumerate(self._players)}
        ordered = sorted(choices, key=lambda name: roster_index.get(str(name), len(roster_index)))
        for agent_name in ordered:
            choice = choices[agent_name]
            if not isinstance(choice, Mapping):
                continue
            good_id = str(choice.get("good", ""))
            if good_id not in orderbooks:
                continue
            orderbooks[good_id].append(
                clearing.Order(
                    agent_id=str(agent_name),
                    good_id=good_id,
                    price=float(choice.get("price", 0.0)),
                    qty=int(choice.get("qty", 0)),
                    side=str(choice.get("side", "")),
                )
            )
            num_orders += 1

        # --- CLEAR THE MARKET (verbatim from MarketPlace._resolve) ---
        ctx = self._clearing_context()
        prices: dict[str, float] = {}
        all_completed_orders: list[str] = []
        for good_id in goods_map:
            if self.market_type == "clearing_house":
                trade_price, completed_orders, _traded = clearing.clear_auction(
                    ctx, good_id, orderbooks[good_id], rnd
                )
            else:
                trade_price, completed_orders = clearing.clear_at_fixed_prices(
                    ctx, good_id, orderbooks[good_id], rnd, goods_map[good_id].price
                )
            if math.isnan(trade_price) and self._price_history:
                # use the last known clearing price
                last = self._price_history[-1].get(good_id)
                trade_price = float("nan") if last is None else float(last)
            prices[good_id] = trade_price
            all_completed_orders.extend(completed_orders)
        self._price_history.append(
            {
                good_id: None if math.isnan(price) else float(price)
                for good_id, price in prices.items()
            }
        )

        priced = {
            good_id: float(price) for good_id, price in prices.items() if math.isfinite(price)
        }
        summary: dict[str, float] = {
            **priced,
            "num_trades": float(len(all_completed_orders)),
            "num_orders": float(num_orders),
        }
        narrative = (
            f"Day {phase.day} round {phase.position + 1} resolved: "
            f"{len(all_completed_orders)} trades, prices "
            f"{ {good_id: round(price, 2) for good_id, price in priced.items()} }."
        )
        return RoundResult(
            summary=summary,
            # Cash is tracked on the backend itself, so there is no separate
            # payoff pool for the base class to accumulate.
            payoffs={},
            narrative=narrative,
            event_extra={
                "day": int(phase.day),
                "round_in_day": int(phase.position),
                "market_type": str(self.market_type),
            },
        )

    # ---- the signaling channel (dial.py) --------------------------------

    def consume_random_edible(self, agent_name: str, *, queue_statement: bool = False) -> str:
        """Eat one random edible from inventory (verbatim ``dial.get_eating_statement``).

        Returns the starvation statement, and leaves the inventory alone, when
        the agent has nothing edible.

        ``queue_statement`` mirrors upstream's queue mechanics:
        ``get_eating_statement`` always appends the statement to the agent's
        outcome queue, and the day driver then *pops* it whenever the DIAL
        simulation runs (all buyers, social and asocial_personal alike) to feed
        the personal-events prompt. Append-then-pop is a no-op, so the ceremony
        passes ``False``; only the ``asocial`` arm — where upstream never pops —
        passes ``True``, leaving the statement to surface in the next market
        observation exactly as upstream's does.
        """
        edible_items = goods_lib.edible_ids(self._table())
        inventory = self._inventory.setdefault(str(agent_name), {})
        agent_inventory = list(inventory.keys())
        agent_edibles = [item for item in agent_inventory if item in edible_items]
        if not agent_edibles:
            statement = prompts.STARVATION_STATEMENT.format(name=agent_name)
        else:
            random_item = self._day_rng(agent_name, "eat").choice(agent_edibles)
            inventory[random_item] -= 1
            statement = prompts.EATING_STATEMENT.format(name=agent_name, item=random_item)
        if queue_statement:
            self._queue(str(agent_name), statement)
        return statement

    def wearing_statement(self, agent_name: str) -> str:
        """Wear one random wearable (verbatim ``dial.get_wearing_statement``).

        Deviation: upstream raises when the agent owns nothing wearable. Killing
        a run at day N over one agent's empty wardrobe is not a condition the
        run cannot continue past, so it is counted instead — the
        ``signaling_missing_wearable`` health counter surfaces it in the
        degraded-run warning and the manifest.
        """
        table = self._table()
        wearable_items = goods_lib.wearable_ids(table)
        inventory = self._inventory.setdefault(str(agent_name), {})
        agent_wearables = [item for item in inventory if item in wearable_items]
        if not agent_wearables:
            SimMetricsCollector.get().increment_counter("signaling_missing_wearable")
            return f"{agent_name} is wearing plain, unremarkable everyday clothes."
        random_item = self._day_rng(agent_name, "wear").choice(agent_wearables)
        found_path = goods_lib.find_tier(table, random_item)
        if found_path:
            item_category, item_tier = found_path
            return prompts.WEARING_STATEMENT.format(
                name=agent_name, item=random_item, tier=item_tier, category=item_category
            )
        return prompts.WEARING_STATEMENT_UNTIERED.format(name=agent_name, item=random_item)

    # ---- observation ----------------------------------------------------

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Render the actor's market view (port of ``_handle_make_observation``)."""
        step = kwargs.get("step")
        step = self.current_episode() if step is None else int(step)
        phase = self._calendar.phase_of(step)
        queued = self._queues.setdefault(str(actor_name), [])
        # Outcomes are drained first whatever the phase: they are the agent's
        # only record of how their last order fared.
        last_action_obs = ""
        if queued:
            last_action_obs = f"{actor_name}'s recent outcomes:\n" + "\n".join(queued) + "\n"
            queued.clear()
        if phase.phase != PHASE_MARKET:
            return f"{last_action_obs}The marketplace is closed for day {phase.day}."

        # Checkpoints store an unpriced good as None (JSON-safe); upstream's
        # observation renders it as nan, so restore that for display parity.
        stored = self._price_history[-1] if self._price_history else {}
        prices = {
            good: (float("nan") if value is None else value) for good, value in stored.items()
        }
        cash = self.cash_of(actor_name)
        inventory = self.inventory_of(actor_name)
        # Upstream tells the agent only the in-day round number — never the day
        # or the horizon (a fresh MarketPlace per day resets its counter).
        round_line = f"Round: {phase.position + 1} is starting\n"
        if self.market_type == "clearing_house":
            common_obs = (
                f"{last_action_obs}"
                f"{round_line}"
                f"Prices last round: {prices}\n"
                f"Cash: {cash:.2f}\n"
                f"{actor_name}'s Inventory: {inventory}\n"
            )
            if self.role_of(actor_name) == ROLE_PRODUCER:
                return f"{common_obs}Submit your order."
            items_available = [self.inventory_of(seller) for seller in self._sellers]
            return (
                f"{common_obs}Items available from producers: {items_available}\nSubmit your bid."
            )

        common_obs = (
            f"{last_action_obs}"
            f"{round_line}"
            f"Cash: {cash:.2f}\n"
            f"{actor_name}'s Inventory: {inventory}\n"
        )
        listings: list[str] = []
        for good in self._goods_map().values():
            if good.price is None:
                continue
            stock = self._goods_inventory.get(good.id, 0)
            if self.show_advert:
                listings.append(f"{good.id}: ${good.price}, {stock} units available. {good.advert}")
            else:
                listings.append(f"{good.id}: ${good.price}, {stock} units available")
        return f"{common_obs}Items available from producers: {listings}\nSubmit your order."

    # ---- checkpoint contract --------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Extend the base round-game state with the market's books and balances."""
        state = super().get_state()
        state.update(
            {
                "cash": {str(name): float(value) for name, value in self._cash.items()},
                "inventory": {
                    str(name): {str(item): int(count) for item, count in items.items()}
                    for name, items in self._inventory.items()
                },
                "roles": {str(name): str(role) for name, role in self._roles.items()},
                "queues": {
                    str(name): [str(entry) for entry in queue]
                    for name, queue in self._queues.items()
                },
                "buyers": list(self._buyers),
                "sellers": list(self._sellers),
                "price_history": [dict(prices) for prices in self._price_history],
                # A failed trade row carries a NaN VWAP upstream; store it as None
                # so the checkpoint is strict-JSON safe, and restore it below.
                "trade_history": [_row_to_json(row) for row in self._trade_history],
                "goods_inventory": {
                    str(good_id): int(count) for good_id, count in self._goods_inventory.items()
                },
            }
        )
        return state

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore the market's books and balances alongside the base round state."""
        super().set_state(state)
        self._cash = {
            str(name): float(value) for name, value in dict(state.get("cash", {})).items()
        }
        self._inventory = {
            str(name): {str(item): int(count) for item, count in dict(items).items()}
            for name, items in dict(state.get("inventory", {})).items()
        }
        self._roles = {str(name): str(role) for name, role in dict(state.get("roles", {})).items()}
        self._queues = {
            str(name): [str(entry) for entry in queue]
            for name, queue in dict(state.get("queues", {})).items()
        }
        self._buyers = [str(name) for name in state.get("buyers", [])]
        self._sellers = [str(name) for name in state.get("sellers", [])]
        self._price_history = [
            {
                str(good_id): (None if price is None else float(price))
                for good_id, price in dict(prices).items()
            }
            for prices in state.get("price_history", [])
        ]
        self._trade_history = [_row_from_json(dict(row)) for row in state.get("trade_history", [])]
        self._goods_inventory = {
            str(good_id): int(count)
            for good_id, count in dict(state.get("goods_inventory", {})).items()
        }

    # ---- internals ------------------------------------------------------

    def _table(self) -> goods_lib.GoodsTable:
        """Return the goods table, loading it on first use (``goods_path`` required)."""
        if self._goods_table is None:
            if not self.goods_path:
                raise ValueError(
                    "MarketplaceApp requires goods_path (input/goods/<item_list>.json)."
                )
            self._goods_table = goods_lib.load_goods_table(self.goods_path)
            self._goods = {good.id: good for good in goods_lib.flatten_goods(self._goods_table)}
        return self._goods_table

    def _goods_map(self) -> dict[str, goods_lib.Good]:
        """Flat ``good id -> Good``, in the goods table's order."""
        self._table()
        return self._goods

    def _queue(self, agent_name: str, outcome: str) -> None:
        """Append one outcome message to an agent's queue (upstream ``agent.queue``)."""
        self._queues.setdefault(str(agent_name), []).append(outcome)

    def _day_rng(self, agent_name: str, purpose: str) -> random.Random:
        """Per-agent, per-day, per-purpose RNG (upstream draws from the global one)."""
        day = self._calendar.day_of(self.current_episode())
        return random.Random(f"{int(self.seed)}:{day}:{agent_name}:{purpose}")

    def _generate_cash_values(self, distribution_type: str, num_agents: int) -> list[float]:
        """Draw starting cash (verbatim ``personas.generate_cash_values``, seeded).

        Deviation: draws come from ``numpy.random.default_rng(seed)`` rather than
        the global numpy state, so the same run seeds the same wealth ladder.
        """
        rng = np.random.default_rng(int(self.seed))
        if distribution_type == "pareto":
            min_cash = 5000.0
            distribution_shape = 1.5
            max_cash_cap = 2500000.0
            cash_values = rng.pareto(distribution_shape, num_agents) * min_cash
            cash_values = np.clip(cash_values, min_cash, max_cash_cap)
        elif distribution_type == "mixed":
            proportions = {"poor": 0.20, "middle": 0.50, "upper": 0.25, "rich": 0.05}
            poor_params = {"mean": np.log(500), "sigma": 0.4}
            middle_params = {"mean": np.log(7_500), "sigma": 0.5}
            upper_params = {"mean": np.log(25_000), "sigma": 0.6}
            rich_params = {"shape": 1.5, "min_val": 100_000}
            n_poor = int(num_agents * proportions["poor"])
            n_middle = int(num_agents * proportions["middle"])
            n_upper = int(num_agents * proportions["upper"])
            n_rich = int(num_agents * proportions["rich"])
            current_total = n_poor + n_middle + n_upper + n_rich
            n_rich += num_agents - current_total
            poor_dist = rng.lognormal(
                mean=poor_params["mean"], sigma=poor_params["sigma"], size=n_poor
            )
            middle_dist = rng.lognormal(
                mean=middle_params["mean"], sigma=middle_params["sigma"], size=n_middle
            )
            upper_dist = rng.lognormal(
                mean=upper_params["mean"], sigma=upper_params["sigma"], size=n_upper
            )
            rich_dist = (rng.pareto(rich_params["shape"], n_rich) + 1) * rich_params["min_val"]
            cash_values = np.concatenate([poor_dist, middle_dist, upper_dist, rich_dist])
            rng.shuffle(cash_values)
        else:
            raise ValueError(f"Unsupported distribution type: '{distribution_type}'.")
        # Upstream stringifies the rounded value and parses it back in make_agents.
        return [float(round(float(value), 2)) for value in cash_values]


def _load_json_list(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSON file that must hold a list of records."""
    if not path:
        raise ValueError("MarketplaceApp requires sellers_path (input/sellers_<item_list>.json).")
    with open(path, encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise TypeError(f"{path}: expected a list of records.")
    return records


def _row_to_json(row: Mapping[str, Any]) -> dict[str, Any]:
    """Make one trade row strict-JSON safe (a failed order's VWAP is NaN)."""
    serialized = dict(row)
    avg = serialized.get("transaction_price_avg")
    if isinstance(avg, float) and math.isnan(avg):
        serialized["transaction_price_avg"] = None
    return serialized


def _row_from_json(row: Mapping[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`_row_to_json`."""
    restored = dict(row)
    if restored.get("transaction_price_avg") is None:
        restored["transaction_price_avg"] = math.nan
    return restored
