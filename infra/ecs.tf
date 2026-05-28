# Sentinel Dashboard — ECS Fargate
#
# Only created when var.deploy_dashboard = true AND var.use_floci = false.
# Floci does not support ECS; the dashboard runs locally via setup_demo.sh.
#
# Prerequisites before running terraform apply:
#   1. Build and push the Docker image:
#        docker build -t sentinel-dashboard .
#        aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
#        docker tag sentinel-dashboard <ecr_repo_url>:latest
#        docker push <ecr_repo_url>:latest
#      (ecr_repo_url is output by this module as `ecr_repository_url`)
#   2. Set required variables: vpc_id, alb_subnet_ids, ecs_subnet_ids
#   3. Set at least one LLM key: anthropic_api_key, openai_api_key, or gemini_api_key

module "ecs" {
  count  = (var.deploy_dashboard && !var.use_floci) ? 1 : 0
  source = "./modules/ecs"

  project     = var.project
  environment = var.environment
  aws_region  = var.aws_region

  vpc_id         = var.vpc_id
  alb_subnet_ids = var.alb_subnet_ids
  ecs_subnet_ids = var.ecs_subnet_ids

  # Image: use provided URI or the ECR repo created by this module on first apply.
  # On first run, set dashboard_image_uri to your ECR URI after the ECR repo is created.
  image_uri     = var.dashboard_image_uri != "" ? var.dashboard_image_uri : "amazon/amazon-ecs-sample:latest"
  desired_count = var.dashboard_desired_count
  cpu           = var.dashboard_cpu
  memory        = var.dashboard_memory

  incidents_table       = "${var.project}-incidents-${var.environment}"
  events_stream_name    = "${var.project}-events-${var.environment}"
  cloudwatch_log_prefix = var.cloudwatch_log_group_prefix

  anthropic_api_key = var.anthropic_api_key
  openai_api_key    = var.openai_api_key
  gemini_api_key    = var.gemini_api_key
  slack_webhook_url = var.slack_webhook_url
  sentinel_api_key  = var.sentinel_api_key
}

# Feed the dashboard URL back to the CloudWatch alarm receiver Lambda.
# The receiver POSTs new incidents here; the dashboard runs AI RCA.
resource "aws_lambda_function_event_invoke_config" "noop" {
  # placeholder — the actual URL wiring is done via the alarm receiver env var below
  count         = 0
  function_name = ""
}

locals {
  dashboard_url = (
    var.deploy_dashboard && !var.use_floci && length(module.ecs) > 0
    ? module.ecs[0].dashboard_url
    : var.sentinel_dashboard_url
  )
}
