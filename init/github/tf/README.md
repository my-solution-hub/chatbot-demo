# GitHub Actions OIDC Bootstrap

This Terraform project creates the AWS resources needed for GitHub Actions to deploy to your AWS account via OIDC (no access keys needed).

## What it creates

1. **OIDC Identity Provider** — Trusts `token.actions.githubusercontent.com`
2. **IAM Role** — `chatbot-demo-github-actions-role` with AdministratorAccess (demo only)
3. Trust policy scoped to `my-solution-hub/chatbot-demo` repo, `main` branch

## Usage

```bash
cd init/github/tf

# Initialize Terraform
terraform init

# Review the plan
terraform plan

# Apply (creates OIDC provider + IAM role)
terraform apply
```

## After Apply

1. Copy the `deploy_role_arn` output value
2. Go to GitHub repo → Settings → Secrets and variables → Actions
3. Add secret: `AWS_DEPLOY_ROLE_ARN` = the role ARN from step 1

## Prerequisites

- Terraform >= 1.7
- AWS CLI configured with `default` profile pointing to your target account
- The target account must be in `ap-northeast-1` (Tokyo)
