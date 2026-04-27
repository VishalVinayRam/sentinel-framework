resource "aws_apigatewayv2_api" "sentinel" {
  name          = "${var.project}-api-${var.environment}"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["Content-Type", "X-Hub-Signature-256", "Authorization"]
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.sentinel.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }
}

resource "aws_apigatewayv2_integration" "pr_agent" {
  api_id                 = aws_apigatewayv2_api.sentinel.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.pr_agent_lambda_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "pr_webhook" {
  api_id    = aws_apigatewayv2_api.sentinel.id
  route_key = "POST /webhooks/github/pr"
  target    = "integrations/${aws_apigatewayv2_integration.pr_agent.id}"
}

resource "aws_lambda_permission" "apigw_pr_agent" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.pr_agent_lambda_arn
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.sentinel.execution_arn}/*/*"
}
