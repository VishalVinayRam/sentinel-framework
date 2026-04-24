output "stream_arns" {
  value = {
    alerts = aws_kinesis_stream.alerts.arn
    logs   = aws_kinesis_stream.logs.arn
    events = aws_kinesis_stream.events.arn
  }
}

output "stream_names" {
  value = {
    alerts = aws_kinesis_stream.alerts.name
    logs   = aws_kinesis_stream.logs.name
    events = aws_kinesis_stream.events.name
  }
}
