"""Shared utilities for AgentCore Action Group Lambda handlers."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def extract_parameters(event: dict) -> Dict[str, str]:
    """Extract parameters from an AgentCore Action Group event into a flat dict.

    Parameters are provided as a list of {"name": ..., "type": ..., "value": ...}
    dicts. This function converts them to a simple {name: value} mapping.
    """
    params: List[dict] = event.get("parameters") or []
    return {p["name"]: p["value"] for p in params if "name" in p and "value" in p}


def build_response(
    event: dict,
    body: dict,
) -> dict:
    """Build a properly formatted AgentCore Action Group Lambda response.

    Args:
        event: The original Action Group event (used to echo back actionGroup,
               apiPath, and httpMethod).
        body: The tool result dict to serialize as the response body.

    Returns:
        A dict matching the expected LambdaResponse shape.
    """
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "apiPath": event.get("apiPath", ""),
            "httpMethod": event.get("httpMethod", "GET"),
            "responseBody": {
                "application/json": {
                    "body": json.dumps(body),
                }
            },
        },
    }


def build_error_response(event: dict, error_type: str) -> dict:
    """Build an error response for the given error type.

    Args:
        event: The original Action Group event.
        error_type: One of "unknown_tool", "invalid_arguments", or a custom message.

    Returns:
        A properly formatted error response.
    """
    return build_response(event, {"error": error_type})
