data "template_file" "state_machine" {
  template = file("${path.module}/../../../infra/step-functions/incident-response.json")
  vars = {
    RootCauseAgentLambdaArn = var.root_cause_agent_arn
  }
}

resource "aws_iam_role" "sfn_exec" {
  name = "${var.project}-sfn-exec-${var.environment}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "sfn_invoke_lambda" {
  name = "${var.project}-sfn-invoke-lambda-${var.environment}"
  role = aws_iam_role.sfn_exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [var.root_cause_agent_arn]
    }]
  })
}

resource "aws_sfn_state_machine" "incident_response" {
  name     = "${var.project}-incident-response-${var.environment}"
  role_arn = aws_iam_role.sfn_exec.arn
  definition = data.template_file.state_machine.rendered

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Orchestrates 5-step incident root cause analysis"
  }
}
