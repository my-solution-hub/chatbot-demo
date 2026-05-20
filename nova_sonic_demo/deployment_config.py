"""Deployment configuration for local vs. cloud mode.

Reads environment variables and produces a validated, immutable
:class:`DeploymentConfig` dataclass. The ``load_config()`` factory is the
primary entry point for application startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeploymentConfig:
    """Immutable deployment configuration.

    Attributes:
        mode: Either ``"local"`` or ``"cloud"``.
        region: AWS region name (e.g. ``"us-east-1"``).
        agent_id: Bedrock AgentCore agent ID (required in cloud mode).
        agent_alias_id: Bedrock AgentCore agent alias ID (required in cloud mode).
        agentcore_endpoint: Optional endpoint override for testing.
        strands_runtime_arn: AgentCore Runtime ARN (alternative to agent_id/alias).
    """

    mode: str
    region: str
    agent_id: Optional[str] = None
    agent_alias_id: Optional[str] = None
    agentcore_endpoint: Optional[str] = None
    strands_runtime_arn: Optional[str] = None

    def validate(self) -> None:
        """Validate configuration consistency.

        Raises:
            ValueError: If *mode* is not ``"local"`` or ``"cloud"``.
        """
        if self.mode not in ("local", "cloud"):
            raise ValueError(
                f"DEPLOYMENT_MODE must be 'local' or 'cloud', got '{self.mode}'"
            )
        # Cloud mode no longer strictly requires agent_id/agent_alias_id
        # because the primary path uses Nova Sonic + remote Lambda dispatch.
        # strands_runtime_arn and agent_id are optional for future use.


def load_config() -> DeploymentConfig:
    """Load deployment configuration from environment variables.

    Environment variables read:
        DEPLOYMENT_MODE: ``"local"`` (default) or ``"cloud"``.
        AGENT_ID: Bedrock AgentCore agent identifier.
        AGENT_ALIAS_ID: Bedrock AgentCore agent alias identifier.
        AWS_REGION: AWS region (defaults to ``"ap-northeast-1"``).

    Returns:
        A validated :class:`DeploymentConfig` instance.

    Raises:
        ValueError: If the resulting configuration is invalid.
    """
    config = DeploymentConfig(
        mode=os.environ.get("DEPLOYMENT_MODE", "local"),
        region=os.environ.get("AWS_REGION", "ap-northeast-1"),
        agent_id=os.environ.get("AGENT_ID") or None,
        agent_alias_id=os.environ.get("AGENT_ALIAS_ID") or None,
        strands_runtime_arn=os.environ.get("STRANDS_RUNTIME_ARN") or None,
    )
    config.validate()
    return config
