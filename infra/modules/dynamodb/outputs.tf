output "table_names" {
  value = {
    incidents          = aws_dynamodb_table.incidents.name
    pr_reviews         = aws_dynamodb_table.pr_reviews.name
    validation_results = aws_dynamodb_table.validation_results.name
    event_store        = aws_dynamodb_table.event_store.name
  }
}

output "stream_arns" {
  value = {
    incidents   = aws_dynamodb_table.incidents.stream_arn
    event_store = aws_dynamodb_table.event_store.stream_arn
  }
}
