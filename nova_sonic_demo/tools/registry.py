from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, Optional

from .time_tool import get_current_time
from .weather_tool import get_weather


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str  # 1..200 chars (validated at construction time)
    schema: dict     # JSON Schema for arguments
    handler: Callable[[dict], Awaitable[dict]]

    def __post_init__(self):
        if not (1 <= len(self.description) <= 200):
            raise ValueError(
                f"Tool {self.name!r} description must be 1..200 chars, got {len(self.description)}"
            )


class ToolRegistry:
    """In-process collection of tool definitions exposed to Nova Sonic."""

    def __init__(self, definitions: Iterable[ToolDefinition]) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for d in definitions:
            if d.name in self._tools:
                raise ValueError(f"duplicate tool name: {d.name}")
            self._tools[d.name] = d

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def to_bedrock_config(self) -> dict:
        """Return the toolConfiguration payload for Nova Sonic promptStart.

        Shape:
            {
              "tools": [
                {
                  "toolSpec": {
                    "name": <tool_name>,
                    "description": <description>,
                    "inputSchema": {"json": <json_schema_as_string>}
                  }
                },
                ...
              ]
            }
        """
        import json
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": d.name,
                        "description": d.description,
                        "inputSchema": {"json": json.dumps(d.schema)},
                    }
                }
                for d in self._tools.values()
            ]
        }


# JSON Schemas exactly as documented in design.md.

GET_CURRENT_TIME_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "timezone": {"type": "string"}
    },
    "required": [],
}

GET_WEATHER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "minLength": 1, "maxLength": 100}
    },
    "required": ["city"],
}


def build_default_registry() -> ToolRegistry:
    """Construct the registry with both demo tools."""
    return ToolRegistry([
        ToolDefinition(
            name="get_current_time",
            description="Return the current ISO 8601 timestamp in the requested timezone.",
            schema=GET_CURRENT_TIME_SCHEMA,
            handler=get_current_time,
        ),
        ToolDefinition(
            name="get_weather",
            description="Return a mocked current weather report for the given city.",
            schema=GET_WEATHER_SCHEMA,
            handler=get_weather,
        ),
    ])


# ---------------------------------------------------------------------------
# Tool_Dispatcher (Task 7)
# ---------------------------------------------------------------------------

import asyncio
from typing import Any

from nova_sonic_demo.logging import ConsoleLogger


def _validate_against_schema(schema: dict, value: Any) -> bool:
    """Lightweight JSON-Schema-style validator covering only what the demo needs.

    Returns True if ``value`` matches ``schema``; False otherwise.
    """
    if not isinstance(value, dict):
        return False
    if schema.get("type") != "object":
        return False

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for req in required:
        if req not in value or value[req] is None:
            return False

    for key, prop_schema in properties.items():
        if key not in value:
            continue
        v = value[key]
        expected_type = prop_schema.get("type")
        if expected_type == "string":
            if not isinstance(v, str):
                return False
            if "minLength" in prop_schema and len(v) < prop_schema["minLength"]:
                return False
            if "maxLength" in prop_schema and len(v) > prop_schema["maxLength"]:
                return False
        elif expected_type == "integer":
            if not isinstance(v, int) or isinstance(v, bool):
                return False
        elif expected_type == "number":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return False
        elif expected_type == "boolean":
            if not isinstance(v, bool):
                return False
        elif expected_type == "object":
            if not isinstance(v, dict):
                return False
        elif expected_type == "array":
            if not isinstance(v, list):
                return False
        # Unknown types are not enforced.

    return True


class ToolDispatcher:
    """Dispatches Tool_Calls to handlers, enforcing timeout and validation."""

    def __init__(
        self,
        registry: ToolRegistry,
        logger: ConsoleLogger,
        timeout_s: float = 10.0,
    ) -> None:
        self._registry = registry
        self._logger = logger
        self._timeout_s = timeout_s

    async def dispatch(
        self,
        tool_use_id: str,
        tool_name: str,
        arguments: dict,
    ) -> dict:
        # 1. Log the tool call BEFORE handler execution.
        self._logger.tool_call(tool_name, arguments)

        # 2. Lookup.
        tool = self._registry.get(tool_name)
        if tool is None:
            result = {"error": "unknown_tool", "tool": tool_name}
            self._logger.tool_result(tool_name, result)
            return result

        # 3. Schema validation.
        if not _validate_against_schema(tool.schema, arguments):
            result = {"error": "invalid_arguments"}
            self._logger.tool_result(tool_name, result)
            return result

        # 4. Run handler with timeout.
        try:
            result = await asyncio.wait_for(
                tool.handler(arguments), timeout=self._timeout_s
            )
        except asyncio.TimeoutError:
            result = {"error": "tool_timeout"}
        except asyncio.CancelledError:
            # Propagate cancellation up to the event loop.
            raise
        except Exception as exc:  # noqa: BLE001 - we deliberately catch all.
            message = str(exc)[:200]
            if not message:
                message = type(exc).__name__[:200]
            result = {"error": message}

        # 5. Log result and return.
        self._logger.tool_result(tool_name, result)
        return result
