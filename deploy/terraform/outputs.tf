# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "prepare_context_function_arn" {
  description = "ARN of the PrepareContext Lambda function"
  value       = aws_lambda_function.prepare_context.arn
}

output "security_analyzer_function_arn" {
  description = "ARN of the Security Analyzer Lambda function"
  value       = aws_lambda_function.security_analyzer.arn
}

output "resilience_analyzer_function_arn" {
  description = "ARN of the Resilience Analyzer Lambda function"
  value       = aws_lambda_function.resilience_analyzer.arn
}

output "cloudtrail_analyzer_parallel_function_arn" {
  description = "ARN of the CloudTrail Analyzer Lambda function (parallel)"
  value       = aws_lambda_function.cloudtrail_analyzer_parallel.arn
}

output "opex_analyzer_function_arn" {
  description = "ARN of the Operational Excellence Analyzer Lambda function"
  value       = aws_lambda_function.opex_analyzer.arn
}

output "capacity_analyzer_function_arn" {
  description = "ARN of the Capacity Analyzer Lambda function"
  value       = aws_lambda_function.capacity_analyzer.arn
}

output "observability_analyzer_function_arn" {
  description = "ARN of the Observability Analyzer Lambda function"
  value       = aws_lambda_function.observability_analyzer.arn
}

output "cost_analyzer_function_arn" {
  description = "ARN of the Cost Analyzer Lambda function"
  value       = aws_lambda_function.cost_analyzer.arn
}

output "report_generator_function_arn" {
  description = "ARN of the Report Generator Lambda function"
  value       = aws_lambda_function.report_generator.arn
}

output "orchestrator_state_machine_arn" {
  description = "ARN of the Step Functions state machine"
  value       = aws_sfn_state_machine.orchestrator.arn
}

output "ssm_config_parameter_name" {
  description = "Name of the SSM Parameter for runtime configuration"
  value       = aws_ssm_parameter.config.name
}

output "eventbridge_schedule_arn" {
  description = "ARN of the EventBridge Scheduler schedule"
  value       = try(aws_scheduler_schedule.review_schedule[0].arn, null)
}

output "shared_lambda_role_arn" {
  description = "ARN of the shared Lambda execution role"
  value       = aws_iam_role.shared_lambda.arn
}

output "ai_analyzer_function_arn" {
  description = "ARN of the AI Analyzer Lambda function"
  value       = aws_lambda_function.ai_analyzer.arn
}
