"""Unit tests for the Tool_Registry."""

from __future__ import annotations

import json

import pytest

from nova_sonic_demo.tools.registry import (
    GET_CURRENT_TIME_SCHEMA,
    GET_WEATHER_SCHEMA,
    ToolDefinition,
    ToolRegistry,
    build_default_registry,
)
from nova_sonic_demo.tools.time_tool import get_current_time
from nova_sonic_demo.tools.weather_tool import get_weather


# ---------------------------------------------------------------------------
# build_default_registry
# ---------------------------------------------------------------------------


def test_build_default_registry_exposes_exactly_two_named_tools():
    registry = build_default_registry()
    assert set(registry.names()) == {"get_current_time", "get_weather"}
    assert len(registry.names()) == 2


def test_default_registry_each_description_within_bounds():
    registry = build_default_registry()
    for name in registry.names():
        tool = registry.get(name)
        assert tool is not None
        assert 1 <= len(tool.description) <= 200


# ---------------------------------------------------------------------------
# to_bedrock_config
# ---------------------------------------------------------------------------


def test_to_bedrock_config_shape_and_schema_round_trip():
    registry = build_default_registry()
    config = registry.to_bedrock_config()

    assert isinstance(config, dict)
    assert set(config.keys()) == {"tools"}
    assert isinstance(config["tools"], list)
    assert len(config["tools"]) == 2

    seen = {}
    for entry in config["tools"]:
        assert set(entry.keys()) == {"toolSpec"}
        spec = entry["toolSpec"]
        assert set(spec.keys()) == {"name", "description", "inputSchema"}
        assert isinstance(spec["name"], str)
        assert isinstance(spec["description"], str)
        assert 1 <= len(spec["description"]) <= 200

        input_schema = spec["inputSchema"]
        assert set(input_schema.keys()) == {"json"}
        assert isinstance(input_schema["json"], str)

        # The serialized JSON must round-trip back to the original schema dict.
        decoded = json.loads(input_schema["json"])
        original = registry.get(spec["name"]).schema
        assert decoded == original

        seen[spec["name"]] = decoded

    assert seen["get_current_time"] == GET_CURRENT_TIME_SCHEMA
    assert seen["get_weather"] == GET_WEATHER_SCHEMA


# ---------------------------------------------------------------------------
# Schema structural invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "schema",
    [GET_CURRENT_TIME_SCHEMA, GET_WEATHER_SCHEMA],
    ids=["get_current_time", "get_weather"],
)
def test_each_schema_is_object_with_typed_properties_and_required_array(schema):
    assert schema["type"] == "object"

    properties = schema["properties"]
    assert isinstance(properties, dict)
    for prop_name, prop_schema in properties.items():
        assert isinstance(prop_schema, dict), f"{prop_name} schema must be a dict"
        assert "type" in prop_schema, f"{prop_name} must declare a type"

    required = schema["required"]
    assert isinstance(required, list)


def test_get_weather_schema_requires_city():
    assert "city" in GET_WEATHER_SCHEMA["required"]


def test_get_current_time_schema_required_is_empty():
    assert GET_CURRENT_TIME_SCHEMA["required"] == []


# ---------------------------------------------------------------------------
# ToolDefinition validation
# ---------------------------------------------------------------------------


def test_tool_definition_rejects_empty_description():
    with pytest.raises(ValueError):
        ToolDefinition(
            name="empty_desc",
            description="",
            schema={"type": "object", "properties": {}, "required": []},
            handler=get_current_time,
        )


def test_tool_definition_rejects_description_over_200_chars():
    too_long = "x" * 201
    with pytest.raises(ValueError):
        ToolDefinition(
            name="too_long",
            description=too_long,
            schema={"type": "object", "properties": {}, "required": []},
            handler=get_current_time,
        )


def test_tool_definition_accepts_boundary_descriptions():
    # length 1 and 200 must both be accepted.
    schema = {"type": "object", "properties": {}, "required": []}
    ToolDefinition(name="a", description="x", schema=schema, handler=get_current_time)
    ToolDefinition(
        name="b", description="x" * 200, schema=schema, handler=get_current_time
    )


# ---------------------------------------------------------------------------
# ToolRegistry behavior
# ---------------------------------------------------------------------------


def test_tool_registry_rejects_duplicate_names():
    schema = {"type": "object", "properties": {}, "required": []}
    a = ToolDefinition(name="dup", description="first", schema=schema, handler=get_current_time)
    b = ToolDefinition(name="dup", description="second", schema=schema, handler=get_weather)

    with pytest.raises(ValueError):
        ToolRegistry([a, b])


def test_tool_registry_get_returns_none_for_missing_name():
    registry = build_default_registry()
    assert registry.get("missing") is None
