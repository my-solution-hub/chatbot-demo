"""Unit tests for Lambda tool handlers (AgentCore Action Group format)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add the Lambda source directory to the path so we can import the handlers
# directly (they use relative imports of shared.py in the same directory).
_lambda_dir = str(Path(__file__).resolve().parent.parent / "infra" / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

from time_handler import lambda_handler as time_handler  # noqa: E402
from weather_handler import lambda_handler as weather_handler  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    action_group: str,
    api_path: str,
    parameters: list | None = None,
    http_method: str = "GET",
) -> dict:
    """Build a minimal AgentCore Action Group event."""
    return {
        "messageVersion": "1.0",
        "agent": {"name": "nova-sonic-chatbot", "version": "1"},
        "inputText": "",
        "sessionId": "test-session-123",
        "actionGroup": action_group,
        "apiPath": api_path,
        "httpMethod": http_method,
        "parameters": parameters or [],
        "requestBody": {},
    }


def _parse_body(response: dict) -> dict:
    """Extract and parse the JSON body from a Lambda response."""
    body_str = response["response"]["responseBody"]["application/json"]["body"]
    return json.loads(body_str)


# ---------------------------------------------------------------------------
# Shared response format tests
# ---------------------------------------------------------------------------


class TestResponseFormat:
    """Verify all responses conform to the AgentCore Action Group format."""

    def test_response_has_message_version(self):
        event = _make_event("time-tools", "/get_current_time")
        resp = time_handler(event, None)
        assert resp["messageVersion"] == "1.0"

    def test_response_echoes_action_group(self):
        event = _make_event("weather-tools", "/get_weather", [{"name": "city", "type": "string", "value": "Tokyo"}])
        resp = weather_handler(event, None)
        assert resp["response"]["actionGroup"] == "weather-tools"

    def test_response_echoes_api_path(self):
        event = _make_event("time-tools", "/get_current_time")
        resp = time_handler(event, None)
        assert resp["response"]["apiPath"] == "/get_current_time"

    def test_response_echoes_http_method(self):
        event = _make_event("weather-tools", "/get_weather", [{"name": "city", "type": "string", "value": "Paris"}], "POST")
        resp = weather_handler(event, None)
        assert resp["response"]["httpMethod"] == "POST"

    def test_response_body_is_valid_json_string(self):
        event = _make_event("time-tools", "/get_current_time")
        resp = time_handler(event, None)
        body_str = resp["response"]["responseBody"]["application/json"]["body"]
        # Must be a string that parses as JSON
        assert isinstance(body_str, str)
        parsed = json.loads(body_str)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Time handler tests
# ---------------------------------------------------------------------------


class TestTimeHandler:
    """Tests for the get_current_time Lambda handler."""

    def test_defaults_to_utc_when_no_timezone_param(self):
        event = _make_event("time-tools", "/get_current_time")
        body = _parse_body(time_handler(event, None))
        assert body["timezone"] == "UTC"
        # Verify ISO 8601 format
        parsed = datetime.fromisoformat(body["timestamp"])
        assert parsed.tzinfo is not None

    def test_respects_timezone_parameter(self):
        event = _make_event(
            "time-tools",
            "/get_current_time",
            [{"name": "timezone", "type": "string", "value": "America/New_York"}],
        )
        body = _parse_body(time_handler(event, None))
        assert body["timezone"] == "America/New_York"
        parsed = datetime.fromisoformat(body["timestamp"])
        assert parsed.tzinfo is not None

    def test_empty_timezone_defaults_to_utc(self):
        event = _make_event(
            "time-tools",
            "/get_current_time",
            [{"name": "timezone", "type": "string", "value": ""}],
        )
        body = _parse_body(time_handler(event, None))
        assert body["timezone"] == "UTC"

    def test_invalid_timezone_returns_error(self):
        event = _make_event(
            "time-tools",
            "/get_current_time",
            [{"name": "timezone", "type": "string", "value": "Not/AZone"}],
        )
        body = _parse_body(time_handler(event, None))
        assert body == {"error": "invalid_timezone"}

    def test_unknown_api_path_returns_unknown_tool(self):
        event = _make_event("time-tools", "/unknown_path")
        body = _parse_body(time_handler(event, None))
        assert body == {"error": "unknown_tool"}


# ---------------------------------------------------------------------------
# Weather handler tests
# ---------------------------------------------------------------------------


class TestWeatherHandler:
    """Tests for the get_weather Lambda handler."""

    def test_returns_weather_for_valid_city(self):
        event = _make_event(
            "weather-tools",
            "/get_weather",
            [{"name": "city", "type": "string", "value": "Tokyo"}],
            "POST",
        )
        body = _parse_body(weather_handler(event, None))
        assert body["city"] == "Tokyo"
        assert body["condition"] in ("sunny", "cloudy", "rainy", "snowy", "windy")
        assert isinstance(body["temperature_c"], int)
        assert -50 <= body["temperature_c"] <= 50

    def test_is_deterministic(self):
        event = _make_event(
            "weather-tools",
            "/get_weather",
            [{"name": "city", "type": "string", "value": "Seattle"}],
            "POST",
        )
        body1 = _parse_body(weather_handler(event, None))
        body2 = _parse_body(weather_handler(event, None))
        assert body1 == body2

    def test_trims_whitespace(self):
        event_trimmed = _make_event(
            "weather-tools",
            "/get_weather",
            [{"name": "city", "type": "string", "value": "Paris"}],
            "POST",
        )
        event_padded = _make_event(
            "weather-tools",
            "/get_weather",
            [{"name": "city", "type": "string", "value": "  Paris  "}],
            "POST",
        )
        body_trimmed = _parse_body(weather_handler(event_trimmed, None))
        body_padded = _parse_body(weather_handler(event_padded, None))
        assert body_padded["city"] == "Paris"
        assert body_padded["condition"] == body_trimmed["condition"]
        assert body_padded["temperature_c"] == body_trimmed["temperature_c"]

    def test_missing_city_parameter_returns_invalid_arguments(self):
        event = _make_event("weather-tools", "/get_weather", [], "POST")
        body = _parse_body(weather_handler(event, None))
        assert body == {"error": "invalid_arguments"}

    def test_empty_city_returns_invalid_arguments(self):
        event = _make_event(
            "weather-tools",
            "/get_weather",
            [{"name": "city", "type": "string", "value": ""}],
            "POST",
        )
        body = _parse_body(weather_handler(event, None))
        assert body == {"error": "invalid_arguments"}

    def test_whitespace_only_city_returns_invalid_arguments(self):
        event = _make_event(
            "weather-tools",
            "/get_weather",
            [{"name": "city", "type": "string", "value": "   "}],
            "POST",
        )
        body = _parse_body(weather_handler(event, None))
        assert body == {"error": "invalid_arguments"}

    def test_unknown_api_path_returns_unknown_tool(self):
        event = _make_event("weather-tools", "/unknown_path")
        body = _parse_body(weather_handler(event, None))
        assert body == {"error": "unknown_tool"}

    def test_produces_same_result_as_in_process_tool(self):
        """Verify Lambda handler produces identical results to the in-process tool."""
        import asyncio
        from nova_sonic_demo.tools import get_weather as in_process_get_weather

        cities = ["Tokyo", "Seattle", "Paris", "London", "Berlin"]
        for city in cities:
            # Lambda handler result
            event = _make_event(
                "weather-tools",
                "/get_weather",
                [{"name": "city", "type": "string", "value": city}],
                "POST",
            )
            lambda_body = _parse_body(weather_handler(event, None))

            # In-process tool result
            in_process_result = asyncio.run(in_process_get_weather({"city": city}))

            assert lambda_body["city"] == in_process_result["city"], f"City mismatch for {city}"
            assert lambda_body["condition"] == in_process_result["condition"], f"Condition mismatch for {city}"
            assert lambda_body["temperature_c"] == in_process_result["temperature_c"], f"Temperature mismatch for {city}"
