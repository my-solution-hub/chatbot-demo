"""DistributionStack: CloudFront distribution."""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from constructs import Construct


class DistributionStack(Stack):
    """Defines CloudFront distribution with WebSocket passthrough."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        alb: elbv2.IApplicationLoadBalancer,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.alb = alb

        # --- Custom Origin Request Policy for adding X-Origin-Verify header ---
        # CloudFront adds this header to all origin requests so the ALB can
        # verify traffic is coming from CloudFront (matches the ALB listener rule).
        origin_request_policy = cloudfront.OriginRequestPolicy(
            self,
            "AllowWebSocketHeaders",
            origin_request_policy_name="ChatbotOriginRequestPolicy",
            header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list(
                "Sec-WebSocket-Key",
                "Sec-WebSocket-Version",
                "Sec-WebSocket-Protocol",
                "Sec-WebSocket-Extensions",
                "Sec-WebSocket-Accept",
                "Connection",
                "Upgrade",
            ),
        )

        # --- ALB Origin with custom header ---
        alb_origin = origins.HttpOrigin(
            domain_name=alb.load_balancer_dns_name,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            custom_headers={
                "X-Origin-Verify": "chatbot-cloudfront-secret",
            },
        )

        # --- Cache Policy: Disabled (for WebSocket path) ---
        # WebSocket connections cannot be cached
        websocket_cache_policy = cloudfront.CachePolicy.CACHING_DISABLED

        # --- Cache Policy: Static assets (default behavior) ---
        static_cache_policy = cloudfront.CachePolicy(
            self,
            "StaticAssetsCachePolicy",
            cache_policy_name="ChatbotStaticAssets",
            default_ttl=Duration.hours(1),
            max_ttl=Duration.days(7),
            min_ttl=Duration.seconds(0),
            header_behavior=cloudfront.CacheHeaderBehavior.none(),
            query_string_behavior=cloudfront.CacheQueryStringBehavior.none(),
            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
        )

        # --- CloudFront Distribution ---
        self.distribution = cloudfront.Distribution(
            self,
            "ChatbotDistribution",
            comment="Nova Sonic Chatbot Demo - CloudFront Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=alb_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=static_cache_policy,
                origin_request_policy=origin_request_policy,
            ),
            additional_behaviors={
                "/ws/session": cloudfront.BehaviorOptions(
                    origin=alb_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=websocket_cache_policy,
                    origin_request_policy=origin_request_policy,
                ),
            },
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "DistributionDomainName",
            value=self.distribution.distribution_domain_name,
            description="CloudFront distribution domain name",
        )
        CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID",
        )
