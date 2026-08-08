from __future__ import annotations

import json
import random
from types import SimpleNamespace

from silisocs.runtime.checkpointing import (
    CHECKPOINT_SCHEMA_VERSION,
    checkpoint_has_backend_state,
    make_checkpoint_data,
    restore_rng_state_from_metadata,
    save_checkpoint,
    should_save_checkpoint,
)
from silisocs.runtime.construction.assembly import RuntimeObjects
from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec


def _checkpoint_config(*, every_n_steps: int | None, explicit_steps: list[int]) -> SimpleNamespace:
    return SimpleNamespace(
        every_n_steps=every_n_steps,
        explicit_steps=explicit_steps,
    )


def test_checkpoint_policy_disabled_by_default() -> None:
    assert (
        should_save_checkpoint(
            3,
            _checkpoint_config(every_n_steps=None, explicit_steps=[]),
        )
        is False
    )


def test_checkpoint_policy_supports_every_n_and_explicit_steps() -> None:
    checkpoint_cfg = _checkpoint_config(every_n_steps=5, explicit_steps=[7, 11])

    assert should_save_checkpoint(5, checkpoint_cfg) is True
    assert should_save_checkpoint(7, checkpoint_cfg) is True
    assert should_save_checkpoint(10, checkpoint_cfg) is True
    assert should_save_checkpoint(8, checkpoint_cfg) is False


def test_save_checkpoint_writes_step_metadata(tmp_path) -> None:
    runtime = RuntimeObjects()

    save_checkpoint(runtime, step=12, checkpoint_path=str(tmp_path))

    checkpoint_file = tmp_path / "step_12_checkpoint.json"
    payload = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert payload["step"] == 12
    assert "runtime_metadata" in payload
    assert "rng_state_b64" in payload["runtime_metadata"]


def test_checkpoint_detects_game_master_backend_state() -> None:
    class _GameMaster:
        name = "gm"

        def get_state(self) -> dict:
            return {"backend": {"backend_type": "test", "state": {"value": 1}}}

    runtime = RuntimeObjects(game_masters=[_GameMaster()])
    runtime.object_specs["gm"] = RuntimeSpec(
        class_path="tests.fake.GM",
        role=RuntimeRole.GAME_MASTER,
        params={"name": "gm"},
    )

    checkpoint = make_checkpoint_data(runtime, step=1)

    assert checkpoint_has_backend_state(checkpoint) is True


def test_checkpoint_runtime_metadata_records_every_game_master() -> None:
    runtime = RuntimeObjects()
    runtime.game_masters = [
        SimpleNamespace(
            name="later_gm",
            backend_type="reddit_like",
            backend=SimpleNamespace(
                action_logger=SimpleNamespace(output_filename="/tmp/run/action_events.jsonl")
            ),
        ),
        SimpleNamespace(
            name="first_gm",
            backend_type="twitter_like",
            backend=SimpleNamespace(
                action_logger=SimpleNamespace(output_filename="/tmp/run/action_events.jsonl")
            ),
        ),
    ]
    runtime.object_specs["later_gm"] = RuntimeSpec(
        class_path="tests.fake.GM",
        role=RuntimeRole.GAME_MASTER,
        params={"name": "later_gm", "sequence": 1},
    )
    runtime.object_specs["first_gm"] = RuntimeSpec(
        class_path="tests.fake.GM",
        role=RuntimeRole.GAME_MASTER,
        params={"name": "first_gm", "sequence": 0},
    )

    checkpoint = make_checkpoint_data(runtime, step=1)

    assert checkpoint["runtime_metadata"]["game_masters"] == [
        {
            "name": "first_gm",
            "backend_type": "twitter_like",
            "sequence": 0,
            "action_events_file": "/tmp/run/action_events.jsonl",
            "output_dir": "/tmp/run",
        },
        {
            "name": "later_gm",
            "backend_type": "reddit_like",
            "sequence": 1,
            "action_events_file": "/tmp/run/action_events.jsonl",
            "output_dir": "/tmp/run",
        },
    ]


def test_restore_rng_state_from_metadata_restores_python_random() -> None:
    random.seed(1234)
    baseline = random.random()
    random.seed(1234)
    expected_first = random.random()
    assert baseline == expected_first

    random.seed(1234)
    checkpoint_state = random.getstate()
    random.random()
    random.random()
    import base64
    import pickle

    payload = {"rng_state_b64": base64.b64encode(pickle.dumps(checkpoint_state)).decode("ascii")}
    restore_rng_state_from_metadata(payload)

    assert random.random() == expected_first


def test_invalid_checkpoint_leaves_runtime_untouched() -> None:
    """Restore must validate every object before mutating any runtime state."""
    from silisocs.agents.fixed import FixedAgent
    from silisocs.runtime.checkpointing.policy import CHECKPOINT_SCHEMA_VERSION
    from silisocs.runtime.checkpointing.state import load_checkpoint_into_runtime
    from silisocs.runtime.construction.assembly import RuntimeObjects
    from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec
    from silisocs.runtime.language_models.base import NoLanguageModel

    model = NoLanguageModel()
    agent = FixedAgent(model=model, name="Alice", actions=[])
    runtime = RuntimeObjects(
        agents=[agent],
        game_masters=[],
        object_specs={
            "Alice": RuntimeSpec(
                class_path="silisocs.agents.fixed.FixedAgent",
                role=RuntimeRole.AGENT,
                compat=None,
                params={"name": "Alice"},
            )
        },
    )
    state_before = agent.get_state()

    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_counter": 7,
        "objects": {
            "Alice": {
                "class_path": "silisocs.agents.fixed.FixedAgent",
                "role": "agent",
                "compat": None,
                "params": {"name": "Alice"},
                "state": {"current_episode": 3},
            },
            "Ghost": {
                "class_path": "does.not.exist.GhostAgent",
                "role": "agent",
                "compat": None,
                "params": {"name": "Ghost"},
                "state": {},
            },
        },
    }

    try:
        load_checkpoint_into_runtime(
            runtime,
            checkpoint,
            models={"m": model},
            object_to_model={"Alice": "m"},
        )
    except ValueError as exc:
        assert "Ghost" in str(exc)
    else:
        raise AssertionError("Expected restore to fail validation for Ghost")

    # Alice's state must NOT have been applied: validation precedes mutation.
    assert agent.get_state() == state_before
    assert runtime.checkpoint_counter == 0
    assert len(runtime.agents) == 1
