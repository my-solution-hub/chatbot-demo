"""Strands Agent entrypoint for AgentCore Runtime.

This agent uses Nova Sonic (BidiAgent) for voice interactions and connects
to the AgentCore Gateway (MCP protocol) for tool access.

AgentCore Runtime requires:
- /invocations POST endpoint
- /ping GET endpoint
- Port 8080
- linux/arm64 platform
"""

from __future__ import annotations

import json
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("strands_agent")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")

SYSTEM_PROMPT = """You are a helpful voice assistant for a smart cat care system.
You can help users with:
- Looking up cat profiles and information
- Checking feeding schedules and recording feedings
- Monitoring health metrics and alerts
- Managing IoT devices (feeders, cameras, sensors)

Always be friendly, concise, and helpful. When you use tools, summarize the
results in a natural conversational way.

If the user asks about the current time or weather, use the appropriate tools.
"""

# ---------------------------------------------------------------------------
# Tools — loaded from MCP Gateway if URL is provided, otherwise use built-in
# ---------------------------------------------------------------------------


def _build_tools():
    """Build the tool list based on available configuration."""
    tools = []

    if MCP_SERVER_URL:
        # Connect to AgentCore Gateway via MCP
        from strands.tools.mcp import MCPClient

        logger.info("Connecting to MCP Gateway: %s", MCP_SERVER_URL)
        mcp_client = MCPClient(url=MCP_SERVER_URL)
        tools.append(mcp_client)
    else:
        # Fallback: use built-in tools for local testing
        from strands_tools import current_time

        tools.append(current_time)
        logger.info("No MCP_SERVER_URL configured, using built-in tools only")

    return tools


# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------

model = BedrockModel(
    model_id=MODEL_ID,
    region_name=AWS_REGION,
)

tools = _build_tools()

agent = Agent(
    model=model,
    tools=tools,
    system_prompt=SYSTEM_PROMPT,
)

# ---------------------------------------------------------------------------
# AgentCore Runtime app
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Process a user request and return the agent response.

    Expected payload:
        {"prompt": "user message here"}

    Returns:
        {"result": "agent response text", "session_id": "..."}
    """
    prompt = payload.get("prompt", "")
    if not prompt:
        return {
            "error": "No prompt provided. Send {'prompt': 'your message'}"
        }

    logger.info("Processing prompt: %s", prompt[:100])

    try:
        result = agent(prompt)
        response = {
            "result": result.message,
            "model": MODEL_ID,
        }
        logger.info("Response generated successfully")
        return response
    except Exception as exc:
        logger.error("Agent invocation failed: %s", exc)
        return {"error": f"Agent error: {type(exc).__name__}: {str(exc)[:500]}"}


if __name__ == "__main__":
    app.run()
