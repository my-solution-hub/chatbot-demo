"""RemoteToolDispatcher — dispatches tool calls to Lambda functions via boto3.

Used in cloud mode: Nova Sonic handles audio locally on Fargate, but tool
calls are routed to Lambda functions (the same ones exposed via AgentCore
Gateway). This avoids running tool logic in-process and keeps the Fargate
container stateless.

The dispatcher maintains the same interface as ToolDispatcher so it can be
swapped in transparently via the session_factory/dispatcher_factory pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from nova_sonic_demo.logging import ConsoleLogger

logger = logging.getLogger("nova_sonic_demo.tools.remote_dispatcher")


class RemoteToolDispatcher:
    """Dispatches tool calls to AWS Lambda functions.

    Parameters
    ----------
    tool_lambda_map:
        Mapping of tool_name → Lambda function ARN/name.
    logger:
        ConsoleLogger instance for logging tool calls/results.
    region:
        AWS region for the Lambda client.
    timeout_s:
        Maximum seconds to wait for a Lambda invocation.
    client_factory:
        Optional injectable factory for creating the boto3 Lambda client.
        Accepts (region,) and returns a client. Used for testing.
    """

    def __init__(
        self,
        tool_lambda_map: dict[str, str],
        logger_instance: ConsoleLogger,
        region: str = "ap-northeast-1",
        timeout_s: float = 10.0,
        client_factory=None,
    ) -> None:
        self._tool_lambda_map = tool_lambda_map
        self._logger = logger_instance
        self._region = region
        self._timeout_s = timeout_s
        self._client_factory = client_factory
        self._client = None

    def _get_client(self):
        """Lazily create the Lambda client."""
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory(self._region)
            else:
                import boto3
                self._client = boto3.client("lambda", region_name=self._region)
        return self._client

    async def dispatch(
        self,
        tool_use_id: str,
        tool_name: str,
        arguments: dict,
    ) -> dict:
        """Invoke the Lambda function for the given tool.

        Parameters
        ----------
        tool_use_id:
            Unique ID for this tool invocation (from Nova Sonic).
        tool_name:
            Name of the tool to invoke.
        arguments:
            Tool arguments as a dict.

        Returns
        -------
        dict
            The tool result. On error, returns {"error": "<message>"}.
        """
        # 1. Log the tool call
        self._logger.tool_call(tool_name, arguments)

        # 2. Look up the Lambda function
        lambda_name = self._tool_lambda_map.get(tool_name)
        if lambda_name is None:
            result = {"error": "unknown_tool", "tool": tool_name}
            self._logger.tool_result(tool_name, result)
            return result

        # 3. Build the Lambda event payload (AgentCore Action Group format)
        event_payload = {
            "apiPath": f"/{tool_name}",
            "httpMethod": "GET",
            "actionGroup": "chatbot-tools",
            "parameters": [
                {"name": k, "type": "string", "value": str(v)}
                for k, v in arguments.items()
            ],
        }

        # 4. Invoke Lambda with timeout
        try:
            result = await asyncio.wait_for(
                self._invoke_lambda(lambda_name, event_payload),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            result = {"error": "tool_timeout"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc)[:200] or type(exc).__name__[:200]
            result = {"error": message}
            logger.error("Lambda invocation failed for %s: %s", tool_name, exc)

        # 5. Log and return
        self._logger.tool_result(tool_name, result)
        return result

    async def _invoke_lambda(self, function_name: str, payload: dict) -> dict:
        """Invoke a Lambda function and parse the response."""
        client = self._get_client()

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload).encode("utf-8"),
            ),
        )

        # Read the response payload
        response_payload = response["Payload"].read()
        if not response_payload:
            return {"error": "empty_response"}

        try:
            data = json.loads(response_payload)
        except (json.JSONDecodeError, TypeError):
            return {"error": "invalid_response_json"}

        # Parse AgentCore Action Group response format
        return self._parse_lambda_response(data)

    @staticmethod
    def _parse_lambda_response(data: dict) -> dict:
        """Parse the Lambda response from AgentCore Action Group format.

        Expected format:
        {
            "messageVersion": "1.0",
            "response": {
                "actionGroup": "...",
                "apiPath": "...",
                "httpMethod": "...",
                "responseBody": {
                    "application/json": {
                        "body": "{\"key\": \"value\"}"
                    }
                }
            }
        }
        """
        if not isinstance(data, dict):
            return {"error": "unexpected_response_format"}

        response = data.get("response")
        if not isinstance(response, dict):
            # Maybe it's a direct result (non-Action-Group format)
            return data

        response_body = response.get("responseBody")
        if not isinstance(response_body, dict):
            return {"error": "missing_response_body"}

        json_body = response_body.get("application/json", {})
        if not isinstance(json_body, dict):
            return {"error": "missing_json_body"}

        body_str = json_body.get("body", "")
        if not body_str:
            return {"error": "empty_body"}

        try:
            return json.loads(body_str)
        except (json.JSONDecodeError, TypeError):
            return {"result": body_str}
