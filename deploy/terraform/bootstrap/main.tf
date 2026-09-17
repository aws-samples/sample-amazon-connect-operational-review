# ============================================================================
# Terraform State Backend Bootstrap
#
# Creates the S3 bucket and DynamoDB table needed for remote state.
# Run this ONCE, then copy the outputs into the main module's backend.tf.
#
# Usage:
#   cd bootstrap
#   terraform init
#   terraform apply
#
# This configuration uses local state intentionally — it bootstraps the
# remote state infrastructure that everything else depends on.
# ============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region for the state backend resources."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Name for the S3 bucket that stores Terraform state."
  type        = string
}

variable "lock_table_name" {
  description = "Name for the DynamoDB table used for state locking."
  type        = string
  default     = "terraform-state-lock"
}

# ---------------------------------------------------------------------------
# S3 Bucket — state storage
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# DynamoDB Table — state locking
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

# ---------------------------------------------------------------------------
# Outputs — copy these into the main module's backend.tf
# ---------------------------------------------------------------------------

output "state_bucket_name" {
  description = "S3 bucket name — use as 'bucket' in backend config."
  value       = aws_s3_bucket.state.id
}

output "lock_table_name" {
  description = "DynamoDB table name — use as 'dynamodb_table' in backend config."
  value       = aws_dynamodb_table.lock.name
}

output "region" {
  description = "AWS region — use as 'region' in backend config."
  value       = var.aws_region
}
