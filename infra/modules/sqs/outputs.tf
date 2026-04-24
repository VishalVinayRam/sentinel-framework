output "queue_urls" {
  value = {
    validation_jobs = aws_sqs_queue.validation_jobs.url
    log_ingestion   = aws_sqs_queue.log_ingestion.url
    notifications   = aws_sqs_queue.notifications.url
  }
}

output "queue_arns" {
  value = {
    validation_jobs = aws_sqs_queue.validation_jobs.arn
    log_ingestion   = aws_sqs_queue.log_ingestion.arn
    notifications   = aws_sqs_queue.notifications.arn
  }
}
