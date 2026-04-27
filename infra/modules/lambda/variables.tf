variable "project" { type = string }
variable "environment" { type = string }
variable "kserve_endpoint" { type = string }
variable "rag_endpoint" { type = string }
variable "github_repo" { type = string }
variable "github_token" { type = string; sensitive = true }
variable "github_webhook_secret" { type = string; sensitive = true }
variable "alerts_stream_arn" { type = string }
variable "logs_stream_arn" { type = string }
variable "validation_jobs_queue_url" { type = string }
variable "events_queue_url" { type = string }
