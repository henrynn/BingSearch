# Bing Search Performance Comparison Report

> Web grounding built for the AI era — quality, speed, and token efficiency at frontier scale

## Background

This benchmark compares three Bing-backed search approaches under the same query and load settings. The goal is to understand the relative performance and behavior of a direct search API path versus two Foundry agent-based search integrations.

**Web IQ** is Microsoft’s state-of-the-art grounding service for AI agents and assistants. It returns ranked, citation-ready context across web, news, images, video, and more, and it’s built on Bing search infrastructure and re-architected for an era of LLMs and multi-step agents.

## Key Differences

| # | Strategy | Description |
|---|----------|-------------|
| 1 | **Web IQ** | Microsoft’s state-of-the-art grounding service. Returns ranked, citation-ready context across web, news, images, video, and more. Built on Bing search infrastructure and re-architected for LLMs and multi-step agents. |
| 2 | **Foundry Grounding+Bing** | Foundry agent with the Bing grounding tool. Adds agent orchestration and tool invocation on top of search, making it flexible for agent workflows. |
| 3 | **Foundry Web Search Tool** | Foundry agent using the web search abstraction. Represents the more general web-search integration path in Foundry. |

## Test Configuration

- **Date**: 2026-06-15
- **Query**: `Latest AI trends` 
- **Requests per strategy**: 1
- **Concurrency**: 1
- **Timeout**: 20.0s
- **Warmup requests**: 0

## Results

### Overall Metrics

| Strategy | Success Rate | Throughput (req/s) | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Max (ms) |
|----------|:-----------:|:-----------------:|--------:|--------:|--------:|--------:|--------:|
| Web IQ | 100.0% | 0.98 | 1019.87 | 1019.87 | 1019.87 | 1019.87 | 1019.87 |

### Tool Latency (proxy)

> For Foundry strategies, Tool Avg is the time to the first tool-related event in the response stream, not the raw backend tool execution time.

| Strategy | Samples | Tool Avg (ms) | Tool P95 (ms) | Tool P99 (ms) |
|----------|:-------:|-------------:|-------------:|-------------:|
| Web IQ | 1 | 1019.87 | 1019.87 | 1019.87 |

