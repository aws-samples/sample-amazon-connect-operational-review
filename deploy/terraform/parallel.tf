# ============================================================================
# Parallel Orchestration Architecture Resources
#
# Deploys the Step Functions fan-out/fan-in architecture:
#   - PrepareContext Lambda (reads SSM config)
#   - 7 Analyzer Lambdas (each with own IAM role)
#   - Report Generator Lambda
#   - Step Functions state machine
#   - SSM Parameter for runtime config
#   - EventBridge rule targeting state machine
#   - Optional Glue Catalog resources
# ============================================================================

# ---------------------------------------------------------------------------
# Local values derived from analyzer-config.json
# ---------------------------------------------------------------------------

locals {
  analyzer_config = jsondecode(file("${path.module}/analyzer-config.json"))

  # Analyzer definitions from config
  analyzers = local.analyzer_config.analyzers

  # Component types for iteration
  analyzer_types = ["cloudtrail", "security", "resilience", "operational_excellence", "capacity", "observability", "cost", "ai"]

  # Glue table definitions
  glue_tables = var.enable_glue_catalog ? local.analyzer_types : []
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Lambda code packaging — Parallel Architecture
# ---------------------------------------------------------------------------

data "archive_file" "prepare_context_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/prepare_context.py"
  output_path = "${path.module}/lambda_packages/prepare_context.zip"
}

data "archive_file" "cloudtrail_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/cloudtrail_analyzer.py"
  output_path = "${path.module}/lambda_packages/cloudtrail_analyzer.zip"
}

data "archive_file" "security_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/security_analyzer.py"
  output_path = "${path.module}/lambda_packages/security_analyzer.zip"
}

data "archive_file" "resilience_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/resilience_analyzer.py"
  output_path = "${path.module}/lambda_packages/resilience_analyzer.zip"
}

data "archive_file" "opex_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/opex_analyzer.py"
  output_path = "${path.module}/lambda_packages/opex_analyzer.zip"
}

data "archive_file" "capacity_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/capacity_analyzer.py"
  output_path = "${path.module}/lambda_packages/capacity_analyzer.zip"
}

data "archive_file" "observability_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/observability_analyzer.py"
  output_path = "${path.module}/lambda_packages/observability_analyzer.zip"
}

data "archive_file" "cost_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/cost_analyzer.py"
  output_path = "${path.module}/lambda_packages/cost_analyzer.zip"
}

data "archive_file" "ai_analyzer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/ai_analyzer.py"
  output_path = "${path.module}/lambda_packages/ai_analyzer.zip"
}

data "archive_file" "report_generator_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/report_generator.py"
  output_path = "${path.module}/lambda_packages/report_generator.zip"
}

data "archive_file" "delete_json_data_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_packages/delete_json_data.py"
  output_path = "${path.module}/lambda_packages/delete_json_data.zip"
}

# ---------------------------------------------------------------------------
# Lambda Layer — Shared Utilities (analyzer_common + graceful_timeout)
# ---------------------------------------------------------------------------

data "archive_file" "shared_utils_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_layers/shared_utils"
  output_path = "${path.module}/lambda_layers/shared_utils.zip"
}

resource "aws_lambda_layer_version" "shared_utils" {
  layer_name          = "ConnectOpsReview-SharedUtils"
  description         = "Shared utilities: analyzer_common.py and graceful_timeout.py"
  filename            = data.archive_file.shared_utils_layer_zip.output_path
  source_code_hash    = data.archive_file.shared_utils_layer_zip.output_base64sha256
  compatible_runtimes = ["python3.12"]
}

# ---------------------------------------------------------------------------
# Data source — current AWS account ID
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Shared Lambda Execution Role — used by all 10 analyzer Lambda functions
# ---------------------------------------------------------------------------

resource "aws_iam_role" "shared_lambda" {
  name = "ConnectOpsReview-SharedLambdaRole-${data.aws_region.current.name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "shared_lambda_basic" {
  role       = aws_iam_role.shared_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "shared_lambda_connect_readonly" {
  name = "ConnectReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "connect:DescribeInstance",
        "connect:DescribeInstanceAttribute",
        "connect:DescribeInstanceStorageConfig",
        "connect:DescribeRoutingProfile",
        "connect:DescribeQueue",
        "connect:DescribeContactFlow",
        "connect:DescribeContactFlowModule",
        "connect:DescribeUser",
        "connect:DescribeHoursOfOperation",
        "connect:DescribeQuickConnect",
        "connect:DescribeSecurityProfile",
        "connect:DescribeAgentStatus",
        "connect:DescribeRule",
        "connect:DescribePhoneNumber",
        "connect:DescribeTrafficDistributionGroup",
        "connect:ListInstances",
        "connect:ListRoutingProfiles",
        "connect:ListQueues",
        "connect:ListContactFlows",
        "connect:ListContactFlowModules",
        "connect:ListUsers",
        "connect:ListHoursOfOperations",
        "connect:ListPhoneNumbers",
        "connect:ListPhoneNumbersV2",
        "connect:ListLambdaFunctions",
        "connect:ListSecurityProfiles",
        "connect:ListInstanceStorageConfigs",
        "connect:ListInstanceAttributes",
        "connect:ListBots",
        "connect:ListPrompts",
        "connect:ListQuickConnects",
        "connect:ListAgentStatuses",
        "connect:ListRules",
        "connect:ListFlowAssociations",
        "connect:ListContactReferences",
        "connect:ListTrafficDistributionGroups",
        "connect:SearchQueues",
        "connect:SearchRoutingProfiles",
        "connect:SearchUsers",
        "connect:GetMetricDataV2",
        "connect:ListUserHierarchyGroups",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_cloudtrail_readonly" {
  name = "CloudTrailReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["cloudtrail:LookupEvents"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_service_quotas_readonly" {
  name = "ServiceQuotasReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "servicequotas:GetServiceQuota",
        "servicequotas:ListServiceQuotas",
        "servicequotas:GetAWSDefaultServiceQuota",
        "servicequotas:ListAWSDefaultServiceQuotas",
        "servicequotas:ListRequestedServiceQuotaChangeHistory",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_cloudwatch_readonly" {
  name = "CloudWatchReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cloudwatch:DescribeAlarms",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarmsForMetric",
        "cloudwatch:ListDashboards",
        "cloudwatch:GetDashboard",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_cost_explorer_readonly" {
  name = "CostExplorerReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ce:GetCostAndUsage"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_kms_readonly" {
  name = "KMSReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "kms:DescribeKey",
        "kms:ListAliases",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_cases_readonly" {
  name = "CasesReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cases:ListDomains",
        "cases:ListCases",
        "cases:GetCase",
        "cases:ListFields",
        "cases:ListTemplates",
        "cases:ListCaseRules",
        "cases:ListLayouts",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_profiles_readonly" {
  name = "ProfilesReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "profile:ListDomains",
        "profile:GetDomain",
        "profile:ListIntegrations",
        "profile:ListProfileObjectTypes",
        "profile:ListEventTriggers",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_appintegrations_readonly" {
  name = "AppIntegrationsReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "app-integrations:ListEventIntegrations",
        "app-integrations:ListDataIntegrations",
        "app-integrations:ListApplications",
        "app-integrations:ListDataIntegrationAssociations",
        "app-integrations:ListEventIntegrationAssociations",
        "app-integrations:GetEventIntegration",
        "app-integrations:GetDataIntegration",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_logs_readonly" {
  name = "LogsReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:DescribeLogGroups",
        "logs:DescribeMetricFilters",
        "logs:DescribeDeliveries",
        "logs:DescribeDeliverySources",
        "logs:DescribeDeliveryDestinations",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_ssm_read_config" {
  name = "SSMReadConfig"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter"]
      Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/connect-ops-review/config"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_qconnect_readonly" {
  name = "QConnectReadOnly"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "qconnect:ListAIAgents",
        "qconnect:GetAIAgent",
        "qconnect:ListAIAgentVersions",
        "qconnect:ListAIPrompts",
        "qconnect:GetAIPrompt",
        "qconnect:ListAIPromptVersions",
        "qconnect:ListAIGuardrails",
        "qconnect:GetAIGuardrail",
        "qconnect:GetAssistant",
        "qconnect:ListAssistants",
        "qconnect:ListKnowledgeBases",
        "qconnect:GetKnowledgeBase",
        "qconnect:ListContents",
        "qconnect:ListAssistantAssociations",
        # wisdom namespace (belt-and-braces for AWS IAM aliasing inconsistency)
        "wisdom:ListAIAgents",
        "wisdom:GetAIAgent",
        "wisdom:ListAIAgentVersions",
        "wisdom:ListAIPrompts",
        "wisdom:GetAIPrompt",
        "wisdom:ListAIPromptVersions",
        "wisdom:ListAIGuardrails",
        "wisdom:GetAIGuardrail",
        "wisdom:GetAssistant",
        "wisdom:ListAssistants",
        "wisdom:ListKnowledgeBases",
        "wisdom:GetKnowledgeBase",
        "wisdom:ListContents",
        "wisdom:ListAssistantAssociations",
      ]
      Resource = [
        "arn:aws:qconnect:*:${data.aws_caller_identity.current.account_id}:*",
        "arn:aws:wisdom:*:${data.aws_caller_identity.current.account_id}:*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_connect_integrations" {
  name = "ConnectIntegrations"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "connect:ListIntegrationAssociations",
        "connect:ListSecurityProfiles",
        "connect:ListSecurityProfileApplications",
      ]
      Resource = "arn:aws:connect:*:${data.aws_caller_identity.current.account_id}:instance/*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_pinpoint" {
  name = "PinpointPhoneNumberValidate"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["mobiletargeting:PhoneNumberValidate"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_s3_read" {
  name = "S3ReadAnalyzerData"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "arn:aws:s3:::${var.s3_reporting_bucket}/data/*"
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_s3_write" {
  name = "S3WriteAnalyzerResults"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:PutObject"]
      Resource = [
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/shared/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/security/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/resilience/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/operational_excellence/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/capacity/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/observability/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/cost/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/cloudtrail/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/data/ai/*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/connect-review_*",
        "arn:aws:s3:::${var.s3_reporting_bucket}/review-metadata/*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "shared_lambda_s3_delete" {
  name = "S3DeleteAnalyzerData"
  role = aws_iam_role.shared_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${var.s3_reporting_bucket}"
        Condition = {
          StringLike = {
            "s3:prefix" = "data/*"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = "arn:aws:s3:::${var.s3_reporting_bucket}/data/*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda Function — PrepareContext
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "prepare_context" {
  function_name    = "ConnectOpsReview-PrepareContext"
  description      = "Reads SSM config, normalizes input into ExecutionContext"
  handler          = "prepare_context.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.prepare_context_zip.output_path
  source_code_hash = data.archive_file.prepare_context_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      CONFIG_SSM_PARAM     = "/connect-ops-review/config"
      CONNECT_INSTANCE_ARN = var.connect_instance_arn
      S3_REPORTING_BUCKET  = var.s3_reporting_bucket
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Functions — Analyzers (parallel)
#
# All analyzer Lambdas use timeout = 900s. The HARD_CEILING constant (840s)
# in prepare_context.py is derived as: Timeout (900) − SAFETY_MARGIN (60) = 840.
# This guarantees a minimum 60s persistence window before Lambda hard-kill.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Lambda Function — CloudTrail Analyzer (parallel)
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "cloudtrail_analyzer_parallel" {
  function_name    = "ConnectOpsReview-CloudTrailAnalyzer"
  description      = "CloudTrail event analysis — API throttling, error patterns, audit trail"
  handler          = "cloudtrail_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 512
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.cloudtrail_zip.output_path
  source_code_hash = data.archive_file.cloudtrail_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "cloudtrail"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Security Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "security_analyzer" {
  function_name    = "ConnectOpsReview-SecurityAnalyzer"
  description      = "Security posture — encryption, access controls, logging, compliance"
  handler          = "security_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.security_analyzer_zip.output_path
  source_code_hash = data.archive_file.security_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "security"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Resilience Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "resilience_analyzer" {
  function_name    = "ConnectOpsReview-ResilienceAnalyzer"
  description      = "Resilience — multi-region, DR readiness, redundancy checks"
  handler          = "resilience_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.resilience_analyzer_zip.output_path
  source_code_hash = data.archive_file.resilience_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "resilience"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Operational Excellence Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "opex_analyzer" {
  function_name    = "ConnectOpsReview-OpExAnalyzer"
  description      = "Operational excellence — contact flows, configuration hygiene, best practices"
  handler          = "opex_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.opex_analyzer_zip.output_path
  source_code_hash = data.archive_file.opex_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "operational_excellence"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Capacity Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "capacity_analyzer" {
  function_name    = "ConnectOpsReview-CapacityAnalyzer"
  description      = "Capacity analysis — service quotas, utilization, growth projections"
  handler          = "capacity_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.capacity_analyzer_zip.output_path
  source_code_hash = data.archive_file.capacity_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "capacity"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Observability Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "observability_analyzer" {
  function_name    = "ConnectOpsReview-ObservabilityAnalyzer"
  description      = "Observability — CloudWatch metrics, alarms, log groups, dashboards"
  handler          = "observability_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.observability_analyzer_zip.output_path
  source_code_hash = data.archive_file.observability_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "observability"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Cost Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "cost_analyzer" {
  function_name    = "ConnectOpsReview-CostAnalyzer"
  description      = "Cost considerations — telephony, usage patterns, optimization opportunities"
  handler          = "cost_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.cost_analyzer_zip.output_path
  source_code_hash = data.archive_file.cost_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "cost"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — AI Analyzer
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "ai_analyzer" {
  function_name    = "ConnectOpsReview-AIAnalyzer"
  description      = "AI features analysis — Q Connect agents, prompts, guardrails, knowledge bases"
  handler          = "ai_analyzer.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.ai_analyzer_zip.output_path
  source_code_hash = data.archive_file.ai_analyzer_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
      COMPONENT_TYPE      = "ai"
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — Report Generator
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "report_generator" {
  function_name    = "ConnectOpsReview-ReportGenerator"
  description      = "Assembles HTML report from analyzer results"
  handler          = "report_generator.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 1024
  role             = aws_iam_role.shared_lambda.arn
  layers           = [aws_lambda_layer_version.shared_utils.arn]
  filename         = data.archive_file.report_generator_zip.output_path
  source_code_hash = data.archive_file.report_generator_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET         = var.s3_reporting_bucket
      DEVOPS_AGENT_AGENT_SPACE_ID = var.devops_agent_agent_space_id
    }
  }
}

# ---------------------------------------------------------------------------
# Lambda Function — DeleteJsonData (best-effort cleanup after report generation)
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "delete_json_data" {
  function_name    = "ConnectOpsReview-DeleteJsonData"
  description      = "Best-effort deletion of JSON analyzer data from S3 after report generation"
  handler          = "delete_json_data.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  role             = aws_iam_role.shared_lambda.arn
  filename         = data.archive_file.delete_json_data_zip.output_path
  source_code_hash = data.archive_file.delete_json_data_zip.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.shared_lambda.name
  }

  environment {
    variables = {
      S3_REPORTING_BUCKET = var.s3_reporting_bucket
    }
  }
}

# ---------------------------------------------------------------------------
# Step Functions — IAM Role
# ---------------------------------------------------------------------------

resource "aws_iam_role" "state_machine_exec" {
  name = "ConnectOpsReview-StateMachineExecRole-${data.aws_region.current.name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "state_machine_invoke_lambdas" {
  name = "InvokeLambdas"
  role = aws_iam_role.state_machine_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["lambda:InvokeFunction"]
      Resource = [
        aws_lambda_function.prepare_context.arn,
        aws_lambda_function.report_generator.arn,
        aws_lambda_function.delete_json_data.arn,
        aws_lambda_function.security_analyzer.arn,
        aws_lambda_function.resilience_analyzer.arn,
        aws_lambda_function.cloudtrail_analyzer_parallel.arn,
        aws_lambda_function.opex_analyzer.arn,
        aws_lambda_function.capacity_analyzer.arn,
        aws_lambda_function.observability_analyzer.arn,
        aws_lambda_function.cost_analyzer.arn,
        aws_lambda_function.ai_analyzer.arn,
      ]
    }]
  })
}

resource "aws_iam_role_policy" "state_machine_logs" {
  name = "CloudWatchLogs"
  role = aws_iam_role.state_machine_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.state_machine.arn}:*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Step Functions — Log Group
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "state_machine" {
  name              = "/aws/states/ConnectOpsReview-Orchestrator"
  retention_in_days = var.log_retention_days

  lifecycle {
    create_before_destroy = false
  }
}

# ---------------------------------------------------------------------------
# Shared Lambda — Log Group
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "shared_lambda" {
  name              = "/aws/lambda/ConnectOpsReview-shared"
  retention_in_days = 30
}

# ---------------------------------------------------------------------------
# Step Functions — State Machine
# ---------------------------------------------------------------------------

resource "aws_sfn_state_machine" "orchestrator" {
  name     = "ConnectOpsReview-Orchestrator"
  role_arn = aws_iam_role.state_machine_exec.arn

  definition = templatefile("${path.module}/state-machine-definition.asl.json", {
    PrepareContextFunctionArn                = aws_lambda_function.prepare_context.arn
    ReportGeneratorFunctionArn               = aws_lambda_function.report_generator.arn
    DeleteJsonDataFunctionArn                = aws_lambda_function.delete_json_data.arn
    SecurityAnalyzerFunctionArn              = aws_lambda_function.security_analyzer.arn
    ResilienceAnalyzerFunctionArn            = aws_lambda_function.resilience_analyzer.arn
    CloudTrailAnalyzerFunctionArn            = aws_lambda_function.cloudtrail_analyzer_parallel.arn
    OperationalExcellenceAnalyzerFunctionArn = aws_lambda_function.opex_analyzer.arn
    CapacityAnalyzerFunctionArn              = aws_lambda_function.capacity_analyzer.arn
    ObservabilityAnalyzerFunctionArn         = aws_lambda_function.observability_analyzer.arn
    CostAnalyzerFunctionArn                  = aws_lambda_function.cost_analyzer.arn
    AIAnalyzerFunctionArn                    = aws_lambda_function.ai_analyzer.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.state_machine.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [
    aws_iam_role_policy.state_machine_invoke_lambdas,
    aws_iam_role_policy.state_machine_logs,
  ]
}

# ---------------------------------------------------------------------------
# SSM Parameter — Runtime Configuration
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "config" {
  name        = "/connect-ops-review/config"
  type        = "String"
  description = "Runtime configuration for Connect Ops Review - edit to customize without redeployment"

  value = jsonencode({
    instanceArn        = ""
    s3ReportingBucket  = ""
    daysBack           = 14
    generateHtmlReport = var.enable_html_report
    retainJsonData     = var.retain_json_data
    enableGlueCatalog  = var.enable_glue_catalog
    analyzers = {
      security               = true
      resilience             = true
      cloudtrail             = true
      operational_excellence = true
      capacity               = true
      observability          = true
      cost                   = true
      ai                     = true
    }
    analyzerTimeouts = {
      security               = 240
      resilience             = 240
      cloudtrail             = 840
      operational_excellence = 240
      capacity               = 540
      observability          = 540
      cost                   = 240
      ai                     = 540
    }
  })

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------------------
# EventBridge Scheduler — Recurring Schedule targeting State Machine
# ---------------------------------------------------------------------------

resource "aws_iam_role" "scheduler_sfn" {
  count = var.enable_review_schedule ? 1 : 0
  name  = "ConnectOpsReview-SchedulerRole-${data.aws_region.current.name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_start_execution" {
  count = var.enable_review_schedule ? 1 : 0
  name  = "StartStateMachine"
  role  = aws_iam_role.scheduler_sfn[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = aws_sfn_state_machine.orchestrator.arn
    }]
  })
}

resource "aws_scheduler_schedule" "review_schedule" {
  count       = var.enable_review_schedule ? 1 : 0
  name        = "ConnectOpsReview-RecurringSchedule"
  description = "Recurring Connect Ops Review execution"
  group_name  = "default"
  state       = "ENABLED"

  schedule_expression          = var.review_schedule_expression
  schedule_expression_timezone = var.review_schedule_timezone

  flexible_time_window {
    mode                      = var.review_flexible_window_minutes > 0 ? "FLEXIBLE" : "OFF"
    maximum_window_in_minutes = var.review_flexible_window_minutes > 0 ? var.review_flexible_window_minutes : null
  }

  target {
    arn      = aws_sfn_state_machine.orchestrator.arn
    role_arn = aws_iam_role.scheduler_sfn[0].arn

    input = jsonencode({
      instanceArn       = var.connect_instance_arn
      s3ReportingBucket = var.s3_reporting_bucket
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}

# ---------------------------------------------------------------------------
# Optional Glue Catalog Resources (behind enable_glue_catalog variable)
# ---------------------------------------------------------------------------

resource "aws_glue_catalog_database" "ops_review" {
  count = var.enable_glue_catalog ? 1 : 0
  name  = "connect_ops_review"

  description = "Amazon Connect Operational Review analyzer output data"
}

resource "aws_glue_catalog_table" "analyzer" {
  for_each      = var.enable_glue_catalog ? toset(local.analyzer_types) : toset([])
  database_name = aws_glue_catalog_database.ops_review[0].name
  name          = each.value

  table_type = "EXTERNAL_TABLE"
  parameters = {
    "classification"            = "json"
    "projection.enabled"        = "true"
    "projection.year.type"      = "integer"
    "projection.year.range"     = "2024,2100"
    "projection.month.type"     = "integer"
    "projection.month.range"    = "1,12"
    "projection.month.digits"   = "2"
    "projection.day.type"       = "integer"
    "projection.day.range"      = "1,31"
    "projection.day.digits"     = "2"
    "storage.location.template" = "s3://${var.s3_reporting_bucket}/data/${each.value}/year=$${year}/month=$${month}/day=$${day}/"
  }

  storage_descriptor {
    location      = "s3://${var.s3_reporting_bucket}/data/${each.value}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "reviewId"
      type = "string"
    }
    columns {
      name = "instanceId"
      type = "string"
    }
    columns {
      name = "accountId"
      type = "string"
    }
    columns {
      name = "awsRegion"
      type = "string"
    }
    columns {
      name = "componentType"
      type = "string"
    }
    columns {
      name = "timestamp"
      type = "string"
    }
    columns {
      name = "daysBack"
      type = "int"
    }
    columns {
      name = "partial"
      type = "boolean"
    }
    columns {
      name = "findings"
      type = "string"
    }
  }

  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
}
