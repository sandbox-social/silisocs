from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from silisocs.runtime.config import ConfigStore
from silisocs.runtime.simulation import Simulation


def _set_checkpoint_config(*, every_n_steps: int | None, explicit_steps: list[int]) -> None:
    ConfigStore.set_config(
        SimpleNamespace(
            sim=SimpleNamespace(
                checkpoint=SimpleNamespace(
                    every_n_steps=every_n_steps,
                    explicit_steps=explicit_steps,
                )
            )
        )
    )


def test_checkpoint_policy_disabled_by_default() -> None:
    sim = Simulation.__new__(Simulation)
    _set_checkpoint_config(every_n_steps=None, explicit_steps=[])

    assert sim._should_save_checkpoint(3) is False


def test_checkpoint_policy_supports_every_n_and_explicit_steps() -> None:
    sim = Simulation.__new__(Simulation)
    _set_checkpoint_config(every_n_steps=5, explicit_steps=[7, 11])

    assert sim._should_save_checkpoint(5) is True
    assert sim._should_save_checkpoint(7) is True
    assert sim._should_save_checkpoint(10) is True
    assert sim._should_save_checkpoint(8) is False


def test_save_checkpoint_writes_step_metadata(tmp_path) -> None:
    sim = Simulation.__new__(Simulation)
    sim._get_state_callback = None
    sim_any = cast(Any, sim)
    sim_any.make_checkpoint_data = lambda: {
        "entities": {},
        "game_masters": {},
        "raw_log": [],
        "checkpoint_counter": 0,
    }

    sim.save_checkpoint(step=12, checkpoint_path=str(tmp_path))

    checkpoint_file = tmp_path / "step_12_checkpoint.json"
    payload = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert payload["step"] == 12
