"""AgentStack: AgentCore Gateway, GatewayTargets, and Runtime.

Deploys:
1. AgentCore Gateway (MCP protocol) — exposes Lambda tools to the agent
2. GatewayTargets — routes tool calls to the appropriate Lambda functions
3. AgentCore Runtime — runs the Strands agent container

References: ref-cdk/lib/agent-stack.ts, ref-cdk/lib/gateway-stack.ts
"""

from __future__ import annotations

from aws_cdk import CfnOutput, CfnResource, Duration, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class AgentStack(Stack):
    """AgentCore Gateway + Runtime for the chatbot demo."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        strands_agent_repo: ecr.IRepository,
        image_tag: str = "latest",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # Lambda Tool Functions
        # =====================================================================

        self.time_function = lambda_.Function(
            self,
            "GetCurrentTimeFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="time_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            description="AgentCore tool: get_current_time",
        )

        self.weather_function = lambda_.Function(
            self,
            "GetWeatherFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="weather_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            description="AgentCore tool: get_weather",
        )

        # =====================================================================
        # AgentCore Gateway (MCP protocol)
        # =====================================================================

        # IAM role for Gateway to invoke Lambda functions
        gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Allows AgentCore Gateway to invoke tool Lambda functions",
        )
        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.time_function.function_arn,
                    self.weather_function.function_arn,
                ],
            )
        )

        self.gateway = CfnResource(
            self,
            "Gateway",
            type="AWS::BedrockAgentCore::Gateway",
            properties={
                "Name": "chatbot-demo-gateway",
                "ProtocolType": "MCP",
                "AuthorizerType": "NONE",
                "RoleArn": gateway_role.role_arn,
            },
        )

        # =====================================================================
        # Gateway Targets — one per Lambda, with tool schemas
        # =====================================================================

        time_target = CfnResource(
            self,
            "TimeTarget",
            type="AWS::BedrockAgentCore::GatewayTarget",
            properties={
                "GatewayIdentifier": self.gateway.get_att("GatewayIdentifier"),
                "Name": "time-tools",
                "CredentialProviderConfigurations": [
                    {"CredentialProviderType": "GATEWAY_IAM_ROLE"}
                ],
                "TargetConfiguration": {
                    "Mcp": {
                        "Lambda": {
                            "LambdaArn": self.time_function.function_arn,
                            "ToolSchema": {
                                "InlinePayload": [
                                    {
                                        "Name": "get_current_time",
                                        "Description": "Get the current time in a specified timezone",
                                        "InputSchema": {
                                            "Type": "object",
                                            "Properties": {
                                                "timezone": {
                                                    "Type": "string",
                                                    "Description": "IANA timezone name (e.g. Asia/Tokyo, UTC). Defaults to UTC.",
                                                }
                                            },
                                            "Required": [],
                                        },
                                    }
                                ]
                            },
                        }
                    }
                },
            },
        )
        time_target.add_dependency(self.gateway)

        weather_target = CfnResource(
            self,
            "WeatherTarget",
            type="AWS::BedrockAgentCore::GatewayTarget",
            properties={
                "GatewayIdentifier": self.gateway.get_att("GatewayIdentifier"),
                "Name": "weather-tools",
                "CredentialProviderConfigurations": [
                    {"CredentialProviderType": "GATEWAY_IAM_ROLE"}
                ],
                "TargetConfiguration": {
                    "Mcp": {
                        "Lambda": {
                            "LambdaArn": self.weather_function.function_arn,
                            "ToolSchema": {
                                "InlinePayload": [
                                    {
                                        "Name": "get_weather",
                                        "Description": "Get the current weather for a city (deterministic demo data)",
                                        "InputSchema": {
                                            "Type": "object",
                                            "Properties": {
                                                "city": {
                                                    "Type": "string",
                                                    "Description": "City name to get weather for",
                                                }
                                            },
                                            "Required": ["city"],
                                        },
                                    }
                                ]
                            },
                        }
                    }
                },
            },
        )
        weather_target.add_dependency(self.gateway)

        # =====================================================================
        # AgentCore Runtime — Strands agent container
        # =====================================================================

        # IAM role for the Runtime to invoke Bedrock models and access Gateway
        runtime_role = iam.Role(
            self,
            "AgentCoreExecRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="AgentCore Runtime execution role for Strands agent",
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock-agentcore:InvokeGateway",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            )
        )

        # Grant ECR pull access
        strands_agent_repo.grant_pull(runtime_role)
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )

        # The Runtime resource
        image_uri = f"{strands_agent_repo.repository_uri}:{image_tag}"
        gateway_url = self.gateway.get_att("GatewayUrl")

        self.strands_runtime = CfnResource(
            self,
            "StrandsRuntime",
            type="AWS::BedrockAgentCore::Runtime",
            properties={
                "AgentRuntimeName": "chatbot_demo_strands",
                "AgentRuntimeArtifact": {
                    "ContainerConfiguration": {"ContainerUri": image_uri},
                },
                "RoleArn": runtime_role.role_arn,
                "NetworkConfiguration": {"NetworkMode": "PUBLIC"},
                "EnvironmentVariables": {
                    "MCP_SERVER_URL": gateway_url,
                    "AWS_REGION": self.region,
                    "MODEL_ID": "us.amazon.nova-pro-v1:0",
                },
            },
        )
        # Ensure role + ECR permissions are ready before runtime creation
        self.strands_runtime.node.add_dependency(runtime_role)

        # =====================================================================
        # Outputs
        # =====================================================================

        self.strands_runtime_arn = self.strands_runtime.get_att("AgentRuntimeArn")

        CfnOutput(
            self,
            "GatewayUrl",
            value=gateway_url.to_string(),
            description="AgentCore Gateway MCP endpoint URL",
        )
        CfnOutput(
            self,
            "StrandsRuntimeArn",
            value=self.strands_runtime_arn.to_string(),
            description="ARN of the Strands AgentCore Runtime",
        )
        CfnOutput(
            self,
            "TimeFunctionArn",
            value=self.time_function.function_arn,
            description="ARN of the get_current_time Lambda",
        )
        CfnOutput(
            self,
            "WeatherFunctionArn",
            value=self.weather_function.function_arn,
            description="ARN of the get_weather Lambda",
        )
