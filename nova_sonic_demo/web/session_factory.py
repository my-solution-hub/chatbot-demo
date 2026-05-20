"""Session manager factory — selects local or cloud session manager.

Based on the :class:`~nova_sonic_demo.deployment_config.DeploymentConfig`,
returns either:
- Local mode: SessionManager with local tool dispatch
- Cloud mode: SessionManager with remote tool dispatch (Lambda via boto3)

Both modes use Nova Sonic for bidirectional audio streaming. The difference
is only in how tool calls are dispatched:
- Local: in-process tool handlers (get_current_time, get_weather)
- Cloud: Lambda invocations (same functions deployed by CDK AgentStack)

Requirements: 1.1, 1.2, 1.6
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable, Union

from nova_sonic_demo.deployment_config import DeploymentConfig
from nova_sonic_demo.web.session_manager import SessionManager


def create_session_manager(
    send_text: Callable[[str], Awaitable[None]],
    send_bytes: Callable[[bytes], Awaitable[None]],
    config: DeploymentConfig,
) -> SessionManager:
    """Factory that returns the appropriate session manager based on mode.

    Parameters
    ----------
    send_text:
        Async callable to send a JSON text message to the WebSocket client.
    send_bytes:
        Async callable to send binary data to the WebSocket client.
    config:
        Validated deployment configuration.

    Returns
    -------
    SessionManager
        A session manager in the ``"ready"`` state. In cloud mode, it uses
        a RemoteToolDispatcher that calls Lambda functions for tool execution.
    """
    if config.mode == "local":
        return SessionManager(send_text, send_bytes)

    if config.mode == "cloud":
        # Cloud mode: same Nova Sonic audio path, but tools dispatched via Lambda
        from nova_sonic_demo.tools.remote_dispatcher import RemoteToolDispatcher
        from nova_sonic_demo.tools.registry import build_default_registry

        # Tool → Lambda function mapping from environment variables
        # These are set by CDK (ComputeStack) from AgentStack outputs
        tool_lambda_map = _build_tool_lambda_map()

        def _cloud_dispatcher_factory(registry, logger_instance):
            return RemoteToolDispatcher(
                tool_lambda_map=tool_lambda_map,
                logger_instance=logger_instance,
                region=config.region,
            )

        return SessionManager(
            send_text,
            send_bytes,
            dispatcher_factory=_cloud_dispatcher_factory,
        )

    # Should not be reachable if config.validate() was called
    raise ValueError(f"Unknown deployment mode: {config.mode}")


def _build_tool_lambda_map() -> dict[str, str]:
    """Build tool name → Lambda ARN/name mapping from environment variables.

    Environment variables:
        TOOL_LAMBDA_TIME: ARN or name of the get_current_time Lambda
        TOOL_LAMBDA_WEATHER: ARN or name of the get_weather Lambda

    Falls back to function names if env vars are not set (for local testing
    of cloud mode without actual Lambda functions).
    """
    return {
        "get_current_time": os.environ.get(
            "TOOL_LAMBDA_TIME", "chatbot-demo-get-current-time"
        ),
        "get_weather": os.environ.get(
            "TOOL_LAMBDA_WEATHER", "chatbot-demo-get-weather"
        ),
    }
