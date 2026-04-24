resource "aws_s3_bucket" "codebase_snapshots" {
  bucket = "${var.project}-codebase-snapshots-${var.environment}"
}

resource "aws_s3_bucket" "incident_logs" {
  bucket = "${var.project}-incident-logs-${var.environment}"
}

resource "aws_s3_bucket" "model_artifacts" {
  bucket = "${var.project}-model-artifacts-${var.environment}"
}

resource "aws_s3_bucket_versioning" "codebase_snapshots" {
  bucket = aws_s3_bucket.codebase_snapshots.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "incident_logs" {
  bucket = aws_s3_bucket.incident_logs.id
  rule {
    id     = "archive-old-logs"
    status = "Enabled"
    expiration {
      days = 90
    }
    filter {
      prefix = "raw/"
    }
  }
}

resource "aws_s3_bucket_notification" "incident_logs_notify" {
  bucket = aws_s3_bucket.incident_logs.id
  queue {
    queue_arn     = "arn:aws:sqs:us-east-1:000000000000:${var.project}-log-ingestion-${var.environment}"
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/"
  }
}
