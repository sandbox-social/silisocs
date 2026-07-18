"""Reference resource-market environment backend."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from silisocs.environments.backends.base import ActionResult, BackendApp, app_action


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
    # Authoritative checkpoint state: full in-memory market state round-trips.
    provides_checkpoint_state = True
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
                    f"{agent_name} could not meet upkeep needs at step {step}: {missing_text}.",
                )
                self._log_action_event(
                    agent_name,
                    "upkeep_shortfall",
                    {
                        "message": self._events[-1],
                        "needs": dict(sorted(missing.items())),
                    },
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
            self._record(
                f"{agent_name} met upkeep needs at step {step}: {needs_text}.",
            )
            self._log_action_event(
                agent_name,
                "upkeep_met",
                {"message": self._events[-1], "needs": dict(sorted(needs.items()))},
            )

    def get_state(self) -> dict[str, Any]:
        """Return serializable backend state for checkpoints."""
        return {
            "cash": dict(self._cash),
            "inventory": {name: dict(items) for name, items in self._inventory.items()},
            "roles": dict(self._roles),
            "satisfaction": dict(self._satisfaction),
            "listings": {
                str(listing_id): dataclasses.asdict(listing)
                for listing_id, listing in self._listings.items()
            },
            "events": list(self._events),
            "next_listing_id": self._next_listing_id,
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore backend state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("ResourceMarketApp checkpoint state must be a mapping.")
        self._cash = {str(name): int(value) for name, value in dict(state.get("cash", {})).items()}
        self._inventory = {
            str(name): {str(resource): int(quantity) for resource, quantity in items.items()}
            for name, items in dict(state.get("inventory", {})).items()
            if isinstance(items, Mapping)
        }
        self._roles = {str(name): str(role) for name, role in dict(state.get("roles", {})).items()}
        self._satisfaction = {
            str(name): int(value) for name, value in dict(state.get("satisfaction", {})).items()
        }
        raw_listings = state.get("listings", {})
        if not isinstance(raw_listings, Mapping):
            raise TypeError("ResourceMarketApp checkpoint listings must be a mapping.")
        self._listings = {}
        for listing_id, raw in raw_listings.items():
            if not isinstance(raw, Mapping):
                raise TypeError("ResourceMarketApp checkpoint listing entries must be mappings.")
            listing = Listing(
                listing_id=int(raw.get("listing_id", listing_id)),
                seller=str(raw.get("seller") or ""),
                resource=str(raw.get("resource") or ""),
                quantity=int(raw.get("quantity", 0)),
                price=int(raw.get("price", 0)),
                open=bool(raw.get("open", True)),
            )
            self._listings[listing.listing_id] = listing
        self._events = [str(event) for event in state.get("events", [])]
        self._next_listing_id = int(state.get("next_listing_id", 1))
        self._restore_committed_events(state.get("committed_events"))

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
        """Add an event to the market feed."""
        self._events.append(event)

    def _ensure_agent(self, agent_name: str) -> str | None:
        if agent_name not in self._cash:
            return f"Unknown market participant: {agent_name}"
        return None

    def _inventory_count(self, agent_name: str, resource: str) -> int:
        return int(self._inventory.setdefault(agent_name, {}).get(resource, 0))

    def _needs_for_agent(self, agent_name: str) -> dict[str, int]:
        role = self._roles.get(agent_name, "")
        raw = self.role_needs.get(agent_name, self.role_needs.get(role, {}))
        if not isinstance(raw, dict):
            raise TypeError(f"role_needs for {role or agent_name!r} must be a mapping.")
        return {
            str(resource): int(quantity) for resource, quantity in raw.items() if int(quantity) > 0
        }

    def _production_capabilities_for_agent(self, agent_name: str) -> dict[str, int] | None:
        raw = self._capability_config(agent_name)
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

    def _capability_config(self, agent_name: str) -> Any:
        role = self._roles.get(agent_name, "")
        if agent_name in self.production_capabilities:
            return self.production_capabilities[agent_name]
        if role in self.production_capabilities:
            return self.production_capabilities[role]
        if "default" in self.production_capabilities:
            return self.production_capabilities["default"]
        return None

    @app_action(
        selectable_name="INSPECT_MARKET",
        description="Inspect current market state",
        tags=("market.inspect",),
    )
    def inspect_market(self, agent_name: str) -> ActionResult:
        """Inspect current cash, inventory, listings, and recent events."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        return ActionResult(self.observe(agent_name), data={})

    @app_action(
        selectable_name="PRODUCE_RESOURCE",
        description="Produce a resource",
        tags=("market.stock",),
        fields={"market.resource": "resource", "market.quantity": "quantity"},
    )
    def produce_resource(self, agent_name: str, resource: str, quantity: int = 1) -> ActionResult:
        """Add newly produced resource units to the current user's inventory."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        quantity = int(quantity)
        if quantity <= 0:
            return ActionResult("Quantity must be positive.", committed=False)
        capabilities = self._production_capabilities_for_agent(agent_name)
        if capabilities is not None:
            max_quantity = capabilities.get(resource)
            if max_quantity is None:
                allowed = ", ".join(sorted(capabilities)) or "none"
                return ActionResult(
                    f"{agent_name} cannot produce {resource}. Allowed resources: {allowed}.",
                    committed=False,
                )
            if quantity > max_quantity:
                return ActionResult(
                    f"{agent_name} can produce at most {max_quantity} {resource} per action.",
                    committed=False,
                )
        inventory = self._inventory.setdefault(agent_name, {})
        inventory[resource] = int(inventory.get(resource, 0)) + quantity
        event = f"{agent_name} produced {quantity} {resource}."
        self._record(event)
        return ActionResult(
            event,
            data={"resource": resource, "quantity": quantity},
        )

    @app_action(
        selectable_name="LIST_RESOURCE",
        description="List a resource for sale",
        tags=("market.listing",),
        fields={
            "market.resource": "resource",
            "market.quantity": "quantity",
            "market.price": "price",
        },
    )
    def list_resource(
        self, agent_name: str, resource: str, quantity: int, price: int
    ) -> ActionResult:
        """Move a resource quantity into an open listing for sale."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        quantity = int(quantity)
        price = int(price)
        if quantity <= 0:
            return ActionResult("Quantity must be positive.", committed=False)
        if price <= 0:
            return ActionResult("Price must be positive.", committed=False)
        available = self._inventory_count(agent_name, resource)
        if available < quantity:
            return ActionResult(
                f"{agent_name} does not have {quantity} {resource} to list.",
                committed=False,
            )

        self._inventory[agent_name][resource] = available - quantity
        listing = Listing(
            listing_id=self._next_listing_id,
            seller=agent_name,
            resource=resource,
            quantity=quantity,
            price=price,
        )
        self._listings[listing.listing_id] = listing
        self._next_listing_id += 1
        event = (
            f"Listing {listing.listing_id} created: {agent_name} sells "
            f"{quantity} {resource} for {price}."
        )
        self._record(event)
        return ActionResult(
            event,
            data={
                "listing_id": listing.listing_id,
                "resource": resource,
                "quantity": quantity,
                "price": price,
            },
        )

    @app_action(
        selectable_name="CANCEL_LISTING",
        description="Cancel one of the user's listings",
        tags=("market.listing",),
        fields={"market.resource": "resource", "market.quantity": "quantity"},
    )
    def cancel_listing(self, agent_name: str, listing_id: int) -> ActionResult:
        """Cancel an open listing and return the resource to the seller."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        listing = self._listings.get(int(listing_id))
        if listing is None or not listing.open:
            return ActionResult(f"Listing {listing_id} is not open.", committed=False)
        if listing.seller != agent_name:
            return ActionResult(
                f"{agent_name} cannot cancel listing {listing_id}.", committed=False
            )
        inventory = self._inventory.setdefault(agent_name, {})
        inventory[listing.resource] = int(inventory.get(listing.resource, 0)) + listing.quantity
        listing.open = False
        event = f"{agent_name} cancelled listing {listing.listing_id}."
        self._record(event)
        return ActionResult(
            event,
            data={
                "listing_id": listing.listing_id,
                "resource": listing.resource,
                "quantity": listing.quantity,
            },
        )

    @app_action(
        selectable_name="BUY_LISTING",
        description="Buy an open market listing",
        tags=("market.trade",),
        fields={
            "market.resource": "resource",
            "market.quantity": "quantity",
            "market.price": "price",
            "market.counterparty": "target_user",
        },
    )
    def buy_listing(self, agent_name: str, listing_id: int) -> ActionResult:
        """Buy an open listing by ID."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        listing_id = int(listing_id)
        listing = self._listings.get(listing_id)
        if listing is None or not listing.open:
            return ActionResult(f"Listing {listing_id} is not open.", committed=False)
        if listing.seller == agent_name:
            return ActionResult("Agents cannot buy their own listing.", committed=False)
        if self._cash[agent_name] < listing.price:
            return ActionResult(
                f"{agent_name} does not have enough cash to buy listing {listing_id}.",
                committed=False,
            )

        self._cash[agent_name] -= listing.price
        self._cash[listing.seller] += listing.price
        buyer_inventory = self._inventory.setdefault(agent_name, {})
        buyer_inventory[listing.resource] = (
            int(buyer_inventory.get(listing.resource, 0)) + listing.quantity
        )
        listing.open = False
        event = (
            f"{agent_name} bought {listing.quantity} {listing.resource} "
            f"from {listing.seller} for {listing.price}."
        )
        self._record(event)
        return ActionResult(
            event,
            data={
                "listing_id": listing_id,
                "resource": listing.resource,
                "quantity": listing.quantity,
                "price": listing.price,
                "target_user": listing.seller,
            },
        )

    @app_action(
        selectable_name="TRANSFER_RESOURCE",
        description="Give inventory to another agent",
        tags=("market.trade",),
        fields={
            "market.resource": "resource",
            "market.quantity": "quantity",
            "market.counterparty": "target_user",
        },
    )
    def transfer_resource(
        self,
        agent_name: str,
        target_user: str,
        resource: str,
        quantity: int,
    ) -> ActionResult:
        """Transfer a resource quantity directly to another market participant."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        target_error = self._ensure_agent(target_user)
        if target_error:
            return ActionResult(target_error, committed=False)
        if agent_name == target_user:
            return ActionResult("Agents cannot transfer resources to themselves.", committed=False)
        quantity = int(quantity)
        if quantity <= 0:
            return ActionResult("Quantity must be positive.", committed=False)
        available = self._inventory_count(agent_name, resource)
        if available < quantity:
            return ActionResult(
                f"{agent_name} does not have {quantity} {resource} to transfer.",
                committed=False,
            )
        self._inventory[agent_name][resource] = available - quantity
        target_inventory = self._inventory.setdefault(target_user, {})
        target_inventory[resource] = int(target_inventory.get(resource, 0)) + quantity
        event = f"{agent_name} transferred {quantity} {resource} to {target_user}."
        self._record(event)
        return ActionResult(
            event,
            data={"resource": resource, "quantity": quantity, "target_user": target_user},
        )

    @app_action(
        selectable_name="CONSUME_RESOURCE",
        description="Consume inventory",
        tags=("market.stock",),
        fields={"market.resource": "resource", "market.quantity": "quantity"},
    )
    def consume_resource(self, agent_name: str, resource: str, quantity: int = 1) -> ActionResult:
        """Consume a resource from the current user's inventory."""
        error = self._ensure_agent(agent_name)
        if error:
            return ActionResult(error, committed=False)
        quantity = int(quantity)
        if quantity <= 0:
            return ActionResult("Quantity must be positive.", committed=False)
        available = self._inventory_count(agent_name, resource)
        if available < quantity:
            return ActionResult(
                f"{agent_name} does not have {quantity} {resource} to consume.",
                committed=False,
            )
        self._inventory[agent_name][resource] = available - quantity
        event = f"{agent_name} consumed {quantity} {resource}."
        self._record(event)
        return ActionResult(
            event,
            data={"resource": resource, "quantity": quantity},
        )
