resource "aws_kinesis_stream" "alerts" {
  name             = "${var.project}-alerts-${var.environment}"
  shard_count      = 2
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Incoming CloudWatch/Prometheus alerts — triggers incident validation"
  }
}

resource "aws_kinesis_stream" "logs" {
  name             = "${var.project}-logs-${var.environment}"
  shard_count      = 4
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Raw log stream — consumed by log analyzer for severity estimation"
  }
}

resource "aws_kinesis_stream" "events" {
  name             = "${var.project}-events-${var.environment}"
  shard_count      = 2
  retention_period = 48

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Domain event backbone — connects all Sentinel sub-systems"
  }
}
