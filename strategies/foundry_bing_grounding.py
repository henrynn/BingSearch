"""
Foundry Grounding with Bing Search strategy.

This strategy creates an Azure AI Foundry agent with the Bing grounding tool.
It adds agent orchestration and tool invocation on top of search, making it
suitable for scenarios where you want an explicit Bing grounding tool inside
a Foundry agent workflow.

Reference:
    https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/bing-tools

Required environment variables
--------------------------------
FOUNDRY_PROJECT_ENDPOINT        – Foundry project endpoint URL.
                                  Format: https://<resource>.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT_NAME   – Deployed model name inside the project.
BING_PROJECT_CONNECTION_NAME    – Bing grounding connection name (preferred).
BING_PROJECT_CONNECTION_ID      – Full connection resource ID (alternative to name).

Authentication
--------------
Uses DefaultAzureCredential.  Run `az login` or configure a service principal
before running.

Usage
-----
    from strategies.foundry_bing_grounding import FoundryBingGroundingStrategy

    strategy = FoundryBingGroundingStrategy()
    strategy.setup()           # creates agent; call once per benchmark run
    result = strategy.invoke(query="Latest AI trends", timeout_s=60)
    print(result)
    strategy.cleanup()         # deletes the agent
"""

import os
import time

from strategies.base import FoundryBaseStrategy, RequestResult


class FoundryBingGroundingStrategy(FoundryBaseStrategy):
    """
    Foundry agent backed by the Grounding with Bing Search tool.

    setup() creates a Foundry agent with BingGroundingTool configured.
    All requests reuse the same agent and, where supported, the same
    conversation to avoid per-request agent creation overhead.
    """

    name = "foundry_bing_grounding"

    def __init__(self) -> None:
        self.project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        self.model_deployment = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
        self.connection_id = os.getenv("BING_PROJECT_CONNECTION_ID")
        self.connection_name = os.getenv("BING_PROJECT_CONNECTION_NAME")

        if not self.project_endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required")
        if not self.model_deployment:
            raise ValueError("FOUNDRY_MODEL_DEPLOYMENT_NAME is required")
        if not self.connection_id and not self.connection_name:
            raise ValueError(
                "Either BING_PROJECT_CONNECTION_ID or BING_PROJECT_CONNECTION_NAME is required"
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

        # Resolve connection ID from name if only a name was provided.
        connection_id = self.connection_id
        if not connection_id and self.connection_name:
            connection = self._project.connections.get(self.connection_name)
            connection_id = connection.id

        self._agent = self._project.agents.create_version(
            agent_name=f"perf-grounding-{int(time.time())}",
            definition=PromptAgentDefinition(
                model=self.model_deployment,
                instructions="You are concise. Always use available search tools for real-time questions.",
                tools=[
                    BingGroundingTool(
                        bing_grounding=BingGroundingSearchToolParameters(
                            search_configurations=[
                                BingGroundingSearchConfiguration(
                                    project_connection_id=connection_id
                                )
                            ]
                        )
                    )
                ],
            ),
            description="Perf comparison agent – Grounding with Bing Search",
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
