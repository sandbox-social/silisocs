"""Checkpoint save strategies — the save-side twin of the restore slot.

``sim.checkpoint.save`` selects how a checkpoint payload is laid out on disk
(the payload itself always comes from ``make_checkpoint_data``):

- ``monolithic_json`` (default): one JSON file per step, byte-compatible with
  every checkpoint written before the slot existed.
- ``sharded``: a manifest JSON plus NDJSON object shards, with large embedded
  SQLite snapshots decoded into sidecar ``.db`` files referenced by path+hash.
  Objects stream out one line at a time, restore can read shards
  incrementally, and the DB lands on disk as a raw file instead of a ~1.33x
  base64 string inside JSON. (The base64 blob still exists in memory while the
  payload is built — eliminating that is the Phase 2 zero-copy snapshot work.)

``load_checkpoint_file`` (state.py) reassembles a sharded checkpoint back into
the monolithic payload shape, so restore code never sees the difference.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib
import itertools
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

# Marker replacing a value extracted to a sidecar file in the sharded layout.
FILE_REF_KEY = "__checkpoint_file_ref__"

# Object-state keys whose (large, base64-encoded) values are extracted to
# sidecar files by the sharded strategy. Today: the SQLite backend snapshot.
_SIDECAR_B64_KEYS = frozenset({"db_snapshot_b64"})


class CheckpointSaveStrategy(ABC):
    """Writes one checkpoint payload to disk."""

    @abstractmethod
    def save(self, checkpoint_data: dict[str, Any], *, step: int, checkpoint_path: str) -> str:
        """Persist ``checkpoint_data`` for ``step``; return the checkpoint file path.

        The returned path must match the ``step_{N}_checkpoint.json`` naming
        contract — resume discovery globs that pattern.
        """


class MonolithicJsonSaveStrategy(CheckpointSaveStrategy):
    """One JSON file holding the whole payload (the default, unchanged format)."""

    def save(self, checkpoint_data: dict[str, Any], *, step: int, checkpoint_path: str) -> str:
        os.makedirs(checkpoint_path, exist_ok=True)
        checkpoint_file = os.path.join(checkpoint_path, f"step_{step}_checkpoint.json")
        # Object params/state are already json_safe'd in make_checkpoint_data.
        # No indent: pretty-printing roughly doubles the bytes written per
        # checkpoint, and checkpoints are machine-read.
        _write_json_atomic(checkpoint_data, checkpoint_file)
        return checkpoint_file


class ShardedCheckpointSaveStrategy(CheckpointSaveStrategy):
    """Manifest + NDJSON object shards + DB snapshot sidecar files.

    The manifest (named ``step_{N}_checkpoint.json`` so resume discovery finds
    it unchanged) carries everything except ``objects`` plus the shard file
    list. Objects are written one JSON line at a time into
    ``step_{N}_objects_{i}.jsonl`` shards, and any object-state value under a
    key in ``_SIDECAR_B64_KEYS`` is decoded from base64 into a raw sidecar file
    next to the manifest, leaving a ``{FILE_REF_KEY, sha256, encoding}`` marker
    in its place.
    """

    def __init__(self, objects_per_shard: int = 500) -> None:
        self.objects_per_shard = max(1, int(objects_per_shard))

    def save(self, checkpoint_data: dict[str, Any], *, step: int, checkpoint_path: str) -> str:
        os.makedirs(checkpoint_path, exist_ok=True)
        objects = checkpoint_data.get("objects", {})
        if not isinstance(objects, Mapping):
            raise ValueError("Checkpoint payload field `objects` must be a mapping.")
        manifest = {key: value for key, value in checkpoint_data.items() if key != "objects"}
        manifest["format"] = "sharded"

        shard_files: list[str] = []
        names = list(objects)
        # Save-wide sidecar sequence: sanitized object names can collide
        # ("GM 1" and "GM_1" both sanitize to "GM_1"), so a per-object counter
        # would let one object's sidecar silently overwrite another's. The
        # object name stays in the filename for debuggability only; restore
        # resolves sidecars via the FILE_REF markers, never the name.
        sidecar_seq = itertools.count()
        for shard_index, start in enumerate(range(0, len(names), self.objects_per_shard)):
            shard_name = f"step_{step}_objects_{shard_index:04d}.jsonl"
            shard_files.append(shard_name)
            with open(os.path.join(checkpoint_path, shard_name), "w", encoding="utf-8") as f:
                for name in names[start : start + self.objects_per_shard]:
                    entry = self._extract_sidecars(
                        objects[name],
                        object_name=str(name),
                        step=step,
                        checkpoint_path=checkpoint_path,
                        sidecar_seq=sidecar_seq,
                    )
                    f.write(json.dumps({"name": str(name), "object": entry}) + "\n")
        manifest["objects_shards"] = shard_files

        checkpoint_file = os.path.join(checkpoint_path, f"step_{step}_checkpoint.json")
        # Atomic replace: the manifest is the discovery key (resume globs it),
        # so it must never exist half-written. Shards/sidecars are already on
        # disk by now; a crash before this point leaves no manifest and resume
        # cleanly falls back to the previous step.
        _write_json_atomic(manifest, checkpoint_file)
        return checkpoint_file

    def _extract_sidecars(
        self,
        entry: Any,
        *,
        object_name: str,
        step: int,
        checkpoint_path: str,
        sidecar_seq: Iterator[int],
    ) -> Any:
        """Return ``entry`` with sidecar-eligible base64 blobs moved to files.

        Containers along the path to a replaced value are copied (the payload may
        alias live object state via ``get_state``, which must not be mutated);
        untouched subtrees are shared as-is.
        """

        def _walk(value: Any) -> Any:
            if not isinstance(value, Mapping):
                if isinstance(value, list):
                    walked_items = [_walk(item) for item in value]
                    return (
                        walked_items
                        if any(new is not old for new, old in zip(walked_items, value, strict=True))
                        else value
                    )
                return value
            replaced: dict[str, Any] | None = None
            for key, sub in value.items():
                if key in _SIDECAR_B64_KEYS and isinstance(sub, str) and sub:
                    sidecar_name = (
                        f"step_{step}_{_safe_name(object_name)}_{next(sidecar_seq):02d}.db"
                    )
                    raw = base64.b64decode(sub.encode("ascii"))
                    with open(os.path.join(checkpoint_path, sidecar_name), "wb") as f:
                        f.write(raw)
                    new_sub: Any = {
                        FILE_REF_KEY: sidecar_name,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "encoding": "base64",
                    }
                else:
                    new_sub = _walk(sub)
                if new_sub is not sub:
                    if replaced is None:
                        replaced = dict(value)
                    replaced[key] = new_sub
            return value if replaced is None else replaced

        return _walk(entry)


def reassemble_sharded_checkpoint(manifest: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Rebuild the monolithic payload shape from a sharded manifest.

    Reads the object shards back into ``objects`` and re-inlines sidecar file
    references (verifying their hash) as the original base64 strings, so callers
    of ``load_checkpoint_file`` see exactly what the monolithic layout stores.
    """
    shard_files = manifest.get("objects_shards")
    if not isinstance(shard_files, list) or not all(isinstance(s, str) for s in shard_files):
        raise ValueError("Sharded checkpoint manifest requires an `objects_shards` file list.")
    objects: dict[str, Any] = {}
    for shard_name in shard_files:
        shard_path = directory / shard_name
        if not shard_path.is_file():
            raise ValueError(f"Sharded checkpoint is missing object shard: {shard_path}")
        with shard_path.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Malformed checkpoint shard line {shard_path}:{line_number}"
                    ) from exc
                if not isinstance(row, Mapping) or "name" not in row or "object" not in row:
                    raise ValueError(
                        f"Checkpoint shard line {shard_path}:{line_number} must be "
                        '{"name": ..., "object": ...}.'
                    )
                objects[str(row["name"])] = _resolve_file_refs(row["object"], directory)
    payload = {
        key: value for key, value in manifest.items() if key not in ("format", "objects_shards")
    }
    payload["objects"] = objects
    return payload


def _resolve_file_refs(value: Any, directory: Path) -> Any:
    """Re-inline ``FILE_REF_KEY`` markers as their original base64 strings."""
    if isinstance(value, Mapping):
        if FILE_REF_KEY in value:
            ref_path = directory / str(value[FILE_REF_KEY])
            if not ref_path.is_file():
                raise ValueError(f"Sharded checkpoint is missing sidecar file: {ref_path}")
            raw = ref_path.read_bytes()
            expected = str(value.get("sha256") or "")
            if expected and hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(
                    f"Checkpoint sidecar file {ref_path} does not match its recorded "
                    "sha256; the checkpoint is corrupt."
                )
            return base64.b64encode(raw).decode("ascii")
        return {key: _resolve_file_refs(sub, directory) for key, sub in value.items()}
    if isinstance(value, list):
        return [_resolve_file_refs(item, directory) for item in value]
    return value


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "object"


def _write_json_atomic(payload: dict[str, Any], path: str) -> None:
    """Write JSON via a same-directory temp file + atomic rename.

    ``step_{N}_checkpoint.json`` is what resume discovery globs and loads; a
    process killed mid-``json.dump`` must not leave a truncated file there
    (auto_resume would pick it as the latest checkpoint and fail on every
    subsequent launch instead of falling back to step N-1).
    """
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise


def build_checkpoint_save_strategy(slot_cfg: Any | None) -> CheckpointSaveStrategy:
    """Build the configured checkpoint save strategy (default: monolithic JSON).

    Mirrors ``build_checkpoint_restore``: a custom ``class_path`` (built with
    ``params``) takes precedence over ``built_in``; the class must subclass
    :class:`CheckpointSaveStrategy`. A missing slot keeps today's format.
    """
    if slot_cfg is None:
        return MonolithicJsonSaveStrategy()

    def _field(name: str) -> Any:
        # Accept OmegaConf nodes AND plain mappings (programmatic callers).
        return (
            slot_cfg.get(name) if isinstance(slot_cfg, Mapping) else getattr(slot_cfg, name, None)
        )

    built_in = str(_field("built_in") or "").strip()
    class_path = str(_field("class_path") or "").strip()
    params = _field("params")
    if class_path:
        return _load_save_strategy(class_path, params)
    kwargs: dict[str, Any] = {}
    if isinstance(params, Mapping):
        kwargs = {str(key): value for key, value in params.items()}
    try:
        if built_in in ("", "monolithic_json"):
            return MonolithicJsonSaveStrategy()
        if built_in == "sharded":
            return ShardedCheckpointSaveStrategy(**kwargs)
    except TypeError as exc:
        # Same friendly wrap as the class_path branch: a typo'd param name
        # should read as a config error, not a raw TypeError.
        raise ValueError(
            f"Could not build sim.checkpoint.save built_in {built_in!r} with "
            f"params {sorted(kwargs)}: {exc}"
        ) from exc
    raise ValueError(
        f"Unknown sim.checkpoint.save.built_in {built_in!r}. Available: "
        "monolithic_json, sharded. Set sim.checkpoint.save.class_path for a "
        "custom strategy."
    )


def _load_save_strategy(class_path: str, params: Any) -> CheckpointSaveStrategy:
    """Import and instantiate a custom CheckpointSaveStrategy from a class path."""
    module_path, _, attr = class_path.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid checkpoint save class_path: {class_path!r}")
    try:
        cls = getattr(importlib.import_module(module_path), attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"Could not import checkpoint save class_path {class_path!r}: {exc}"
        ) from exc
    kwargs: dict[str, Any] = {}
    if isinstance(params, Mapping):
        kwargs = {str(key): value for key, value in params.items()}
    try:
        strategy = cls(**kwargs)
    except TypeError as exc:
        raise ValueError(
            f"Could not instantiate checkpoint save {class_path!r} with params "
            f"{sorted(kwargs)}: {exc}"
        ) from exc
    if not isinstance(strategy, CheckpointSaveStrategy):
        raise TypeError(
            f"Checkpoint save class {class_path!r} must subclass CheckpointSaveStrategy."
        )
    return strategy
