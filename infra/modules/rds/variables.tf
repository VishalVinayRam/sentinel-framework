variable "project" { type = string }
variable "environment" { type = string }
variable "db_password" {
  type      = string
  sensitive = true
  default   = "sentinel_local_pass_123"  # overridden in prod via Secrets Manager
}
