"""
Foundry Web Search Tool strategy.

This strategy creates an Azure AI Foundry agent with the Web Search Tool.
It uses the Foundry web search abstraction inside the agent runtime and
represents the more general web-search integration path in Foundry – no
explicit Bing connection configuration is required.

Reference:
    https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-search

Required environment variables
--------------------------------
FOUNDRY_PROJECT_ENDPOINT        – Foundry project endpoint URL.
                                  Format: https://<resource>.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT_NAME   – Deployed model name inside the project.

Authentication
--------------
Uses DefaultAzureCredential.  Run `az login` or configure a service principal
before running.

Usage
-----
    from strategies.foundry_web_search import FoundryWebSearchStrategy

    strategy = FoundryWebSearchStrategy()
    strategy.setup()           # creates agent; call once per benchmark run
    result = strategy.invoke(query="Latest AI trends", timeout_s=60)
    print(result)
    strategy.cleanup()         # deletes the agent
"""

import os
import time

from strategies.base import FoundryBaseStrategy, RequestResult


class FoundryWebSearchStrategy(FoundryBaseStrategy):
    """
    Foundry agent backed by the Web Search Tool.

    setup() creates a Foundry agent with WebSearchTool enabled.
    All requests reuse the same agent and, where supported, the same
    conversation to avoid per-request agent creation overhead.
    """

    name = "foundry_web_search"

    def __init__(self) -> None:
        self.project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        self.model_deployment = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")

        if not self.project_endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required")
        if not self.model_deployment:
            raise ValueError("FOUNDRY_MODEL_DEPLOYMENT_NAME is required")

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
                "Missing dependency. Install: pip install azure-ai-projects azure-identity"
            ) from exc

        self._project = AIProjectClient(
            endpoint=self.project_endpoint,
            credential=DefaultAzureCredential(),
        )
        self._openai = self._project.get_openai_client()

        # Reuse a single conversation across all requests to avoid per-request
        # session overhead.  Falls back gracefully if conversations are unavailable.
        try:
            conversation = self._openai.conversations.create()
            self._conversation_id = conversation.id
        except Exception:
            self._conversation_id = None

        self._agent = self._project.agents.create_version(
            agent_name=f"perf-websearch-{int(time.time())}",
            definition=PromptAgentDefinition(
                model=self.model_deployment,
                instructions="You are concise. Always use available search tools for real-time questions.",
                tools=[WebSearchTool()],
            ),
            description="Perf comparison agent – Web Search Tool",
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
