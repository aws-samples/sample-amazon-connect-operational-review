# Terraform State Backend Bootstrap

One-time setup to create the S3 bucket and DynamoDB table for Terraform remote state.

## Usage

```bash
cd bootstrap

terraform init
terraform apply -var="state_bucket_name=my-tf-state-connect-ops-review"
```

## After Bootstrap

Copy the output values into the main module's `backend.tf`:

```hcl
terraform {
  backend "s3" {
    bucket         = "<state_bucket_name output>"
    key            = "connect-ops-review/terraform.tfstate"
    region         = "<region output>"
    dynamodb_table = "<lock_table_name output>"
    encrypt        = true
  }
}
```

Then initialize the main module:

```bash
cd ..
terraform init
```

Terraform will ask if you want to migrate local state to the new backend — answer yes.

## Variables

| Name | Description | Default |
|---|---|---|
| `aws_region` | AWS region | `us-east-1` |
| `state_bucket_name` | S3 bucket name for state (required) | — |
| `lock_table_name` | DynamoDB table name for locking | `terraform-state-lock` |
