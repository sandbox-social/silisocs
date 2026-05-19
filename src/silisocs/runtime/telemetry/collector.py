"""Simulation metrics collector."""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from typing import Any

import psutil


def _snapshot_resources() -> dict[str, Any]:
    """Grab a point-in-time CPU, memory, and optional GPU snapshot."""
    proc = psutil.Process()
    snap: dict[str, Any] = {
        "cpu_percent_process": proc.cpu_percent(interval=0),
        "cpu_percent_system": psutil.cpu_percent(interval=0),
        "memory_rss_mb": proc.memory_info().rss / (1024 * 1024),
        "memory_vms_mb": proc.memory_info().vms / (1024 * 1024),
        "memory_percent": proc.memory_percent(),
        "system_memory_percent": psutil.virtual_memory().percent,
        "open_file_descriptors": len(proc.open_files()),
        "thread_count": proc.num_threads(),
    }
    try:
        import pynvml

        pynvml.nvmlInit()
        gpus: list[dict[str, Any]] = []
        for idx in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpus.append(
                {
                    "id": idx,
                    "name": pynvml.nvmlDeviceGetName(handle),
                    "gpu_util_percent": util.gpu,
                    "memory_util_percent": util.memory,
                    "memory_used_mb": mem.used / (1024 * 1024),
                    "memory_total_mb": mem.total / (1024 * 1024),
                }
            )
        snap["gpus"] = gpus
    except Exception:
        snap["gpus"] = []
    return snap


class SimMetricsCollector:
    """Thread-safe metrics accumulator for one simulation run."""

    _instance: SimMetricsCollector | None = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> SimMetricsCollector:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> SimMetricsCollector:
        with cls._lock:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._phase_timings: list[dict[str, Any]] = []
        self._episode_metrics: list[dict[str, Any]] = []
        self._resource_snapshots: list[dict[str, Any]] = []
        self._meta: dict[str, Any] = {}
        self._sim_start: float | None = None
        self._sim_end: float | None = None
        self._lock_data = threading.Lock()

    class _PhaseTimer:
        """Context manager that records wall-clock time for a named phase."""

        def __init__(self, collector: SimMetricsCollector, name: str) -> None:
            self._collector = collector
            self._name = name
            self._start = 0.0

        def __enter__(self) -> SimMetricsCollector._PhaseTimer:
            self._start = time.time()
            return self

        def __exit__(self, *_exc: object) -> None:
            elapsed = time.time() - self._start
            with self._collector._lock_data:
                self._collector._phase_timings.append(
                    {"phase": self._name, "duration_s": round(elapsed, 4)}
                )

    def phase(self, name: str) -> _PhaseTimer:
        """Return a context manager that times a named phase."""
        return self._PhaseTimer(self, name)

    def mark_sim_start(self) -> None:
        self._sim_start = time.time()
        self.snapshot_resources("sim_start")

    def mark_sim_end(self) -> None:
        self._sim_end = time.time()
        self.snapshot_resources("sim_end")

    def log_episode(self, episode: int, **kwargs: Any) -> None:
        with self._lock_data:
            self._episode_metrics.append({"episode": episode, **kwargs})

    def snapshot_resources(self, label: str = "") -> None:
        snap = _snapshot_resources()
        snap["label"] = label
        snap["timestamp"] = time.time()
        with self._lock_data:
            self._resource_snapshots.append(snap)

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock_data:
            self._meta[key] = value

    def to_dict(self) -> dict[str, Any]:
        total = None
        if self._sim_start is not None and self._sim_end is not None:
            total = round(self._sim_end - self._sim_start, 4)
        return {
            "system": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu_count_logical": psutil.cpu_count(logical=True),
                "cpu_count_physical": psutil.cpu_count(logical=False),
                "total_ram_mb": round(psutil.virtual_memory().total / (1024 * 1024), 1),
            },
            "meta": dict(self._meta),
            "total_sim_duration_s": total,
            "phase_timings": list(self._phase_timings),
            "episode_metrics": list(self._episode_metrics),
            "resource_snapshots": list(self._resource_snapshots),
        }

    def write_json(self, output_dir: str, filename: str = "sim_metrics.json") -> None:
        path = os.path.join(output_dir, filename)
        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
