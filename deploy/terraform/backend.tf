# Terraform remote state backend.
#
# SETUP:
#   1. Run the bootstrap/ configuration first to create the S3 bucket and
#      DynamoDB table (see bootstrap/README.md).
#   2. Uncomment the block below and fill in the values from the bootstrap
#      outputs.
#   3. Run `terraform init` to migrate from local to remote state.
#
# Until you uncomment this block, Terraform uses local state (fine for
# initial development and testing).

# terraform {
#   backend "s3" {
#     bucket         = "YOUR-STATE-BUCKET-NAME"
#     key            = "connect-ops-review/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "YOUR-LOCK-TABLE-NAME"
#     encrypt        = true
#   }
# }
