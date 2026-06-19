"""Reference virtual-space environment backend."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from silisocs.environments.backends.base import BackendApp, app_action


@dataclass
class RoomTask:
    """Durable task placed in a virtual room."""

    task_id: str
    room: str
    description: str
    required_effort: int
    progress: int = 0
    complete: bool = False
    completion_message: str = ""


@dataclass
class VirtualSpaceApp(BackendApp):
    """In-memory room environment where agents move and talk when co-located."""

    action_logger: Any = None
    app_description: str = "A virtual space where agents can move between rooms and talk."
    rooms: list[str] = field(default_factory=lambda: ["atrium", "garden", "workshop"])
    starting_room: str = "atrium"
    room_descriptions: dict[str, str] = field(default_factory=dict)
    connections: dict[str, list[str]] | None = None
    room_tasks: list[dict[str, Any]] = field(default_factory=list)
    recent_event_limit: int = 8

    def __post_init__(self) -> None:
        super().__init__()
        self.rooms = [str(room) for room in self.rooms]
        if not self.rooms:
            raise ValueError("VirtualSpaceApp requires at least one room.")
        if self.starting_room not in self.rooms:
            raise ValueError(f"starting_room must be one of rooms: {self.rooms}")
        self._locations: dict[str, str] = {}
        self._notes: dict[str, list[str]] = {}
        self._tasks: dict[str, RoomTask] = {}
        self._events: list[str] = []

    def name(self) -> str:
        return "virtual_space"

    def description(self) -> str:
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del kwargs
        self._locations = dict.fromkeys(agent_names, self.starting_room)
        self._notes = {room: [] for room in self.rooms}
        self._tasks = self._build_tasks()
        self._events = [
            f"Virtual space opened with {len(agent_names)} agents in {self.starting_room}."
        ]

    def get_state(self) -> dict[str, Any]:
        """Return serializable backend state for checkpoints."""
        return {
            "locations": dict(self._locations),
            "notes": {room: list(notes) for room, notes in self._notes.items()},
            "tasks": {task_id: dataclasses.asdict(task) for task_id, task in self._tasks.items()},
            "events": list(self._events),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore backend state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("VirtualSpaceApp checkpoint state must be a mapping.")
        self._locations = {
            str(name): str(room) for name, room in dict(state.get("locations", {})).items()
        }
        raw_notes = state.get("notes", {})
        if not isinstance(raw_notes, Mapping):
            raise TypeError("VirtualSpaceApp checkpoint notes must be a mapping.")
        self._notes = {
            str(room): [str(note) for note in notes]
            for room, notes in raw_notes.items()
            if isinstance(notes, list)
        }
        raw_tasks = state.get("tasks", {})
        if not isinstance(raw_tasks, Mapping):
            raise TypeError("VirtualSpaceApp checkpoint tasks must be a mapping.")
        self._tasks = {}
        for task_id, raw in raw_tasks.items():
            if not isinstance(raw, Mapping):
                raise TypeError("VirtualSpaceApp checkpoint task entries must be mappings.")
            task = RoomTask(
                task_id=str(raw.get("task_id") or task_id),
                room=str(raw.get("room") or ""),
                description=str(raw.get("description") or ""),
                required_effort=max(1, int(raw.get("required_effort", 1))),
                progress=max(0, int(raw.get("progress", 0))),
                complete=bool(raw.get("complete", False)),
                completion_message=str(raw.get("completion_message") or ""),
            )
            self._tasks[task.task_id] = task
        self._events = [str(event) for event in state.get("events", [])]

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
        notes = self._notes.get(location, [])
        note_text = "\n".join(f"  {note}" for note in notes) if notes else "none"
        tasks = [task for task in self._tasks.values() if task.room == location]
        task_text = "\n".join(f"  {self._format_task(task)}" for task in tasks) if tasks else "none"
        events = self._events[-limit:] if limit > 0 else []
        event_text = "\n".join(f"  {event}" for event in events) if events else "none"
        return (
            "VIRTUAL SPACE STATE\n"
            f"Actor: {actor_name}\n"
            f"Location: {location}\n"
            f"Room: {self.room_descriptions.get(location, 'No room description provided.')}\n"
            f"Present here: {', '.join(present) if present else 'none'}\n"
            f"Exits: {', '.join(exits) if exits else 'none'}\n"
            f"Room notes:\n{note_text}\n"
            f"Room tasks:\n{task_text}\n"
            f"Recent events:\n{event_text}"
        )

    def _record(self, event: str) -> None:
        self._events.append(event)
        self._emit_event_log(event, event_type="virtual_space")

    def _ensure_agent(self, agent_name: str) -> str | None:
        if agent_name not in self._locations:
            return f"Unknown virtual-space participant: {agent_name}"
        return None

    def _exits(self, room: str) -> list[str]:
        if self.connections is None:
            return sorted(other for other in self.rooms if other != room)
        return sorted(str(destination) for destination in self.connections.get(room, []))

    def _build_tasks(self) -> dict[str, RoomTask]:
        tasks: dict[str, RoomTask] = {}
        for raw in self.room_tasks:
            if not isinstance(raw, dict):
                raise TypeError("VirtualSpaceApp.room_tasks entries must be mappings.")
            task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
            if not task_id:
                raise ValueError("VirtualSpaceApp room tasks require task_id.")
            room = str(raw.get("room") or "").strip()
            if room not in self.rooms:
                raise ValueError(f"Task {task_id!r} references unknown room {room!r}.")
            if task_id in tasks:
                raise ValueError(f"Duplicate room task id: {task_id}")
            required_effort = max(1, int(raw.get("required_effort", 1)))
            tasks[task_id] = RoomTask(
                task_id=task_id,
                room=room,
                description=str(raw.get("description") or task_id),
                required_effort=required_effort,
                progress=max(0, int(raw.get("progress", 0))),
                complete=bool(raw.get("complete", False)),
                completion_message=str(raw.get("completion_message") or ""),
            )
            if tasks[task_id].progress >= tasks[task_id].required_effort:
                tasks[task_id].complete = True
        return tasks

    @staticmethod
    def _format_task(task: RoomTask) -> str:
        status = "complete" if task.complete else f"{task.progress}/{task.required_effort}"
        return f"{task.task_id}: {task.description} [{status}]"

    @app_action(selectable_name="LOOK", description="Inspect the current room")
    def look(self, agent_name: str) -> str:
        """Inspect the current room, nearby agents, exits, and recent events."""
        error = self._ensure_agent(agent_name)
        if error:
            return error
        return self.observe(agent_name)

    @app_action(selectable_name="MOVE", description="Move to another room")
    def move(self, agent_name: str, destination: str) -> str:
        """Move the current user to an adjacent destination room."""
        error = self._ensure_agent(agent_name)
        if error:
            return error
        destination = str(destination)
        current_room = self._locations[agent_name]
        if destination == current_room:
            return f"{agent_name} is already in {destination}."
        if destination not in self.rooms:
            return f"Unknown destination: {destination}. Available rooms: {', '.join(self.rooms)}"
        if destination not in self._exits(current_room):
            return f"{destination} is not reachable from {current_room}."

        self._locations[agent_name] = destination
        event = f"{agent_name} moved from {current_room} to {destination}."
        self._record(event)
        return event

    @app_action(selectable_name="LEAVE_NOTE", description="Leave a note in the current room")
    def leave_note(self, agent_name: str, message: str) -> str:
        """Leave a persistent note visible to later room occupants."""
        error = self._ensure_agent(agent_name)
        if error:
            return error
        message = str(message).strip()
        if not message:
            return "Message must not be empty."
        room = self._locations[agent_name]
        note = f"{agent_name}: {message}"
        self._notes.setdefault(room, []).append(note)
        event = f"{agent_name} left a note in {room}: {message}"
        self._record(event)
        return event

    @app_action(selectable_name="WORK_ON_TASK", description="Work on a room task")
    def work_on_task(self, agent_name: str, task_id: str, effort: int = 1) -> str:
        """Contribute effort to an incomplete task in the current room."""
        error = self._ensure_agent(agent_name)
        if error:
            return error
        task_id = str(task_id).strip()
        task = self._tasks.get(task_id)
        if task is None:
            return f"Unknown task: {task_id}."
        room = self._locations[agent_name]
        if task.room != room:
            return f"Task {task_id} is in {task.room}, not {room}."
        if task.complete:
            return f"Task {task_id} is already complete."
        effort = int(effort)
        if effort <= 0:
            return "Effort must be positive."
        task.progress = min(task.required_effort, task.progress + effort)
        if task.progress >= task.required_effort:
            task.complete = True
            message = task.completion_message or f"Task {task.task_id} is complete."
            event = f"{agent_name} completed task {task.task_id}: {message}"
        else:
            event = (
                f"{agent_name} worked on task {task.task_id} "
                f"({task.progress}/{task.required_effort})."
            )
        self._record(event)
        return event

    @app_action(selectable_name="TALK", description="Talk to another agent in the same room")
    def talk(self, agent_name: str, target_user: str, message: str) -> str:
        """Send a message to another agent who is present in the same room."""
        error = self._ensure_agent(agent_name)
        if error:
            return error
        target_error = self._ensure_agent(target_user)
        if target_error:
            return target_error
        if agent_name == target_user:
            return "Agents cannot talk to themselves."
        current_room = self._locations[agent_name]
        target_room = self._locations[target_user]
        if current_room != target_room:
            return f"{target_user} is not in the same room as {agent_name}."
        message = str(message).strip()
        if not message:
            return "Message must not be empty."

        event = f"{agent_name} told {target_user}: {message}"
        self._record(event)
        return event
