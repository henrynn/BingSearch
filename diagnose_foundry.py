import os
import time
import traceback


def load_env_file(env_path: str = ".env") -> None:
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def log(msg: str) -> None:
    print(f"[diag] {msg}")


def main() -> None:
    load_env_file()

    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    model = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "")
    conn_name = os.getenv("BING_PROJECT_CONNECTION_NAME", "")

    log(f"endpoint={endpoint}")
    log(f"model={model}")
    log(f"connection_name={conn_name}")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import PromptAgentDefinition, WebSearchTool

        cred = DefaultAzureCredential()
        token = cred.get_token("https://cognitiveservices.azure.com/.default")
        log(f"DefaultAzureCredential token acquired, expires_on={token.expires_on}")

        project = AIProjectClient(endpoint=endpoint, credential=cred)
        openai = project.get_openai_client()
        log("AIProjectClient initialized")

        if conn_name:
            c = project.connections.get(conn_name)
            log(f"Connection resolved by name. id={c.id}")

        # Step 1: no-tool baseline
        log("Creating baseline agent (no tools)...")
        a1 = project.agents.create_version(
            agent_name=f"diag-baseline-{int(time.time())}",
            definition=PromptAgentDefinition(
                model=model,
                instructions="You are concise.",
                tools=[],
            ),
            description="diag baseline",
        )
        log(f"Baseline agent created: {a1.name} v{a1.version}")

        t0 = time.perf_counter()
        r1 = openai.responses.create(
            stream=False,
            input="Reply with exactly: ok",
            timeout=60,
            extra_body={"agent_reference": {"name": a1.name, "type": "agent_reference"}},
        )
        t1 = (time.perf_counter() - t0) * 1000
        log(f"Baseline response ok in {t1:.2f} ms; has_output={bool(getattr(r1, 'output_text', None))}")

        project.agents.delete_version(agent_name=a1.name, agent_version=a1.version)
        log("Baseline agent deleted")

        # Step 2: web search tool
        log("Creating web search agent...")
        a2 = project.agents.create_version(
            agent_name=f"diag-websearch-{int(time.time())}",
            definition=PromptAgentDefinition(
                model=model,
                instructions="You are concise and use web search for up-to-date questions.",
                tools=[WebSearchTool()],
            ),
            description="diag websearch",
        )
        log(f"Web search agent created: {a2.name} v{a2.version}")

        t0 = time.perf_counter()
        r2 = openai.responses.create(
            stream=False,
            tool_choice="required",
            input="What are today's top AI news headlines?",
            timeout=60,
            extra_body={"agent_reference": {"name": a2.name, "type": "agent_reference"}},
        )
        t2 = (time.perf_counter() - t0) * 1000
        log(f"Web search response ok in {t2:.2f} ms; has_output={bool(getattr(r2, 'output_text', None))}")

        project.agents.delete_version(agent_name=a2.name, agent_version=a2.version)
        log("Web search agent deleted")

        log("Diagnosis completed successfully")
    except Exception as exc:
        log(f"ERROR type={type(exc).__name__}")
        log(f"ERROR message={exc}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
