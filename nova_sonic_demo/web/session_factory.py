"""Session manager factory — selects local or cloud session manager.

Based on the :class:`~nova_sonic_demo.deployment_config.DeploymentConfig`,
returns either the existing :class:`SessionManager` (local mode) or an
:class:`AgentCoreSessionManager` (cloud mode).

Requirements: 1.1, 1.2, 1.6
"""

from __future__ import annotations

from typing import Awaitable, Callable, Union

from nova_sonic_demo.deployment_config import DeploymentConfig
from nova_sonic_demo.web.session_manager import SessionManager


def create_session_manager(
    send_text: Callable[[str], Awaitable[None]],
    send_bytes: Callable[[bytes], Awaitable[None]],
    config: DeploymentConfig,
) -> Union[SessionManager, "AgentCoreSessionManager"]:  # noqa: F821
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
    SessionManager or AgentCoreSessionManager
        A session manager in the ``"ready"`` state.

    Raises
    ------
    NotImplementedError
        If ``config.mode == "cloud"`` and AgentCoreSessionManager is not yet
        available.
    """
    if config.mode == "local":
        return SessionManager(send_text, send_bytes)

    if config.mode == "cloud":
        # AgentCoreSessionManager will be implemented in task 2.1.
        # For now, raise NotImplementedError to signal the placeholder.
        try:
            from nova_sonic_demo.web.agentcore_session_manager import (
                AgentCoreSessionManager,
            )

            return AgentCoreSessionManager(
                send_text,
                send_bytes,
                agent_id=config.agent_id,
                agent_alias_id=config.agent_alias_id,
                region=config.region,
            )
        except ImportError:
            raise NotImplementedError(
                "AgentCoreSessionManager is not yet implemented. "
                "Cloud mode will be available after task 2.1 is complete."
            )

    # Should not be reachable if config.validate() was called
    raise ValueError(f"Unknown deployment mode: {config.mode}")
