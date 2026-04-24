output "s3_buckets" {
  description = "S3 bucket names"
  value       = module.s3.bucket_names
}

output "dynamodb_tables" {
  description = "DynamoDB table names"
  value       = module.dynamodb.table_names
}

output "kinesis_streams" {
  description = "Kinesis stream names and ARNs"
  value       = module.kinesis.stream_arns
}

output "sqs_queues" {
  description = "SQS queue URLs"
  value       = module.sqs.queue_urls
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = module.rds.endpoint
}

output "elasticache_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = module.elasticache.endpoint
}
