# CloudWatch Alarm → SNS → Lambda → Sentinel incident receiver
#
# Alarm naming convention: <severity>-<service>-<metric>
# e.g. p1-auth-service-error-rate, p2-payments-latency-high

# ── Lambda ────────────────────────────────────────────────────────────────────

data "archive_file" "cw_alarm_receiver" {
  type        = "zip"
  source_file = "${path.root}/../services/cloudwatch-alarm-receiver/handler.py"
  output_path = "${path.root}/.terraform/cw_alarm_receiver.zip"
}

resource "aws_lambda_function" "cw_alarm_receiver" {
  function_name    = "${var.project}-cw-alarm-receiver-${var.environment}"
  filename         = data.archive_file.cw_alarm_receiver.output_path
  source_code_hash = data.archive_file.cw_alarm_receiver.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.11"
  role             = aws_iam_role.cw_alarm_receiver.arn
  timeout          = 30

  environment {
    variables = {
      INCIDENTS_TABLE        = "${var.project}-incidents"
      EVENTS_STREAM          = "${var.project}-events"
      AWS_REGION             = var.aws_region
      SENTINEL_DASHBOARD_URL = var.sentinel_dashboard_url
    }
  }

  tags = {
    Project     = var.project
    Environment = var.environment
    Component   = "cloudwatch-alarm-receiver"
  }
}

# ── IAM ───────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "cw_alarm_receiver" {
  name = "${var.project}-cw-alarm-receiver-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "cw_alarm_receiver" {
  name = "cw-alarm-receiver-policy"
  role = aws_iam_role.cw_alarm_receiver.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = "arn:aws:dynamodb:*:*:table/${var.project}-incidents"
      },
      {
        Effect   = "Allow"
        Action   = ["kinesis:PutRecord"]
        Resource = "arn:aws:kinesis:*:*:stream/${var.project}-events"
      },
    ]
  })
}

# ── SNS topic ─────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "cloudwatch_alarms" {
  name = "${var.project}-cloudwatch-alarms-${var.environment}"

  tags = {
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_sns_topic_subscription" "cw_alarm_to_lambda" {
  topic_arn = aws_sns_topic.cloudwatch_alarms.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.cw_alarm_receiver.arn
}

resource "aws_lambda_permission" "sns_invoke_cw_receiver" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cw_alarm_receiver.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.cloudwatch_alarms.arn
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "cloudwatch_alarms_sns_topic_arn" {
  description = "Set this ARN as the AlarmActions target on your CloudWatch alarms."
  value       = aws_sns_topic.cloudwatch_alarms.arn
}
