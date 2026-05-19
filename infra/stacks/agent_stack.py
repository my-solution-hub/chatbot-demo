"""AgentStack: Lambda functions and AgentCore agent configuration."""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class AgentStack(Stack):
    """Defines Lambda tool functions and AgentCore agent with Action Groups."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Lambda: get_current_time ---
        self.time_function = lambda_.Function(
            self,
            "GetCurrentTimeFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="time_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            description="AgentCore Action Group handler for get_current_time tool",
        )

        # --- Lambda: get_weather ---
        self.weather_function = lambda_.Function(
            self,
            "GetWeatherFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="weather_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            description="AgentCore Action Group handler for get_weather tool",
        )

        # --- AgentCore Agent Configuration ---
        # NOTE: CDK L2 constructs for Bedrock AgentCore are not yet available.
        # Using CfnResource to define the agent. When L2 constructs become available,
        # replace this with the higher-level construct.
        self.agent = self.node.try_find_child("AgentCoreAgent")  # placeholder ref

        # Define the AgentCore agent using CfnResource (L1 CloudFormation resource)
        self.agent_resource = self._create_agent_resource()

        # --- IAM Role for AgentCore ---
        self.agent_role = iam.Role(
            self,
            "AgentCoreExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Execution role for Bedrock AgentCore agent",
        )

        # AgentCore needs bedrock:InvokeModel for Nova Sonic
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/amazon.nova-sonic-v1:0",
                ],
            )
        )

        # AgentCore needs lambda:InvokeFunction for tool Lambdas
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.time_function.function_arn,
                    self.weather_function.function_arn,
                ],
            )
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "TimeFunctionArn",
            value=self.time_function.function_arn,
            description="ARN of the get_current_time Lambda function",
        )
        CfnOutput(
            self,
            "WeatherFunctionArn",
            value=self.weather_function.function_arn,
            description="ARN of the get_weather Lambda function",
        )

    def _create_agent_resource(self):
        """Create the AgentCore agent using CfnResource.

        NOTE: When CDK L2 constructs for Bedrock Agents become available in
        aws-cdk-lib, replace this with the higher-level construct. The CfnAgent
        resource type is 'AWS::Bedrock::Agent'.

        The agent is configured with:
        - Foundation model: amazon.nova-sonic-v1:0
        - Two action groups: time-tools and weather-tools
        - Each action group points to its respective Lambda function
        """
        # Using CfnResource since aws_bedrock L2 constructs may not cover AgentCore
        # fully. This defines the agent at the CloudFormation level.
        from aws_cdk import CfnResource

        agent = CfnResource(
            self,
            "AgentCoreAgent",
            type="AWS::Bedrock::Agent",
            properties={
                "AgentName": "nova-sonic-chatbot",
                "FoundationModel": "amazon.nova-sonic-v1:0",
                "Instruction": (
                    "You are a friendly voice assistant. Keep replies short and natural. "
                    "When the user asks about the time, call the get_current_time tool. "
                    "When the user asks about the weather, call the get_weather tool. "
                    "After a tool returns, summarize the result in one or two sentences."
                ),
                "IdleSessionTTLInSeconds": 600,
                "AgentResourceRoleArn": self.agent_role.role_arn
                if hasattr(self, "agent_role")
                else "",
                "ActionGroups": [
                    {
                        "ActionGroupName": "time-tools",
                        "Description": "Tools for getting current time information",
                        "ActionGroupExecutor": {
                            "Lambda": self.time_function.function_arn,
                        },
                        "ApiSchema": {
                            "Payload": self._time_tool_openapi_schema(),
                        },
                    },
                    {
                        "ActionGroupName": "weather-tools",
                        "Description": "Tools for getting weather information",
                        "ActionGroupExecutor": {
                            "Lambda": self.weather_function.function_arn,
                        },
                        "ApiSchema": {
                            "Payload": self._weather_tool_openapi_schema(),
                        },
                    },
                ],
            },
        )
        return agent

    @staticmethod
    def _time_tool_openapi_schema() -> str:
        """Return OpenAPI schema for the get_current_time action group."""
        import json

        schema = {
            "openapi": "3.0.0",
            "info": {
                "title": "Time Tools",
                "version": "1.0.0",
                "description": "Tools for getting current time information",
            },
            "paths": {
                "/get_current_time": {
                    "get": {
                        "summary": "Get the current time",
                        "description": "Returns the current time in the specified timezone",
                        "operationId": "get_current_time",
                        "parameters": [
                            {
                                "name": "timezone",
                                "in": "query",
                                "description": "IANA timezone name (e.g. UTC, America/New_York). Defaults to UTC.",
                                "required": False,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Current time response",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "timestamp": {
                                                    "type": "string",
                                                    "description": "ISO 8601 timestamp",
                                                },
                                                "timezone": {
                                                    "type": "string",
                                                    "description": "Timezone used",
                                                },
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
        return json.dumps(schema)

    @staticmethod
    def _weather_tool_openapi_schema() -> str:
        """Return OpenAPI schema for the get_weather action group."""
        import json

        schema = {
            "openapi": "3.0.0",
            "info": {
                "title": "Weather Tools",
                "version": "1.0.0",
                "description": "Tools for getting weather information",
            },
            "paths": {
                "/get_weather": {
                    "post": {
                        "summary": "Get weather for a city",
                        "description": "Returns the current weather conditions for the specified city",
                        "operationId": "get_weather",
                        "parameters": [
                            {
                                "name": "city",
                                "in": "query",
                                "description": "City name to get weather for",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Weather response",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "city": {
                                                    "type": "string",
                                                    "description": "City name",
                                                },
                                                "condition": {
                                                    "type": "string",
                                                    "description": "Weather condition",
                                                },
                                                "temperature_c": {
                                                    "type": "integer",
                                                    "description": "Temperature in Celsius",
                                                },
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
        return json.dumps(schema)
