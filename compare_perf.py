"""
Bing Search Performance Benchmark
==================================
Compares three search integration approaches under identical load settings:

  - webiq                  Web IQ direct API
  - foundry_bing_grounding Foundry agent + Grounding with Bing Search tool
  - foundry_web_search     Foundry agent + Web Search Tool

Each strategy is implemented as a standalone, importable module under the
strategies/ package so they can be studied, reused, or extended independently.

Usage
-----
    python compare_perf.py \\
        --requests 20 --concurrency 1 --timeout 60 \\
        --strategies webiq,foundry_bing_grounding,foundry_web_search \\
        --output-json report/compare_20x1.json \\
        --output-html report/perf_report.html
"""

import argparse
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from strategies import RequestResult, Strategy, build_strategy
from strategies.base import load_env_file as _load_env_file


DEFAULT_QUERY = "Latest AI trends"
DEFAULT_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env_file(env_path: str = ".env") -> None:
    """Load .env from a given path into os.environ (skip already-set keys)."""
    _load_env_file(env_path)


def log_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[progress] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def summarize(total_requests: int, elapsed_s: float, results: List[RequestResult]) -> Dict[str, Any]:
    latencies = sorted(r.latency_ms for r in results)
    tool_latencies = sorted(r.tool_latency_ms for r in results if r.tool_latency_ms is not None)
    ok_count = sum(1 for r in results if r.ok)

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
            "failure": total_requests - ok_count,
            "success_rate": round((ok_count / total_requests) * 100, 2) if total_requests else 0,
        },
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0,
            "avg": round(statistics.mean(latencies), 2) if latencies else 0,
            "p50": round(_percentile(latencies, 0.50), 2) if latencies else 0,
            "p95": round(_percentile(latencies, 0.95), 2) if latencies else 0,
            "p99": round(_percentile(latencies, 0.99), 2) if latencies else 0,
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "tool_latency_ms": {
            "available_samples": len(tool_latencies),
            "min": round(min(tool_latencies), 2) if tool_latencies else None,
            "avg": round(statistics.mean(tool_latencies), 2) if tool_latencies else None,
            "p50": round(_percentile(tool_latencies, 0.50), 2) if tool_latencies else None,
            "p95": round(_percentile(tool_latencies, 0.95), 2) if tool_latencies else None,
            "p99": round(_percentile(tool_latencies, 0.99), 2) if tool_latencies else None,
            "max": round(max(tool_latencies), 2) if tool_latencies else None,
            "note": (
                "Proxy metric. For Foundry strategies this is time-to-first-tool-event "
                "in the stream, not raw backend tool execution time."
            ),
        },
        "status_distribution": status_counter,
        "top_errors": sorted(error_counter.items(), key=lambda x: x[1], reverse=True)[:5],
    }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

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

        for idx in range(max(0, warmup_requests)):
            try:
                strategy.invoke(query=query, timeout_s=timeout_s)
                log_progress(show_progress, f"{strategy.name}: warmup {idx + 1}/{warmup_requests} done")
            except Exception:
                log_progress(show_progress, f"{strategy.name}: warmup {idx + 1}/{warmup_requests} failed")

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


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def _strategy_label(name: str) -> str:
    return {
        "webiq": "Web IQ",
        "foundry_bing_grounding": "Foundry Grounding+Bing",
        "foundry_web_search": "Foundry Web Search Tool",
    }.get(name, name)


def write_html_report(output: Dict[str, Any], html_path: str) -> None:
    config = output.get("config", {})
    results = output.get("results", [])

    chart_rows: List[Dict[str, Any]] = [r for r in results if isinstance(r, dict) and "error" not in r]
    labels = [_strategy_label(r.get("strategy", "unknown")) for r in chart_rows]
    throughput = [r.get("summary", {}).get("throughput_rps", 0) for r in chart_rows]
    avg_latency = [r.get("latency_ms", {}).get("avg", 0) for r in chart_rows]
    p95_latency = [r.get("latency_ms", {}).get("p95", 0) for r in chart_rows]
    tool_avg = [r.get("tool_latency_ms", {}).get("avg") for r in chart_rows]

    tool_rows_html = "".join(
        (
            "<tr>"
            f'<td>{_strategy_label(r.get("strategy", "unknown"))}</td>'
            f'<td style="text-align:center">{r.get("tool_latency_ms", {}).get("available_samples", 0)}</td>'
            f'<td style="text-align:right">{r.get("tool_latency_ms", {}).get("avg") or "—"}</td>'
            f'<td style="text-align:right">{r.get("tool_latency_ms", {}).get("p95") or "—"}</td>'
            f'<td style="text-align:right">{r.get("tool_latency_ms", {}).get("p99") or "—"}</td>'
            "</tr>"
        )
        for r in results
        if isinstance(r, dict) and "error" not in r
    )

    rows_html = "".join(
        (
            "<tr>"
            f'<td>{_strategy_label(r.get("strategy", "unknown"))}</td>'
            f'<td>{r.get("summary", {}).get("success_rate", 0)}%</td>'
            f'<td>{r.get("summary", {}).get("throughput_rps", 0)}</td>'
            f'<td>{r.get("latency_ms", {}).get("avg", 0)}</td>'
            f'<td>{r.get("latency_ms", {}).get("p95", 0)}</td>'
            f'<td>{r.get("latency_ms", {}).get("p99", 0)}</td>'
            "</tr>"
            if "error" not in r
            else (
                "<tr>"
                f'<td>{_strategy_label(r.get("strategy", "unknown"))}</td>'
                f'<td colspan="5">ERROR: {r.get("error")}</td>'
                "</tr>"
            )
        )
        for r in results
    )

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Performance Compare Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #f5f7fb; --card: #ffffff; --line: #d0d5dd;
            --text: #1f2937; --muted: #475467;
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
        ol li {{ margin-bottom: 6px; }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="card">
            <h2 style="margin-top:0">Bing Search Performance Compare</h2>
            <div class="meta">
                <span class="pill">requests: {config.get("requests")}</span>
                <span class="pill">concurrency: {config.get("concurrency")}</span>
                <span class="pill">timeout_s: {config.get("timeout_s")}</span>
                <span class="pill">warmup_requests: {config.get("warmup_requests")}</span>
            </div>
            <p style="color:var(--muted)">query: {config.get("query", "")}</p>
        </div>

        <div class="card">
            <h3 style="margin-top:0">Background</h3>
            <p>This benchmark compares three Bing-backed search approaches under the same query and load settings. The goal is to understand the relative performance and behavior of a direct search API path versus two Foundry agent-based search integrations.</p>
            <p><strong>Web IQ</strong> is Microsoft&#x2019;s state-of-the-art grounding service for AI agents and assistants. It returns ranked, citation-ready context across web, news, images, video, and more, and it&#x2019;s built on Bing search infrastructure and re-architected for an era of LLMs and multi-step agents.</p>
            <p>All runs use the same request count, concurrency, timeout, and no warmup by default, so the results are comparable at the system level.</p>
            <h3>Key Differences</h3>
            <ol>
                <li><strong>Web IQ</strong> &#x2013; Microsoft&#x2019;s state-of-the-art grounding service. Returns ranked, citation-ready context across web, news, images, video, and more. Built on Bing search infrastructure and re-architected for LLMs and multi-step agents.</li>
                <li><strong>Foundry Grounding+Bing</strong> &#x2013; Foundry agent with the Bing grounding tool. Adds agent orchestration and tool invocation on top of search, making it flexible for agent workflows.</li>
                <li><strong>Foundry Web Search Tool</strong> &#x2013; Foundry agent using the web search abstraction. Represents the more general web-search integration path in Foundry.</li>
            </ol>
        </div>

        <div class="card">
            <h3 style="margin-top:0">Tool Latency (proxy)</h3>
            <p style="color:var(--muted);font-size:13px;margin-top:0">For Foundry strategies, Tool Avg is the time to the first tool-related event in the response stream, not the raw backend tool execution time.</p>
            <table>
                <thead>
                    <tr>
                        <th>Strategy</th>
                        <th style="text-align:center">Samples</th>
                        <th style="text-align:right">Tool Avg (ms)</th>
                        <th style="text-align:right">Tool P95 (ms)</th>
                        <th style="text-align:right">Tool P99 (ms)</th>
                    </tr>
                </thead>
                <tbody>{tool_rows_html}</tbody>
            </table>
        </div>

        <div class="card">
            <h3 style="margin-top:0">Metrics</h3>
            <table>
                <thead>
                    <tr>
                        <th>Strategy</th><th>Success Rate</th><th>Throughput (req/s)</th>
                        <th>Avg Latency (ms)</th><th>P95 (ms)</th><th>P99 (ms)</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>

        <div class="card">
            <h3 style="margin-top:0">Charts</h3>
            <div class="grid">
                <div class="chart"><canvas id="c1"></canvas></div>
                <div class="chart"><canvas id="c2"></canvas></div>
                <div class="chart"><canvas id="c3"></canvas></div>
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
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(f"window.COMPARE_REPORT = {payload};\n")


def write_md_report(output: Dict[str, Any], md_path: str) -> None:
    cfg = output.get("config", {})
    results = output.get("results", [])
    import datetime

    lines: List[str] = []
    lines.append("# Bing Search Performance Comparison Report")
    lines.append("")
    lines.append("> Web grounding built for the AI era — quality, speed, and token efficiency at frontier scale")
    lines.append("")

    lines.append("## Background")
    lines.append("")
    lines.append(
        "This benchmark compares three Bing-backed search approaches under the same query and load settings. "
        "The goal is to understand the relative performance and behavior of a direct search API path versus "
        "two Foundry agent-based search integrations."
    )
    lines.append("")
    lines.append(
        "**Web IQ** is Microsoft\u2019s state-of-the-art grounding service for AI agents and assistants. "
        "It returns ranked, citation-ready context across web, news, images, video, and more, and it\u2019s "
        "built on Bing search infrastructure and re-architected for an era of LLMs and multi-step agents."
    )
    lines.append("")

    lines.append("## Key Differences")
    lines.append("")
    lines.append("| # | Strategy | Description |")
    lines.append("|---|----------|-------------|")
    lines.append(
        "| 1 | **Web IQ** | "
        "Microsoft\u2019s state-of-the-art grounding service. Returns ranked, citation-ready context across "
        "web, news, images, video, and more. Built on Bing search infrastructure and re-architected for LLMs "
        "and multi-step agents. |"
    )
    lines.append(
        "| 2 | **Foundry Grounding+Bing** | "
        "Foundry agent with the Bing grounding tool. Adds agent orchestration and tool invocation on top of "
        "search, making it flexible for agent workflows. |"
    )
    lines.append(
        "| 3 | **Foundry Web Search Tool** | "
        "Foundry agent using the web search abstraction. Represents the more general web-search integration "
        "path in Foundry. |"
    )
    lines.append("")

    lines.append("## Test Configuration")
    lines.append("")
    lines.append(f"- **Date**: {datetime.date.today()}")
    lines.append(f"- **Query**: `{cfg.get('query', '')}` ")
    lines.append(f"- **Requests per strategy**: {cfg.get('requests')}")
    lines.append(f"- **Concurrency**: {cfg.get('concurrency')}")
    lines.append(f"- **Timeout**: {cfg.get('timeout_s')}s")
    lines.append(f"- **Warmup requests**: {cfg.get('warmup_requests', 0)}")
    lines.append("")

    lines.append("## Results")
    lines.append("")
    lines.append("### Overall Metrics")
    lines.append("")
    lines.append("| Strategy | Success Rate | Throughput (req/s) | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Max (ms) |")
    lines.append("|----------|:-----------:|:-----------------:|--------:|--------:|--------:|--------:|--------:|")
    for r in results:
        label = _strategy_label(r.get("strategy", "unknown"))
        if "error" in r:
            lines.append(f"| {label} | \u2717 | — | — | — | — | — | ERROR: {r['error']} |")
        else:
            s = r.get("summary", {})
            lat = r.get("latency_ms", {})
            lines.append(
                f"| {label} "
                f"| {s.get('success_rate', 0)}% "
                f"| {s.get('throughput_rps', 0)} "
                f"| {lat.get('avg', 0)} "
                f"| {lat.get('p50', 0)} "
                f"| {lat.get('p95', 0)} "
                f"| {lat.get('p99', 0)} "
                f"| {lat.get('max', 0)} |"
            )
    lines.append("")

    lines.append("### Tool Latency (proxy)")
    lines.append("")
    lines.append(
        "> For Foundry strategies, Tool Avg is the time to the first tool-related event in the "
        "response stream, not the raw backend tool execution time."
    )
    lines.append("")
    lines.append("| Strategy | Samples | Tool Avg (ms) | Tool P95 (ms) | Tool P99 (ms) |")
    lines.append("|----------|:-------:|-------------:|-------------:|-------------:|")
    for r in results:
        if "error" in r:
            continue
        label = _strategy_label(r.get("strategy", "unknown"))
        tl = r.get("tool_latency_ms", {})
        lines.append(
            f"| {label} "
            f"| {tl.get('available_samples', 0)} "
            f"| {tl.get('avg') or '—'} "
            f"| {tl.get('p95') or '—'} "
            f"| {tl.get('p99') or '—'} |"
        )
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    load_env_file()

    parser = argparse.ArgumentParser(
        description="Compare performance across Bing search implementations"
    )
    parser.add_argument("--requests", type=int, default=20, help="Total request count per strategy")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent workers")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument("--warmup-requests", type=int, default=0, help="Warmup requests per strategy (excluded from stats)")
    parser.add_argument("--output-json", type=str, default="", help="Optional path to save result JSON")
    parser.add_argument("--output-html", type=str, default="", help="Optional path to save HTML report")
    parser.add_argument("--output-md", type=str, default="", help="Optional path to save Markdown report")
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show live progress logs on stderr",
    )
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY, help="Test query")
    parser.add_argument(
        "--strategies",
        type=str,
        default="webiq,foundry_bing_grounding,foundry_web_search",
        help="Comma-separated list of strategies to run",
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
            output["results"].append({"strategy": name, "error": str(exc)})

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

    if args.output_md:
        write_md_report(output, args.output_md)
        log_progress(args.progress, f"md saved: {args.output_md}")

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
