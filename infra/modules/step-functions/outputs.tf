output "state_machine_arn" {
  value = aws_sfn_state_machine.incident_response.arn
}

output "state_machine_name" {
  value = aws_sfn_state_machine.incident_response.name
}
