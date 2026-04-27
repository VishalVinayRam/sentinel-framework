output "api_endpoint" {
  value = aws_apigatewayv2_api.sentinel.api_endpoint
}

output "webhook_url" {
  value = "${aws_apigatewayv2_api.sentinel.api_endpoint}/webhooks/github/pr"
}
