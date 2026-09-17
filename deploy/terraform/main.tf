# ============================================================================
# Amazon Connect Operational Review — Terraform Module
#
# All infrastructure is deployed via the parallel architecture in parallel.tf:
#   - PrepareContext Lambda (reads SSM config)
#   - 7 Analyzer Lambdas (each with own IAM role)
#   - Report Generator Lambda
#   - Step Functions state machine
#   - SSM Parameter for runtime config
#   - EventBridge Scheduler targeting state machine
#   - Optional Glue Catalog resources
#
# Lambda code is packaged from lambda_packages/ via archive_file data sources.
# Run scripts/update_tf_lambda.py to populate that directory from the code
# repository before running terraform plan/apply.
# ============================================================================
