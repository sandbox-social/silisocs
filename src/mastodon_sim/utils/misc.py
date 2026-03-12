import atexit
import importlib
import json
import logging
import os
import platform
import queue
import sys
import threading
import time
from collections.abc import Mapping
from itertools import count
from typing import Any

import psutil


def write_concordia_logs(results_log, output_rootname):
    file_path = os.path.join(output_rootname, "logs.html")
    try:
        with open(file_path, "w", encoding="utf-8") as html_file:
            html_file.write(results_log)
        print(f"HTML content successfully saved to {file_path}")
    except OSError as e:
        print(f"Error saving HTML content: {e}")


def get_prefab_instance(entity_prefab, module_path):
    print(f"[Loader] Loading prefab: {entity_prefab} from {module_path}")
    entity_name, entity_type = entity_prefab.split("__")
    try:
        # e.g. importlib.import_module("scenarios.election.entity_lib.voter")
        build_entity_module = importlib.import_module(module_path)
        # e.g., getattr(module, "Entity")
        build_entity_class = getattr(build_entity_module, entity_type)
    except ImportError:
        print(f"Error: Could not import module: {entity_name}")
    except AttributeError:
        print(f"Error: Module {entity_name} does not have class: {entity_type}")
    except Exception as e:
        print(f"An error occurred while loading prefab {entity_prefab}: {e}")
    # return the *instantiated* class
    return build_entity_class()


# Create a custom StreamHandler that redirects stdout to the logger
class StdoutToLogger:
    def __init__(self, logger, log_level=logging.INFO):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ""

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

    def flush(self):
        pass


def get_sentence_encoder(model_name):
    # Setup sentence encoder
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        import sentence_transformers

    st_model = sentence_transformers.SentenceTransformer(model_name)
    embedder = lambda x: st_model.encode(x, show_progress_bar=False)
    return embedder


class _AsyncJsonlWriter:
    """Single-file ordered JSONL writer with bounded queue and batch flushes."""

    def __init__(self, output_filename: str, max_queue_size: int = 200_000):
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
                    if nxt is None:
                        continue
                    batch.append(nxt)

                f.writelines(json.dumps(payload, ensure_ascii=True) + "\n" for payload in batch)
                f.flush()

    def write(self, payload: Mapping[str, Any]) -> None:
        self._queue.put(dict(payload))

    def close(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        self._thread.join(timeout=5)


class _AsyncJsonlWriterRegistry:
    def __init__(self):
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


_JSONL_WRITERS = _AsyncJsonlWriterRegistry()
atexit.register(_JSONL_WRITERS.close_all)


def write_jsonl_item(out_item: Mapping[str, Any], output_filename: str) -> None:
    """Write one JSONL payload through the shared async writer registry."""
    _JSONL_WRITERS.get(output_filename).write(out_item)


class EventLogger:
    def __init__(self, event_type, output_filename):
        self.episode_idx = None
        self.output_filename = output_filename
        self.type = event_type
        self.dummy = None
        self._sequence = count()
        self._seq_lock = threading.Lock()

    def _next_seq(self) -> int:
        with self._seq_lock:
            return next(self._sequence)

    def _prepare_item(self, log_item: dict[str, Any]) -> dict[str, Any]:
        item = dict(log_item)
        item["episode"] = self.episode_idx
        item["event_type"] = self.type
        item["event_index"] = self._next_seq()
        if self.type == "action":
            item.setdefault("data", {})
            item["data"]["suggested_action"] = self.dummy
        return item

    def log(self, log_data):
        if isinstance(log_data, list):
            for log_item in log_data:
                prepared = self._prepare_item(log_item)
                write_jsonl_item(prepared, self.output_filename)
        else:
            prepared = self._prepare_item(log_data)
            write_jsonl_item(prepared, self.output_filename)


def configure_logging(logger):
    # supress verbose printing of hydra's api logging so only warnings (or greater issues) are printed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Redirect stdout to the logger
    sys.stdout = StdoutToLogger(logger)


# ---------------------------------------------------------------------------
# SimMetricsCollector — lightweight singleton that accumulates timing, resource
# usage, and sim metadata throughout a run, then writes a single JSON summary.
# ---------------------------------------------------------------------------


def _snapshot_resources() -> dict[str, Any]:
    """Grab a point-in-time snapshot of CPU, memory, and (optional) GPU usage."""
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
    # GPU metrics (best-effort via pynvml)
    try:
        import pynvml

        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        gpus: list[dict[str, Any]] = []
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpus.append(
                {
                    "id": i,
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
    """Thread-safe metrics accumulator for a single simulation run."""

    _instance: "SimMetricsCollector | None" = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "SimMetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> "SimMetricsCollector":
        with cls._lock:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._phase_timings: list[dict[str, Any]] = []
        self._episode_metrics: list[dict[str, Any]] = []
        self._resource_snapshots: list[dict[str, Any]] = []
        self._meta: dict[str, Any] = {}
        self._sim_start: float | None = None
        self._sim_end: float | None = None
        self._lock_data = threading.Lock()

    # -- context-manager for named phases -----------------------------------

    class _PhaseTimer:
        """Context manager that records wall-clock time for a named phase."""

        def __init__(self, collector: "SimMetricsCollector", name: str):
            self._collector = collector
            self._name = name
            self._start = 0.0

        def __enter__(self):
            self._start = time.time()
            return self

        def __exit__(self, *_exc):
            elapsed = time.time() - self._start
            with self._collector._lock_data:
                self._collector._phase_timings.append(
                    {
                        "phase": self._name,
                        "duration_s": round(elapsed, 4),
                    }
                )

    def phase(self, name: str) -> _PhaseTimer:
        """Return a context-manager that times a named phase."""
        return self._PhaseTimer(self, name)

    # -- sim-level bookkeeping ----------------------------------------------

    def mark_sim_start(self):
        self._sim_start = time.time()
        self.snapshot_resources("sim_start")

    def mark_sim_end(self):
        self._sim_end = time.time()
        self.snapshot_resources("sim_end")

    # -- per-episode metrics ------------------------------------------------

    def log_episode(self, episode: int, **kwargs: Any):
        """Log per-episode data (duration, agent counts, etc.)."""
        entry = {"episode": episode, **kwargs}
        with self._lock_data:
            self._episode_metrics.append(entry)

    # -- resource snapshots -------------------------------------------------

    def snapshot_resources(self, label: str = ""):
        snap = _snapshot_resources()
        snap["label"] = label
        snap["timestamp"] = time.time()
        with self._lock_data:
            self._resource_snapshots.append(snap)

    # -- arbitrary metadata -------------------------------------------------

    def set_meta(self, key: str, value: Any):
        with self._lock_data:
            self._meta[key] = value

    # -- serialisation & writing --------------------------------------------

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
            "meta": self._meta,
            "total_sim_duration_s": total,
            "phase_timings": list(self._phase_timings),
            "episode_metrics": list(self._episode_metrics),
            "resource_snapshots": list(self._resource_snapshots),
        }

    def write_json(self, output_dir: str, filename: str = "sim_metrics.json"):
        path = os.path.join(output_dir, filename)
        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        print(f"Simulation metrics written to {path}")
