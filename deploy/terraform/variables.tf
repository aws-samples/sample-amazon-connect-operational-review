# ---------------------------------------------------------------------------
# Required variables — must be provided by the caller
# ---------------------------------------------------------------------------

variable "connect_instance_arn" {
  description = "ARN of the Amazon Connect instance. Example: arn:aws:connect:us-east-1:123456789012:instance/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
  type        = string

  validation {
    condition     = can(regex("^arn:aws:connect:[a-z0-9-]+:[0-9]{12}:instance/[a-f0-9-]+$", var.connect_instance_arn))
    error_message = "Must be a valid Amazon Connect instance ARN."
  }
}

variable "s3_reporting_bucket" {
  description = "Name of an existing S3 bucket for uploading operational review reports."
  type        = string

  validation {
    condition     = length(var.s3_reporting_bucket) >= 3 && length(var.s3_reporting_bucket) <= 63
    error_message = "Must be a valid S3 bucket name (3-63 characters)."
  }
}

# ---------------------------------------------------------------------------
# Optional variables — sensible defaults provided
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "log_retention_days" {
  description = "CloudWatch Log Group retention in days."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to all resources (merged with provider default_tags)."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Parallel Architecture variables
# ---------------------------------------------------------------------------

variable "enable_glue_catalog" {
  description = "Enable optional Glue Catalog database and tables for Athena querying of analyzer output."
  type        = bool
  default     = false
}

variable "enable_review_schedule" {
  description = "Whether the EventBridge Scheduler resources are created. When false, no scheduler IAM role, policy, or schedule resources are deployed."
  type        = bool
  default     = false
}

variable "review_schedule_expression" {
  description = "EventBridge Scheduler schedule expression for automated review execution. Supports rate() and cron() expressions."
  type        = string
  default     = "cron(0 9 ? * MON *)"

  validation {
    condition     = can(regex("^(rate|cron)\\(.*\\)$", var.review_schedule_expression))
    error_message = "Must be a valid rate() or cron() expression."
  }
}

variable "review_schedule_timezone" {
  description = "IANA timezone for the schedule expression (e.g., 'America/New_York', 'UTC'). Allows scheduling in the customer's local time."
  type        = string
  default     = "UTC"
}

variable "review_flexible_window_minutes" {
  description = "Flexible time window in minutes (0 = disabled). When set, the schedule fires randomly within this window to avoid thundering herd."
  type        = number
  default     = 0
}

# ---------------------------------------------------------------------------
# Report Configuration
# ---------------------------------------------------------------------------

variable "enable_html_report" {
  description = "Whether to generate an HTML report after analysis. When false, only JSON data is produced."
  type        = bool
  default     = true
}

variable "retain_json_data" {
  description = "Whether to retain JSON analyzer data after report generation. When false, JSON data partitions are deleted after the HTML report is created."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Optional Integrations
# ---------------------------------------------------------------------------

variable "devops_agent_agent_space_id" {
  description = "Optional Amazon DevOps Agent Space ID. When configured, per-section 'Ask DevOps Agent' buttons are injected into the HTML report with a CLI command generator modal. Leave empty to disable."
  type        = string
  default     = ""
}
