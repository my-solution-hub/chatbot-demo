#!/usr/bin/env python3
"""CDK app entry point for the Nova Sonic chatbot demo cloud deployment."""

import os

import aws_cdk as cdk

from stacks.ecr_stack import EcrStack
from stacks.network_stack import NetworkStack
from stacks.compute_stack import ComputeStack
from stacks.agent_stack import AgentStack
from stacks.distribution_stack import DistributionStack

app = cdk.App()

# Environment configuration
region = os.environ.get("AWS_REGION", os.environ.get("CDK_DEPLOY_REGION", "ap-northeast-1"))
account = os.environ.get("CDK_DEPLOY_ACCOUNT", os.environ.get("CDK_DEFAULT_ACCOUNT"))

env = cdk.Environment(account=account, region=region)

# Image tag — CI passes commit SHA via context. Local deploys use "latest".
image_tag = app.node.try_get_context("imageTag") or "latest"

# --- Stacks ---

# ECR must be deployed first (images pushed between ECR and Compute deploys)
ecr_stack = EcrStack(app, "ChatbotEcrStack", env=env)

network_stack = NetworkStack(app, "ChatbotNetworkStack", env=env)

# AgentStack: Gateway + Runtime (depends on ECR for the agent image)
agent_stack = AgentStack(
    app,
    "ChatbotAgentStack",
    strands_agent_repo=ecr_stack.strands_agent_repo,
    image_tag=image_tag,
    env=env,
)
agent_stack.add_dependency(ecr_stack)

# ComputeStack: Fargate proxy (depends on Network + ECR + Agent for runtime ARN)
compute_stack = ComputeStack(
    app,
    "ChatbotComputeStack",
    vpc=network_stack.vpc,
    proxy_repo=ecr_stack.proxy_repo,
    image_tag=image_tag,
    strands_runtime_arn=agent_stack.strands_runtime_arn.to_string(),
    tool_lambda_arns={
        "get_current_time": agent_stack.time_function.function_arn,
        "get_weather": agent_stack.weather_function.function_arn,
    },
    env=env,
)
compute_stack.add_dependency(network_stack)
compute_stack.add_dependency(ecr_stack)
compute_stack.add_dependency(agent_stack)

distribution_stack = DistributionStack(
    app,
    "ChatbotDistributionStack",
    alb=compute_stack.alb,
    env=env,
)
distribution_stack.add_dependency(compute_stack)

app.synth()
