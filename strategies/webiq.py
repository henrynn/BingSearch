"""
Web IQ strategy – direct API path.

Web IQ is Microsoft's state-of-the-art grounding service for AI agents and
assistants.  It returns ranked, citation-ready context across web, news,
images, video, and more, and it is built on Bing search infrastructure and
re-architected for an era of LLMs and multi-step agents.

Reference: https://www.microsoft.com/en-us/webiq

Required environment variables
--------------------------------
WEBIQ_API_KEY   – API key for the Web IQ endpoint.
WEBIQ_URL       – (optional) Override the default endpoint URL.

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

import requests

from strategies.base import RequestResult


class WebIQStrategy:
    """
    Calls the Web IQ search endpoint directly with an API key.

    This is the simplest and typically lowest-latency path because there is
    no agent orchestration layer – each request goes straight to the search
    service and returns ranked, citation-ready context.
    """

    name = "webiq"

    def __init__(self) -> None:
        self.url = os.getenv("WEBIQ_URL", "https://api.microsoft.ai/v3/search/web")
        api_key = os.getenv("WEBIQ_API_KEY")
        if not api_key:
            raise ValueError("WEBIQ_API_KEY is required for the webiq strategy")

        self.headers = {
            "host": "api.microsoft.ai",
            "x-apikey": api_key,
            "content-type": "application/json",
        }

    def setup(self) -> None:
        """No-op – no persistent resources needed."""
        return

    def invoke(self, query: str, timeout_s: float) -> RequestResult:
        payload = {
            "query": query,
            "maxResults": 10,
            "language": "en",
            "region": "US",
            "contentFormat": "html",
            "maxLength": 10000,
        }
        t0 = time.perf_counter()
        try:
            r = requests.post(self.url, headers=self.headers, json=payload, timeout=timeout_s)
            latency_ms = (time.perf_counter() - t0) * 1000
            return RequestResult(
                ok=200 <= r.status_code < 300,
                status_code=r.status_code,
                latency_ms=latency_ms,
                # For a direct API, total latency equals the tool latency.
                tool_latency_ms=latency_ms,
                tool_latency_source="direct_api_total",
            )
        except requests.exceptions.RequestException as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return RequestResult(ok=False, status_code=None, latency_ms=latency_ms, error=str(exc))

    def cleanup(self) -> None:
        """No-op – no persistent resources to release."""
        return
