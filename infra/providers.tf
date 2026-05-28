terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── Provider ──────────────────────────────────────────────────────────────────
#
# Two modes, controlled by var.use_floci:
#
#   use_floci = true  (default)
#     Targets Floci (local AWS emulator). All endpoints redirect to localhost:4566.
#     Dummy credentials are injected — no real AWS account needed.
#     Run: terraform apply  (no extra vars needed)
#
#   use_floci = false
#     Targets real AWS. Credentials come from the standard chain:
#       env vars  →  ~/.aws/credentials  →  IAM instance/task role
#     Run: terraform apply -var="use_floci=false" -var="aws_region=us-east-1"

locals {
  floci = var.use_floci ? var.floci_endpoint : null
}

provider "aws" {
  region = var.aws_region

  # Floci mode only — real AWS uses the default credential chain
  access_key                  = var.use_floci ? "test" : null
  secret_key                  = var.use_floci ? "test" : null
  skip_credentials_validation = var.use_floci
  skip_metadata_api_check     = var.use_floci
  skip_requesting_account_id  = var.use_floci

  # Endpoint overrides — null means "use the real AWS endpoint"
  endpoints {
    s3              = local.floci
    dynamodb        = local.floci
    kinesis         = local.floci
    lambda          = local.floci
    apigateway      = local.floci
    apigatewayv2    = local.floci
    sqs             = local.floci
    sns             = local.floci
    elasticache     = local.floci
    rds             = local.floci
    secretsmanager  = local.floci
    kms             = local.floci
    iam             = local.floci
    sts             = local.floci
    cloudwatch      = local.floci
    logs            = local.floci
    events          = local.floci
    stepfunctions   = local.floci
    cognitoidentity = local.floci
    cognitoidp      = local.floci
    ecr             = local.floci
    ecs             = local.floci
  }
}
