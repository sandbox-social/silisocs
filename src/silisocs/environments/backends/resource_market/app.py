"""Reference resource-market environment backend."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from silisocs.environments.backends.base import BackendApp, app_action


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
class ResourceMarketApp(BackendApp):
    """Small in-memory market where agents produce, list, buy, and consume resources."""

    action_logger: Any = None
    app_description: str = "A resource market environment."
    initial_cash: int = 20
    initial_inventory: dict[str, int] = field(default_factory=lambda: {"food": 1})
    resources: list[str] = field(default_factory=lambda: ["food", "wood", "ore"])
    production_capabilities: dict[str, Any] = field(default_factory=dict)
    role_needs: dict[str, dict[str, int]] = field(default_factory=dict)
    upkeep_interval: int = 0
    initial_satisfaction: int = 0
    fulfilled_need_reward: int = 1
    shortage_penalty: int = 1
    recent_event_limit: int = 8

    def __post_init__(self) -> None:
        super().__init__()
        self._cash: dict[str, int] = {}
        self._inventory: dict[str, dict[str, int]] = {}
        self._roles: dict[str, str] = {}
        self._satisfaction: dict[str, int] = {}
        self._listings: dict[int, Listing] = {}
        self._events: list[str] = []
        self._next_listing_id = 1
        self._upkeep_interval = max(0, int(self.upkeep_interval))

    def name(self) -> str:
        return "resource_market"

    def description(self) -> str:
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        sim_roles = dict(kwargs.get("sim_roles") or {})
        self._cash = {name: int(self.initial_cash) for name in agent_names}
        self._inventory = {
            name: {resource: int(quantity) for resource, quantity in self.initial_inventory.items()}
            for name in agent_names
        }
        self._roles = {name: str(sim_roles.get(name, "")) for name in agent_names}
        self._satisfaction = {name: int(self.initial_satisfaction) for name in agent_names}
        for name in agent_names:
            for resource in self.resources:
                self._inventory[name].setdefault(resource, 0)
        self._listings = {}
        self._events = [f"Market opened for {len(agent_names)} agents."]
        self._next_listing_id = 1

    def update(self, *, step: int, agent_names: Sequence[str], context: Any | None = None) -> None:
        del context
        if self._upkeep_interval <= 0 or step <= 0 or step % self._upkeep_interval:
            return
        for agent_name in agent_names:
            needs = self._needs_for_agent(agent_name)
            if not needs:
                continue
            missing = {
                resource: quantity
                for resource, quantity in needs.items()
                if self._inventory_count(agent_name, resource) < quantity
            }
            if missing:
                self._satisfaction[agent_name] = (
                    int(self._satisfaction.get(agent_name, 0)) - self.shortage_penalty
                )
                missing_text = ", ".join(
                    f"{quantity} {resource}" for resource, quantity in sorted(missing.items())
                )
                self._record(
                    f"{agent_name} could not meet upkeep needs at step {step}: {missing_text}."
                )
                continue

            for resource, quantity in needs.items():
                self._inventory[agent_name][resource] = (
                    self._inventory_count(agent_name, resource) - quantity
                )
            self._satisfaction[agent_name] = (
                int(self._satisfaction.get(agent_name, 0)) + self.fulfilled_need_reward
            )
            needs_text = ", ".join(
                f"{quantity} {resource}" for resource, quantity in sorted(needs.items())
            )
            self._record(f"{agent_name} met upkeep needs at step {step}: {needs_text}.")

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        limit = int(kwargs.get("limit", self.recent_event_limit) or self.recent_event_limit)
        inventory = self._inventory.get(actor_name, {})
        inventory_text = ", ".join(
            f"{resource}: {quantity}" for resource, quantity in sorted(inventory.items())
        )
        capabilities = self._production_capabilities_for_agent(actor_name)
        if capabilities is None:
            production_text = "any configured resource"
        elif capabilities:
            production_text = ", ".join(
                f"{resource} (max {quantity})"
                for resource, quantity in sorted(capabilities.items())
            )
        else:
            production_text = "none"
        needs = self._needs_for_agent(actor_name)
        needs_text = (
            ", ".join(f"{resource}: {quantity}" for resource, quantity in sorted(needs.items()))
            if needs
            else "none"
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
            f"Role: {self._roles.get(actor_name, '') or 'none'}\n"
            f"Cash: {self._cash.get(actor_name, 0)}\n"
            f"Satisfaction: {self._satisfaction.get(actor_name, 0)}\n"
            f"Inventory: {inventory_text or 'none'}\n"
            f"Production capabilities: {production_text}\n"
            f"Upkeep needs: {needs_text}\n"
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

    def _needs_for_agent(self, current_user: str) -> dict[str, int]:
        role = self._roles.get(current_user, "")
        raw = self.role_needs.get(current_user, self.role_needs.get(role, {}))
        if not isinstance(raw, dict):
            raise TypeError(f"role_needs for {role or current_user!r} must be a mapping.")
        return {
            str(resource): int(quantity) for resource, quantity in raw.items() if int(quantity) > 0
        }

    def _production_capabilities_for_agent(self, current_user: str) -> dict[str, int] | None:
        raw = self._capability_config(current_user)
        if raw is None:
            return None
        if isinstance(raw, list):
            return {str(resource): 1 for resource in raw}
        if isinstance(raw, dict):
            return {
                str(resource): max(1, int(quantity))
                for resource, quantity in raw.items()
                if bool(quantity)
            }
        raise TypeError(
            "production_capabilities values must be mappings of resource to max quantity "
            "or lists of resource names."
        )

    def _capability_config(self, current_user: str) -> Any:
        role = self._roles.get(current_user, "")
        if current_user in self.production_capabilities:
            return self.production_capabilities[current_user]
        if role in self.production_capabilities:
            return self.production_capabilities[role]
        if "default" in self.production_capabilities:
            return self.production_capabilities["default"]
        return None

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
        capabilities = self._production_capabilities_for_agent(current_user)
        if capabilities is not None:
            max_quantity = capabilities.get(resource)
            if max_quantity is None:
                allowed = ", ".join(sorted(capabilities)) or "none"
                return f"{current_user} cannot produce {resource}. Allowed resources: {allowed}."
            if quantity > max_quantity:
                return f"{current_user} can produce at most {max_quantity} {resource} per action."
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

    @app_action(selectable_name="CANCEL_LISTING", description="Cancel one of the user's listings")
    def cancel_listing(self, current_user: str, listing_id: int) -> str:
        """Cancel an open listing and return the resource to the seller."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        listing = self._listings.get(int(listing_id))
        if listing is None or not listing.open:
            return f"Listing {listing_id} is not open."
        if listing.seller != current_user:
            return f"{current_user} cannot cancel listing {listing_id}."
        inventory = self._inventory.setdefault(current_user, {})
        inventory[listing.resource] = int(inventory.get(listing.resource, 0)) + listing.quantity
        listing.open = False
        event = f"{current_user} cancelled listing {listing.listing_id}."
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

    @app_action(selectable_name="TRANSFER_RESOURCE", description="Give inventory to another agent")
    def transfer_resource(
        self,
        current_user: str,
        target_user: str,
        resource: str,
        quantity: int,
    ) -> str:
        """Transfer a resource quantity directly to another market participant."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        target_error = self._ensure_agent(target_user)
        if target_error:
            return target_error
        if current_user == target_user:
            return "Agents cannot transfer resources to themselves."
        quantity = int(quantity)
        if quantity <= 0:
            return "Quantity must be positive."
        available = self._inventory_count(current_user, resource)
        if available < quantity:
            return f"{current_user} does not have {quantity} {resource} to transfer."
        self._inventory[current_user][resource] = available - quantity
        target_inventory = self._inventory.setdefault(target_user, {})
        target_inventory[resource] = int(target_inventory.get(resource, 0)) + quantity
        event = f"{current_user} transferred {quantity} {resource} to {target_user}."
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
