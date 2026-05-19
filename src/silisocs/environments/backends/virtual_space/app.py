"""Minimal non-social virtual-space environment backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from silisocs.environments.backends.base import BackendApp, app_action


@dataclass
class VirtualSpaceApp(BackendApp):
    """In-memory room environment where agents move and talk when co-located."""

    action_logger: Any = None
    app_description: str = "A virtual space where agents can move between rooms and talk."
    rooms: list[str] = field(default_factory=lambda: ["atrium", "garden", "workshop"])
    starting_room: str = "atrium"
    room_descriptions: dict[str, str] = field(default_factory=dict)
    connections: dict[str, list[str]] | None = None
    recent_event_limit: int = 8

    def __post_init__(self) -> None:
        super().__init__()
        self.rooms = [str(room) for room in self.rooms]
        if not self.rooms:
            raise ValueError("VirtualSpaceApp requires at least one room.")
        if self.starting_room not in self.rooms:
            raise ValueError(f"starting_room must be one of rooms: {self.rooms}")
        self._locations: dict[str, str] = {}
        self._events: list[str] = []

    def name(self) -> str:
        return "virtual_space"

    def description(self) -> str:
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del kwargs
        self._locations = dict.fromkeys(agent_names, self.starting_room)
        self._events = [
            f"Virtual space opened with {len(agent_names)} agents in {self.starting_room}."
        ]

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        limit = int(kwargs.get("limit", self.recent_event_limit) or self.recent_event_limit)
        location = self._locations.get(actor_name)
        if location is None:
            return f"Unknown virtual-space participant: {actor_name}"

        present = sorted(
            name
            for name, room in self._locations.items()
            if room == location and name != actor_name
        )
        exits = self._exits(location)
        events = self._events[-limit:] if limit > 0 else []
        event_text = "\n".join(f"  {event}" for event in events) if events else "none"
        return (
            "VIRTUAL SPACE STATE\n"
            f"Actor: {actor_name}\n"
            f"Location: {location}\n"
            f"Room: {self.room_descriptions.get(location, 'No room description provided.')}\n"
            f"Present here: {', '.join(present) if present else 'none'}\n"
            f"Exits: {', '.join(exits) if exits else 'none'}\n"
            f"Recent events:\n{event_text}"
        )

    def _record(self, event: str) -> None:
        self._events.append(event)
        log_fn = getattr(self.action_logger, "log", None)
        if callable(log_fn):
            log_fn({"event_type": "virtual_space", "message": event})

    def _ensure_agent(self, current_user: str) -> str | None:
        if current_user not in self._locations:
            return f"Unknown virtual-space participant: {current_user}"
        return None

    def _exits(self, room: str) -> list[str]:
        if self.connections is None:
            return sorted(other for other in self.rooms if other != room)
        return sorted(str(destination) for destination in self.connections.get(room, []))

    @app_action(selectable_name="LOOK", description="Inspect the current room")
    def look(self, current_user: str) -> str:
        """Inspect the current room, nearby agents, exits, and recent events."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        return self.observe(current_user)

    @app_action(selectable_name="MOVE", description="Move to another room")
    def move(self, current_user: str, destination: str) -> str:
        """Move the current user to an adjacent destination room."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        destination = str(destination)
        current_room = self._locations[current_user]
        if destination == current_room:
            return f"{current_user} is already in {destination}."
        if destination not in self.rooms:
            return f"Unknown destination: {destination}. Available rooms: {', '.join(self.rooms)}"
        if destination not in self._exits(current_room):
            return f"{destination} is not reachable from {current_room}."

        self._locations[current_user] = destination
        event = f"{current_user} moved from {current_room} to {destination}."
        self._record(event)
        return event

    @app_action(selectable_name="TALK", description="Talk to another agent in the same room")
    def talk(self, current_user: str, target_user: str, message: str) -> str:
        """Send a message to another agent who is present in the same room."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        target_error = self._ensure_agent(target_user)
        if target_error:
            return target_error
        if current_user == target_user:
            return "Agents cannot talk to themselves."
        current_room = self._locations[current_user]
        target_room = self._locations[target_user]
        if current_room != target_room:
            return f"{target_user} is not in the same room as {current_user}."
        message = str(message).strip()
        if not message:
            return "Message must not be empty."

        event = f"{current_user} told {target_user}: {message}"
        self._record(event)
        return event
