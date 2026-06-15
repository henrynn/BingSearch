"""
Shared types and the Foundry streaming base class used by all three strategies.
"""
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


def load_env_file(env_path: str = ".env") -> None:
    """Load key=value pairs from *env_path* into os.environ.

    Existing environment variables are never overwritten, so shell-level
    exports always take precedence.  Lines starting with ``#`` and blank
    lines are ignored.  Both ``export KEY=VALUE`` and bare ``KEY=VALUE``
    forms are supported.  Surrounding single or double quotes are stripped.
    """
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class RequestResult:
    ok: bool
    status_code: Optional[int]
    latency_ms: float
    tool_latency_ms: Optional[float] = None
    tool_latency_source: Optional[str] = None
    error: Optional[str] = None


class Strategy(Protocol):
    """Minimal interface every search strategy must satisfy."""

    name: str

    def setup(self) -> None:
        """One-time initialisation: create connections, agents, etc."""
        ...

    def invoke(self, query: str, timeout_s: float) -> RequestResult:
        """Execute one search request and return timing + outcome."""
        ...

    def cleanup(self) -> None:
        """Release any resources created during setup."""
        ...


class FoundryBaseStrategy:
    """
    Shared Foundry agent logic.

    Concrete strategy classes inherit this and only need to implement
    ``setup()`` (to create the right agent) and ``cleanup()``.  All
    request timing and tool-event probing live here so the measurement
    methodology is identical across Foundry-based strategies.
    """

    _project: Any
    _openai: Any
    _agent: Any
    _conversation_id: Optional[str]

    def _invoke_with_tool_probe(self, query: str, timeout_s: float) -> RequestResult:
        """
        Stream a response and record two timing points:

        * ``latency_ms``      – total end-to-end time until the stream closes.
        * ``tool_latency_ms`` – proxy for tool latency: time-to-first-tool-related
          ``response.output_item.done`` event in the stream.  This is *not* the raw
          backend tool execution time; it includes model pre-processing and network
          overhead up to the moment the first tool result arrives.
        """
        if self._openai is None or self._agent is None:
            return RequestResult(
                ok=False, status_code=None, latency_ms=0.0, error="Strategy not initialized"
            )

        t0 = time.perf_counter()
        first_tool_event_ms: Optional[float] = None
        output_text = None

        try:
            extra_kwargs: Dict[str, Any] = {}
            if getattr(self, "_conversation_id", None):
                extra_kwargs["conversation"] = self._conversation_id

            stream_response = self._openai.responses.create(
                stream=True,
                tool_choice="required",
                input=query,
                timeout=timeout_s,
                extra_body={
                    "agent_reference": {"name": self._agent.name, "type": "agent_reference"}
                },
                **extra_kwargs,
            )

            for event in stream_response:
                event_type = getattr(event, "type", "") or ""

                if first_tool_event_ms is None and event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    item_type = getattr(item, "type", "") if item is not None else ""
                    if item_type and (
                        "search" in item_type or "tool" in item_type or "ground" in item_type
                    ):
                        first_tool_event_ms = (time.perf_counter() - t0) * 1000

                if event_type == "response.completed":
                    response = getattr(event, "response", None)
                    if response is not None:
                        output_text = getattr(response, "output_text", None)

            latency_ms = (time.perf_counter() - t0) * 1000
            ok = bool(output_text)
            return RequestResult(
                ok=ok,
                status_code=200 if ok else None,
                latency_ms=latency_ms,
                tool_latency_ms=first_tool_event_ms,
                tool_latency_source="first_tool_event_in_stream",
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return RequestResult(
                ok=False, status_code=None, latency_ms=latency_ms, error=str(exc)
            )
