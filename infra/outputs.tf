output "cloudwatch_alarm_receiver_sns_arn" {
  description = "Set this ARN as AlarmActions on your CloudWatch alarms."
  value       = aws_sns_topic.cloudwatch_alarms.arn
}

output "sentinel_dashboard_url" {
  description = "Sentinel dashboard URL (ALB DNS). Set this as SENTINEL_DASHBOARD_URL if managing the Lambda separately."
  value       = length(module.ecs) > 0 ? module.ecs[0].dashboard_url : "(deploy_dashboard=false)"
}

output "ecr_repository_url" {
  description = "Push your Docker image here before deploying the ECS service."
  value       = length(module.ecs) > 0 ? module.ecs[0].ecr_repository_url : "(deploy_dashboard=false)"
}

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
