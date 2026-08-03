"""Concordia's market clearing rules, transcribed verbatim.

This module is deliberately *only* the clearing algorithm — no framework
imports, no backend, nothing but dataclasses and arithmetic — so it can be read
side by side with
``concordia/contrib/components/game_master/marketplace.py`` at the pinned commit
and diffed by eye. That is the same claim
``tests/test_signaling_replication.py`` makes mechanically, by running identical
order books through this code and through the upstream file itself.

**Do not refactor for style.** Every branch here is upstream's, including the
parts that read like bugs, because reproducing the experiment means reproducing
its market:

* a cash-short buyer's bid is zeroed *and* skipped (``bids[i].qty = 0; i += 1``),
  so that buyer takes no further part in the good's clearing this round;
* ``successful_traders`` doubles as the dedup set for failure messages, so an
  agent who traded never receives one;
* the closing ``raise ValueError("No asks were placed.")`` is unreachable (the
  empty-book case returns much earlier).

The three complexity suppressions on :func:`clear_auction` exist for the same
reason. The only deliberate deviation is noted on
:func:`clear_at_fixed_prices`.

The functions mutate the caller's ledgers in place through :class:`ClearingContext`,
which is how upstream works too (it mutates ``self._cash`` / ``self._inventory``
directly).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass
class Order:
    """One side of one order book entry (upstream ``marketplace.Order``).

    ``qty`` is decremented in place by the clearing loop, exactly as upstream
    mutates its ``Order`` objects, so order books are rebuilt per round.
    """

    agent_id: str
    good_id: str
    price: float
    qty: int
    side: str


class Fulfillment(dict):
    """``{order id: {filled_qty, total_value}}`` with upstream's implicit default."""

    def __missing__(self, key: int) -> dict[str, Any]:
        """Create the zeroed fulfillment record upstream assumes exists."""
        value: dict[str, Any] = {"filled_qty": 0, "total_value": 0.0}
        self[key] = value
        return value


@dataclass
class ClearingContext:
    """The mutable ledgers and callbacks one round of clearing works against.

    Bundling them keeps the clearing functions free of the backend: everything
    they touch arrives through here. The dicts and the list are the backend's own
    objects, mutated in place.
    """

    cash: dict[str, float]
    inventory: dict[str, dict[str, int]]
    goods_inventory: dict[str, int]
    trade_history: list[dict[str, Any]]
    #: Deliver one outcome message to an agent (the backend's observation queue).
    queue: Callable[[str, str], None]
    #: Map a round index to its day, for the trade-history rows.
    day_of: Callable[[int], int]
    #: Run seed, used only by the fixed-price rationing shuffle.
    seed: int = 42


def log_trade_history(  # noqa: PLR0913 - upstream's parameter list
    ctx: ClearingContext,
    good_id: str,
    orders: list[Order],
    original_quantities: Mapping[int, float],
    order_fulfillment: Mapping[int, dict[str, Any]],
    rnd: int,
) -> None:
    """Log the outcome of orders for a good (verbatim ``_logtrade_history``)."""
    for order in orders:
        order_id = id(order)
        original_qty = original_quantities.get(order_id)
        if original_qty is None:
            continue

        fulfillment = order_fulfillment.get(order_id)
        transaction_occurred = False
        if fulfillment and fulfillment["filled_qty"] > 0:
            filled_qty = fulfillment["filled_qty"]
            total_value = fulfillment["total_value"]
            # Calculate average price (VWAP)
            avg_price = total_value / filled_qty
            transaction_occurred = True
            status = "Partial" if filled_qty < original_qty else "Filled"
        else:
            filled_qty, total_value, avg_price, status = 0, 0.0, math.nan, "Failed"

        ctx.trade_history.append(
            {
                "round": int(rnd),
                "day": int(ctx.day_of(rnd)),
                "agent": order.agent_id,
                "good": good_id,
                "side": order.side,
                "order_price": order.price,
                "order_qty": original_qty,
                "status": status,
                "transaction_occurred": transaction_occurred,
                "transaction_price_avg": avg_price,
                "transaction_qty": filled_qty,
                "transaction_value": total_value,
            }
        )


def clear_auction(  # noqa: C901, PLR0912, PLR0915 - verbatim upstream transcription
    ctx: ClearingContext, good_id: str, orderbook: list[Order], rnd: int
) -> tuple[float, list[str], bool]:
    """Continuous double-auction clearing (verbatim ``MarketPlace._clear_auction``)."""
    bids = [o for o in orderbook if o.side == "bid"]
    asks = [o for o in orderbook if o.side == "ask"]
    completed_orders: list[str] = []

    # Initialize tracking for Trade History
    original_quantities = {id(o): o.qty for o in (bids + asks)}
    order_fulfillment: dict[int, dict[str, Any]] = Fulfillment()

    if not bids or not asks:
        # If there are no bids or no asks, all existing orders for this good fail.
        for order in bids + asks:
            outcome = (
                f"Your {order.side} for {order.qty} {good_id} at"
                f" ${order.price:.2f} did not result in a trade as there were no"
                " counterparties."
            )
            ctx.queue(order.agent_id, outcome)
        # Log failed orders before returning
        log_trade_history(ctx, good_id, bids + asks, original_quantities, order_fulfillment, rnd)
        if asks:
            lowest_ask_price = min(order.price for order in asks)
            return (lowest_ask_price, completed_orders, False)
        return float("nan"), completed_orders, False

    bids.sort(key=lambda o: o.price, reverse=True)
    asks.sort(key=lambda o: o.price)

    # ---  Keep track of who successfully trades ---
    successful_traders: set[str] = set()

    i = j = 0
    trade_price: float | None = None
    while i < len(bids) and j < len(asks) and bids[i].price >= asks[j].price:
        buyer = bids[i].agent_id
        seller = asks[j].agent_id
        seller_stock = ctx.inventory.setdefault(seller, {}).get(good_id, 0)

        trade_price = (bids[i].price + asks[j].price) / 2
        qty = min(bids[i].qty, asks[j].qty, seller_stock)
        trade_value = trade_price * qty

        # Check if buyer can afford the quantity they are trying to buy
        if ctx.cash.get(buyer, 0.0) < trade_value:
            outcome = (
                f"You attempted to BUY {qty} {good_id} at ${trade_price:.2f} each,"
                " but you do not have enough cash. Order failed."
            )
            ctx.queue(buyer, outcome)
            bids[i].qty = 0  # Mark bid as filled to prevent further attempts
            i += 1
            continue

        if qty == 0:
            if seller_stock == 0:
                j += 1  # Seller is out of stock, move to next seller
            elif bids[i].qty == 0:
                i += 1  # Buyer order filled, move to next buyer
            elif asks[j].qty == 0:
                j += 1  # Seller order filled, move to next seller
            continue
        # 1. Move goods and cash
        ctx.cash[buyer] = ctx.cash.get(buyer, 0.0) - trade_value
        ctx.cash[seller] = ctx.cash.get(seller, 0.0) + trade_value
        buyer_inventory = ctx.inventory.setdefault(buyer, {})
        buyer_inventory[good_id] = buyer_inventory.get(good_id, 0) + qty
        ctx.inventory[seller][good_id] -= qty

        # Update order fulfillment tracking
        order_fulfillment[id(bids[i])]["filled_qty"] += qty
        order_fulfillment[id(bids[i])]["total_value"] += trade_value
        order_fulfillment[id(asks[j])]["filled_qty"] += qty
        order_fulfillment[id(asks[j])]["total_value"] += trade_value

        # 2. Create and record SUCCESS outcome observations
        ctx.queue(buyer, f"You successfully BOUGHT {qty} {good_id} at ${trade_price:.2f} each.")
        successful_traders.add(buyer)

        ctx.queue(seller, f"You successfully SOLD {qty} {good_id} at ${trade_price:.2f} each.")
        successful_traders.add(seller)

        # Add to completed orders list
        completed_orders.append(
            f"Seller {seller} sold {qty} units at {trade_price} of {good_id} to Buyer {buyer}"
        )

        # 3. Reduce outstanding quantities
        bids[i].qty -= qty
        asks[j].qty -= qty

        # 4. Advance whichever order is fully satisfied
        if bids[i].qty == 0:
            i += 1
        if asks[j].qty == 0:
            j += 1

    # --- Log all orders (successful and unsuccessful) to history ---
    log_trade_history(ctx, good_id, bids + asks, original_quantities, order_fulfillment, rnd)

    # --- Log outcomes for agents whose orders were NOT filled ---
    for order in bids + asks:
        # Check if agent's order had remaining quantity AND they did not succeed
        # in any trade for this good. This handles agents whose prices were not
        # competitive.
        if order.qty > 0 and order.agent_id not in successful_traders:
            outcome = (
                f"Your {order.side} for {order.qty} {good_id} at"
                f" ${order.price:.2f} did not result in a trade."
            )
            ctx.queue(order.agent_id, outcome)

            # Add this agent to the set to prevent duplicate "failure" messages
            # if they had multiple unsuccessful orders for the same good.
            successful_traders.add(order.agent_id)

    if trade_price is not None:
        return (trade_price, completed_orders, True)
    # trade_price is None, find the lowest ask price
    if asks:  # asks is a list of ask Order objects
        lowest_ask_price = min(order.price for order in asks)
        return (lowest_ask_price, completed_orders, False)
    raise ValueError("No asks were placed.")


def clear_at_fixed_prices(
    ctx: ClearingContext, good_id: str, orderbook: list[Order], rnd: int, price: float | None
) -> tuple[float, list[str]]:
    """Clear at the good's posted price (verbatim ``_clear_at_fixed_prices``).

    Deviation: the rationing shuffle draws from a round/good-seeded RNG rather
    than the global one, so a replayed or resumed run rations the same way.
    """
    if price is None:
        raise ValueError(f"Good '{good_id}' has no price for fixed_prices market.")

    if good_id not in ctx.goods_inventory:
        return float("nan"), []

    fixed_price = float(price)
    completed_orders: list[str] = []

    # Only consider bids from consumers.
    bids = [o for o in orderbook if o.side == "bid" and o.price >= fixed_price]

    original_quantities = {id(o): o.qty for o in bids}
    order_fulfillment: dict[int, dict[str, Any]] = Fulfillment()

    if not bids or ctx.goods_inventory[good_id] == 0:
        for order in bids:
            ctx.queue(order.agent_id, f"Your bid for {order.qty} {good_id} did not trade.")
        log_trade_history(ctx, good_id, bids, original_quantities, order_fulfillment, rnd)
        return float("nan"), []

    # Randomize who gets to buy if supply is limited.
    random.Random(f"{ctx.seed}:{rnd}:{good_id}").shuffle(bids)

    for bid in bids:
        if ctx.goods_inventory[good_id] == 0:
            break  # Market is out of stock

        buyer = bid.agent_id
        qty_to_buy = min(bid.qty, ctx.goods_inventory[good_id])
        trade_value = fixed_price * qty_to_buy

        if ctx.cash.get(buyer, 0.0) < trade_value:
            ctx.queue(
                buyer,
                f"You attempted to BUY {qty_to_buy} {good_id}, but do not have enough cash.",
            )
            continue

        if qty_to_buy > 0:
            # Decrement market inventory and agent cash
            ctx.goods_inventory[good_id] -= qty_to_buy
            ctx.cash[buyer] = ctx.cash.get(buyer, 0.0) - trade_value
            buyer_inventory = ctx.inventory.setdefault(buyer, {})
            buyer_inventory[good_id] = buyer_inventory.get(good_id, 0) + qty_to_buy

            # Log fulfillment
            order_fulfillment[id(bid)]["filled_qty"] += qty_to_buy
            order_fulfillment[id(bid)]["total_value"] += trade_value

            ctx.queue(
                buyer,
                f"You successfully BOUGHT {qty_to_buy} {good_id} at the fixed price"
                f" of ${fixed_price:.2f}.",
            )
            completed_orders.append(
                f"Buyer {buyer} bought {qty_to_buy} of {good_id} from the market."
            )

    log_trade_history(ctx, good_id, bids, original_quantities, order_fulfillment, rnd)
    return fixed_price, completed_orders
