"""Lambda handler for the get_weather tool (AgentCore Action Group)."""

from __future__ import annotations

import hashlib
from typing import Any

from shared import build_error_response, build_response, extract_parameters

# Same conditions list as the in-process tool.
CONDITIONS = ("sunny", "cloudy", "rainy", "snowy", "windy")


def _stable_seed(city: str) -> int:
    """Produce a deterministic integer seed from a city name."""
    digest = hashlib.sha256(city.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _get_weather(parameters: dict) -> dict:
    """Execute get_weather logic.

    Uses the same deterministic algorithm as the in-process tool:
    - Strips whitespace from city, lowercases for hashing.
    - Condition is selected by seed % len(CONDITIONS).
    - Temperature is (seed % 101) - 50, giving range [-50, 50].
    - Returns {"error": "invalid_arguments"} when city is missing or empty.
    """
    raw_city = parameters.get("city")
    if not isinstance(raw_city, str):
        return {"error": "invalid_arguments"}

    city = raw_city.strip()
    if not city:
        return {"error": "invalid_arguments"}

    seed = _stable_seed(city.lower())
    condition = CONDITIONS[seed % len(CONDITIONS)]
    temperature_c = (seed % 101) - 50

    return {
        "city": city,
        "condition": condition,
        "temperature_c": temperature_c,
    }


def lambda_handler(event: dict, context: Any) -> dict:
    """AgentCore Action Group Lambda handler for weather tools.

    Handles:
        /get_weather - Returns deterministic weather for the given city.

    Unknown API paths return {"error": "unknown_tool"}.
    Missing required 'city' parameter returns {"error": "invalid_arguments"}.
    """
    api_path = event.get("apiPath", "")

    if api_path == "/get_weather":
        parameters = extract_parameters(event)
        if "city" not in parameters:
            return build_error_response(event, "invalid_arguments")
        result = _get_weather(parameters)
        return build_response(event, result)

    return build_error_response(event, "unknown_tool")
