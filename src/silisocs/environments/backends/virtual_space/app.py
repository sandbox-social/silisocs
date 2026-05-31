"""Reference virtual-space environment backend."""

from __future__ import annotations

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

    @app_action(selectable_name="LEAVE_NOTE", description="Leave a note in the current room")
    def leave_note(self, current_user: str, message: str) -> str:
        """Leave a persistent note visible to later room occupants."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        message = str(message).strip()
        if not message:
            return "Message must not be empty."
        room = self._locations[current_user]
        note = f"{current_user}: {message}"
        self._notes.setdefault(room, []).append(note)
        event = f"{current_user} left a note in {room}: {message}"
        self._record(event)
        return event

    @app_action(selectable_name="WORK_ON_TASK", description="Work on a room task")
    def work_on_task(self, current_user: str, task_id: str, effort: int = 1) -> str:
        """Contribute effort to an incomplete task in the current room."""
        error = self._ensure_agent(current_user)
        if error:
            return error
        task_id = str(task_id).strip()
        task = self._tasks.get(task_id)
        if task is None:
            return f"Unknown task: {task_id}."
        room = self._locations[current_user]
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
            event = f"{current_user} completed task {task.task_id}: {message}"
        else:
            event = (
                f"{current_user} worked on task {task.task_id} "
                f"({task.progress}/{task.required_effort})."
            )
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
