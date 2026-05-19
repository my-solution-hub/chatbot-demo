# terraform.tfvars — Configuration for GitHub Actions OIDC bootstrap

project_name = "chatbot-demo"

# GitHub repository
github_org  = "my-solution-hub"
github_repo = "chatbot-demo"

# AWS configuration (default profile → ap-northeast-1)
aws_profile   = "default"
aws_region    = "ap-northeast-1"
deploy_branch = "main"

# Common tags
tags = {
  Project   = "chatbot-demo"
  ManagedBy = "terraform"
  Component = "oidc-bootstrap"
}
