from __future__ import annotations

from silisocs.environments.backends.resource_market.app import ResourceMarketApp


def test_resource_market_initializes_agents_with_cash_and_inventory() -> None:
    app = ResourceMarketApp(initial_cash=25, initial_inventory={"food": 2})

    app.initialize(agent_names=["Alice", "Bob"])

    observation = app.observe("Alice")
    assert "Cash: 25" in observation
    assert "food: 2" in observation
    assert "Open listings: none" in observation


def test_resource_market_listing_and_purchase_updates_state() -> None:
    app = ResourceMarketApp(initial_cash=20, initial_inventory={"food": 2})
    app.initialize(agent_names=["Alice", "Bob"])

    listing_result = app.invoke_action_with_kwargs(
        "LIST_RESOURCE",
        {
            "agent_name": "Alice",
            "resource": "food",
            "quantity": 1,
            "price": 7,
        },
    )
    buy_result = app.invoke_action_with_kwargs(
        "BUY_LISTING",
        {
            "agent_name": "Bob",
            "listing_id": 1,
        },
    )

    assert "Listing 1 created" in listing_result
    assert "Bob bought 1 food from Alice for 7" in buy_result
    assert "Cash: 27" in app.observe("Alice")
    assert "food: 1" in app.observe("Alice")
    assert "Cash: 13" in app.observe("Bob")
    assert "food: 3" in app.observe("Bob")


def test_resource_market_role_capabilities_and_upkeep_change_state() -> None:
    app = ResourceMarketApp(
        initial_cash=10,
        initial_inventory={"food": 1, "wood": 0},
        resources=["food", "wood"],
        production_capabilities={
            "farmer": {"food": 2},
            "woodworker": {"wood": 1},
        },
        role_needs={"farmer": {"wood": 1}, "woodworker": {"food": 1}},
        upkeep_interval=1,
    )
    app.initialize(
        agent_names=["Alice", "Bob"],
        sim_roles={"Alice": "farmer", "Bob": "woodworker"},
    )

    blocked = app.invoke_action_with_kwargs(
        "PRODUCE_RESOURCE",
        {"agent_name": "Alice", "resource": "wood", "quantity": 1},
    )
    produced = app.invoke_action_with_kwargs(
        "PRODUCE_RESOURCE",
        {"agent_name": "Bob", "resource": "wood", "quantity": 1},
    )
    transferred = app.invoke_action_with_kwargs(
        "TRANSFER_RESOURCE",
        {"agent_name": "Bob", "target_user": "Alice", "resource": "wood", "quantity": 1},
    )
    app.update(step=1, agent_names=["Alice", "Bob"])

    alice_observation = app.observe("Alice")
    bob_observation = app.observe("Bob")
    assert "Alice cannot produce wood" in blocked
    assert "Bob produced 1 wood" in produced
    assert "Bob transferred 1 wood to Alice" in transferred
    assert "Role: farmer" in alice_observation
    assert "Satisfaction: 1" in alice_observation
    assert "Satisfaction: 1" in bob_observation


def test_resource_market_can_cancel_open_listing() -> None:
    app = ResourceMarketApp(initial_cash=20, initial_inventory={"food": 2})
    app.initialize(agent_names=["Alice"])
    app.list_resource(agent_name="Alice", resource="food", quantity=1, price=5)

    result = app.invoke_action_with_kwargs(
        "CANCEL_LISTING",
        {
            "agent_name": "Alice",
            "listing_id": 1,
        },
    )

    assert "Alice cancelled listing 1" in result
    observation = app.observe("Alice")
    assert "food: 2" in observation
    assert "Open listings: none" in observation


def test_resource_market_rejects_invalid_market_actions() -> None:
    app = ResourceMarketApp(initial_cash=5, initial_inventory={"food": 0})
    app.initialize(agent_names=["Alice", "Bob"])

    list_result = app.invoke_action_with_kwargs(
        "LIST_RESOURCE",
        {
            "agent_name": "Alice",
            "resource": "food",
            "quantity": 1,
            "price": 3,
        },
    )
    app.produce_resource(agent_name="Alice", resource="food", quantity=1)
    app.list_resource(agent_name="Alice", resource="food", quantity=1, price=6)
    buy_result = app.invoke_action_with_kwargs(
        "BUY_LISTING",
        {
            "agent_name": "Bob",
            "listing_id": 1,
        },
    )

    assert "does not have 1 food" in list_result
    assert "does not have enough cash" in buy_result


def test_resource_market_consume_and_finished_actions() -> None:
    app = ResourceMarketApp(initial_cash=5, initial_inventory={"food": 2})
    app.initialize(agent_names=["Alice"])

    consume_result = app.invoke_action_with_kwargs(
        "CONSUME_RESOURCE",
        {
            "agent_name": "Alice",
            "resource": "food",
            "quantity": 1,
        },
    )

    assert "Alice consumed 1 food" in consume_result
    assert "food: 1" in app.observe("Alice")
    assert app.invoke_action_with_kwargs("FINISHED", {}) == "Finished action episode"
