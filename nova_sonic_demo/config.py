"""Configuration constants and AWS region/credential resolution.

This module is intentionally import-light: ``boto3`` is imported lazily inside
the functions that need it so other modules (e.g. ``audio``) can use the typed
exceptions defined here without pulling in boto3 at import time.
"""

from __future__ import annotations

import os
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Bedrock model identifier for Amazon Nova Sonic (Nova 2 Sonic v1).
MODEL_ID = "amazon.nova-2-sonic-v1:0"

# Audio format constants.
INPUT_SAMPLE_RATE_HZ = 16_000
OUTPUT_SAMPLE_RATE_HZ = 24_000
INPUT_FRAME_SAMPLES = 320  # 20 ms at 16 kHz

# VAD / batching defaults (used by AudioCapturer + VADGate).
VAD_FRAME_MS = 20                  # webrtcvad accepts 10/20/30 ms only
VAD_AGGRESSIVENESS = 2             # 0..3; higher = more aggressive at cutting silence
VAD_BATCH_FRAMES = 4               # send a batched 80 ms chunk per HTTP/2 message
VAD_HANGOVER_MS = 800              # keep streaming this long after last voice frame
VAD_PREROLL_MS = 200               # include this much pre-trigger audio when speech starts

# Audio player jitter buffer (warmup before playback begins).
PLAYER_PREBUFFER_MS = 250

# Timing deadlines (seconds).
TOOL_DISPATCH_DEADLINE_S = 0.5
TOOL_RESULT_DEADLINE_S = 0.5
TOOL_TIMEOUT_S = 10.0
SESSION_OPEN_TIMEOUT_S = 10.0
SHUTDOWN_DEADLINE_S = 5.0

# AWS regions that currently support Nova Sonic v2 on Bedrock.
SUPPORTED_REGIONS = {"us-east-1", "us-east-2", "us-west-2", "ap-northeast-1"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MissingCredentialsError(Exception):
    """Raised when AWS credentials cannot be resolved from the SDK chain."""


class UnsupportedRegionError(Exception):
    """Raised when the resolved AWS region does not support Nova Sonic v2."""


class MissingDeviceError(Exception):
    """Raised when no input or output audio device is available.

    The ``kind`` attribute is either ``"input"`` or ``"output"``.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"Missing {kind} device")


class BedrockOpenError(Exception):
    """Raised when the Bedrock bidirectional stream cannot be opened.

    ``category`` is one of ``"auth"``, ``"network"``, ``"region"``,
    ``"model"``, or ``"timeout"``. ``underlying`` is the original exception
    or message that triggered the failure.
    """

    def __init__(self, category: str, underlying: object) -> None:
        self.category = category
        self.underlying = underlying
        super().__init__(f"{category}: {underlying}")


# ---------------------------------------------------------------------------
# Region and credential resolution
# ---------------------------------------------------------------------------


def resolve_region() -> Optional[str]:
    """Return the AWS region to use for the demo, or ``None`` if not set.

    Precedence:

    1. ``AWS_REGION`` environment variable.
    2. ``AWS_DEFAULT_REGION`` environment variable.
    3. ``boto3.Session().region_name`` (which itself reads the SDK
       configuration chain, e.g. ``~/.aws/config``).

    Empty strings are treated as unset. This function never raises;
    validation is the responsibility of :func:`validate_region`.
    """
    env_region = os.environ.get("AWS_REGION")
    if env_region:
        return env_region

    default_region = os.environ.get("AWS_DEFAULT_REGION")
    if default_region:
        return default_region

    # Fall back to the boto3 session's resolved region. Import lazily so this
    # module remains testable without boto3 installed at import time.
    import boto3  # noqa: WPS433 (intentional local import)

    session_region = boto3.Session().region_name
    if session_region:
        return session_region
    return None


def validate_region(region: Optional[str]) -> None:
    """Raise :class:`UnsupportedRegionError` if ``region`` is not supported."""
    if region is None or region not in SUPPORTED_REGIONS:
        display = region if region else "(none)"
        raise UnsupportedRegionError(
            f"Region {display} does not support Nova Sonic v2"
        )


def assert_credentials_resolvable() -> None:
    """Verify that AWS credentials can be resolved from the SDK chain.

    Raises :class:`MissingCredentialsError` if no credentials are found or if
    the underlying ``boto3`` call raises.
    """
    import boto3  # noqa: WPS433 (intentional local import)

    try:
        credentials = boto3.Session().get_credentials()
    except Exception as exc:  # pragma: no cover - boto3 internals
        raise MissingCredentialsError(
            f"AWS credentials could not be resolved: {exc}"
        ) from exc

    if credentials is None:
        raise MissingCredentialsError(
            "AWS credentials could not be resolved from the SDK chain"
        )
