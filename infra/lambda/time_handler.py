"""Lambda handler for the get_current_time tool (AgentCore Action Group)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared import build_error_response, build_response, extract_parameters


def _get_current_time(parameters: dict) -> dict:
    """Execute get_current_time logic.

    Uses the same behaviour as the in-process tool:
    - Defaults to UTC when timezone is missing or empty.
    - Returns {"error": "invalid_timezone"} for unrecognised timezone names.
    - Returns ISO 8601 timestamp with timezone info.
    """
    tz_name = parameters.get("timezone") or ""
    tz_name = tz_name.strip()
    if not tz_name:
        tz_name = "UTC"

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        return {"error": "invalid_timezone"}

    now = datetime.now(tz)
    return {"timestamp": now.isoformat(), "timezone": tz_name}


def lambda_handler(event: dict, context: Any) -> dict:
    """AgentCore Action Group Lambda handler for time tools.

    Handles:
        /get_current_time - Returns current time in the requested timezone.

    Unknown API paths return {"error": "unknown_tool"}.
    """
    api_path = event.get("apiPath", "")

    if api_path == "/get_current_time":
        parameters = extract_parameters(event)
        result = _get_current_time(parameters)
        return build_response(event, result)

    return build_error_response(event, "unknown_tool")
