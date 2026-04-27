locals {
  common_env = {
    FLOCI_ENDPOINT         = "http://host.docker.internal:4566"
    AWS_DEFAULT_REGION     = "us-east-1"
    AWS_ACCESS_KEY_ID      = "test"
    AWS_SECRET_ACCESS_KEY  = "test"
    KSERVE_ENDPOINT        = var.kserve_endpoint
    INCIDENTS_TABLE        = "${var.project}-incidents-${var.environment}"
    PR_REVIEWS_TABLE       = "${var.project}-pr-reviews-${var.environment}"
    VALIDATION_RESULTS_TABLE = "${var.project}-validation-results-${var.environment}"
    EVENTS_STREAM          = "${var.project}-events-${var.environment}"
    VALIDATION_JOBS_QUEUE_URL = var.validation_jobs_queue_url
    EVENTS_QUEUE_URL       = var.events_queue_url
    RAG_ENDPOINT           = var.rag_endpoint
    GITHUB_REPO            = var.github_repo
    CODEBASE_BUCKET        = "${var.project}-codebase-snapshots-${var.environment}"
  }
}

resource "aws_iam_role" "lambda_exec" {
  name = "${var.project}-lambda-exec-${var.environment}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project}-lambda-policy-${var.environment}"
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:*", "kinesis:*", "sqs:*", "s3:*", "secretsmanager:GetSecretValue", "logs:*"]
      Resource = "*"
    }]
  })
}

resource "aws_lambda_function" "pr_security_agent" {
  function_name = "${var.project}-pr-security-agent-${var.environment}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 120
  memory_size   = 512
  filename      = "${path.module}/../../../services/pr-security-agent/lambda.zip"

  environment {
    variables = merge(local.common_env, {
      GITHUB_TOKEN          = var.github_token
      GITHUB_WEBHOOK_SECRET = var.github_webhook_secret
    })
  }
}

resource "aws_lambda_function" "validator" {
  function_name = "${var.project}-validator-${var.environment}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256
  filename      = "${path.module}/../../../services/validator/lambda.zip"
  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_event_source_mapping" "validator_kinesis" {
  event_source_arn              = var.alerts_stream_arn
  function_name                 = aws_lambda_function.validator.arn
  starting_position             = "LATEST"
  batch_size                    = 10
  bisect_batch_on_function_error = true
}

resource "aws_lambda_function" "log_analyzer" {
  function_name = "${var.project}-log-analyzer-${var.environment}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512
  filename      = "${path.module}/../../../services/log-analyzer/lambda.zip"
  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_event_source_mapping" "log_analyzer_kinesis" {
  event_source_arn              = var.logs_stream_arn
  function_name                 = aws_lambda_function.log_analyzer.arn
  starting_position             = "LATEST"
  batch_size                    = 25
  bisect_batch_on_function_error = true
}

resource "aws_lambda_function" "root_cause_agent" {
  function_name = "${var.project}-root-cause-agent-${var.environment}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 1024
  filename      = "${path.module}/../../../services/root-cause-agent/lambda.zip"
  environment {
    variables = merge(local.common_env, {
      GITHUB_TOKEN = var.github_token
    })
  }
}
