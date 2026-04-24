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
