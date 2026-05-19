variable "project_name" {
  type        = string
  description = "Project name used for resource naming"
  default     = "chatbot-demo"
}

variable "github_org" {
  type        = string
  description = "GitHub organization or username"
}

variable "github_repo" {
  type        = string
  description = "GitHub repository name (without org prefix)"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI profile to use"
  default     = "default"
}

variable "aws_region" {
  type        = string
  description = "AWS region for deployment"
  default     = "ap-northeast-1"
}

variable "deploy_branch" {
  type        = string
  description = "Branch that triggers deployment"
  default     = "main"
}

variable "tags" {
  type        = map(string)
  description = "Common tags for all resources"
  default = {
    Project   = "chatbot-demo"
    ManagedBy = "terraform"
    Component = "oidc-bootstrap"
  }
}
