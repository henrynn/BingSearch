import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

import requests


DEFAULT_QUERY = "Latest AI trends"
DEFAULT_TIMEOUT = 30.0


def log_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[progress] {message}", file=sys.stderr, flush=True)


def load_env_file(env_path: str = ".env") -> None:
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
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
    name: str

    def setup(self) -> None:
        ...

    def invoke(self, query: str, timeout_s: float) -> RequestResult:
        ...

    def cleanup(self) -> None:
        ...


class WebIQStrategy:
    name = "webiq"

    def __init__(self) -> None:
        self.url = os.getenv("WEBIQ_URL", "https://api.microsoft.ai/v3/search/web")
        api_key = os.getenv("WEBIQ_API_KEY")
        if not api_key:
            raise ValueError("WEBIQ_API_KEY is required for webiq strategy")

        self.headers = {
            "host": "api.microsoft.ai",
            "x-apikey": api_key,
            "content-type": "application/json",
        }

    def setup(self) -> None:
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
                tool_latency_ms=latency_ms,
                tool_latency_source="direct_api_total",
            )
        except requests.exceptions.RequestException as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return RequestResult(ok=False, status_code=None, latency_ms=latency_ms, error=str(exc))

    def cleanup(self) -> None:
        return


class FoundryBaseStrategy:
    _project: Any
    _openai: Any
    _agent: Any
    _conversation_id: Optional[str]

    def _invoke_with_tool_probe(self, query: str, timeout_s: float) -> RequestResult:
        if self._openai is None or self._agent is None:
            return RequestResult(ok=False, status_code=None, latency_ms=0.0, error="Strategy not initialized")

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
                extra_body={"agent_reference": {"name": self._agent.name, "type": "agent_reference"}},
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
            return RequestResult(ok=False, status_code=None, latency_ms=latency_ms, error=str(exc))


class FoundryBingGroundingStrategy(FoundryBaseStrategy):
    name = "foundry_bing_grounding"

    def __init__(self) -> None:
        self.project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        self.model_deployment = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
        self.connection_id = os.getenv("BING_PROJECT_CONNECTION_ID")
        self.connection_name = os.getenv("BING_PROJECT_CONNECTION_NAME")

        if not self.project_endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required for foundry_bing_grounding")
        if not self.model_deployment:
            raise ValueError("FOUNDRY_MODEL_DEPLOYMENT_NAME is required for foundry_bing_grounding")
        if not self.connection_id and not self.connection_name:
            raise ValueError(
                "Either BING_PROJECT_CONNECTION_ID or BING_PROJECT_CONNECTION_NAME is required for foundry_bing_grounding"
            )

        self._project = None
        self._openai = None
        self._agent = None
        self._conversation_id = None

    def setup(self) -> None:
        try:
            from azure.ai.projects import AIProjectClient
            from azure.ai.projects.models import (
                BingGroundingSearchConfiguration,
                BingGroundingSearchToolParameters,
                BingGroundingTool,
                PromptAgentDefinition,
            )
            from azure.identity import DefaultAzureCredential
        except Exception as exc:
            raise RuntimeError(
                "Missing dependency for Foundry strategies. Install: pip install azure-ai-projects azure-identity"
            ) from exc

        self._project = AIProjectClient(
            endpoint=self.project_endpoint,
            credential=DefaultAzureCredential(),
        )
        self._openai = self._project.get_openai_client()
        try:
            conversation = self._openai.conversations.create()
            self._conversation_id = conversation.id
        except Exception:
            # Conversation reuse is an optimization; benchmark still works if unavailable.
            self._conversation_id = None

        connection_id = self.connection_id
        if not connection_id and self.connection_name:
            connection = self._project.connections.get(self.connection_name)
            connection_id = connection.id

        self._agent = self._project.agents.create_version(
            agent_name=f"perf-grounding-{int(time.time())}",
            definition=PromptAgentDefinition(
                model=self.model_deployment,
                instructions=(
                    "You are concise. Always use available search tools for real-time questions."
                ),
                tools=[
                    BingGroundingTool(
                        bing_grounding=BingGroundingSearchToolParameters(
                            search_configurations=[
                                BingGroundingSearchConfiguration(project_connection_id=connection_id)
                            ]
                        )
                    )
                ],
            ),
            description="Perf comparison agent - grounding with bing",
        )

    def invoke(self, query: str, timeout_s: float) -> RequestResult:
        return self._invoke_with_tool_probe(query=query, timeout_s=timeout_s)

    def cleanup(self) -> None:
        if self._project is not None and self._agent is not None:
            try:
                self._project.agents.delete_version(
                    agent_name=self._agent.name,
                    agent_version=self._agent.version,
                )
            except Exception:
                pass


class FoundryWebSearchStrategy(FoundryBaseStrategy):
    name = "foundry_web_search"

    def __init__(self) -> None:
        self.project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        self.model_deployment = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")

        if not self.project_endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required for foundry_web_search")
        if not self.model_deployment:
            raise ValueError("FOUNDRY_MODEL_DEPLOYMENT_NAME is required for foundry_web_search")

        self._project = None
        self._openai = None
        self._agent = None
        self._conversation_id = None

    def setup(self) -> None:
        try:
            from azure.ai.projects import AIProjectClient
            from azure.ai.projects.models import PromptAgentDefinition, WebSearchTool
            from azure.identity import DefaultAzureCredential
        except Exception as exc:
            raise RuntimeError(
                "Missing dependency for Foundry strategies. Install: pip install azure-ai-projects azure-identity"
            ) from exc

        self._project = AIProjectClient(
            endpoint=self.project_endpoint,
            credential=DefaultAzureCredential(),
        )
        self._openai = self._project.get_openai_client()
        try:
            conversation = self._openai.conversations.create()
            self._conversation_id = conversation.id
        except Exception:
            # Conversation reuse is an optimization; benchmark still works if unavailable.
            self._conversation_id = None
        self._agent = self._project.agents.create_version(
            agent_name=f"perf-websearch-{int(time.time())}",
            definition=PromptAgentDefinition(
                model=self.model_deployment,
                instructions=(
                    "You are concise. Always use available search tools for real-time questions."
                ),
                tools=[WebSearchTool()],
            ),
            description="Perf comparison agent - web search tool",
        )

    def invoke(self, query: str, timeout_s: float) -> RequestResult:
        return self._invoke_with_tool_probe(query=query, timeout_s=timeout_s)

    def cleanup(self) -> None:
        if self._project is not None and self._agent is not None:
            try:
                self._project.agents.delete_version(
                    agent_name=self._agent.name,
                    agent_version=self._agent.version,
                )
            except Exception:
                pass


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


def summarize(total_requests: int, elapsed_s: float, results: List[RequestResult]) -> Dict[str, Any]:
    latencies = sorted(r.latency_ms for r in results)
    tool_latencies = sorted(r.tool_latency_ms for r in results if r.tool_latency_ms is not None)
    ok_count = sum(1 for r in results if r.ok)
    fail_count = total_requests - ok_count

    status_counter: Dict[str, int] = {}
    error_counter: Dict[str, int] = {}
    for r in results:
        key = str(r.status_code) if r.status_code is not None else "EXC"
        status_counter[key] = status_counter.get(key, 0) + 1
        if r.error:
            error_counter[r.error] = error_counter.get(r.error, 0) + 1

    return {
        "summary": {
            "elapsed_s": round(elapsed_s, 4),
            "throughput_rps": round(total_requests / elapsed_s, 2) if elapsed_s > 0 else 0,
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
        "tool_latency_ms": {
            "available_samples": len(tool_latencies),
            "min": round(min(tool_latencies), 2) if tool_latencies else None,
            "avg": round(statistics.mean(tool_latencies), 2) if tool_latencies else None,
            "p50": round(percentile(tool_latencies, 0.50), 2) if tool_latencies else None,
            "p95": round(percentile(tool_latencies, 0.95), 2) if tool_latencies else None,
            "p99": round(percentile(tool_latencies, 0.99), 2) if tool_latencies else None,
            "max": round(max(tool_latencies), 2) if tool_latencies else None,
            "note": "Proxy metric. For Foundry, this is time-to-first-tool-event in stream, not raw backend tool execution time.",
        },
        "status_distribution": status_counter,
        "top_errors": sorted(error_counter.items(), key=lambda x: x[1], reverse=True)[:5],
    }


def run_strategy(
    strategy: Strategy,
    total_requests: int,
    concurrency: int,
    query: str,
    timeout_s: float,
    warmup_requests: int,
    show_progress: bool,
) -> Dict[str, Any]:
    results: List[RequestResult] = []
    lock = threading.Lock()

    strategy.setup()
    try:
        log_progress(show_progress, f"{strategy.name}: setup complete")
        # Warmup requests are excluded from measurement window.
        for idx in range(max(0, warmup_requests)):
            try:
                strategy.invoke(query=query, timeout_s=timeout_s)
                log_progress(
                    show_progress,
                    f"{strategy.name}: warmup {idx + 1}/{max(0, warmup_requests)} done",
                )
            except Exception:
                log_progress(
                    show_progress,
                    f"{strategy.name}: warmup {idx + 1}/{max(0, warmup_requests)} failed",
                )

        start = time.perf_counter()

        def task() -> RequestResult:
            return strategy.invoke(query=query, timeout_s=timeout_s)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(task) for _ in range(total_requests)]
            for idx, future in enumerate(as_completed(futures), start=1):
                r = future.result()
                with lock:
                    results.append(r)
                if idx == total_requests or idx % max(1, total_requests // 10) == 0:
                    log_progress(show_progress, f"{strategy.name}: measured {idx}/{total_requests}")

        elapsed_s = time.perf_counter() - start
        report = summarize(total_requests, elapsed_s, results)
        report["strategy"] = strategy.name
        report["warmup_requests"] = max(0, warmup_requests)
        log_progress(show_progress, f"{strategy.name}: completed in {elapsed_s:.2f}s")
        return report
    finally:
        strategy.cleanup()


def build_strategy(name: str) -> Strategy:
    if name == "webiq":
        return WebIQStrategy()
    if name == "foundry_bing_grounding":
        return FoundryBingGroundingStrategy()
    if name == "foundry_web_search":
        return FoundryWebSearchStrategy()
    raise ValueError(f"Unsupported strategy: {name}")


def _strategy_label(name: str) -> str:
    labels = {
        "webiq": "Web IQ",
        "foundry_bing_grounding": "Foundry Grounding+Bing",
        "foundry_web_search": "Foundry Web Search Tool",
    }
    return labels.get(name, name)


def write_html_report(output: Dict[str, Any], html_path: str) -> None:
    config = output.get("config", {})
    results = output.get("results", [])

    chart_rows: List[Dict[str, Any]] = [r for r in results if isinstance(r, dict) and "error" not in r]
    labels = [_strategy_label(r.get("strategy", "unknown")) for r in chart_rows]
    throughput = [r.get("summary", {}).get("throughput_rps", 0) for r in chart_rows]
    avg_latency = [r.get("latency_ms", {}).get("avg", 0) for r in chart_rows]
    p95_latency = [r.get("latency_ms", {}).get("p95", 0) for r in chart_rows]
    tool_avg = [r.get("tool_latency_ms", {}).get("avg") for r in chart_rows]

    html = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Performance Compare Report</title>
    <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>
    <style>
        :root {{
            --bg: #f5f7fb;
            --card: #ffffff;
            --line: #d0d5dd;
            --text: #1f2937;
            --muted: #475467;
        }}
        body {{ margin: 0; font-family: Segoe UI, Microsoft YaHei, sans-serif; background: var(--bg); color: var(--text); }}
        .wrap {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
        .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
        .meta {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .pill {{ border: 1px solid #bfd3ff; border-radius: 999px; padding: 4px 10px; background: #ecf3ff; color: #0d47a1; font-size: 13px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; }}
        th {{ background: #fafbfc; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
        .chart {{ border: 1px solid var(--line); border-radius: 10px; padding: 10px; background: #fff; }}
    </style>
</head>
<body>
    <div class=\"wrap\">
        <div class=\"card\">
            <h2 style=\"margin-top:0\">Bing Search Performance Compare</h2>
            <div class=\"meta\">
                <span class=\"pill\">requests: {config.get("requests")}</span>
                <span class=\"pill\">concurrency: {config.get("concurrency")}</span>
                <span class=\"pill\">timeout_s: {config.get("timeout_s")}</span>
                <span class=\"pill\">warmup_requests: {config.get("warmup_requests")}</span>
            </div>
            <p style=\"color:var(--muted)\">query: {config.get("query", "")}</p>

        <div class="card">
            <h3 style="margin-top:0">Background</h3>
            <p>This benchmark compares three Bing-backed search approaches under the same query and load settings. The goal is to understand the relative performance and behavior of a direct search API path versus two Foundry agent-based search integrations.</p>
            <p><strong>Web IQ</strong> is Microsoft’s state-of-the-art grounding service for AI agents and assistants. It returns ranked, citation-ready context across web, news, images, video, and more, and it’s built on Bing search infrastructure and re-architected for an era of LLMs and multi-step agents.</p>
            <p>All runs in this report use the same request count, concurrency, timeout, and no warmup by default, so the results are comparable at the system level.</p>
            <h3>Key Differences</h3>
            <ol>
                <li><strong>Web IQ</strong> is Microsoft’s state-of-the-art grounding service for AI agents and assistants. It returns ranked, citation-ready context across web, news, images, video, and more, and it’s built on Bing search infrastructure and re-architected for an era of LLMs and multi-step agents.</li>
                <li><strong>Foundry Grounding+Bing</strong> uses a Foundry agent with the Bing grounding tool. It adds agent orchestration and tool invocation on top of search, which makes it more flexible for agent workflows.</li>
                <li><strong>Foundry Web Search Tool</strong> uses the Foundry web search abstraction inside the agent runtime. It is also agent-based, and it represents the more general web-search integration path in Foundry.</li>
            </ol>
        </div>
        </div>

        <div class=\"card\">
            <h3 style=\"margin-top:0\">Metrics</h3>
            <table>
                <thead>
                    <tr>
                        <th>Strategy</th>
                        <th>Success Rate</th>
                        <th>Throughput</th>
                        <th>Avg Latency</th>
                        <th>P95</th>
                        <th>Tool Avg (proxy)</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join([
                        '<tr>'
                        + f'<td>{_strategy_label(r.get("strategy", "unknown"))}</td>'
                        + f'<td>{r.get("summary", {}).get("success_rate", 0)}%</td>'
                        + f'<td>{r.get("summary", {}).get("throughput_rps", 0)}</td>'
                        + f'<td>{r.get("latency_ms", {}).get("avg", 0)}</td>'
                        + f'<td>{r.get("latency_ms", {}).get("p95", 0)}</td>'
                        + f'<td>{r.get("tool_latency_ms", {}).get("avg")}</td>'
                        + '</tr>'
                        if "error" not in r else
                        '<tr>'
                        + f'<td>{_strategy_label(r.get("strategy", "unknown"))}</td>'
                        + '<td colspan="5">ERROR: ' + str(r.get("error")) + '</td>'
                        + '</tr>'
                        for r in results
                    ])}
                </tbody>
            </table>
        </div>

        <div class=\"card\">
            <h3 style=\"margin-top:0\">Charts</h3>
            <div class=\"grid\">
                <div class=\"chart\"><canvas id=\"c1\"></canvas></div>
                <div class=\"chart\"><canvas id=\"c2\"></canvas></div>
                <div class=\"chart\"><canvas id=\"c3\"></canvas></div>
            </div>
        </div>
    </div>
    <script>
        const labels = {json.dumps(labels, ensure_ascii=False)};
        const throughput = {json.dumps(throughput, ensure_ascii=False)};
        const avgLatency = {json.dumps(avg_latency, ensure_ascii=False)};
        const p95Latency = {json.dumps(p95_latency, ensure_ascii=False)};
        const toolAvg = {json.dumps(tool_avg, ensure_ascii=False)};

        new Chart(document.getElementById('c1'), {{
            type: 'bar',
            data: {{ labels, datasets: [{{ label: 'Throughput (req/s)', data: throughput }}] }}
        }});

        new Chart(document.getElementById('c2'), {{
            type: 'bar',
            data: {{ labels, datasets: [{{ label: 'Avg Latency (ms)', data: avgLatency }}] }}
        }});

        new Chart(document.getElementById('c3'), {{
            type: 'bar',
            data: {{ labels, datasets: [
                {{ label: 'P95 (ms)', data: p95Latency }},
                {{ label: 'Tool Avg proxy (ms)', data: toolAvg }}
            ] }}
        }});
    </script>
</body>
</html>
"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


def write_js_data(output: Dict[str, Any], js_path: str) -> None:
    payload = json.dumps(output, ensure_ascii=False)
    content = f"window.COMPARE_REPORT = {payload};\n"
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(content)


def main() -> None:
    load_env_file()

    parser = argparse.ArgumentParser(description="Compare performance across Bing search implementations")
    parser.add_argument("--requests", type=int, default=20, help="Total request count per strategy")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent workers")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument("--warmup-requests", type=int, default=0, help="Warmup requests per strategy (excluded from stats)")
    parser.add_argument("--output-json", type=str, default="", help="Optional path to save result JSON")
    parser.add_argument("--output-html", type=str, default="", help="Optional path to save HTML report")
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show live progress logs on stderr (default: enabled)",
    )
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY, help="Test query")
    parser.add_argument(
        "--strategies",
        type=str,
        default="webiq,foundry_bing_grounding,foundry_web_search",
        help="Comma separated list: webiq,foundry_bing_grounding,foundry_web_search",
    )
    args = parser.parse_args()

    if args.requests <= 0:
        raise ValueError("--requests must be > 0")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be > 0")
    if args.warmup_requests < 0:
        raise ValueError("--warmup-requests must be >= 0")

    strategy_names = [s.strip() for s in args.strategies.split(",") if s.strip()]

    output: Dict[str, Any] = {
        "config": {
            "requests": args.requests,
            "concurrency": args.concurrency,
            "timeout_s": args.timeout,
            "warmup_requests": args.warmup_requests,
            "query": args.query,
            "strategies": strategy_names,
        },
        "results": [],
    }

    for name in strategy_names:
        try:
            log_progress(args.progress, f"starting strategy: {name}")
            strategy = build_strategy(name)
            report = run_strategy(
                strategy=strategy,
                total_requests=args.requests,
                concurrency=args.concurrency,
                query=args.query,
                timeout_s=args.timeout,
                warmup_requests=args.warmup_requests,
                show_progress=args.progress,
            )
            output["results"].append(report)
        except Exception as exc:
            output["results"].append(
                {
                    "strategy": name,
                    "error": str(exc),
                }
            )

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        log_progress(args.progress, f"json saved: {args.output_json}")

        sidecar_js = str(Path(args.output_json).with_suffix(".js"))
        write_js_data(output, sidecar_js)
        log_progress(args.progress, f"js data saved: {sidecar_js}")

    if args.output_html:
        write_html_report(output, args.output_html)
        log_progress(args.progress, f"html saved: {args.output_html}")

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
