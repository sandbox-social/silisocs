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
            "current_user": "Alice",
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
    app.move(current_user="Casey", destination="garden")

    talk_result = app.invoke_action_with_kwargs(
        "TALK",
        {
            "current_user": "Alice",
            "target_user": "Bob",
            "message": "Want to explore the garden?",
        },
    )
    blocked_result = app.invoke_action_with_kwargs(
        "TALK",
        {
            "current_user": "Alice",
            "target_user": "Casey",
            "message": "Can you hear me?",
        },
    )

    assert "Alice told Bob: Want to explore the garden?" in talk_result
    assert "not in the same room" in blocked_result
    assert "Alice told Bob: Want to explore the garden?" in app.observe("Bob")
    assert "Can you hear me?" not in app.observe("Casey")


def test_virtual_space_factory_and_finished_action() -> None:
    app = create_backend_app(
        "virtual_space",
        params={"rooms": ["atrium", "lab"], "starting_room": "lab"},
    )
    app.initialize(agent_names=["Alice"])

    assert isinstance(app, VirtualSpaceApp)
    assert "Location: lab" in app.invoke_action_with_kwargs("LOOK", {"current_user": "Alice"})
    assert app.invoke_action_with_kwargs("FINISHED", {}) == "Finished action episode"
