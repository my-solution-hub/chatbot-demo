# outputs.tf

output "oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider (pre-existing)"
  value       = data.aws_iam_openid_connect_provider.github.arn
}

output "deploy_role_arn" {
  description = "ARN of the IAM role for GitHub Actions — set as GitHub secret AWS_DEPLOY_ROLE_ARN"
  value       = aws_iam_role.github_actions.arn
}

output "deploy_role_name" {
  description = "Name of the IAM role for GitHub Actions"
  value       = aws_iam_role.github_actions.name
}

output "aws_region" {
  description = "AWS region for deployment"
  value       = var.aws_region
}
