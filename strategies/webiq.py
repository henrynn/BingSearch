"""
Web IQ strategy – official Python SDK path.

Web IQ is Microsoft's state-of-the-art grounding service for AI agents and
assistants.  It returns ranked, citation-ready context across web, news,
images, video, and more, and it is built on Bing search infrastructure and
re-architected for an era of LLMs and multi-step agents.

Reference: https://www.microsoft.com/en-us/webiq

Required environment variables
--------------------------------
WEBIQ_API_KEY   – API key for the Web IQ endpoint.

Dependency
----------
    pip install webiq

Usage
-----
    from strategies.webiq import WebIQStrategy

    strategy = WebIQStrategy()
    strategy.setup()
    result = strategy.invoke(query="Latest AI trends", timeout_s=20)
    print(result)
    strategy.cleanup()
"""

import os
import time
from typing import Optional

from strategies.base import RequestResult


class WebIQStrategy:
    """
    Uses the official ``webiq`` SDK to call the Web IQ web search endpoint.

    The SDK client is created once in ``setup()`` and reused across all
    ``invoke()`` calls (connection pooling / keep-alive handled by the SDK).
    """

    name = "webiq"

    def __init__(self) -> None:
        api_key = os.getenv("WEBIQ_API_KEY")
        if not api_key:
            raise ValueError("WEBIQ_API_KEY is required for the webiq strategy")
        self._api_key = api_key
        self._client: Optional[object] = None

    def setup(self) -> None:
        """Create and open the SDK client (reused across all requests)."""
        try:
            from webiq import WebIQClient
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency. Install: pip install webiq"
            ) from exc
        self._client = WebIQClient(api_key=self._api_key)
        self._client.__enter__()

    def invoke(self, query: str, timeout_s: float) -> RequestResult:
        if self._client is None:
            return RequestResult(
                ok=False, status_code=None, latency_ms=0.0, error="Client not initialized"
            )

        from webiq.types import ContentFormat

        t0 = time.perf_counter()
        try:
            result = self._client.web.search(
                query,
                max_results=10,
                content_format=ContentFormat.html,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            ok = result is not None
            return RequestResult(
                ok=ok,
                status_code=200 if ok else None,
                latency_ms=latency_ms,
                # For a direct API call, total latency equals the tool latency.
                tool_latency_ms=latency_ms,
                tool_latency_source="direct_api_total",
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return RequestResult(ok=False, status_code=None, latency_ms=latency_ms, error=str(exc))

    def cleanup(self) -> None:
        """Close the SDK client and release the underlying connection pool."""
        if self._client is not None:
            try:
                self._client.__exit__(None, None, None)
            except Exception:
                pass
            self._client = None
