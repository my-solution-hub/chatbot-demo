#!/usr/bin/env python3
"""CDK app entry point for the Nova Sonic chatbot demo cloud deployment."""

import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.compute_stack import ComputeStack
from stacks.agent_stack import AgentStack
from stacks.distribution_stack import DistributionStack

app = cdk.App()

# Environment configuration
region = os.environ.get("AWS_REGION", os.environ.get("CDK_DEPLOY_REGION", "ap-northeast-1"))
account = os.environ.get("CDK_DEPLOY_ACCOUNT", os.environ.get("CDK_DEFAULT_ACCOUNT"))

env = cdk.Environment(account=account, region=region)

# Stack instantiation
network_stack = NetworkStack(app, "ChatbotNetworkStack", env=env)

compute_stack = ComputeStack(
    app,
    "ChatbotComputeStack",
    vpc=network_stack.vpc,
    env=env,
)
compute_stack.add_dependency(network_stack)

agent_stack = AgentStack(app, "ChatbotAgentStack", env=env)

distribution_stack = DistributionStack(
    app,
    "ChatbotDistributionStack",
    alb=compute_stack.alb,
    env=env,
)
distribution_stack.add_dependency(compute_stack)

app.synth()
