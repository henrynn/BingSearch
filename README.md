# Bing Search Integration Guide

This repository shows three ways to integrate Bing-backed search into an AI agent workflow and compare their performance:

- Grounding with Bing Search
- Web Search Tool
- Web IQ

The benchmark outputs and charts are stored under [report](report), and the current comparison dashboard is [report/perf_report_2026-06-15.html](report/perf_report_2026-06-15.html).

## Why Search Integration Matters

Search gives an agent access to up-to-date, citation-ready context that is not available in the model weights alone. In practice, search can be used to:

- ground answers in current public information
- provide citations and source links
- improve factual correctness for news, product, and policy questions
- support multi-step agent workflows that need fresh context

## The Three Options

### 1. Grounding with Bing Search

Grounding with Bing Search is the Bing-native grounding path for Foundry agents. It is best when you want an explicit Bing grounding tool inside an agent workflow.

Use this when:

- you want a Foundry agent to invoke Bing grounding directly
- you need citation-ready context from web search
- you want an agent-style search flow with Bing configuration control

High-level setup:

1. Create a Bing Grounding resource.
2. Add it as a project connection in Foundry.
3. Create an agent with the Bing grounding tool.
4. Send a user query through the agent runtime.

Reference:
- https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/bing-tools?tabs=prompt-agents&pivots=python

### 2. Web Search Tool

Web Search Tool is the Foundry search abstraction for agents. It provides web-grounded context without requiring you to build a Bing-specific grounding configuration in the same way.

Use this when:

- you want an agent-native web search experience
- you want a simpler search integration path inside Foundry
- you want to compare Foundry search behavior with Bing grounding

High-level setup:

1. Create a Foundry project and model deployment.
2. Enable the Web Search Tool on the agent.
3. Create the agent and send a query through the responses API.
4. Use returned citations in the agent response.

Reference:
- https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-search?tabs=prompt-agents&pivots=python

### 3. Web IQ

Web IQ is Microsoft’s state-of-the-art grounding service for AI agents and assistants. It returns ranked, citation-ready context across web, news, images, video, and more, and it is built on Bing search infrastructure and re-architected for an era of LLMs and multi-step agents.

Use this when:

- you want a direct search grounding service for agent or assistant experiences
- you want ranked, citation-ready context that can be injected into the model context window
- you want to compare direct search-style grounding with Foundry-based options

Reference:
- https://www.microsoft.com/en-us/webiq

## How the Integration Works

A typical search-enabled agent flow looks like this:

1. The user asks a question that needs fresh information.
2. The agent decides whether search is needed.
3. The search capability retrieves relevant web context.
4. The model uses that context to produce a grounded response.
5. The response includes citations or source references where supported.

The main design difference between the three options is where search is handled:

- **Grounding with Bing Search**: Bing grounding is explicitly configured as a tool in the agent.
- **Web Search Tool**: search is exposed as a Foundry agent tool with a simpler abstraction.
- **Web IQ**: search is provided as a grounding service that returns ranked, citation-ready context.

## Configuration Summary

### Grounding with Bing Search

Required pieces:

- Foundry project endpoint
- model deployment name
- Bing project connection
- Azure credentials for the project

Typical workflow:

- create the Bing resource
- connect it to the project
- resolve the connection in code
- create the agent with the grounding tool

### Web Search Tool

Required pieces:

- Foundry project endpoint
- model deployment name
- Azure credentials for the project

Typical workflow:

- create the agent with the web search tool
- optionally configure user location or custom search behavior
- run the query through the agent runtime

### Web IQ

Required pieces depend on the specific API or product surface you are using, but the core idea is the same:

- use Web IQ as the grounding layer
- pass the retrieved context into the agent or assistant workflow
- render citations or ranked context in your application

## Performance Report

This repository includes a comparison report generated from the benchmark script.

- Metrics data: [report/compare_20x1.json](report/compare_20x1.json)
- Report dashboard: [report/perf_report_2026-06-15.html](report/perf_report_2026-06-15.html)

The report is set up to compare:

- Web IQ
- Grounding with Bing Search
- Web Search Tool

## Repository Files

- [compare_perf.py](compare_perf.py): benchmark runner and report generator
- [perf_test.py](perf_test.py): direct Web IQ benchmark script
- [diagnose_foundry.py](diagnose_foundry.py): Foundry connectivity and search diagnostics
- [report](report): generated benchmark outputs

## Notes on the Web IQ Performance Image

If you have a separate Web IQ performance image, place it in the report folder and reference it here. This README intentionally avoids naming a missing image file so the repository stays valid as-is.

## Suggested Next Step

Run the benchmark, then open the HTML dashboard in the report folder and compare total latency, throughput, and the tool-latency proxy side by side.
