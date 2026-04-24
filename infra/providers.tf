terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Floci local AWS emulator endpoint
locals {
  floci_endpoint = var.floci_endpoint
}

provider "aws" {
  region                      = var.aws_region
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3             = local.floci_endpoint
    dynamodb       = local.floci_endpoint
    kinesis        = local.floci_endpoint
    lambda         = local.floci_endpoint
    apigateway     = local.floci_endpoint
    apigatewayv2   = local.floci_endpoint
    sqs            = local.floci_endpoint
    sns            = local.floci_endpoint
    elasticache    = local.floci_endpoint
    rds            = local.floci_endpoint
    secretsmanager = local.floci_endpoint
    kms            = local.floci_endpoint
    iam            = local.floci_endpoint
    sts            = local.floci_endpoint
    cloudwatch     = local.floci_endpoint
    logs           = local.floci_endpoint
    events         = local.floci_endpoint
    stepfunctions  = local.floci_endpoint
    cognitoidentity = local.floci_endpoint
    cognitoidp      = local.floci_endpoint
  }
}
