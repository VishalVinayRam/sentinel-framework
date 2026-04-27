output "pr_agent_arn" {
  value = aws_lambda_function.pr_security_agent.arn
}

output "validator_arn" {
  value = aws_lambda_function.validator.arn
}

output "log_analyzer_arn" {
  value = aws_lambda_function.log_analyzer.arn
}

output "root_cause_agent_arn" {
  value = aws_lambda_function.root_cause_agent.arn
}

output "lambda_exec_role_arn" {
  value = aws_iam_role.lambda_exec.arn
}
