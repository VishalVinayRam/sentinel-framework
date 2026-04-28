module "s3" {
  source      = "./modules/s3"
  project     = var.project
  environment = var.environment
}

module "dynamodb" {
  source      = "./modules/dynamodb"
  project     = var.project
  environment = var.environment
}

module "kinesis" {
  source      = "./modules/kinesis"
  project     = var.project
  environment = var.environment
}

module "sqs" {
  source      = "./modules/sqs"
  project     = var.project
  environment = var.environment
}

module "rds" {
  source      = "./modules/rds"
  project     = var.project
  environment = var.environment
}

module "elasticache" {
  source      = "./modules/elasticache"
  project     = var.project
  environment = var.environment
}

module "lambda" {
  source                    = "./modules/lambda"
  project                   = var.project
  environment               = var.environment
  kserve_endpoint           = var.kserve_endpoint
  rag_endpoint              = var.rag_endpoint
  github_repo               = var.github_repo
  github_token              = var.github_token
  github_webhook_secret     = var.github_webhook_secret
  alerts_stream_arn         = module.kinesis.stream_arns["alerts"]
  logs_stream_arn           = module.kinesis.stream_arns["logs"]
  validation_jobs_queue_url = module.sqs.queue_urls["validation_jobs"]
  events_queue_url          = module.sqs.queue_urls["notifications"]
}

module "api_gateway" {
  source              = "./modules/api-gateway"
  project             = var.project
  environment         = var.environment
  pr_agent_lambda_arn = module.lambda.pr_agent_arn
}
