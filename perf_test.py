import argparse
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


DEFAULT_URL = "https://api.microsoft.ai/v3/search/web"
DEFAULT_HEADERS = {
    "host": "api.microsoft.ai",
    "x-apikey": "ejQXP9DvRLrfnUPDHM9PYAPS6ZPzS6NFPhgOxl15Rfc=",
    "content-type": "application/json",
}
DEFAULT_PAYLOAD = {
    "query": "Latest AI trends",
    "maxResults": 10,
    "language": "en",
    "region": "US",
    "contentFormat": "html",
    "maxLength": 10000,
}


@dataclass
class RequestResult:
    ok: bool
    status_code: Optional[int]
    latency_ms: float
    error: Optional[str] = None


def percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def one_request(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: float,
) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp = session.post(url, headers=headers, json=payload, timeout=timeout)
        latency_ms = (time.perf_counter() - t0) * 1000
        return RequestResult(
            ok=200 <= resp.status_code < 300,
            status_code=resp.status_code,
            latency_ms=latency_ms,
            error=None,
        )
    except requests.exceptions.RequestException as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return RequestResult(ok=False, status_code=None, latency_ms=latency_ms, error=str(exc))


def run_benchmark(
    total_requests: int,
    concurrency: int,
    timeout: float,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    lock = threading.Lock()
    results: List[RequestResult] = []
    session_local = threading.local()

    start = time.perf_counter()

    def task() -> RequestResult:
        # Reuse one session per worker thread to keep TCP connections alive.
        if not hasattr(session_local, "session"):
            session_local.session = requests.Session()
        session: requests.Session = session_local.session
        return one_request(session, url, headers, payload, timeout)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(task) for _ in range(total_requests)]
        for future in as_completed(futures):
            r = future.result()
            with lock:
                results.append(r)

    total_time_s = time.perf_counter() - start

    latencies = sorted(r.latency_ms for r in results)
    ok_count = sum(1 for r in results if r.ok)
    fail_count = total_requests - ok_count

    status_counter: Dict[str, int] = {}
    error_counter: Dict[str, int] = {}
    for r in results:
        key = str(r.status_code) if r.status_code is not None else "EXC"
        status_counter[key] = status_counter.get(key, 0) + 1
        if r.error:
            error_counter[r.error] = error_counter.get(r.error, 0) + 1

    report = {
        "config": {
            "total_requests": total_requests,
            "concurrency": concurrency,
            "timeout_s": timeout,
            "url": url,
            "payload": payload,
        },
        "summary": {
            "elapsed_s": round(total_time_s, 4),
            "throughput_rps": round(total_requests / total_time_s, 2) if total_time_s > 0 else 0,
            "success": ok_count,
            "failure": fail_count,
            "success_rate": round((ok_count / total_requests) * 100, 2) if total_requests else 0,
        },
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0,
            "avg": round(statistics.mean(latencies), 2) if latencies else 0,
            "p50": round(percentile(latencies, 0.50), 2) if latencies else 0,
            "p95": round(percentile(latencies, 0.95), 2) if latencies else 0,
            "p99": round(percentile(latencies, 0.99), 2) if latencies else 0,
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "status_distribution": status_counter,
        "top_errors": sorted(error_counter.items(), key=lambda x: x[1], reverse=True)[:5],
    }

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Performance test for Microsoft AI Web Search API")
    parser.add_argument("--requests", type=int, default=50, help="Total request count")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent workers")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout in seconds")
    parser.add_argument("--url", type=str, default=DEFAULT_URL, help="Target URL")
    parser.add_argument("--query", type=str, default=DEFAULT_PAYLOAD["query"], help="Search query")
    parser.add_argument("--max-results", type=int, default=DEFAULT_PAYLOAD["maxResults"], help="maxResults")
    parser.add_argument("--language", type=str, default=DEFAULT_PAYLOAD["language"], help="language")
    parser.add_argument("--region", type=str, default=DEFAULT_PAYLOAD["region"], help="region")
    parser.add_argument(
        "--content-format",
        type=str,
        default=DEFAULT_PAYLOAD["contentFormat"],
        help="contentFormat",
    )
    parser.add_argument("--max-length", type=int, default=DEFAULT_PAYLOAD["maxLength"], help="maxLength")
    args = parser.parse_args()

    if args.requests <= 0:
        raise ValueError("--requests must be > 0")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be > 0")

    payload = {
        "query": args.query,
        "maxResults": args.max_results,
        "language": args.language,
        "region": args.region,
        "contentFormat": args.content_format,
        "maxLength": args.max_length,
    }

    report = run_benchmark(
        total_requests=args.requests,
        concurrency=args.concurrency,
        timeout=args.timeout,
        url=args.url,
        headers=DEFAULT_HEADERS,
        payload=payload,
    )

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
