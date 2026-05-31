"""JSONL event logging helpers."""

from __future__ import annotations

import atexit
import dataclasses
import json
import os
import queue
import threading
import time
from collections.abc import Mapping, Sequence
from enum import Enum
from itertools import count
from typing import Any


class _AsyncJsonlWriter:
    """Single-file ordered JSONL writer with bounded queue and batched flushes."""

    def __init__(self, output_filename: str, max_queue_size: int = 200_000) -> None:
        self._output_filename = output_filename
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        os.makedirs(os.path.dirname(self._output_filename) or ".", exist_ok=True)
        with open(self._output_filename, "a", encoding="utf-8") as f:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    item = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    continue
                batch: list[dict[str, Any]] = [item]
                while len(batch) < 4096:
                    try:
                        nxt = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is not None:
                        batch.append(nxt)
                f.writelines(json.dumps(payload, ensure_ascii=True) + "\n" for payload in batch)
                f.flush()

    def write(self, payload: Mapping[str, Any]) -> None:
        self._queue.put(dict(payload))

    def close(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        self._thread.join(timeout=5)

    def flush(self, timeout_s: float = 5.0) -> None:
        deadline = time.time() + max(0.1, float(timeout_s))
        while time.time() < deadline:
            if self._queue.empty():
                return
            time.sleep(0.01)


class _AsyncJsonlWriterRegistry:
    def __init__(self) -> None:
        self._writers: dict[str, _AsyncJsonlWriter] = {}
        self._lock = threading.Lock()

    def get(self, output_filename: str) -> _AsyncJsonlWriter:
        with self._lock:
            writer = self._writers.get(output_filename)
            if writer is None:
                writer = _AsyncJsonlWriter(output_filename)
                self._writers[output_filename] = writer
            return writer

    def close_all(self) -> None:
        with self._lock:
            writers = list(self._writers.values())
            self._writers.clear()
        for writer in writers:
            writer.close()

    def flush_all(self, timeout_s: float = 5.0) -> None:
        with self._lock:
            writers = list(self._writers.values())
        for writer in writers:
            writer.flush(timeout_s=timeout_s)


_JSONL_WRITERS = _AsyncJsonlWriterRegistry()
atexit.register(_JSONL_WRITERS.close_all)


def write_jsonl_item(out_item: Mapping[str, Any], output_filename: str) -> None:
    """Write one JSONL payload through the shared async writer registry."""
    _JSONL_WRITERS.get(output_filename).write(_json_safe(out_item))


def flush_jsonl_writers(timeout_s: float = 5.0) -> None:
    """Flush buffered JSONL writes to disk."""
    _JSONL_WRITERS.flush_all(timeout_s=timeout_s)


class EventLogger:
    """Structured event logger for action/probe JSONL files."""

    def __init__(self, event_type: str, output_filename: str) -> None:
        self.episode_idx: int | None = None
        self.output_filename = output_filename
        self.type = event_type
        self._sequence = count()
        self._seq_lock = threading.Lock()

    def _next_seq(self) -> int:
        with self._seq_lock:
            return next(self._sequence)

    def _prepare_item(self, log_item: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(log_item)
        item["episode"] = self.episode_idx
        item["event_type"] = self.type
        item["event_index"] = self._next_seq()
        return item

    def log(self, log_data: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> None:
        if not isinstance(log_data, Mapping):
            for log_item in log_data:
                write_jsonl_item(self._prepare_item(log_item), self.output_filename)
            return
        write_jsonl_item(self._prepare_item(log_data), self.output_filename)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_safe(to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    return str(value)
