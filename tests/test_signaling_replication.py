"""Tests for the SiliSocS port of the Concordia signaling example.

The suite is organised around the port's definition of done
(``replications/signaling/docs/DESIGN.md`` §7). The load-bearing tests are the
two *differential* ones, which compare our implementation against the verbatim
pinned upstream source executed in-process (see ``tests/signaling_pinned.py``):

* the clearing house — identical allocations, prices, cash, and inventory for
  the same order book, across the awkward cases (empty side, partial fill,
  cash-short buyer, stock-short seller, crossing at equal prices);
* the dyad schedule — byte-identical pairings for the same roster and seed.

Everything else is a deterministic invariant that needs no language model, so
the whole file runs in seconds.
"""

from __future__ import annotations

import json
import math
import re
import types
from pathlib import Path

import pytest
import yaml
from replications.signaling.components import dyads, goods, prompts
from replications.signaling.components.calendar import (
    DATE_REFLECTION_KINDS,
    PHASE_DATE,
    PHASE_DATE_REFLECTION,
    PHASE_DIAL_SETUP,
    PHASE_MARKET,
    PHASE_MARKET_REFLECTION,
    DayCalendar,
)

from tests import signaling_pinned

REPLICATION = Path(__file__).resolve().parent.parent / "replications" / "signaling"
PERSONAS = REPLICATION / "input" / "personas_la.json"
CONF = REPLICATION / "conf"

pinned_required = pytest.mark.skipif(
    signaling_pinned.pinned_root() is None,
    reason="pinned Concordia checkout unavailable (set CONCORDIA_SIGNALING_ROOT)",
)


class _FakeLogger:
    """Stands in for the run's action EventLogger (episode stamp + sink)."""

    def __init__(self, episode_idx: int = 0) -> None:
        self.episode_idx = episode_idx
        self.rows: list[dict] = []

    def log(self, row: dict) -> None:
        self.rows.append(dict(row))


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #


def test_calendar_partition_is_total_and_disjoint() -> None:
    calendar = DayCalendar(rounds_per_day=5, dial_turns=80, date_reflections=4)
    assert calendar.day_length == 91
    phases = [calendar.phase_of(step) for step in range(calendar.day_length)]
    assert [p.day for p in phases] == [0] * calendar.day_length
    counts: dict[str, int] = {}
    for phase in phases:
        counts[phase.phase] = counts.get(phase.phase, 0) + 1
    assert counts == {
        PHASE_MARKET: 5,
        PHASE_MARKET_REFLECTION: 1,
        PHASE_DIAL_SETUP: 1,
        PHASE_DATE: 80,
        PHASE_DATE_REFLECTION: 4,
    }
    # Ordering: market ... reflection, setup, dates, then the four reflections.
    assert phases[4].phase == PHASE_MARKET
    assert phases[5].phase == PHASE_MARKET_REFLECTION
    assert phases[6].phase == PHASE_DIAL_SETUP
    assert phases[7].phase == PHASE_DATE
    assert [p.kind for p in phases[-4:]] == list(DATE_REFLECTION_KINDS)
    # Day boundary.
    assert calendar.phase_of(91) == (1, PHASE_MARKET, 0, "")
    assert calendar.day_of(91) == 1
    assert calendar.dial_setup_steps(3) == [6, 97, 188]


def test_calendar_asocial_shape_has_no_dates() -> None:
    calendar = DayCalendar(rounds_per_day=5, dial_turns=0, date_reflections=0)
    assert calendar.day_length == 7
    assert calendar.runs_dates is False
    assert calendar.phase_of(6).phase == PHASE_DIAL_SETUP
    assert calendar.num_steps(5) == 35
    assert calendar.dial_setup_steps(2) == [6, 13]


def test_calendar_rejects_inconsistent_shapes() -> None:
    with pytest.raises(ValueError, match="date turns"):
        DayCalendar(rounds_per_day=5, dial_turns=0, date_reflections=4)
    with pytest.raises(ValueError, match="rounds_per_day"):
        DayCalendar(rounds_per_day=0)
    with pytest.raises(ValueError, match="Unknown calendar key"):
        DayCalendar.from_params({"nope": 1})


# --------------------------------------------------------------------------- #
# Shipped configuration must agree with the calendar
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("world", ["default", "asocial", "asocial_personal"])
def test_world_config_arithmetic_matches_calendar(world: str) -> None:
    cfg = yaml.safe_load((CONF / "world" / f"{world}.yaml").read_text())
    calendar = DayCalendar.from_params(cfg["calendar"])
    # OmegaConf cannot do arithmetic, so these two are written out by hand. The
    # run itself checks them at startup (CalendarLoopStrategy); this test catches
    # a stale number in a COMMITTED world file without launching anything.
    assert cfg["day_length"] == calendar.day_length
    assert cfg["num_steps"] == calendar.num_steps(cfg["num_days"])
    # The day-boundary ceremony derives its own schedule from the calendar, so a
    # world file must not carry a hand-written intervention list any more.
    assert "interventions" not in cfg


def test_calendar_loop_strategy_rejects_a_stale_num_steps() -> None:
    """The startup check, which is what a CLI calendar override runs into.

    ``test_world_config_arithmetic_matches_calendar`` only sees committed files;
    this is the guard that also catches ``calendar.rounds_per_day=3`` typed at the
    command line, which composes cleanly and would truncate the experiment.
    """
    from replications.signaling.components.loop import CalendarLoopStrategy

    calendar = {"rounds_per_day": 5, "dial_turns": 0, "date_reflections": 0}
    strategy = CalendarLoopStrategy(calendar=calendar, num_days=5, day_length=7)
    strategy._check(35)  # the consistent case: no exception
    with pytest.raises(ValueError, match="does not match the day calendar"):
        strategy._check(34)
    stale_day_length = CalendarLoopStrategy(calendar=calendar, num_days=5, day_length=91)
    with pytest.raises(ValueError, match="day_length"):
        stale_day_length._check(35)


@pytest.mark.parametrize("world", ["default", "asocial", "asocial_personal"])
def test_world_config_roster_sizes_match_input_files(world: str) -> None:
    cfg = yaml.safe_load((CONF / "world" / f"{world}.yaml").read_text())
    item_list = cfg["item_list"]
    sellers = json.loads((REPLICATION / "input" / f"sellers_{item_list}.json").read_text())
    # The persona pipeline CYCLES records when count exceeds the file (creating
    # "Seller_1 2"), so a mismatch would silently corrupt the seller roster.
    assert cfg["num_sellers"] == len(sellers)
    personas = json.loads(PERSONAS.read_text())
    assert cfg["num_agents"] <= len(personas)


def test_conditions_differ_only_in_calendar_and_ceremony() -> None:
    worlds = {
        name: yaml.safe_load((CONF / "world" / f"{name}.yaml").read_text())
        for name in ("default", "asocial", "asocial_personal")
    }
    varying = {
        "condition",
        "calendar",
        "day_length",
        "num_steps",
        "dial_setup",
        "buyer_chain",
        "setting",
        "event",
    }
    baseline = {k: v for k, v in worlds["default"].items() if k not in varying}
    for name, cfg in worlds.items():
        assert {k: v for k, v in cfg.items() if k not in varying} == baseline, name


# --------------------------------------------------------------------------- #
# Dyads — differential against the pinned upstream generator
# --------------------------------------------------------------------------- #


@pinned_required
@pytest.mark.parametrize("num_agents", [10, 20, 50])
@pytest.mark.parametrize("seed", [42, 7])
def test_dyad_schedule_matches_pinned_upstream(num_agents: int, seed: int) -> None:
    upstream = signaling_pinned.load_pinned_personas()
    names = list(upstream["PLAYER_SEX"])[:num_agents]

    theirs, their_leftovers = upstream["generate_mixed_sex_dates"](names, 5, seed=seed)
    ours, our_leftovers = dyads.generate_mixed_sex_dates(
        names, 5, upstream["PLAYER_SEX"], seed=seed
    )

    assert ours == theirs
    assert our_leftovers == their_leftovers


def test_dyad_schedule_properties() -> None:
    sexes = dyads.load_sex_map(PERSONAS)
    names = [record["name"] for record in dyads.load_persona_records(PERSONAS)][:20]
    schedule, _ = dyads.generate_mixed_sex_dates(names, 5, sexes, seed=42)

    seen: set[frozenset[str]] = set()
    for day, pairs in schedule.items():
        assert len({name for pair in pairs for name in pair}) == 2 * len(pairs), day
        for first, second in pairs:
            assert sexes[first] != sexes[second]
            key = frozenset((first, second))
            assert key not in seen, f"repeat pairing {sorted(key)} on day {day}"
            seen.add(key)


def test_speakers_alternate_between_turns() -> None:
    sexes = dyads.load_sex_map(PERSONAS)
    names = [record["name"] for record in dyads.load_persona_records(PERSONAS)][:10]
    schedule, _ = dyads.generate_mixed_sex_dates(names, 2, sexes, seed=42)
    first = dyads.speakers_for_turn(schedule, 0, 0)
    second = dyads.speakers_for_turn(schedule, 0, 1)
    assert len(first) == len(schedule[0])
    assert set(first).isdisjoint(second)
    assert set(first) | set(second) == {name for pair in schedule[0] for name in pair}
    # Exactly one speaker per dyad, every turn.
    assert dyads.speakers_for_turn(schedule, 0, 2) == first


# --------------------------------------------------------------------------- #
# Marketplace — differential against the pinned upstream clearing house
# --------------------------------------------------------------------------- #

GOODS_FIXTURE = {
    "Food": {
        "Low": {"Cup Noodles": {"price": 2.5, "inventory": 10, "advert": "cheap"}},
        "High": {"Michelin Meal": {"price": 75.0, "inventory": 2, "advert": "fancy"}},
    },
    "Clothing": {
        "Low": {"Plain Tee": {"price": 8.0, "inventory": 6, "advert": "plain"}},
        "High": {"Designer Coat": {"price": 400.0, "inventory": 1, "advert": "loud"}},
    },
}

# (side, agent, good, price, qty) sets chosen to hit every branch of the ported
# matching loop: no counterparty, exact cross, partial fill, cash-short buyer,
# stock-short seller, equal prices, and multiple bidders competing.
ORDER_BOOKS: dict[str, list[tuple[str, str, str, float, int]]] = {
    "simple_cross": [
        ("bid", "B1", "Cup Noodles", 4.0, 2),
        ("ask", "S1", "Cup Noodles", 3.0, 2),
    ],
    "no_counterparty": [
        ("bid", "B1", "Cup Noodles", 4.0, 2),
        ("ask", "S2", "Plain Tee", 9.0, 1),
    ],
    "partial_fill": [
        ("bid", "B1", "Cup Noodles", 5.0, 5),
        ("ask", "S1", "Cup Noodles", 3.0, 2),
    ],
    "cash_short_buyer": [
        ("bid", "B2", "Designer Coat", 900.0, 1),
        ("ask", "S4", "Designer Coat", 500.0, 1),
    ],
    "stock_short_seller": [
        ("bid", "B1", "Michelin Meal", 90.0, 5),
        ("ask", "S3", "Michelin Meal", 70.0, 5),
    ],
    "equal_prices": [
        ("bid", "B1", "Plain Tee", 10.0, 1),
        ("ask", "S2", "Plain Tee", 10.0, 1),
    ],
    "competing_bidders": [
        ("bid", "B1", "Plain Tee", 12.0, 2),
        ("bid", "B2", "Plain Tee", 9.0, 2),
        ("ask", "S2", "Plain Tee", 8.0, 3),
    ],
    "no_bids": [
        ("ask", "S1", "Cup Noodles", 3.0, 2),
        ("ask", "S2", "Plain Tee", 9.0, 1),
    ],
}

# Starting balances; B2 is deliberately too poor for the Designer Coat.
START_CASH = {"B1": 500.0, "B2": 120.0, "S1": 0.0, "S2": 0.0, "S3": 0.0, "S4": 0.0}
START_INVENTORY: dict[str, dict[str, int]] = {
    "B1": {"Plain Tee": 1},
    "B2": {"Plain Tee": 1},
    "S1": {"Cup Noodles": 10},
    "S2": {"Plain Tee": 6},
    "S3": {"Michelin Meal": 2},
    "S4": {"Designer Coat": 1},
}
SELLER_GOOD = {"S1": "Cup Noodles", "S2": "Plain Tee", "S3": "Michelin Meal", "S4": "Designer Coat"}


@pytest.fixture
def market_fixture(tmp_path: Path) -> dict:
    """Write the goods/sellers JSON the backend loads, plus buyer names."""
    goods_path = tmp_path / "goods.json"
    goods_path.write_text(json.dumps(GOODS_FIXTURE), encoding="utf-8")
    table = goods.load_goods_table(goods_path)
    flat = goods.flatten_goods(table)
    by_id = {good.id: good for good in flat}
    sellers = [
        {
            "name": name,
            "good": good_id,
            "category": by_id[good_id].category,
            "quality": by_id[good_id].quality,
            "cost": by_id[good_id].price,
            "stock": START_INVENTORY[name][good_id],
            "goal": f"You are a seller of {good_id}.",
        }
        for name, good_id in SELLER_GOOD.items()
    ]
    sellers_path = tmp_path / "sellers.json"
    sellers_path.write_text(json.dumps(sellers), encoding="utf-8")
    # Buyers must be known personas; take real names from the converted file.
    persona_names = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:2]
    return {
        "goods_path": str(goods_path),
        "sellers_path": str(sellers_path),
        "table": table,
        "goods": flat,
        "buyers": persona_names,
    }


def _build_ours(market_fixture: dict, market_type: str, step: int = 0):
    from replications.signaling.components.marketplace_app import MarketplaceApp

    app = MarketplaceApp(
        goods_path=market_fixture["goods_path"],
        sellers_path=market_fixture["sellers_path"],
        personas_path=str(PERSONAS),
        market_type=market_type,
        seed=1,
        calendar={
            "rounds_per_day": 2,
            "dial_turns": 0,
            "date_reflections": 0,
        },
    )
    app.action_logger = _FakeLogger(step)
    roster = list(market_fixture["buyers"]) + list(SELLER_GOOD)
    app.initialize(roster)
    return app, roster


def _seed_balances(app, market_fixture: dict) -> dict[str, str]:
    """Install the fixture's cash/inventory through the checkpoint seam.

    Using ``get_state``/``set_state`` (rather than poking privates) makes this a
    second, incidental check that the checkpoint contract really carries the
    balances the clearing house reads.
    """
    buyers = market_fixture["buyers"]
    alias = {"B1": buyers[0], "B2": buyers[1], **{name: name for name in SELLER_GOOD}}
    state = app.get_state()
    state["cash"] = {alias[name]: value for name, value in START_CASH.items()}
    state["inventory"] = {
        alias[name]: dict(inventory) for name, inventory in START_INVENTORY.items()
    }
    app.set_state(state)
    return alias


def _run_upstream(market_fixture: dict, orders, market_type: str) -> dict:
    namespace = signaling_pinned.load_pinned_marketplace()
    good_cls, order_cls, agent_cls = (
        namespace["Good"],
        namespace["Order"],
        namespace["MarketplaceAgent"],
    )
    upstream_goods = [
        good_cls(
            category=good.category,
            quality=good.quality,
            id=good.id,
            price=good.price,
            inventory=good.inventory,
            advert=good.advert,
        )
        for good in market_fixture["goods"]
    ]
    agents = [
        agent_cls(
            name=name,
            role="producer" if name in SELLER_GOOD else "consumer",
            cash=START_CASH[name],
            inventory=dict(START_INVENTORY[name]),
            queue=[],
        )
        for name in START_CASH
    ]
    market = signaling_pinned.make_pinned_market(
        namespace, agents=agents, goods=upstream_goods, market_type=market_type
    )
    by_id = {good.id: good for good in upstream_goods}
    for side, agent, good_id, price, qty in orders:
        market._orderbooks[good_id].append(
            order_cls(agent_id=agent, good=by_id[good_id], price=price, qty=qty, side=side)
        )
    prices: dict[str, float] = {}
    for good_id in by_id:
        if market_type == "clearing_house":
            price, _completed, _traded = market._clear_auction(good_id)
        else:
            price, _completed = market._clear_at_fixed_prices(good_id)
        prices[good_id] = price
    return {
        "cash": {agent.name: round(agent.cash, 6) for agent in agents},
        "inventory": {
            agent.name: {k: v for k, v in agent.inventory.items() if v} for agent in agents
        },
        "prices": prices,
    }


def _normalise_prices(prices: dict[str, float]) -> dict[str, float | None]:
    return {
        good: (None if value is None or math.isnan(float(value)) else round(float(value), 6))
        for good, value in prices.items()
    }


@pinned_required
@pytest.mark.parametrize("book", sorted(ORDER_BOOKS))
def test_clearing_house_matches_pinned_upstream(market_fixture: dict, book: str) -> None:
    """The port's clearing house must be indistinguishable from upstream's."""
    orders = ORDER_BOOKS[book]
    expected = _run_upstream(market_fixture, orders, "clearing_house")

    app, _roster = _build_ours(market_fixture, "clearing_house")
    alias = _seed_balances(app, market_fixture)
    for side, agent, good_id, price, qty in orders:
        error = app.record_choice(
            alias[agent], {"side": side, "good": good_id, "price": price, "qty": qty}
        )
        assert error is None, error
    app.update(step=1, agent_names=list(alias.values()))

    inverse = {value: key for key, value in alias.items()}
    ours_cash = {inverse[name]: round(app.cash_of(name), 6) for name in alias.values()}
    ours_inventory = {
        inverse[name]: {k: v for k, v in app.inventory_of(name).items() if v}
        for name in alias.values()
    }
    assert ours_cash == expected["cash"]
    assert ours_inventory == expected["inventory"]

    ours_prices = _normalise_prices(app.price_history[0])
    assert ours_prices == _normalise_prices(expected["prices"])


@pinned_required
@pytest.mark.parametrize("book", ["simple_cross", "competing_bidders", "cash_short_buyer"])
def test_fixed_price_mode_matches_pinned_upstream(market_fixture: dict, book: str) -> None:
    """The fixed-price control is in the library but off the CLI, exactly as upstream.

    ``_clear_at_fixed_prices`` shuffles bids to ration scarce stock, so the two
    implementations only have to agree on the invariants the mode guarantees.
    """
    orders = ORDER_BOOKS[book]
    expected = _run_upstream(market_fixture, orders, "fixed_prices")

    app, _roster = _build_ours(market_fixture, "fixed_prices")
    alias = _seed_balances(app, market_fixture)
    for side, agent, good_id, price, qty in orders:
        app.record_choice(alias[agent], {"side": side, "good": good_id, "price": price, "qty": qty})
    app.update(step=1, agent_names=list(alias.values()))

    listed = {good.id: good.price for good in market_fixture["goods"]}
    for good_id, price in app.price_history[0].items():
        if price is not None and not math.isnan(float(price)):
            assert float(price) <= float(listed[good_id]) + 1e-9
    # Nobody may spend more than they had, and no buyer goes negative.
    for name in alias.values():
        assert app.cash_of(name) >= -1e-9
    assert set(_normalise_prices(expected["prices"])) == set(
        _normalise_prices(app.price_history[0])
    )


def test_non_market_steps_are_not_resolved_as_rounds(market_fixture: dict) -> None:
    """Only market rounds resolve; the other 86 steps of a day must not log one."""
    from replications.signaling.components.marketplace_app import MarketplaceApp

    app = MarketplaceApp(
        goods_path=market_fixture["goods_path"],
        sellers_path=market_fixture["sellers_path"],
        personas_path=str(PERSONAS),
        seed=1,
        calendar={
            "rounds_per_day": 2,
            "dial_turns": 3,
            "date_reflections": 0,
        },
    )
    logger = _FakeLogger(0)
    app.action_logger = logger
    app.initialize(list(market_fixture["buyers"]) + list(SELLER_GOOD))
    calendar = app.calendar
    app.update(step=calendar.day_length, agent_names=[])
    resolved = [row for row in logger.rows if row.get("label") == "round_resolved"]
    assert len(resolved) == calendar.rounds_per_day
    assert [row["data"]["round"] for row in resolved] == list(range(calendar.rounds_per_day))


def test_marketplace_checkpoint_roundtrip(market_fixture: dict) -> None:
    app, roster = _build_ours(market_fixture, "clearing_house")
    alias = _seed_balances(app, market_fixture)
    app.record_choice(alias["B1"], {"side": "bid", "good": "Cup Noodles", "price": 4.0, "qty": 2})
    app.update(step=1, agent_names=roster)
    app.action_logger.episode_idx = 1
    # A round left mid-buffer must survive too.
    app.record_choice(alias["S1"], {"side": "ask", "good": "Cup Noodles", "price": 3.0, "qty": 1})

    state = json.loads(json.dumps(app.get_state()))
    restored, _ = _build_ours(market_fixture, "clearing_house", step=1)
    restored.set_state(state)

    assert restored.get_state() == app.get_state()
    for name in alias.values():
        assert restored.cash_of(name) == app.cash_of(name)
        assert restored.inventory_of(name) == app.inventory_of(name)
    assert restored.price_history == app.price_history
    assert restored.trade_history == app.trade_history


def test_starvation_only_when_no_food(market_fixture: dict) -> None:
    app, _roster = _build_ours(market_fixture, "clearing_house")
    alias = _seed_balances(app, market_fixture)
    buyer = alias["B1"]

    # Seeded inventory holds a Plain Tee and no food.
    starving = app.consume_random_edible(buyer)
    assert "starving" in starving.lower()
    assert app.inventory_of(buyer).get("Cup Noodles", 0) == 0

    state = app.get_state()
    state["inventory"][buyer]["Cup Noodles"] = 2
    app.set_state(state)
    eating = app.consume_random_edible(buyer)
    assert "starving" not in eating.lower()
    assert "Cup Noodles" in eating
    assert app.inventory_of(buyer)["Cup Noodles"] == 1


def test_wearing_statement_names_the_quality_tier(market_fixture: dict) -> None:
    app, _roster = _build_ours(market_fixture, "clearing_house")
    alias = _seed_balances(app, market_fixture)
    buyer = alias["B2"]
    statement = app.wearing_statement(buyer)
    assert "Plain Tee" in statement
    # The tier is the signal: it must be surfaced, not just the item name.
    assert "Low" in statement and "Clothing" in statement


def test_eating_draw_is_replay_stable(market_fixture: dict) -> None:
    """Two identically-seeded backends must consume the same item.

    Upstream draws from the global RNG, which a concurrent, resumable engine
    cannot reproduce; the port derives a per-agent-per-day stream instead.
    """
    picks = []
    for _ in range(2):
        app, _roster = _build_ours(market_fixture, "clearing_house")
        alias = _seed_balances(app, market_fixture)
        state = app.get_state()
        state["inventory"][alias["B1"]] = {"Cup Noodles": 3, "Michelin Meal": 3}
        app.set_state(state)
        picks.append(app.consume_random_edible(alias["B1"]))
    assert picks[0] == picks[1]


def test_asocial_eating_statement_is_queued_into_the_next_observation(market_fixture: dict) -> None:
    """`queue_statement=True` mirrors upstream's never-popped asocial queue."""
    app, _roster = _build_ours(market_fixture, "clearing_house")
    alias = _seed_balances(app, market_fixture)
    state = app.get_state()
    state["inventory"][alias["B1"]] = {"Cup Noodles": 3}
    app.set_state(state)
    statement = app.consume_random_edible(alias["B1"], queue_statement=True)
    assert "eating" in statement
    # The statement surfaces in the buyer's next observation, upstream-style.
    assert statement in app.observe(alias["B1"])


def test_sellers_restock_to_pristine_ledgers(market_fixture: dict) -> None:
    """`restock_sellers` is upstream's daily fresh-seller regeneration."""
    app, _roster = _build_ours(market_fixture, "clearing_house")
    assert app.cash_of("S1") == 100.0  # personas.make_agents' default seller cash
    alias = _seed_balances(app, market_fixture)  # drains seller cash/stock
    assert app.cash_of("S1") == START_CASH["S1"]
    app.restock_sellers()
    assert app.cash_of("S1") == 100.0
    assert app.inventory_of("S1") == {"Cup Noodles": START_INVENTORY["S1"]["Cup Noodles"]}
    # Buyers are untouched: their carry-over is the mechanism.
    assert app.cash_of(alias["B1"]) == START_CASH["B1"]


def test_orderbooks_are_built_in_roster_order(market_fixture: dict) -> None:
    """Equal-price ties must resolve identically whatever order turns commit in.

    Upstream builds each round's book in ``acting_player_names`` order and the
    clearing loop's stable sort breaks price ties by book position, so book
    order is mechanism. The port sorts the round's choices into roster order
    before building books, making the outcome independent of the dict's
    (turn-completion) insertion order.
    """

    def clear(reverse: bool):
        app, _roster = _build_ours(market_fixture, "clearing_house")
        alias = _seed_balances(app, market_fixture)
        orders = [
            (alias["B1"], {"side": "bid", "good": "Plain Tee", "price": 10.0, "qty": 3}),
            (alias["B2"], {"side": "bid", "good": "Plain Tee", "price": 10.0, "qty": 3}),
            (alias["S2"], {"side": "ask", "good": "Plain Tee", "price": 8.0, "qty": 3}),
        ]
        app.resolve_round(0, dict(reversed(orders) if reverse else orders))
        return app, alias

    first_app, alias = clear(reverse=False)
    second_app, _ = clear(reverse=True)
    assert first_app.trade_history == second_app.trade_history
    assert {n: first_app.cash_of(n) for n in alias.values()} == {
        n: second_app.cash_of(n) for n in alias.values()
    }
    # Only 3 units existed; the roster-first buyer won the tie in both runs.
    filled = [r for r in first_app.trade_history if r["status"] == "Filled" and r["side"] == "bid"]
    assert [r["agent"] for r in filled] == [alias["B1"]]


# --------------------------------------------------------------------------- #
# Journal + date backends
# --------------------------------------------------------------------------- #


def _journal(step: int, *, num_days: int = 2, dial_turns: int = 4, date_reflections: int = 4):
    from replications.signaling.components.journal_app import JournalApp

    app = JournalApp(
        personas_path=str(PERSONAS),
        seed=42,
        num_days=num_days,
        calendar={
            "rounds_per_day": 2,
            "dial_turns": dial_turns,
            "date_reflections": date_reflections,
        },
    )
    app.action_logger = _FakeLogger(step)
    names = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:6]
    app.initialize(names)
    return app, names


def test_journal_records_each_reflection_kind() -> None:
    """Every reflection commits, and its message is the upstream memory tag.

    The engine observes the resolve message back into the acting agent, so the
    message IS upstream's ``agent.observe('[Reflection] ...')`` feedback — the
    cross-day social memory the experiment rests on.
    """
    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    kinds = []
    for step in range(calendar.day_length):
        kind = calendar.reflection_kind(step)
        if not kind:
            continue
        app, names = _journal(step)
        text = f"A reflection ({kind})."
        committed, message = app.invoke_action_detailed(
            "RECORD_REFLECTION", {"agent_name": names[0], "text": text}
        )
        assert committed, message
        if kind == "market_reflection":
            assert message == f"[marketplace reflection] {text}"
        else:
            # The rating text here carries no number, so it falls back to the
            # plain reflection tag, exactly as upstream's no-match branch does.
            assert message == f"[Reflection] {text}"
        kinds.append(app.reflections_for(names[0])[-1]["kind"])
    assert kinds == ["market_reflection", *DATE_REFLECTION_KINDS]


def test_journal_extracts_the_rating() -> None:
    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    rating_step = next(
        step for step in range(calendar.day_length) if calendar.reflection_kind(step) == "rating"
    )
    app, names = _journal(rating_step)
    committed, message = app.invoke_action_detailed(
        "RECORD_REFLECTION", {"agent_name": names[0], "text": "I would rate them 7.5 out of 10."}
    )
    assert committed
    row = app.reflections_for(names[0])[-1]
    assert row["rating"] == pytest.approx(7.5)
    assert row["partner"]
    # Upstream re-observes a parsed rating in its special memory form, with the
    # regex's raw matched string interpolated.
    assert message == f"[Reflection] {names[0]} rated {row['partner']} as 7.5/10"


def test_journal_rejects_reflection_outside_a_reflection_step() -> None:
    app, names = _journal(0)  # step 0 is a market round
    committed, message = app.invoke_action_detailed(
        "RECORD_REFLECTION", {"agent_name": names[0], "text": "too early"}
    )
    assert not committed
    assert message


def _date_app(step: int):
    from replications.signaling.components.date_app import DateMessagingApp

    app = DateMessagingApp(
        personas_path=str(PERSONAS),
        seed=42,
        num_days=2,
        calendar={
            "rounds_per_day": 2,
            "dial_turns": 4,
            "date_reflections": 4,
        },
    )
    app.action_logger = _FakeLogger(step)
    names = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:6]
    app.initialize(names)
    return app, names


def test_date_messages_are_restricted_to_todays_partner() -> None:
    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    date_step = next(step for step in range(calendar.day_length) if calendar.is_date_step(step))
    app, names = _date_app(date_step)
    speaker = names[0]
    partner = app.partner_of(speaker, date_step)
    assert partner

    committed, message = app.invoke_action_detailed(
        "SEND_MESSAGE", {"agent_name": speaker, "recipient": partner, "text": "Hello."}
    )
    assert committed, message

    stranger = next(name for name in names if name not in {speaker, partner})
    committed, message = app.invoke_action_detailed(
        "SEND_MESSAGE", {"agent_name": speaker, "recipient": stranger, "text": "Hi."}
    )
    assert not committed
    assert partner in message


def test_date_observation_is_scoped_to_today() -> None:
    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    date_step = next(step for step in range(calendar.day_length) if calendar.is_date_step(step))
    app, names = _date_app(date_step)
    speaker = names[0]
    partner = app.partner_of(speaker, date_step)
    app.invoke_action_detailed(
        "SEND_MESSAGE", {"agent_name": speaker, "recipient": partner, "text": "Day one line."}
    )
    assert "Day one line." in app.observe(speaker)

    # Same backend, next day: yesterday's conversation must not be visible.
    next_day_step = date_step + calendar.day_length
    app.action_logger.episode_idx = next_day_step
    assert "Day one line." not in app.observe(speaker)


def test_journal_and_date_state_roundtrip() -> None:
    """Both thin backends must answer identically after a save→restore."""
    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    rating_step = next(
        step for step in range(calendar.day_length) if calendar.reflection_kind(step) == "rating"
    )
    journal, names = _journal(rating_step)
    journal.invoke_action_detailed(
        "RECORD_REFLECTION", {"agent_name": names[0], "text": "An 8.0 evening."}
    )
    restored_journal, _ = _journal(rating_step)
    restored_journal.set_state(journal.get_state())
    assert restored_journal.reflections_for(names[0]) == journal.reflections_for(names[0])

    date_step = next(step for step in range(calendar.day_length) if calendar.is_date_step(step))
    date_app, date_names = _date_app(date_step)
    speaker = date_names[0]
    partner = date_app.partner_of(speaker, date_step)
    date_app.invoke_action_detailed(
        "SEND_MESSAGE", {"agent_name": speaker, "recipient": partner, "text": "Roundtrip line."}
    )
    restored_date, _ = _date_app(date_step)
    restored_date.set_state(date_app.get_state())
    assert restored_date.observe(speaker) == date_app.observe(speaker)


# --------------------------------------------------------------------------- #
# Next-acting gating
# --------------------------------------------------------------------------- #


def _fake_context(backend, agent_names):
    return types.SimpleNamespace(
        gm_name="gm",
        backend=backend,
        agents=[types.SimpleNamespace(name=name) for name in agent_names],
        agent_names=tuple(agent_names),
        agent_flow_tags={},
        model=None,
        event_logger=None,
    )


def test_calendar_next_acting_gates_by_phase() -> None:
    from replications.signaling.components.next_acting import CalendarNextActing

    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    buyers = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:4]
    roster = [*buyers, "Seller_1", "Seller_2"]
    logger = _FakeLogger(0)
    backend = types.SimpleNamespace(
        action_logger=logger, current_episode=lambda: logger.episode_idx
    )
    component = CalendarNextActing(
        agent_names=roster,
        context=_fake_context(backend, roster),
        phases=["market"],
        roles="all",
        personas_path=str(PERSONAS),
        calendar={
            "rounds_per_day": 2,
            "dial_turns": 4,
            "date_reflections": 4,
        },
    )
    for step in range(calendar.day_length):
        logger.episode_idx = step
        selected = component.acting_agent_names()
        assert selected == (roster if calendar.is_market_step(step) else [])


def test_journal_next_acting_selects_buyers_only() -> None:
    from replications.signaling.components.next_acting import CalendarNextActing

    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    buyers = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:4]
    roster = [*buyers, "Seller_1"]
    logger = _FakeLogger(0)
    backend = types.SimpleNamespace(
        action_logger=logger, current_episode=lambda: logger.episode_idx
    )
    component = CalendarNextActing(
        agent_names=roster,
        context=_fake_context(backend, roster),
        phases=["market_reflection", "date_reflection"],
        roles="buyers",
        personas_path=str(PERSONAS),
        calendar={
            "rounds_per_day": 2,
            "dial_turns": 4,
            "date_reflections": 4,
        },
    )
    for step in range(calendar.day_length):
        logger.episode_idx = step
        selected = component.acting_agent_names()
        assert selected == (buyers if calendar.is_journal_step(step) else [])


def test_calendar_next_acting_rejects_unknown_phase() -> None:
    from replications.signaling.components.next_acting import CalendarNextActing

    backend = types.SimpleNamespace(action_logger=_FakeLogger(0), current_episode=lambda: 0)
    calendar = {"rounds_per_day": 2, "dial_turns": 4, "date_reflections": 4}
    with pytest.raises(ValueError, match="phase"):
        CalendarNextActing(
            agent_names=[],
            context=_fake_context(backend, []),
            phases=["marketplace"],  # typo for "market"
            personas_path=str(PERSONAS),
            calendar=calendar,
        )


def test_calendar_component_requires_a_calendar_block() -> None:
    """An omitted `calendar:` must fail loudly rather than default to the social shape.

    A component silently built with 80 date turns inside an asocial run would
    simply never act, which is invisible until the artifacts come back empty.
    """
    from replications.signaling.components.next_acting import CalendarNextActing

    backend = types.SimpleNamespace(action_logger=_FakeLogger(0), current_episode=lambda: 0)
    with pytest.raises(ValueError, match=r"calendar"):
        CalendarNextActing(
            agent_names=[],
            context=_fake_context(backend, []),
            phases=["market"],
            personas_path=str(PERSONAS),
        )


class _CeremonyBackend:
    """Minimal marketplace stand-in for the day-boundary component."""

    def __init__(self) -> None:
        self.updates: list[int] = []
        self.queued: dict[str, list[str]] = {}
        self.restocks = 0
        self.seller_names: list[str] = []

    def update(self, *, step: int, agent_names, context=None) -> None:
        del agent_names, context
        self.updates.append(int(step))

    def consume_random_edible(self, agent_name: str, *, queue_statement: bool = False) -> str:
        statement = f"{agent_name} ate a taco."
        if queue_statement:
            self.queued.setdefault(agent_name, []).append(statement)
        return statement

    def wearing_statement(self, agent_name: str) -> str:
        return f"{agent_name} is wearing a coat."

    def restock_sellers(self) -> None:
        self.restocks += 1


class _CeremonyAgent:
    """Agent stand-in that records what the ceremony injects."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.observations: list[str] = []
        self.prompts: list[str] = []
        self.state: dict = {"memory": [f"{name} initial"]}
        self.set_states: list[dict] = []

        def _sample(prompt: str) -> str:
            self.prompts.append(prompt)
            return "first***second"

        self.model = types.SimpleNamespace(sample_text=_sample)

    def observe(self, observation: str) -> None:
        self.observations.append(observation)

    def get_state(self) -> dict:
        return dict(self.state)

    def set_state(self, state: dict) -> None:
        self.set_states.append(dict(state))


def _ceremony(enabled: bool = True):
    from replications.signaling.components.dial_handler import DayBoundaryUpdateComponent

    backend = _CeremonyBackend()
    component = DayBoundaryUpdateComponent(
        context=types.SimpleNamespace(backend=backend, model=None),
        calendar={"rounds_per_day": 2, "dial_turns": 2, "date_reflections": 4},
        personas_path=str(PERSONAS),
        seed=1,
        num_days=2,
        enabled=enabled,
        shared_setup=False,
        personal_events=True,
        num_personal_events=2,
    )
    # The first four personas alternate male/female, giving two dyads per day
    # with enough variety for unique pairings across both days — every buyer
    # is on a date each day, so every buyer receives personal events.
    buyers = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:4]
    agents = [_CeremonyAgent(name) for name in buyers]
    return component, backend, agents


def test_dial_setup_component_fires_only_on_the_calendar_step() -> None:
    """The schedule is DERIVED: no at_step list, and every other step is a plain update."""
    calendar = DayCalendar(rounds_per_day=2, dial_turns=2, date_reflections=4)
    component, backend, agents = _ceremony()
    for step in range(2 * calendar.day_length):
        component.update(step=step, agents=agents)

    # The slot's ordinary job still happens on every step.
    assert backend.updates == list(range(2 * calendar.day_length))
    # ... and the ceremony only on the two dial_setup steps.
    setup_steps = [
        step
        for step in range(2 * calendar.day_length)
        if calendar.phase_of(step).phase == PHASE_DIAL_SETUP
    ]
    assert setup_steps == calendar.dial_setup_steps(2)
    for agent in agents:
        # Two personal events per buyer per day, and no shared setup in this mode.
        assert len(agent.observations) == 2 * len(setup_steps)
        assert not any("Shared" in text for text in agent.observations)


def test_dial_setup_component_disabled_still_eats_daily() -> None:
    """The asocial arm: no generated content, but the daily eating draw still runs.

    Upstream calls ``get_eating_statement`` for every consumer at the end of
    every day in EVERY condition; with no DIAL run the statement stays queued
    and surfaces in the next market observation. A control arm that never eats
    would confound the comparison (food never depletes, starvation impossible).
    """
    calendar = DayCalendar(rounds_per_day=2, dial_turns=2, date_reflections=4)
    component, backend, agents = _ceremony(enabled=False)
    for step in range(2 * calendar.day_length):
        component.update(step=step, agents=agents)
    assert backend.updates == list(range(2 * calendar.day_length))
    # Nothing generated or injected...
    assert all(not agent.observations for agent in agents)
    # ...but one queued eating statement per buyer per day.
    for agent in agents:
        assert backend.queued[agent.name] == [f"{agent.name} ate a taco."] * 2


def test_enabled_ceremony_hands_eating_to_the_events_prompt_not_the_queue() -> None:
    """With the DIAL run on, upstream pops the statement for the events prompt."""
    calendar = DayCalendar(rounds_per_day=2, dial_turns=2, date_reflections=4)
    component, backend, agents = _ceremony(enabled=True)
    for step in range(calendar.day_length):
        component.update(step=step, agents=agents)
    assert backend.queued == {}
    # The eating statement conditions the personal-events generation.
    prompts_seen = [p for agent in agents for p in agent.prompts]
    assert prompts_seen
    assert all("ate a taco" in prompt for prompt in prompts_seen)


def test_sellers_are_reset_at_each_day_start() -> None:
    """Upstream regenerates sellers fresh every morning: ledgers AND memory."""
    calendar = DayCalendar(rounds_per_day=2, dial_turns=2, date_reflections=4)
    component, backend, agents = _ceremony()
    seller = _CeremonyAgent("Seller_1")
    backend.seller_names = [seller.name]
    roster = [*agents, seller]

    for step in range(calendar.day_length):
        component.update(step=step, agents=roster)
    # Day 0: baseline snapshotted, nothing reset yet.
    assert backend.restocks == 0
    assert seller.set_states == []

    # Mutate the seller's live state, then cross into day 1.
    seller.state = {"memory": ["polluted by day 0"]}
    component.update(step=calendar.day_length, agents=roster)
    assert backend.restocks == 1
    # The seller was restored to the step-0 snapshot, not today's state.
    assert seller.set_states == [{"memory": ["Seller_1 initial"]}]
    # Buyers keep their memory: no set_state calls on them.
    assert all(not agent.set_states for agent in agents)


def test_seller_baselines_roundtrip_through_component_state() -> None:
    """A resumed run must reset sellers to the same pristine snapshot."""
    calendar = DayCalendar(rounds_per_day=2, dial_turns=2, date_reflections=4)
    component, backend, agents = _ceremony()
    seller = _CeremonyAgent("Seller_1")
    backend.seller_names = [seller.name]
    component.update(step=0, agents=[*agents, seller])

    restored_component, restored_backend, _ = _ceremony()
    restored_backend.seller_names = [seller.name]
    restored_component.set_state(component.get_state())
    seller.state = {"memory": ["mid-run noise"]}
    restored_component.update(step=calendar.day_length, agents=[*agents, seller])
    assert restored_backend.restocks == 1
    assert seller.set_states[-1] == {"memory": ["Seller_1 initial"]}


def test_dyad_next_acting_selects_one_speaker_per_dyad() -> None:
    from replications.signaling.components.next_acting import DyadTurnNextActing

    calendar = DayCalendar(rounds_per_day=2, dial_turns=4, date_reflections=4)
    buyers = [r["name"] for r in dyads.load_persona_records(PERSONAS)][:6]
    roster = [*buyers, "Seller_1"]
    logger = _FakeLogger(0)
    backend = types.SimpleNamespace(
        action_logger=logger, current_episode=lambda: logger.episode_idx
    )
    component = DyadTurnNextActing(
        agent_names=roster,
        context=_fake_context(backend, roster),
        personas_path=str(PERSONAS),
        seed=42,
        num_days=2,
        calendar={
            "rounds_per_day": 2,
            "dial_turns": 4,
            "date_reflections": 4,
        },
    )
    schedule = dyads.build_schedule(buyers, num_days=2, personas_path=str(PERSONAS), seed=42)
    previous: list[str] = []
    for step in range(calendar.day_length):
        logger.episode_idx = step
        selected = component.acting_agent_names()
        if not calendar.is_date_step(step):
            assert selected == []
            continue
        assert len(selected) == len(schedule[0])
        if previous:
            assert set(selected).isdisjoint(previous)
        previous = selected


# --------------------------------------------------------------------------- #
# End to end, with no language model
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = REPLICATION / "tools" / "run_scripted.sh"


def _run_scripted(world: str, output: Path, *overrides: str) -> None:
    """Drive one condition through the real runtime with the scripted provider."""
    import subprocess

    result = subprocess.run(
        [str(RUN_SCRIPT), world, f"output_rootname={output}", *overrides],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "status=success" in result.stdout + result.stderr


def _labels(run_dir: Path, gm: str) -> dict[str, int]:
    path = run_dir / gm / "action_events.jsonl"
    if not path.is_file():
        return {}
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            label = str(json.loads(line).get("label", ""))
            counts[label] = counts.get(label, 0) + 1
    return counts


@pytest.mark.subprocess
def test_end_to_end_social_run_exercises_every_phase(tmp_path: Path) -> None:
    """A whole social run: orders clear, dates alternate, ratings parse."""
    output = tmp_path / "social"
    _run_scripted("smoke", output)

    cfg = yaml.safe_load((CONF / "world" / "smoke.yaml").read_text())
    calendar = DayCalendar.from_params(cfg["calendar"])
    buyers, days = cfg["num_agents"], cfg["num_days"]

    market = _labels(output, "market_gm")
    assert market["bid"] == buyers * calendar.rounds_per_day * days
    assert market["ask"] == cfg["num_sellers"] * calendar.rounds_per_day * days
    assert market["round_resolved"] == calendar.rounds_per_day * days

    # Trades really cleared — a run where nothing crosses would still "pass"
    # every structural check above.
    resolved = [
        json.loads(line)
        for line in (output / "market_gm" / "action_events.jsonl").read_text().splitlines()
        if '"round_resolved"' in line
    ]
    assert sum(float(row["data"].get("num_trades", 0)) for row in resolved) > 0

    journal = _labels(output, "journal_gm")
    assert journal["record_reflection"] == buyers * (1 + calendar.date_reflections) * days

    ratings = [
        json.loads(line)["data"]
        for line in (output / "journal_gm" / "action_events.jsonl").read_text().splitlines()
        if '"rating"' in line
    ]
    parsed = [row["rating"] for row in ratings if row.get("kind") == "rating"]
    assert len(parsed) == buyers * days
    assert all(isinstance(value, (int, float)) for value in parsed)

    dates = _labels(output, "date_gm")
    assert dates["send_message"] == (buyers // 2) * calendar.dial_turns * days

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["status"] == "success"
    assert (output / "checkpoints").is_dir()


@pytest.mark.subprocess
@pytest.mark.parametrize("world", ["asocial", "asocial_personal"])
def test_end_to_end_asocial_runs_have_no_dates(world: str, tmp_path: Path) -> None:
    output = tmp_path / world
    _run_scripted(world, output)

    cfg = yaml.safe_load((CONF / "world" / f"{world}.yaml").read_text())
    market = _labels(output, "market_gm")
    assert market["bid"] == cfg["num_agents"] * cfg["calendar"]["rounds_per_day"] * cfg["num_days"]
    # No conversational surface in these arms, and only the market reflection.
    assert _labels(output, "date_gm") == {}
    journal = _labels(output, "journal_gm")
    assert journal["record_reflection"] == cfg["num_agents"] * cfg["num_days"]


@pytest.mark.subprocess
def test_end_to_end_resume_from_a_day_boundary_matches_the_full_run(tmp_path: Path) -> None:
    """A run resumed at the day-1 checkpoint replays day 2 identically.

    This exercises the whole restore surface at once: the snapshot backends,
    the day-boundary component's seller baselines, and the calendar machinery
    (a resumed run must restock sellers to the SAME pristine state and pair
    the same dyads). Scripted behaviour is deterministic, so day 2's committed
    actions must match the uninterrupted run's exactly.
    """
    import shutil

    full = tmp_path / "full"
    _run_scripted("smoke", full)

    cfg = yaml.safe_load((CONF / "world" / "smoke.yaml").read_text())
    day_length = DayCalendar.from_params(cfg["calendar"]).day_length

    # Truncate a copy of the run to its day-1 checkpoint, as if it had died there.
    source = tmp_path / "source"
    shutil.copytree(full, source)
    kept = []
    for ckpt in (source / "checkpoints").glob("step_*_checkpoint.json"):
        step = int(ckpt.name.split("_")[1])
        if step > day_length:
            ckpt.unlink()
        else:
            kept.append(step)
    assert kept, "expected a day-boundary checkpoint to resume from"

    resumed = tmp_path / "resumed"
    _run_scripted("smoke", resumed, f"sim.checkpoint.source_run={source}")

    def _events(run_dir: Path, gm: str, since: int | None = None) -> list[tuple]:
        path = run_dir / gm / "action_events.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
        )
        return sorted(
            (
                row["episode"],
                row.get("label"),
                row.get("source_user"),
                json.dumps(row.get("data") or {}, sort_keys=True),
            )
            for row in rows
            if isinstance(row.get("episode"), int) and (since is None or row["episode"] >= since)
        )

    gms = ["market_gm", "journal_gm", "date_gm"]
    resumed_events = {gm: _events(resumed, gm) for gm in gms}
    first_resumed = min(
        (events[0][0] for events in resumed_events.values() if events), default=None
    )
    # The resumed run really resumed (day 2), not started over.
    assert first_resumed is not None and first_resumed >= day_length - 1
    for gm in gms:
        assert resumed_events[gm] == _events(full, gm, since=first_resumed), gm


@pytest.mark.subprocess
def test_metrics_evaluator_reports_every_measure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from replications.signaling.evaluators import metrics

    output = tmp_path / "metrics_run"
    _run_scripted("smoke", output)
    report = metrics.compute(output)

    assert report["counts"]["orders"] > 0
    assert report["counts"]["ratings"] > 0
    assert report["status_share"]["overall"] is not None
    assert report["ratings"]["mean"] is not None
    assert report["prices"]["per_day"]
    # The condition is the world config's key, not the (constant) scenario name.
    assert report["condition"] == "smoke"

    # The report must not depend on the caller's working directory: goods_path
    # in the effective config is repo-relative, and a silently empty goods
    # table would grade every purchase non-High (a plausible wrong answer).
    monkeypatch.chdir(tmp_path)
    assert metrics.compute(output)["status_share"] == report["status_share"]


# --------------------------------------------------------------------------- #
# Prompt fidelity — every ported prompt pinned against the upstream sources
# --------------------------------------------------------------------------- #

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _canon(text: str) -> str:
    """Collapse a prompt to its comparable core.

    Removes whitespace and quote characters (upstream texts are split across
    adjacent string literals and f-strings in source) and replaces every
    ``{placeholder}`` with a sentinel, so differing variable names
    (``{name}`` vs ``{entity_name}`` vs ``{agent_data.name}``) compare equal
    while every WORD of the prompt must match exactly.
    """
    collapsed = _PLACEHOLDER.sub("@", text)
    return "".join(collapsed.split()).replace('"', "").replace("'", "")


def _pinned_source(relative: str) -> str:
    root = signaling_pinned.pinned_root()
    assert root is not None
    source = (root / relative).read_text(encoding="utf-8")
    # Upstream texts are f-string fragments; drop the prefix so adjacent
    # literals concatenate cleanly once quotes are removed. Literal \n escapes
    # inside those literals are newlines in the rendered prompt.
    source = source.replace("f'", "'").replace('f"', '"').replace("\\n", " ")
    return _canon(source)


#: (port text, upstream file the text must appear in, verbatim). The market
#: CTAs re-insert the word JSON the port drops (the documented deviation) so
#: the remainder must match upstream to the word.
_PINNED_PROMPTS: list[tuple[str, str]] = [
    (prompts.GOAL_TEXT, "examples/signaling/simulation.py"),
    (prompts.MARKET_REFLECTION_PROMPT, "examples/signaling/simulation.py"),
    (prompts.MARKET_REFLECTION_MEMORY, "examples/signaling/simulation.py"),
    (prompts.STARVATION_STATEMENT, "examples/signaling/dial.py"),
    (prompts.EATING_STATEMENT, "examples/signaling/dial.py"),
    (prompts.WEARING_STATEMENT, "examples/signaling/dial.py"),
    (prompts.WEARING_STATEMENT_UNTIERED, "examples/signaling/dial.py"),
    (prompts.DATE_SUMMARY_PROMPT, "examples/signaling/dial.py"),
    (prompts.VISUAL_IMPRESSION_PROMPT, "examples/signaling/dial.py"),
    (prompts.COMPARISON_PROMPT, "examples/signaling/dial.py"),
    (prompts.RATING_PROMPT, "examples/signaling/dial.py"),
    (prompts.RATING_MEMORY, "examples/signaling/dial.py"),
    (prompts.CONVERSATION_OPENING, "examples/signaling/dial.py"),
    (
        prompts.SHARED_DIALOGUE_SETUP_PROMPT,
        "concordia/contrib/components/game_master/day_in_the_life_initializer.py",
    ),
    (
        prompts.PERSONAL_MUNDANE_EVENTS_PROMPT,
        "concordia/contrib/components/game_master/day_in_the_life_initializer.py",
    ),
    (
        prompts.PERSONAL_EVENT_OBSERVATION,
        "concordia/contrib/components/game_master/day_in_the_life_initializer.py",
    ),
    (
        prompts.SHARED_SETUP_OBSERVATION,
        "concordia/contrib/components/game_master/day_in_the_life_initializer.py",
    ),
    (prompts.DATE_CALL_TO_ACTION, "concordia/typing/entity.py"),
    (prompts.SITUATION_PERCEPTION_QUESTION, "examples/signaling/agents/consumer.py"),
    (prompts.CONSUMER_SELF_PERCEPTION_QUESTION, "examples/signaling/agents/consumer.py"),
    (prompts.CONSUMER_EVALUATION_QUESTION, "examples/signaling/agents/consumer.py"),
    (prompts.CONVO_SITUATION_QUESTION, "examples/signaling/agents/convo_agent.py"),
    (prompts.CONVO_SELF_PERCEPTION_QUESTION, "examples/signaling/agents/convo_agent.py"),
    (prompts.PERSON_BY_SITUATION_QUESTION, "examples/signaling/agents/convo_agent.py"),
    (prompts.LAST_SENTENCE_QUESTION, "examples/signaling/agents/convo_agent.py"),
    (prompts.PINK_NOISE_QUESTION, "examples/signaling/agents/convo_agent.py"),
    # The marketplace CTAs, with the dropped word JSON restored; the Format /
    # Return-only lines upstream appends are the documented deviation.
    (
        prompts.CONSUMER_CALL_TO_ACTION.replace("one bid", "one JSON bid").split("Ensure")[0],
        "concordia/contrib/components/game_master/marketplace.py",
    ),
    ("Ensure price*qty ≤ cash.", "concordia/contrib/components/game_master/marketplace.py"),
    (
        prompts.PRODUCER_CALL_TO_ACTION.replace("one ask", "one JSON ask"),
        "concordia/contrib/components/game_master/marketplace.py",
    ),
]


@pinned_required
@pytest.mark.parametrize(
    "port_text, upstream_file",
    _PINNED_PROMPTS,
    ids=[f"{i:02d}_{Path(f).stem}" for i, (_, f) in enumerate(_PINNED_PROMPTS)],
)
def test_prompts_are_verbatim_from_the_pinned_upstream(port_text: str, upstream_file: str) -> None:
    """Every ported prompt must appear word-for-word in the pinned source.

    Prompts are mechanism in this experiment; a paraphrase is an uncontrolled
    change. This is the gate that catches wording drift in either direction —
    it would have caught the hand-merged CTA the port originally shipped.
    """
    assert _canon(port_text) in _pinned_source(upstream_file)


@pinned_required
def test_date_themes_are_verbatim_from_the_pinned_upstream() -> None:
    source = _pinned_source(
        "concordia/contrib/components/game_master/day_in_the_life_initializer.py"
    )
    for theme in prompts.DATE_THEMES:
        assert _canon(theme) in source


def test_date_gm_call_to_action_matches_the_prompts_module() -> None:
    """The date CTA lives in YAML (a config-owned template); pin it to prompts.py."""
    env_cfg = yaml.safe_load((CONF / "env" / "signaling.yaml").read_text())
    gms = {gm["gm_name"]: gm for gm in env_cfg["gm_orchestration"]["gms"]}
    template = gms["date_gm"]["components"]["action_prompt"]["params"]["action_prompt"]
    assert template.strip() == prompts.DATE_CALL_TO_ACTION
