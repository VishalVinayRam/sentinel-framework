variable "use_floci" {
  description = "Set to false to target real AWS. Defaults to true (Floci local emulator)."
  type        = bool
  default     = true
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "floci_endpoint" {
  description = "Floci local AWS emulator endpoint"
  type        = string
  default     = "http://localhost:4566"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "local"
}

variable "project" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "sentinel"
}

variable "kserve_endpoint" {
  description = "KServe inference endpoint URL (running in Minikube)"
  type        = string
  default     = "http://localhost:8080"
}

variable "rag_endpoint" {
  description = "RAG service endpoint URL"
  type        = string
  default     = "http://localhost:8001"
}

variable "github_repo" {
  description = "GitHub repo in owner/repo format"
  type        = string
  default     = "VishalVinayRam/Project-KEMM"
}

variable "github_token" {
  description = "GitHub personal access token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "github_webhook_secret" {
  description = "Secret for validating GitHub webhook signatures"
  type        = string
  sensitive   = true
  default     = ""
}

variable "sentinel_dashboard_url" {
  description = "URL of the Sentinel dashboard API. Set automatically when using the ECS module; override for external deployments."
  type        = string
  default     = ""
}

# ── ECS / Dashboard deployment ─────────────────────────────────────────────

variable "vpc_id" {
  description = "VPC ID for ECS tasks and ALB."
  type        = string
  default     = ""
}

variable "alb_subnet_ids" {
  description = "Public subnet IDs for the Application Load Balancer."
  type        = list(string)
  default     = []
}

variable "ecs_subnet_ids" {
  description = "Private subnet IDs for ECS Fargate tasks (need NAT for outbound)."
  type        = list(string)
  default     = []
}

variable "dashboard_image_uri" {
  description = "ECR image URI for the Sentinel dashboard (e.g. 123456789.dkr.ecr.us-east-1.amazonaws.com/sentinel-dashboard:latest). Leave empty and Terraform will reference the ECR repo it creates."
  type        = string
  default     = ""
}

variable "dashboard_desired_count" {
  description = "Number of ECS Fargate tasks to run."
  type        = number
  default     = 1
}

variable "dashboard_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "dashboard_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 1024
}

# ── LLM API keys ───────────────────────────────────────────────────────────
# These are passed as environment variables to the ECS task.
# For production, consider storing them in Secrets Manager and referencing
# via var.*_key_secret_arn instead.

variable "anthropic_api_key" {
  description = "Anthropic API key — enables Claude RCA."
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API key — enables GPT RCA."
  type        = string
  sensitive   = true
  default     = ""
}

variable "gemini_api_key" {
  description = "Google Gemini API key — enables Gemini RCA."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL for P1/P2 incident notifications."
  type        = string
  sensitive   = true
  default     = ""
}

variable "cloudwatch_log_group_prefix" {
  description = "CloudWatch log group prefix for your services. Lambda default: /aws/lambda/  ECS default: /ecs/"
  type        = string
  default     = "/aws/lambda/"
}

variable "sentinel_api_key" {
  description = "Static API key to protect the Sentinel dashboard REST endpoints."
  type        = string
  sensitive   = true
  default     = ""
}

variable "deploy_dashboard" {
  description = "Set to true to deploy the ECS Fargate dashboard. Requires vpc_id, alb_subnet_ids, ecs_subnet_ids."
  type        = bool
  default     = false
}
