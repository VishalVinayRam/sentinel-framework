variable "aws_region" {
  description = "AWS region (used for Floci emulation)"
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
  description = "URL of the Sentinel dashboard API (e.g. https://sentinel.internal). CloudWatch Lambda forwards incidents here."
  type        = string
  default     = ""
}
