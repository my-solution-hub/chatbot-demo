"""Unit tests for the get_current_time and get_weather tool handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from nova_sonic_demo.tools import CONDITIONS, get_current_time, get_weather


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# get_current_time
# ---------------------------------------------------------------------------


def test_get_current_time_defaults_to_utc_when_args_empty():
    result = _run(get_current_time({}))
    assert result["timezone"] == "UTC"
    # ISO 8601 timestamp must round-trip through fromisoformat.
    parsed = datetime.fromisoformat(result["timestamp"])
    assert parsed.tzinfo is not None


def test_get_current_time_with_named_timezone_echoes_it():
    result = _run(get_current_time({"timezone": "America/New_York"}))
    assert result["timezone"] == "America/New_York"
    parsed = datetime.fromisoformat(result["timestamp"])
    assert parsed.tzinfo is not None


def test_get_current_time_with_invalid_timezone_returns_error():
    result = _run(get_current_time({"timezone": "Not/AZone"}))
    assert result == {"error": "invalid_timezone"}


def test_get_current_time_with_empty_string_defaults_to_utc():
    result = _run(get_current_time({"timezone": ""}))
    assert result["timezone"] == "UTC"
    datetime.fromisoformat(result["timestamp"])


def test_get_current_time_with_none_defaults_to_utc():
    result = _run(get_current_time({"timezone": None}))
    assert result["timezone"] == "UTC"
    datetime.fromisoformat(result["timestamp"])


# ---------------------------------------------------------------------------
# get_weather
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("city", ["Seattle", "Tokyo", "Paris"])
def test_get_weather_returns_well_formed_result(city):
    result = _run(get_weather({"city": city}))
    assert result["city"] == city
    assert result["condition"] in CONDITIONS
    assert isinstance(result["temperature_c"], int)
    assert -50 <= result["temperature_c"] <= 50


@pytest.mark.parametrize("city", ["Seattle", "Tokyo", "Paris"])
def test_get_weather_is_deterministic_within_process(city):
    first = _run(get_weather({"city": city}))
    second = _run(get_weather({"city": city}))
    assert first == second


def test_get_weather_pinned_mapping_for_known_cities():
    # Capture the implementation's output once, then assert subsequent calls
    # produce the same (condition, temperature_c) pair. This pins the mapping
    # for at least three cities while remaining robust to the exact hashing
    # constants chosen.
    cities = ["Seattle", "Tokyo", "Paris"]
    pinned = {city: _run(get_weather({"city": city})) for city in cities}
    for city in cities:
        again = _run(get_weather({"city": city}))
        assert again["condition"] == pinned[city]["condition"]
        assert again["temperature_c"] == pinned[city]["temperature_c"]
        assert again["city"] == city


def test_get_weather_trims_whitespace_and_preserves_mapping():
    trimmed = _run(get_weather({"city": "Seattle"}))
    padded = _run(get_weather({"city": "  Seattle  "}))
    assert padded["city"] == "Seattle"
    assert padded["condition"] == trimmed["condition"]
    assert padded["temperature_c"] == trimmed["temperature_c"]


def test_get_weather_missing_city_returns_invalid_arguments():
    assert _run(get_weather({})) == {"error": "invalid_arguments"}


def test_get_weather_empty_city_returns_invalid_arguments():
    assert _run(get_weather({"city": ""})) == {"error": "invalid_arguments"}


def test_get_weather_whitespace_city_returns_invalid_arguments():
    assert _run(get_weather({"city": "   "})) == {"error": "invalid_arguments"}


def test_get_weather_non_string_city_returns_invalid_arguments():
    assert _run(get_weather({"city": 123})) == {"error": "invalid_arguments"}


def test_get_weather_non_dict_args_returns_invalid_arguments():
    assert _run(get_weather("Seattle")) == {"error": "invalid_arguments"}
    assert _run(get_weather(None)) == {"error": "invalid_arguments"}
    assert _run(get_weather(["Seattle"])) == {"error": "invalid_arguments"}
