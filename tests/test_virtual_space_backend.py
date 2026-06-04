from __future__ import annotations

from silisocs.environments.backends.factory import create_backend_app
from silisocs.environments.backends.virtual_space.app import VirtualSpaceApp


def test_virtual_space_initializes_agents_in_starting_room() -> None:
    app = VirtualSpaceApp(
        rooms=["atrium", "garden"],
        starting_room="atrium",
        room_descriptions={"atrium": "A bright central hall."},
    )

    app.initialize(agent_names=["Alice", "Bob"])

    observation = app.observe("Alice")
    assert "Location: atrium" in observation
    assert "A bright central hall." in observation
    assert "Present here: Bob" in observation
    assert "Exits: garden" in observation


def test_virtual_space_move_updates_location_and_observations() -> None:
    app = VirtualSpaceApp(rooms=["atrium", "garden"], starting_room="atrium")
    app.initialize(agent_names=["Alice", "Bob"])

    result = app.invoke_action_with_kwargs(
        "MOVE",
        {
            "agent_name": "Alice",
            "destination": "garden",
        },
    )

    assert "Alice moved from atrium to garden" in result
    assert "Location: garden" in app.observe("Alice")
    assert "Present here: none" in app.observe("Alice")
    assert "Present here: none" in app.observe("Bob")


def test_virtual_space_talk_requires_same_room_and_records_message() -> None:
    app = VirtualSpaceApp(rooms=["atrium", "garden"], starting_room="atrium")
    app.initialize(agent_names=["Alice", "Bob", "Casey"])
    app.move(agent_name="Casey", destination="garden")

    talk_result = app.invoke_action_with_kwargs(
        "TALK",
        {
            "agent_name": "Alice",
            "target_user": "Bob",
            "message": "Want to explore the garden?",
        },
    )
    blocked_result = app.invoke_action_with_kwargs(
        "TALK",
        {
            "agent_name": "Alice",
            "target_user": "Casey",
            "message": "Can you hear me?",
        },
    )

    assert "Alice told Bob: Want to explore the garden?" in talk_result
    assert "not in the same room" in blocked_result
    assert "Alice told Bob: Want to explore the garden?" in app.observe("Bob")
    assert "Can you hear me?" not in app.observe("Casey")


def test_virtual_space_notes_and_room_tasks_change_observations() -> None:
    app = VirtualSpaceApp(
        rooms=["atrium", "garden"],
        starting_room="atrium",
        room_tasks=[
            {
                "task_id": "welcome_board",
                "room": "atrium",
                "description": "Prepare a welcome board.",
                "required_effort": 2,
                "completion_message": "The board is ready.",
            }
        ],
    )
    app.initialize(agent_names=["Alice", "Bob"])

    note_result = app.invoke_action_with_kwargs(
        "LEAVE_NOTE",
        {
            "agent_name": "Alice",
            "message": "Meet by the welcome board.",
        },
    )
    partial_result = app.invoke_action_with_kwargs(
        "WORK_ON_TASK",
        {
            "agent_name": "Alice",
            "task_id": "welcome_board",
            "effort": 1,
        },
    )
    complete_result = app.invoke_action_with_kwargs(
        "WORK_ON_TASK",
        {
            "agent_name": "Bob",
            "task_id": "welcome_board",
            "effort": 1,
        },
    )

    observation = app.observe("Bob")
    assert "Alice left a note" in note_result
    assert "welcome_board (1/2)" in partial_result
    assert "completed task welcome_board" in complete_result
    assert "Alice: Meet by the welcome board." in observation
    assert "welcome_board: Prepare a welcome board. [complete]" in observation
    assert "The board is ready" in observation


def test_virtual_space_factory_and_finished_action() -> None:
    app = create_backend_app(
        "virtual_space",
        params={"rooms": ["atrium", "lab"], "starting_room": "lab"},
    )
    app.initialize(agent_names=["Alice"])

    assert isinstance(app, VirtualSpaceApp)
    assert "Location: lab" in app.invoke_action_with_kwargs("LOOK", {"agent_name": "Alice"})
    assert app.invoke_action_with_kwargs("FINISHED", {}) == "Finished action episode"


def test_virtual_space_checkpoint_state_restores_world() -> None:
    app = VirtualSpaceApp(rooms=["atrium", "garden"], starting_room="atrium")
    app.initialize(agent_names=["Alice", "Bob"])
    app.move(agent_name="Alice", destination="garden")
    app.leave_note(agent_name="Alice", message="Meet here.")

    restored = VirtualSpaceApp(rooms=["atrium", "garden"], starting_room="atrium")
    restored.set_state(app.get_state())

    alice_observation = restored.observe("Alice")
    bob_observation = restored.observe("Bob")
    assert "Location: garden" in alice_observation
    assert "Alice: Meet here." in alice_observation
    assert "Present here: none" in bob_observation
