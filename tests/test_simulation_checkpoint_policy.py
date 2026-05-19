from __future__ import annotations

import json
import random
from types import SimpleNamespace

from silisocs.runtime.checkpointing import (
    CHECKPOINT_SCHEMA_VERSION,
    restore_rng_state_from_metadata,
    save_checkpoint,
    should_save_checkpoint,
)
from silisocs.runtime.construction.assembly import RuntimeObjects


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
