variable "project"     { type = string }
variable "environment" { type = string }
variable "aws_region"  { type = string }

variable "vpc_id"         { type = string }
variable "alb_subnet_ids" { type = list(string) }
variable "ecs_subnet_ids" { type = list(string) }

variable "image_uri"      { type = string }   # ECR URI or external image
variable "desired_count"  { type = number; default = 1 }
variable "cpu"            { type = number; default = 512 }
variable "memory"         { type = number; default = 1024 }

variable "incidents_table"        { type = string }
variable "events_stream_name"     { type = string }
variable "cloudwatch_log_prefix"  { type = string; default = "/aws/lambda/" }

variable "anthropic_api_key"  { type = string; sensitive = true; default = "" }
variable "openai_api_key"     { type = string; sensitive = true; default = "" }
variable "gemini_api_key"     { type = string; sensitive = true; default = "" }
variable "slack_webhook_url"  { type = string; sensitive = true; default = "" }
variable "sentinel_api_key"   { type = string; sensitive = true; default = "" }
