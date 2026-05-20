"""EcrStack: ECR repositories for container images."""

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class EcrStack(Stack):
    """Defines ECR repositories. Deployed first so images can be pushed before other stacks."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.proxy_repo = ecr.Repository(
            self,
            "ProxyRepo",
            repository_name="chatbot-demo-proxy",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=10, description="Keep last 10 images"),
            ],
        )

        # Strands agent container for AgentCore Runtime
        self.strands_agent_repo = ecr.Repository(
            self,
            "StrandsAgentRepo",
            repository_name="chatbot-demo-strands-agent",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=10, description="Keep last 10 images"),
            ],
        )
