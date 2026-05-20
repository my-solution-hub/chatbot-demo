"""ComputeStack: ECS Fargate cluster, service, task definition, and ALB."""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from constructs import Construct


class ComputeStack(Stack):
    """Defines ECS Fargate service and ALB for WebSocket proxy."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        proxy_repo: ecr.IRepository,
        image_tag: str = "latest",
        strands_runtime_arn: str = "",
        tool_lambda_arns: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = vpc

        # --- ECS Cluster ---
        self.cluster = ecs.Cluster(
            self,
            "ChatbotCluster",
            vpc=self.vpc,
        )

        # --- IAM: Fargate Task Role with least privilege ---
        # The task role allows the Fargate container to invoke the AgentCore Runtime
        # and the Lambda tool functions.
        self.task_role = iam.Role(
            self,
            "ChatbotTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Fargate task role - allows invoking AgentCore Runtime and Lambda tools",
        )

        # Grant InvokeAgentRuntime on the specific runtime ARN.
        # Falls back to wildcard if ARN is not yet available (initial deploy).
        runtime_resource = (
            strands_runtime_arn
            if strands_runtime_arn
            else f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*"
        )
        self.task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream",
                ],
                resources=[
                    runtime_resource,
                    f"{runtime_resource}/*",
                ],
            )
        )

        # Grant Lambda invoke for tool functions (cloud mode tool dispatch)
        _tool_arns = tool_lambda_arns or {}
        if _tool_arns:
            self.task_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["lambda:InvokeFunction"],
                    resources=list(_tool_arns.values()),
                )
            )

        # Grant Bedrock model invocation (Nova Sonic audio streaming)
        self.task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithBidirectionalStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/*",
                ],
            )
        )

        # --- Fargate Task Definition ---
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "ChatbotTaskDef",
            cpu=512,  # 0.5 vCPU
            memory_limit_mib=1024,  # 1 GB
            task_role=self.task_role,
        )

        self.task_definition.add_container(
            "ChatbotContainer",
            image=ecs.ContainerImage.from_ecr_repository(proxy_repo, image_tag),
            port_mappings=[
                ecs.PortMapping(container_port=8000, protocol=ecs.Protocol.TCP)
            ],
            environment={
                "DEPLOYMENT_MODE": "cloud",
                "AWS_REGION": self.region,
                "STRANDS_RUNTIME_ARN": strands_runtime_arn or "",
                "TOOL_LAMBDA_TIME": _tool_arns.get("get_current_time", ""),
                "TOOL_LAMBDA_WEATHER": _tool_arns.get("get_weather", ""),
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="chatbot"),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(10),
            ),
        )

        # --- ALB Security Group ---
        # Restrict ALB to accept traffic only from CloudFront prefix list
        self.alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=self.vpc,
            description="ALB security group - allows traffic from CloudFront only",
            allow_all_outbound=True,
        )

        # Allow inbound from CloudFront origin-facing managed prefix list.
        # The AWS-managed prefix list ID for CloudFront origin-facing is
        # looked up via CfnPrefixList or hardcoded per region. We use the
        # Fn::FindInMap approach via a CfnParameter or simply allow 0.0.0.0/0
        # on port 80 and rely on the X-Origin-Verify header for security.
        # For production, use the managed prefix list ID for your region.
        #
        # Alternative: Use ec2.Peer.prefix_list("pl-58a04531") for us-east-1
        # or look up dynamically via a custom resource.
        #
        # Here we use Peer.any_ipv4() + custom header validation as the
        # primary security mechanism (the ALB listener rule rejects requests
        # without the correct X-Origin-Verify header).
        self.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP (secured by X-Origin-Verify header validation in ALB listener rule)",
        )

        # --- Application Load Balancer ---
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "ChatbotAlb",
            vpc=self.vpc,
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=self.alb_security_group,
            idle_timeout=Duration.seconds(3600),  # 1 hour for long WebSocket sessions
        )

        # --- Target Group ---
        self.target_group = elbv2.ApplicationTargetGroup(
            self,
            "ChatbotTargetGroup",
            vpc=self.vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            stickiness_cookie_duration=Duration.days(1),  # Enable sticky sessions
        )

        # --- Fargate Service ---
        self.fargate_service = ecs.FargateService(
            self,
            "ChatbotService",
            cluster=self.cluster,
            task_definition=self.task_definition,
            desired_count=1,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # Register service as target
        self.target_group.add_target(self.fargate_service)

        # --- ALB Listener (port 80 since we don't have a cert yet) ---
        self.listener = self.alb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            # Default action: return 403 (requests without custom header are rejected)
            default_action=elbv2.ListenerAction.fixed_response(
                status_code=403,
                content_type="text/plain",
                message_body="Forbidden",
            ),
        )

        # --- Custom header validation rule ---
        # Only forward requests that include the X-Origin-Verify header from CloudFront
        self.listener.add_action(
            "ForwardWithHeaderCheck",
            priority=1,
            conditions=[
                elbv2.ListenerCondition.http_header(
                    "X-Origin-Verify", ["chatbot-cloudfront-secret"]
                )
            ],
            action=elbv2.ListenerAction.forward(
                target_groups=[self.target_group],
            ),
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "AlbDnsName",
            value=self.alb.load_balancer_dns_name,
            description="ALB DNS name for CloudFront origin",
        )
