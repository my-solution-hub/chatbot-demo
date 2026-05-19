"""Tests for the session manager factory function.

Verifies that create_session_manager returns the correct session manager
type based on deployment mode configuration.
"""

from __future__ import annotations

import pytest

from nova_sonic_demo.deployment_config import DeploymentConfig
from nova_sonic_demo.web.session_factory import create_session_manager
from nova_sonic_demo.web.session_manager import SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_send_text(msg: str) -> None:
    pass


async def _noop_send_bytes(data: bytes) -> None:
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateSessionManagerLocal:
    """Tests for local mode."""

    def test_local_mode_returns_session_manager(self) -> None:
        config = DeploymentConfig(mode="local", region="us-east-1")
        manager = create_session_manager(_noop_send_text, _noop_send_bytes, config)
        assert isinstance(manager, SessionManager)

    def test_local_mode_manager_starts_in_ready_state(self) -> None:
        config = DeploymentConfig(mode="local", region="us-east-1")
        manager = create_session_manager(_noop_send_text, _noop_send_bytes, config)
        assert manager.state == "ready"


class TestCreateSessionManagerCloud:
    """Tests for cloud mode."""

    def test_cloud_mode_returns_agentcore_session_manager(self) -> None:
        from nova_sonic_demo.web.agentcore_session_manager import (
            AgentCoreSessionManager,
        )

        config = DeploymentConfig(
            mode="cloud",
            region="us-east-1",
            agent_id="test-agent-id",
            agent_alias_id="test-alias-id",
        )
        manager = create_session_manager(_noop_send_text, _noop_send_bytes, config)
        assert isinstance(manager, AgentCoreSessionManager)

    def test_cloud_mode_manager_starts_in_ready_state(self) -> None:
        config = DeploymentConfig(
            mode="cloud",
            region="us-east-1",
            agent_id="test-agent-id",
            agent_alias_id="test-alias-id",
        )
        manager = create_session_manager(_noop_send_text, _noop_send_bytes, config)
        assert manager.state == "ready"
