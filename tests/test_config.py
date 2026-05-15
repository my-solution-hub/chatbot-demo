"""Unit tests for ``nova_sonic_demo.config``.

Covers:

* Region resolution precedence (``AWS_REGION`` > ``AWS_DEFAULT_REGION`` >
  ``boto3.Session().region_name``).
* :func:`validate_region` raising :class:`UnsupportedRegionError` for
  unsupported and missing regions.
* :func:`assert_credentials_resolvable` raising
  :class:`MissingCredentialsError` when boto3 cannot resolve credentials.

The boto3 ``Session`` class is monkeypatched so the tests do not perform any
network or credential-chain side effects.
"""

from __future__ import annotations

import boto3
import pytest

from nova_sonic_demo import config
from nova_sonic_demo.config import (
    BedrockOpenError,
    MissingCredentialsError,
    MissingDeviceError,
    SUPPORTED_REGIONS,
    UnsupportedRegionError,
    assert_credentials_resolvable,
    resolve_region,
    validate_region,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubSession:
    """A drop-in replacement for ``boto3.Session`` used in tests."""

    def __init__(self, *, region_name=None, credentials=object()):
        # ``credentials`` defaults to a sentinel (truthy) so that
        # ``get_credentials`` returns something non-None by default.
        self._region = region_name
        self._credentials = credentials

    @property
    def region_name(self):
        return self._region

    def get_credentials(self):
        return self._credentials


def _patch_session(monkeypatch, *, region_name=None, credentials=object(),
                   raises=None):
    """Install a stub for ``boto3.Session`` returning the given attributes."""

    def _factory(*args, **kwargs):
        if raises is not None:
            raise raises
        return _StubSession(region_name=region_name, credentials=credentials)

    monkeypatch.setattr(boto3, "Session", _factory)


def _clear_aws_env(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_constants_have_expected_values():
    assert config.MODEL_ID == "amazon.nova-2-sonic-v1:0"
    assert config.INPUT_SAMPLE_RATE_HZ == 16_000
    assert config.OUTPUT_SAMPLE_RATE_HZ == 24_000
    assert config.INPUT_FRAME_SAMPLES == 320
    assert config.TOOL_DISPATCH_DEADLINE_S == 0.5
    assert config.TOOL_RESULT_DEADLINE_S == 0.5
    assert config.TOOL_TIMEOUT_S == 10.0
    assert config.SESSION_OPEN_TIMEOUT_S == 10.0
    assert config.SHUTDOWN_DEADLINE_S == 5.0
    assert SUPPORTED_REGIONS == {"us-east-1", "us-east-2", "us-west-2", "ap-northeast-1"}
    # VAD / jitter buffer defaults (Task: bandwidth optimization).
    assert config.VAD_FRAME_MS in (10, 20, 30)
    assert 0 <= config.VAD_AGGRESSIVENESS <= 3
    assert config.VAD_BATCH_FRAMES >= 1
    assert config.VAD_HANGOVER_MS > 0
    assert config.VAD_PREROLL_MS >= 0
    assert config.PLAYER_PREBUFFER_MS >= 0


# ---------------------------------------------------------------------------
# resolve_region precedence
# ---------------------------------------------------------------------------


def test_resolve_region_prefers_aws_region_env(monkeypatch):
    _clear_aws_env(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    _patch_session(monkeypatch, region_name="eu-central-1")

    assert resolve_region() == "us-east-1"


def test_resolve_region_falls_back_to_aws_default_region(monkeypatch):
    _clear_aws_env(monkeypatch)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    _patch_session(monkeypatch, region_name="eu-central-1")

    assert resolve_region() == "us-west-2"


def test_resolve_region_falls_back_to_boto3_session(monkeypatch):
    _clear_aws_env(monkeypatch)
    _patch_session(monkeypatch, region_name="us-east-2")

    assert resolve_region() == "us-east-2"


def test_resolve_region_returns_none_when_nothing_configured(monkeypatch):
    _clear_aws_env(monkeypatch)
    _patch_session(monkeypatch, region_name=None)

    assert resolve_region() is None


def test_resolve_region_treats_empty_env_as_unset(monkeypatch):
    """Empty strings in env vars must not short-circuit the precedence chain."""
    _clear_aws_env(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "")
    _patch_session(monkeypatch, region_name="us-west-2")

    assert resolve_region() == "us-west-2"


# ---------------------------------------------------------------------------
# validate_region
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("region", sorted(SUPPORTED_REGIONS))
def test_validate_region_accepts_supported_regions(region):
    # Should not raise.
    validate_region(region)


def test_validate_region_rejects_unsupported_region():
    with pytest.raises(UnsupportedRegionError) as excinfo:
        validate_region("eu-central-1")
    assert "eu-central-1" in str(excinfo.value)


def test_validate_region_rejects_none_with_placeholder():
    with pytest.raises(UnsupportedRegionError) as excinfo:
        validate_region(None)
    assert "(none)" in str(excinfo.value)


# ---------------------------------------------------------------------------
# assert_credentials_resolvable
# ---------------------------------------------------------------------------


def test_assert_credentials_resolvable_passes_when_credentials_present(monkeypatch):
    _patch_session(monkeypatch, credentials=object())
    # Should not raise.
    assert_credentials_resolvable()


def test_assert_credentials_resolvable_raises_when_credentials_missing(monkeypatch):
    _patch_session(monkeypatch, credentials=None)
    with pytest.raises(MissingCredentialsError):
        assert_credentials_resolvable()


def test_assert_credentials_resolvable_raises_when_session_raises(monkeypatch):
    _patch_session(monkeypatch, raises=RuntimeError("boom"))
    with pytest.raises(MissingCredentialsError) as excinfo:
        assert_credentials_resolvable()
    assert "boom" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


def test_missing_device_error_carries_kind():
    err = MissingDeviceError("input")
    assert err.kind == "input"
    assert "input" in str(err)


def test_bedrock_open_error_stringifies_with_category_and_underlying():
    underlying = ValueError("nope")
    err = BedrockOpenError("auth", underlying)
    assert err.category == "auth"
    assert err.underlying is underlying
    assert str(err) == "auth: nope"


def test_bedrock_open_error_accepts_string_underlying():
    err = BedrockOpenError("timeout", "open exceeded 10s")
    assert err.category == "timeout"
    assert str(err) == "timeout: open exceeded 10s"
