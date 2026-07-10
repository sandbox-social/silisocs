"""The Model Proxy: one telemetry plane for harness and native model calls.

Real harnesses (Hermes, OpenClaw) talk OpenAI-compatible HTTP to a ``base_url`` we
control. :class:`HarnessModelProxy` is a loopback HTTP server that forwards each
``/v1/chat/completions`` request *byte-for-byte* to the upstream provider — preserving
the harness's exact request, which terminating into our ``sample_*`` API would distort —
while observing everything: the provider's ``usage`` block flows into the SAME
``llm_usage`` counters/by-phase buckets/pricing as native agents (via a duck-typed
:class:`UsageAccumulator` the run summary picks up automatically), and every call is
logged to ``prompts_and_responses.jsonl`` with agent/episode/phase.

Attribution rides a per-agent routing token (the harness-side "api_key"): the token maps
to an :class:`Upstream` (agent name + real endpoint/key, or a ``scripted`` upstream for
deterministic no-key tests). Real provider keys live only here — harness configs and
workspaces hold routing tokens, never real credentials.

v1 scope: OpenAI chat-completions protocol, non-streaming (adapters are configured for
non-streaming); streaming pass-through with ``include_usage`` injection is a documented
follow-up that lands with the streaming OpenClaw path.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_USAGE_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
_USAGE_PHASES = ("probe", "action", "other")


def _new_usage_bucket() -> dict[str, int]:
    return dict.fromkeys((*_USAGE_TOKEN_FIELDS, "calls_with_usage", "calls_without_usage"), 0)


class UsageAccumulator:
    """A duck-typed language-model stand-in that only accounts token usage/retries.

    It exposes the same ``get_usage_counters(phase)`` / ``get_retry_counters(phase)`` /
    ``_model_name`` surface the run-level ``collect_usage_summary`` reads off native
    models, so adding an accumulator to the run's ``models`` collection folds harness
    usage into the unified ``llm_usage`` with zero changes to the summary code.
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = str(model_name)
        self._lock = threading.Lock()
        self._usage_totals = _new_usage_bucket()
        self._phase_usage = {phase: _new_usage_bucket() for phase in _USAGE_PHASES}
        self._calls_total = 0
        self._failed_calls_total = 0
        self._retries_total = 0
        self._phase_counters = {
            phase: {"calls": 0, "failed": 0, "retries": 0} for phase in _USAGE_PHASES
        }

    @staticmethod
    def _norm_phase(phase: str) -> str:
        normalized = str(phase or "other").strip().lower()
        return normalized if normalized in _USAGE_PHASES else "other"

    def record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        total_tokens: int | None = None,
        phase: str = "other",
    ) -> None:
        phase = self._norm_phase(phase)
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        total = int(total_tokens) if total_tokens else prompt_tokens + completion_tokens
        with self._lock:
            for bucket in (self._usage_totals, self._phase_usage[phase]):
                bucket["prompt_tokens"] += prompt_tokens
                bucket["completion_tokens"] += completion_tokens
                bucket["total_tokens"] += total
                bucket["calls_with_usage"] += 1

    def record_call_without_usage(self, *, phase: str = "other") -> None:
        phase = self._norm_phase(phase)
        with self._lock:
            for bucket in (self._usage_totals, self._phase_usage[phase]):
                bucket["calls_without_usage"] += 1

    def record_retry(self, retries: int, *, success: bool, phase: str = "other") -> None:
        phase = self._norm_phase(phase)
        with self._lock:
            self._calls_total += 1
            self._retries_total += int(retries or 0)
            counters = self._phase_counters[phase]
            counters["calls"] += 1
            counters["retries"] += int(retries or 0)
            if not success:
                self._failed_calls_total += 1
                counters["failed"] += 1

    def get_usage_counters(self, phase: str = "all") -> dict[str, int]:
        normalized = str(phase).strip().lower()
        with self._lock:
            return dict(self._phase_usage.get(normalized, self._usage_totals))

    def get_retry_counters(self, phase: str = "all") -> dict[str, int]:
        normalized = str(phase).strip().lower()
        with self._lock:
            if normalized in self._phase_counters:
                return dict(self._phase_counters[normalized])
            return {
                "calls": self._calls_total,
                "failed": self._failed_calls_total,
                "retries": self._retries_total,
            }


@dataclass
class Upstream:
    """Resolved routing target for one agent's harness model calls.

    ``kind='http'`` forwards to ``base_url`` with ``api_key``; ``kind='scripted'``
    returns ``scripted_content`` locally (deterministic, no network, no key). ``phase``
    tags the recorded usage bucket (probe/action/other). ``accumulator`` receives the
    usage; agents on the same effective model share one accumulator (native-parity
    dedup).
    """

    agent_name: str
    accumulator: UsageAccumulator
    kind: str = "scripted"
    base_url: str = ""
    api_key: str = ""
    model_label: str = "harness-model"
    phase: str = "action"
    scripted_content: str = "Scripted proxy response."
    scripted_usage: tuple[int, int] = (8, 4)
    extra_headers: dict[str, str] = field(default_factory=dict)


class HarnessModelProxy:
    """Loopback OpenAI-compatible proxy that unifies harness model telemetry."""

    def __init__(self, *, prompt_logger: Any = None, host: str = "127.0.0.1") -> None:
        self._host = host
        self._prompt_logger = prompt_logger
        self._tokens: dict[str, Upstream] = {}
        self._accumulators: dict[str, UsageAccumulator] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._token_seq = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def accumulator_for(self, model_label: str) -> UsageAccumulator:
        """Return (creating if needed) the shared accumulator for a model label."""
        with self._lock:
            acc = self._accumulators.get(model_label)
            if acc is None:
                acc = UsageAccumulator(model_label)
                self._accumulators[model_label] = acc
            return acc

    def register_agent(
        self,
        agent_name: str,
        *,
        kind: str = "scripted",
        base_url: str = "",
        api_key: str = "",
        model_label: str = "harness-model",
        phase: str = "action",
        scripted_content: str = "Scripted proxy response.",
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """Register an agent's upstream and return its routing token (its api_key)."""
        accumulator = self.accumulator_for(model_label)
        with self._lock:
            self._token_seq += 1
            token = f"silisocs-harness-{self._token_seq}-{agent_name}"
            self._tokens[token] = Upstream(
                agent_name=agent_name,
                accumulator=accumulator,
                kind=kind,
                base_url=base_url,
                api_key=api_key,
                model_label=model_label,
                phase=phase,
                scripted_content=scripted_content,
                extra_headers=dict(extra_headers or {}),
            )
        return token

    def usage_models(self) -> list[UsageAccumulator]:
        """The accumulators to add to the run's ``models`` collection for summary."""
        with self._lock:
            return list(self._accumulators.values())

    def upstream_for_token(self, token: str) -> Upstream | None:
        with self._lock:
            return self._tokens.get(str(token or "").strip())

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("HarnessModelProxy is not started.")
        address = self._server.server_address
        return f"http://{address[0]!s}:{int(address[1])}/v1"

    def start(self) -> None:
        if self._server is not None:
            return
        proxy = self

        class _Handler(_ProxyRequestHandler):
            _proxy = proxy

        self._server = ThreadingHTTPServer((self._host, 0), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="harness-model-proxy", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> HarnessModelProxy:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    # Request handling (called by the HTTP handler)
    # ------------------------------------------------------------------ #

    def handle_chat_completion(
        self, token: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """Forward (or script) one chat-completion; record usage; return (status, json)."""
        upstream = self.upstream_for_token(token)
        if upstream is None:
            return 401, {"error": {"message": "Unknown harness routing token.", "type": "auth"}}

        if upstream.kind == "scripted":
            status, response = self._scripted_response(upstream, body)
        else:
            status, response = self._forward(upstream, body)

        self._record(upstream, body, status, response)
        return status, response

    def _scripted_response(
        self, upstream: Upstream, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        prompt_tokens, completion_tokens = upstream.scripted_usage
        response = {
            "id": "chatcmpl-scripted",
            "object": "chat.completion",
            "model": str(body.get("model") or upstream.model_label),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": upstream.scripted_content},
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return 200, response

    def _forward(self, upstream: Upstream, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        import httpx

        url = upstream.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if upstream.api_key:
            headers["Authorization"] = f"Bearer {upstream.api_key}"
        headers.update(upstream.extra_headers)
        # Force non-streaming in v1 so usage is present in the single JSON response.
        forwarded = {**body, "stream": False}
        try:
            with httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
                resp = client.post(url, json=forwarded, headers=headers)
            payload = resp.json() if resp.content else {}
            return resp.status_code, payload if isinstance(payload, dict) else {}
        except Exception as exc:  # network/timeout — surface as an OpenAI-shaped error
            return 502, {"error": {"message": f"Upstream request failed: {exc}", "type": "proxy"}}

    def _record(
        self,
        upstream: Upstream,
        body: dict[str, Any],
        status: int,
        response: dict[str, Any],
    ) -> None:
        usage = response.get("usage") if isinstance(response, dict) else None
        success = 200 <= status < 300
        if isinstance(usage, dict):
            upstream.accumulator.record_usage(
                int(usage.get("prompt_tokens", 0) or 0),
                int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0) or None,
                phase=upstream.phase,
            )
        else:
            upstream.accumulator.record_call_without_usage(phase=upstream.phase)
        upstream.accumulator.record_retry(0, success=success, phase=upstream.phase)
        self._log_prompt(upstream, body, response)

    def _log_prompt(
        self, upstream: Upstream, body: dict[str, Any], response: dict[str, Any]
    ) -> None:
        log = getattr(self._prompt_logger, "log", None)
        if not callable(log):
            return
        messages = body.get("messages") if isinstance(body, dict) else None
        prompt_text = ""
        if isinstance(messages, list) and messages:
            prompt_text = str((messages[-1] or {}).get("content", "") or "")
        output_text = ""
        choices = response.get("choices") if isinstance(response, dict) else None
        if isinstance(choices, list) and choices:
            output_text = str(((choices[0] or {}).get("message") or {}).get("content", "") or "")
        usage = response.get("usage") if isinstance(response, dict) else None
        row: dict[str, Any] = {
            "prompt": prompt_text,
            "output": output_text,
            "agent_name": upstream.agent_name,
            "phase": upstream.phase,
            "source": "harness_proxy",
            "model": upstream.model_label,
        }
        if isinstance(usage, dict):
            row["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
            row["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)
        log(row)


class _ProxyRequestHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible request handler bound to a :class:`HarnessModelProxy`."""

    _proxy: HarnessModelProxy

    def log_message(self, *args: Any) -> None:  # silence default stderr access log
        del args

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _token(self) -> str:
        auth = self.headers.get("Authorization", "") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return auth.strip()

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._send_json(200, {"object": "list", "data": [{"id": "harness-model"}]})
            return
        self._send_json(404, {"error": {"message": "Not found", "type": "proxy"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json(404, {"error": {"message": "Not found", "type": "proxy"}})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "Invalid JSON body.", "type": "proxy"}})
            return
        status, payload = self._proxy.handle_chat_completion(self._token(), body)
        self._send_json(status, payload)
