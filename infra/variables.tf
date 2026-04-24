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
