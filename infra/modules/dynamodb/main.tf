resource "aws_dynamodb_table" "incidents" {
  name           = "${var.project}-incidents-${var.environment}"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "incident_id"
  range_key      = "created_at"

  attribute {
    name = "incident_id"
    type = "S"
  }
  attribute {
    name = "created_at"
    type = "S"
  }
  attribute {
    name = "severity"
    type = "S"
  }
  attribute {
    name = "service_name"
    type = "S"
  }

  global_secondary_index {
    name            = "severity-created-index"
    hash_key        = "severity"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "service-created-index"
    hash_key        = "service_name"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Incident event store (CQRS write model)"
  }
}

resource "aws_dynamodb_table" "pr_reviews" {
  name         = "${var.project}-pr-reviews-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pr_id"
  range_key    = "reviewed_at"

  attribute {
    name = "pr_id"
    type = "S"
  }
  attribute {
    name = "reviewed_at"
    type = "S"
  }
  attribute {
    name = "risk_level"
    type = "S"
  }

  global_secondary_index {
    name            = "risk-reviewed-index"
    hash_key        = "risk_level"
    range_key       = "reviewed_at"
    projection_type = "ALL"
  }

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "PR security review results"
  }
}

resource "aws_dynamodb_table" "validation_results" {
  name         = "${var.project}-validation-results-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "alert_id"
  range_key    = "validated_at"

  attribute {
    name = "alert_id"
    type = "S"
  }
  attribute {
    name = "validated_at"
    type = "S"
  }
  attribute {
    name = "is_real_incident"
    type = "S"
  }

  global_secondary_index {
    name            = "real-incident-index"
    hash_key        = "is_real_incident"
    range_key       = "validated_at"
    projection_type = "ALL"
  }

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Alert validation results (real vs false positive)"
  }
}

resource "aws_dynamodb_table" "event_store" {
  name         = "${var.project}-event-store-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "aggregate_id"
  range_key    = "sequence_number"

  attribute {
    name = "aggregate_id"
    type = "S"
  }
  attribute {
    name = "sequence_number"
    type = "N"
  }
  attribute {
    name = "event_type"
    type = "S"
  }

  global_secondary_index {
    name            = "event-type-index"
    hash_key        = "event_type"
    range_key       = "sequence_number"
    projection_type = "ALL"
  }

  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Event sourcing store — append-only domain events"
  }
}
