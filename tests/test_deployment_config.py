"""Unit tests for ``nova_sonic_demo.deployment_config``.

Covers:

* DeploymentConfig validation logic for both modes.
* load_config() reading from environment variables with defaults.
* Edge cases: empty strings, missing env vars, invalid modes.
"""

from __future__ import annotations

import pytest

from nova_sonic_demo.deployment_config import DeploymentConfig, load_config


# ---------------------------------------------------------------------------
# DeploymentConfig.validate() — mode validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    """Validate that mode must be 'local' or 'cloud'."""

    def test_local_mode_valid(self):
        cfg = DeploymentConfig(mode="local", region="us-east-1")
        cfg.validate()  # Should not raise

    def test_cloud_mode_valid_with_agent_fields(self):
        cfg = DeploymentConfig(
            mode="cloud",
            region="us-east-1",
            agent_id="AGENT123",
            agent_alias_id="ALIAS456",
        )
        cfg.validate()  # Should not raise

    def test_cloud_mode_valid_without_agent_fields(self):
        """Cloud mode no longer requires agent_id — uses Nova Sonic + remote Lambda."""
        cfg = DeploymentConfig(
            mode="cloud",
            region="us-east-1",
        )
        cfg.validate()  # Should not raise

    def test_cloud_mode_valid_with_runtime_arn(self):
        cfg = DeploymentConfig(
            mode="cloud",
            region="us-east-1",
            strands_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456:runtime/test",
        )
        cfg.validate()  # Should not raise

    @pytest.mark.parametrize("bad_mode", ["", "LOCAL", "Cloud", "production", "dev", "test"])
    def test_invalid_mode_raises(self, bad_mode):
        cfg = DeploymentConfig(mode=bad_mode, region="us-east-1")
        with pytest.raises(ValueError, match="DEPLOYMENT_MODE must be 'local' or 'cloud'"):
            cfg.validate()


# ---------------------------------------------------------------------------
# DeploymentConfig — frozen dataclass
# ---------------------------------------------------------------------------


class TestFrozenDataclass:
    """DeploymentConfig is immutable."""

    def test_cannot_mutate_mode(self):
        cfg = DeploymentConfig(mode="local", region="us-east-1")
        with pytest.raises(AttributeError):
            cfg.mode = "cloud"  # type: ignore[misc]

    def test_cannot_mutate_agent_id(self):
        cfg = DeploymentConfig(mode="cloud", region="us-east-1", agent_id="X", agent_alias_id="Y")
        with pytest.raises(AttributeError):
            cfg.agent_id = "Z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_config() — environment variable loading
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """load_config() reads from environment and validates."""

    def test_defaults_to_local_mode(self, monkeypatch):
        monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_ALIAS_ID", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("STRANDS_RUNTIME_ARN", raising=False)

        cfg = load_config()
        assert cfg.mode == "local"
        assert cfg.region == "ap-northeast-1"
        assert cfg.agent_id is None
        assert cfg.agent_alias_id is None

    def test_reads_cloud_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
        monkeypatch.setenv("AGENT_ID", "MY_AGENT")
        monkeypatch.setenv("AGENT_ALIAS_ID", "MY_ALIAS")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        monkeypatch.delenv("STRANDS_RUNTIME_ARN", raising=False)

        cfg = load_config()
        assert cfg.mode == "cloud"
        assert cfg.region == "us-west-2"
        assert cfg.agent_id == "MY_AGENT"
        assert cfg.agent_alias_id == "MY_ALIAS"

    def test_reads_cloud_mode_without_agent_fields(self, monkeypatch):
        """Cloud mode works without agent_id (uses Nova Sonic + remote Lambda)."""
        monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_ALIAS_ID", raising=False)
        monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
        monkeypatch.delenv("STRANDS_RUNTIME_ARN", raising=False)

        cfg = load_config()
        assert cfg.mode == "cloud"
        assert cfg.agent_id is None
        assert cfg.agent_alias_id is None

    def test_reads_strands_runtime_arn_from_env(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
        monkeypatch.setenv("STRANDS_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123:runtime/test")
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_ALIAS_ID", raising=False)
        monkeypatch.setenv("AWS_REGION", "us-east-1")

        cfg = load_config()
        assert cfg.mode == "cloud"
        assert cfg.strands_runtime_arn == "arn:aws:bedrock-agentcore:us-east-1:123:runtime/test"

    def test_raises_on_invalid_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "staging")
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_ALIAS_ID", raising=False)

        with pytest.raises(ValueError, match="DEPLOYMENT_MODE must be 'local' or 'cloud'"):
            load_config()

    def test_empty_agent_id_env_treated_as_none(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
        monkeypatch.setenv("AGENT_ID", "")
        monkeypatch.setenv("AGENT_ALIAS_ID", "ALIAS")
        monkeypatch.delenv("STRANDS_RUNTIME_ARN", raising=False)

        cfg = load_config()
        assert cfg.agent_id is None

    def test_empty_agent_alias_id_env_treated_as_none(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
        monkeypatch.setenv("AGENT_ID", "AGENT")
        monkeypatch.setenv("AGENT_ALIAS_ID", "")
        monkeypatch.delenv("STRANDS_RUNTIME_ARN", raising=False)

        cfg = load_config()
        assert cfg.agent_alias_id is None


# ---------------------------------------------------------------------------
# DeploymentConfig — optional agentcore_endpoint
# ---------------------------------------------------------------------------


class TestAgentcoreEndpoint:
    """agentcore_endpoint is optional and defaults to None."""

    def test_default_is_none(self):
        cfg = DeploymentConfig(mode="local", region="us-east-1")
        assert cfg.agentcore_endpoint is None

    def test_can_be_set_for_testing(self):
        cfg = DeploymentConfig(
            mode="cloud",
            region="us-east-1",
            agent_id="A",
            agent_alias_id="B",
            agentcore_endpoint="http://localhost:9999",
        )
        cfg.validate()
        assert cfg.agentcore_endpoint == "http://localhost:9999"
