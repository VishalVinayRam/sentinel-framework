resource "aws_sqs_queue" "validation_dlq" {
  name                       = "${var.project}-validation-dlq-${var.environment}"
  message_retention_seconds  = 1209600 # 14 days
  tags = {
    Project = var.project
    Purpose = "Dead letter queue for failed validation jobs"
  }
}

resource "aws_sqs_queue" "validation_jobs" {
  name                       = "${var.project}-validation-jobs-${var.environment}"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 3600
  receive_wait_time_seconds  = 20  # long polling

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.validation_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Project = var.project
    Purpose = "Smoke test jobs dispatched during incident validation"
  }
}

resource "aws_sqs_queue" "log_ingestion_dlq" {
  name                      = "${var.project}-log-ingestion-dlq-${var.environment}"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "log_ingestion" {
  name                       = "${var.project}-log-ingestion-${var.environment}"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 3600
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.log_ingestion_dlq.arn
    maxReceiveCount     = 5
  })

  tags = {
    Project = var.project
    Purpose = "Log files dropped to S3 trigger this queue for batch processing"
  }
}

resource "aws_sqs_queue" "notification_dlq" {
  name                      = "${var.project}-notification-dlq-${var.environment}"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "notifications" {
  name                       = "${var.project}-notifications-${var.environment}"
  visibility_timeout_seconds = 30
  message_retention_seconds  = 3600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.notification_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Project = var.project
    Purpose = "SNS fan-out target for on-call notifications"
  }
}
