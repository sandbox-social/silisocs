"""Public checkpointing helpers used by the native runner."""

from silisocs.runtime.checkpointing.policy import (
    CHECKPOINT_SCHEMA_VERSION,
    should_save_checkpoint,
)
from silisocs.runtime.checkpointing.replay_mappers import (
    get_replay_mapper,
    microblog_event_to_replay_action,
    register_replay_mapper,
)
from silisocs.runtime.checkpointing.restore import (
    CheckpointRestoreStrategy,
    SocialActionEventReplayRestore,
    build_checkpoint_restore,
    build_per_gm_checkpoint_restores,
    run_checkpoint_restores,
)
from silisocs.runtime.checkpointing.serialization import json_safe
from silisocs.runtime.checkpointing.state import (
    checkpoint_authoritative_gm_names,
    checkpoint_has_backend_state,
    checkpoint_runtime_metadata,
    has_checkpoints,
    load_checkpoint_file,
    load_checkpoint_into_runtime,
    make_checkpoint_data,
    resolve_checkpoint_source,
    restore_rng_state_from_metadata,
    save_checkpoint,
    select_resume_source,
)

__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointRestoreStrategy",
    "SocialActionEventReplayRestore",
    "build_checkpoint_restore",
    "build_per_gm_checkpoint_restores",
    "checkpoint_authoritative_gm_names",
    "checkpoint_has_backend_state",
    "checkpoint_runtime_metadata",
    "get_replay_mapper",
    "has_checkpoints",
    "json_safe",
    "load_checkpoint_file",
    "load_checkpoint_into_runtime",
    "make_checkpoint_data",
    "microblog_event_to_replay_action",
    "register_replay_mapper",
    "resolve_checkpoint_source",
    "restore_rng_state_from_metadata",
    "run_checkpoint_restores",
    "save_checkpoint",
    "select_resume_source",
    "should_save_checkpoint",
]
