"""Minimal non-social resource market environment backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from silisocs.environments.backends.base import EnvironmentApp, app_action


@dataclass
class Listing:
    """Open market listing."""

    listing_id: int
    seller: str
    resource: str
    quantity: int
    price: int
    open: bool = True


@dataclass
class ResourceMarketApp(EnvironmentApp):
    """Small in-memory market where agents produce, list, buy, and consume resources."""

    action_logger: Any = None
    app_description: str = "A resource market environment."
    initial_cash: int = 20
    initial_inventory: dict[str, int] = field(default_factory=lambda: {"food": 1})
    resources: list[str] = field(default_factory=lambda: ["food", "wood", "ore"])
    recent_event_limit: int = 8

    def __post_init__(self) -> None:
        super().__init__()
        self._cash: dict[str, int] = {}
        self._inventory: dict[str, dict[str, int]] = {}
        self._listings: dict[int, Listing] = {}
        self._events: list[str] = []
        self._next_listing_id = 1

    def name(self) -> str:
        return "resource_market"

    def description(self) -> str:
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del kwargs
        self._cash = {name: int(self.initial_cash) for name in agent_names}
        self._inventory = {
            name: {resource: int(quantity) for resource, quantity in self.initial_inventory.items()}
            for name in agent_names
        }
        for name in agent_names:
            for resource in self.resources:
                self._inventory[name].setdefault(resource, 0)
        self._listings = {}
        self._events = [f"Market opened for {len(agent_names)} agents."]
        self._next_listing_id = 1

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        limit = int(kwargs.get("limit", self.recent_event_limit) or self.recent_event_limit)
        inventory = self._inventory.get(actor_name, {})
        inventory_text = ", ".join(
            f"{resource}: {quantity}" for resource, quantity in sorted(inventory.items())
        )
        listings = [listing for listing in self._listings.values() if listing.open]
        if listings:
            listing_text = "\n".join(
                f"  {listing.listing_id}: {listing.seller} sells "
                f"{listing.quantity} {listing.resource} for {listing.price}"
                for listing in listings
            )
        else:
            listing_text = "none"
        events = self._events[-limit:] if limit > 0 else []
        event_text = "\n".join(f"  {event}" for event in events) if events else "none"
        return (
            "RESOURCE MARKET STATE\n"
            f"Actor: {actor_name}\n"
            f"Cash: {self._cash.get(actor_name, 0)}\n"
            f"Inventory: {inventory_text or 'none'}\n"
            f"Open listings: {listing_text}\n"
            f"Recent events:\n{event_text}"
        )

    def _record(self, event: str) -> None:
        self._events.append(event)
        log_fn = getattr(self.action_logger, "log", None)
        if callable(log_fn):
            log_fn({"event_type": "resource_market", "message": event})

    def _ensure_agent(self, current_user: str) -> str | None:
        if current_user not in self._cash:
            return f"Unknown market participant: {current_user}"
        return None

    def _inventory_count(self, current_user: str, resource: str) -> int:
        return int(self._inventory.setdefault(current_user, {}).get(resource, 0))

    @app_action(selectable_name="INSPECT_MARKET", description="Inspect current market state")
    def inspect_market(self, current_user: str) -> str:
        """Inspect current cash, inventory, listings, and recent events."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        return self.observe(current_user)

    @app_action(selectable_name="PRODUCE_RESOURCE", description="Produce a resource")
    def produce_resource(self, current_user: str, resource: str, quantity: int = 1) -> str:
        """Add newly produced resource units to the current user's inventory."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        quantity = int(quantity)
        if quantity <= 0:
            return "Quantity must be positive."
        inventory = self._inventory.setdefault(current_user, {})
        inventory[resource] = int(inventory.get(resource, 0)) + quantity
        event = f"{current_user} produced {quantity} {resource}."
        self._record(event)
        return event

    @app_action(selectable_name="LIST_RESOURCE", description="List a resource for sale")
    def list_resource(self, current_user: str, resource: str, quantity: int, price: int) -> str:
        """Move a resource quantity into an open listing for sale."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        quantity = int(quantity)
        price = int(price)
        if quantity <= 0:
            return "Quantity must be positive."
        if price <= 0:
            return "Price must be positive."
        available = self._inventory_count(current_user, resource)
        if available < quantity:
            return f"{current_user} does not have {quantity} {resource} to list."

        self._inventory[current_user][resource] = available - quantity
        listing = Listing(
            listing_id=self._next_listing_id,
            seller=current_user,
            resource=resource,
            quantity=quantity,
            price=price,
        )
        self._listings[listing.listing_id] = listing
        self._next_listing_id += 1
        event = (
            f"Listing {listing.listing_id} created: {current_user} sells "
            f"{quantity} {resource} for {price}."
        )
        self._record(event)
        return event

    @app_action(selectable_name="BUY_LISTING", description="Buy an open market listing")
    def buy_listing(self, current_user: str, listing_id: int) -> str:
        """Buy an open listing by ID."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        listing_id = int(listing_id)
        listing = self._listings.get(listing_id)
        if listing is None or not listing.open:
            return f"Listing {listing_id} is not open."
        if listing.seller == current_user:
            return "Agents cannot buy their own listing."
        if self._cash[current_user] < listing.price:
            return f"{current_user} does not have enough cash to buy listing {listing_id}."

        self._cash[current_user] -= listing.price
        self._cash[listing.seller] += listing.price
        buyer_inventory = self._inventory.setdefault(current_user, {})
        buyer_inventory[listing.resource] = (
            int(buyer_inventory.get(listing.resource, 0)) + listing.quantity
        )
        listing.open = False
        event = (
            f"{current_user} bought {listing.quantity} {listing.resource} "
            f"from {listing.seller} for {listing.price}."
        )
        self._record(event)
        return event

    @app_action(selectable_name="CONSUME_RESOURCE", description="Consume inventory")
    def consume_resource(self, current_user: str, resource: str, quantity: int = 1) -> str:
        """Consume a resource from the current user's inventory."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        quantity = int(quantity)
        if quantity <= 0:
            return "Quantity must be positive."
        available = self._inventory_count(current_user, resource)
        if available < quantity:
            return f"{current_user} does not have {quantity} {resource} to consume."
        self._inventory[current_user][resource] = available - quantity
        event = f"{current_user} consumed {quantity} {resource}."
        self._record(event)
        return event
