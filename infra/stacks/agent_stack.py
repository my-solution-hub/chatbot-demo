"""AgentStack: Lambda functions for AgentCore tool execution.

AgentCore agent registration is done separately via the AgentCore CLI/SDK,
not through CloudFormation. This stack only deploys the Lambda functions
that AgentCore invokes as tools.
"""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class AgentStack(Stack):
    """Defines Lambda tool functions invoked by AgentCore."""

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
            description="AgentCore tool handler for get_current_time",
        )

        # --- Lambda: get_weather ---
        self.weather_function = lambda_.Function(
            self,
            "GetWeatherFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="weather_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            description="AgentCore tool handler for get_weather",
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "TimeFunctionArn",
            value=self.time_function.function_arn,
            description="ARN of the get_current_time Lambda function (configure in AgentCore)",
        )
        CfnOutput(
            self,
            "WeatherFunctionArn",
            value=self.weather_function.function_arn,
            description="ARN of the get_weather Lambda function (configure in AgentCore)",
        )
