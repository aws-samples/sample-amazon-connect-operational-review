# Amazon Connect Automated Operational Review — Terraform

## Audience

This document is for **deployers** — customers or TAMs deploying the module into an AWS account. For repository development, contribution flow, and the `lambda_packages/` sync workflow, see [`docs/developer-guide.md`](../../docs/developer-guide.md) in the repo root.

## Why Use This

Amazon Connect contact centers grow in complexity over time — phone numbers accumulate, encryption settings drift, quotas approach limits, and logging gaps appear silently. This solution automates the operational health assessment that would otherwise take days of manual API calls and console checks.

After a single `terraform apply`, you get a weekly HTML report covering Security, Resilience, Operational Excellence, Capacity, Observability, Cost, and AI — delivered to your S3 bucket, viewable in any browser.

## What You Get

A self-contained HTML report (~200 KB) with:
- Executive summary dashboard showing pass/fail/warn counts per pillar at a glance
- Detailed findings per check with specific recommendations
- Color-coded status badges (green=pass, red=fail, amber=warn, blue=review, grey=error)
- No external dependencies — open directly in any browser, share via email or S3 pre-signed URL

Automated operational health assessment for Amazon Connect instances. Deploys a Step Functions orchestrator that runs 8 independent analyzer Lambda functions in parallel, assessing your Connect instance across Security, Resilience, Operational Excellence, Capacity, Observability, Cost, CloudTrail, and AI — then produces a comprehensive HTML report uploaded to S3.

## What Gets Deployed

| Resource | Description |
| -------- | ----------- |
| 10 Lambda functions | PrepareContext, 8 analyzers (Security, Resilience, CloudTrail, OpEx, Capacity, Observability, Cost, AI), Report Generator |
| Lambda Layer | Shared utilities (analyzer_common, graceful_timeout) |
| Step Functions state machine | Orchestrates parallel execution of all analyzers |
| IAM roles | Shared execution role (least-privilege), state machine role, scheduler role |
| SSM Parameter | Runtime configuration (edit to customize without redeployment) |
| EventBridge Scheduler | Automated recurring execution (default: weekly) |
| CloudWatch Log Group | State machine execution logs |
| Glue Catalog (optional) | Database + tables for Athena querying of analyzer results |

## Prerequisites

Before deploying, confirm the following.

### Required tooling

- [Terraform](https://developer.hashicorp.com/terraform/install) **>= 1.5**
- AWS CLI configured with credentials for the target account (used by the Terraform AWS provider)

### Required AWS services

The module provisions resources in the following services; they must be available (and not opted-out) in the target region:

- Amazon Connect (an existing instance must already be provisioned)
- AWS Lambda
- AWS IAM
- AWS Step Functions
- Amazon EventBridge Scheduler
- AWS Systems Manager Parameter Store (SSM)
- Amazon CloudWatch Logs
- Amazon S3
- AWS Glue (only if `enable_glue_catalog = true`)

### Deployer IAM permissions

The identity running `terraform apply` must be able to create Lambda functions, IAM roles and policies, Step Functions state machines, EventBridge schedules, SSM parameters, CloudWatch log groups, and (optionally) Glue databases and tables. Attach `AdministratorAccess` for simplicity, or scope down to the least-privilege deploy policy documented in [Least-Privilege Deploy Permissions](#least-privilege-deploy-permissions) below — note the additional read-back permissions the Terraform provider requires.

### Stack-runtime IAM permissions

The module creates a shared execution role that the analyzer Lambdas assume at runtime. It is granted read access to the target Amazon Connect instance, Amazon S3 (for report and JSON data upload), Amazon CloudWatch, CloudTrail, Amazon Q in Connect (AI analyzer), and the Cases/Customer Profiles APIs.

### Required Amazon Connect instance state

- The instance must exist and be in the **same AWS region** as the module deployment.
- You must have the instance ARN in the form `arn:aws:connect:<region>:<account-id>:instance/<instance-id>`.
- The identity used at runtime must be entitled to `DescribeInstance`, `ListPhoneNumbers`, and the other Connect read APIs used by the analyzers (all covered by the stack-runtime role above).

### Supported/tested regions

The module has been tested primarily in `us-east-1`, `us-west-2`, `eu-west-1`, and `ap-southeast-2`. Any region in which Amazon Connect and the services listed above are all generally available should work; the module deployment and the Connect instance must be in the same region.

### Account-level quotas

Default account quotas are typically sufficient. Confirm the following if you have a heavily-used account:

- Lambda: at least 10 concurrent executions (the analyzers run in parallel).
- Step Functions: at least 1 additional state machine per deployment.
- IAM: at least 4 additional roles per deployment.
- CloudWatch Logs: no per-account limit typically applies.
- S3: an existing bucket for reports (in the same region as the deployment).

### Remote state (recommended for teams)

For shared environments or CI/CD pipelines, configure an S3 + DynamoDB remote-state backend before the first `terraform apply` — see [Remote State Backend](#remote-state-backend-recommended-for-teams) below.

## Least-Privilege Deploy Permissions

If you deploy with a scoped IAM role (rather than `AdministratorAccess`), the Terraform AWS provider requires several **read-back permissions** beyond the standard create/update/delete actions. These are used during `terraform plan` and `terraform apply` to refresh resource state and validate configurations.

> **Note:** These are Terraform-specific. CloudFormation deployments with admin permissions are unaffected.

### Additional Read-Back Permissions

| Permission | Why It's Needed |
| ---------- | --------------- |
| `logs:ListTagsForResource` | CloudWatch Logs tag read-back during plan/refresh |
| `glue:GetTags` | Glue resource tag read-back during plan/refresh |
| `states:ValidateStateMachineDefinition` | Step Functions definition validation on create/update |
| `states:ListStateMachineVersions` | Step Functions version read-back during plan/refresh |

### Sample IAM Policy

Below is a minimal IAM policy that covers the core deploy actions plus the required read-back permissions. Adjust resource ARNs to match your account and region.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CoreDeployPermissions",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:ListVersionsByFunction",
        "lambda:PublishLayerVersion",
        "lambda:GetLayerVersion",
        "lambda:DeleteLayerVersion",
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:PassRole",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "states:CreateStateMachine",
        "states:UpdateStateMachine",
        "states:DeleteStateMachine",
        "states:DescribeStateMachine",
        "states:TagResource",
        "logs:CreateLogGroup",
        "logs:DeleteLogGroup",
        "logs:DescribeLogGroups",
        "logs:PutRetentionPolicy",
        "logs:TagLogGroup",
        "logs:ListTagsLogResource",
        "ssm:PutParameter",
        "ssm:GetParameter",
        "ssm:DeleteParameter",
        "ssm:AddTagsToResource",
        "ssm:ListTagsForResource",
        "scheduler:CreateSchedule",
        "scheduler:UpdateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule",
        "glue:CreateDatabase",
        "glue:DeleteDatabase",
        "glue:GetDatabase",
        "glue:CreateTable",
        "glue:UpdateTable",
        "glue:DeleteTable",
        "glue:GetTable",
        "glue:GetTables",
        "s3:GetBucketLocation",
        "s3:ListBucket"
      ],
      "Resource": "*"
    },
    {
      "Sid": "TerraformReadBackPermissions",
      "Effect": "Allow",
      "Action": [
        "logs:ListTagsForResource",
        "glue:GetTags",
        "states:ValidateStateMachineDefinition",
        "states:ListStateMachineVersions"
      ],
      "Resource": "*"
    }
  ]
}
```

Without the `TerraformReadBackPermissions` statement, `terraform plan` or `terraform apply` will fail with `AccessDeniedException` when the provider attempts to read tags or validate state machine definitions.

## Quick Start

### 1. Configure

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
connect_instance_arn = "arn:aws:connect:us-east-1:123456789012:instance/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
s3_reporting_bucket  = "my-connect-ops-review-reports"
aws_region           = "us-east-1"
```

### 2. Deploy

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 3. Verify

```bash
terraform output orchestrator_state_machine_arn
terraform output ssm_config_parameter_name
```

## Parameters

The module exposes **13** variables. Two are required; eleven are optional and have sensible defaults.

### Required variables

| Variable | Type | Description |
|----------|------|-------------|
| `connect_instance_arn` | `string` | ARN of your Amazon Connect instance. Must match the pattern `arn:aws:connect:<region>:<account-id>:instance/<instance-id>` (validated via a `validation` block on the variable) and be in the same region as the deployment. |
| `s3_reporting_bucket` | `string` | Name (not ARN) of an **existing** S3 bucket in the same region as the deployment. HTML reports and (optionally) raw JSON data are written to this bucket. Must be 3–63 characters (validated via a `validation` block on the variable). |

### Optional variables

| Variable | Type | Default | Allowed Values | Behavior When Set to Default |
|----------|------|---------|----------------|------------------------------|
| `aws_region` | `string` | `"us-east-1"` | Any AWS region code | The module deploys into `us-east-1`. Must match the region of the Amazon Connect instance and the reporting S3 bucket. |
| `log_retention_days` | `number` | `30` | Any positive integer supported by CloudWatch Logs (e.g. `1`, `7`, `30`, `90`, `365`) | State-machine and Lambda CloudWatch log groups retain events for 30 days, then expire. |
| `tags` | `map(string)` | `{}` | Any tag key/value map | No custom tags are applied. Tags declared on the AWS provider's `default_tags` block still apply. |
| `enable_glue_catalog` | `bool` | `false` | `true` \| `false` | No Glue database or tables are created. Analyzer JSON output is still written to `s3://<bucket>/data/…` (subject to `retain_json_data`), but Athena is not queryable out of the box. See [D1](../../docs/parameter-dependencies.md#d1). |
| `enable_review_schedule` | `bool` | `false` | `true` \| `false` | **No EventBridge Scheduler resources are created.** The state machine can only be triggered manually (see "Running a Review" below). See [D3](../../docs/parameter-dependencies.md#d3). |
| `review_schedule_expression` | `string` | `"cron(0 9 ? * MON *)"` | Any valid `rate(...)` or `cron(...)` expression (validated via a `validation` block matching `^(rate\|cron)\(.*\)$`) | Runs the review every Monday at 09:00 in the timezone set by `review_schedule_timezone`. Ignored unless `enable_review_schedule = true` — see [D3](../../docs/parameter-dependencies.md#d3). |
| `review_schedule_timezone` | `string` | `"UTC"` | Any valid IANA timezone identifier (e.g. `America/New_York`, `Europe/London`, `Asia/Tokyo`) | The schedule expression above is interpreted in UTC. Ignored unless `enable_review_schedule = true` — see [D3](../../docs/parameter-dependencies.md#d3). |
| `review_flexible_window_minutes` | `number` | `0` | Any non-negative integer (`0` = disabled) | The schedule fires at its exact scheduled time (`mode = "OFF"`). Values `> 0` set `mode = "FLEXIBLE"` and the schedule fires at a random offset within the window. Ignored unless `enable_review_schedule = true` — see [D3](../../docs/parameter-dependencies.md#d3) and [D5](../../docs/parameter-dependencies.md#d5). |
| `enable_html_report` | `bool` | `true` | `true` \| `false` | Sets the initial SSM value at deploy time. Edit `/connect-ops-review/config` post-deploy to change without redeploying. When `true`, the `ReportGeneratorFunction` produces a single-file HTML report at `s3://<bucket>/connect-review_<alias>_<region>_<timestamp>.html` after all analyzers complete. See [D2](../../docs/parameter-dependencies.md#d2). |
| `retain_json_data` | `bool` | `false` | `true` \| `false` | Sets the initial SSM value at deploy time. Edit `/connect-ops-review/config` post-deploy to change without redeploying. Runtime enforces `retainJsonData=true` when Glue is enabled or the HTML report is disabled (see [D1](../../docs/parameter-dependencies.md#d1) / [D2](../../docs/parameter-dependencies.md#d2)). |
| `devops_agent_agent_space_id` | `string` | `""` (empty) | Free-form string (Agent Space ID) | No "Ask DevOps Agent" buttons are rendered in the HTML report; the report still works otherwise. Has no effect if `enable_html_report = false` — see [D4](../../docs/parameter-dependencies.md#d4). |

<!-- BEGIN:parameter-dependencies -->
## Parameter Dependencies

Several variables interact with each other at runtime — enabling one may require or disable another. The canonical, deployment-method-agnostic reference for these cross-parameter rules lives at [`docs/parameter-dependencies.md`](../../docs/parameter-dependencies.md) and applies to both the CloudFormation and Terraform deployments. The summary below lists the rules that apply to this (Terraform) deployment; follow the "Full rule" link for the IF/THEN/BECAUSE detail and the observable failure mode.

| ID | Summary | Full rule |
|----|---------|-----------|
| D1 | If `enable_glue_catalog = true` then `retain_json_data = true` is required so the Glue tables have data to point at after each run. | [docs/parameter-dependencies.md#d1](../../docs/parameter-dependencies.md#d1) |
| D2 | If `enable_html_report = false` then raw JSON in `s3://<bucket>/data/…` is the only output; `retain_json_data` becomes a no-op in that configuration. | [docs/parameter-dependencies.md#d2](../../docs/parameter-dependencies.md#d2) |
| D3 | If `enable_review_schedule = false` then `review_schedule_expression`, `review_schedule_timezone`, and `review_flexible_window_minutes` are no-ops (no scheduler resources are created). | [docs/parameter-dependencies.md#d3](../../docs/parameter-dependencies.md#d3) |
| D4 | If `enable_html_report = false` then `devops_agent_agent_space_id` has no effect (there is no HTML report to inject "Ask DevOps Agent" buttons into). | [docs/parameter-dependencies.md#d4](../../docs/parameter-dependencies.md#d4) |
| D5 | (Terraform only) If `review_flexible_window_minutes > 0` then the schedule fires at a random offset within the window (`mode = "FLEXIBLE"`); useful for fanning many stacks' schedules apart, undesirable for one-off tests. | [docs/parameter-dependencies.md#d5](../../docs/parameter-dependencies.md#d5) |
<!-- END:parameter-dependencies -->

## Differences from the CloudFormation deployment

The CloudFormation and Terraform deployments produce functionally equivalent stacks, but the two surfaces are not identical. The table below enumerates every meaningful divergence; the mirror of this table lives in [`deploy/cloudformation/README.md`](../cloudformation/README.md#differences-from-the-terraform-deployment) and is intentionally byte-identical.

| Concept | CloudFormation | Terraform | Notes |
|---------|----------------|-----------|-------|
| Parameter/variable count | 9 parameters | 13 variables | Terraform exposes 4 additional inputs (`aws_region`, `log_retention_days`, `tags`, `review_flexible_window_minutes`) not surfaced by the CFN template. |
| AWS region selection | Implicit — the stack is created in whatever region the CloudFormation client targets. | Explicit — `aws_region` variable, default `"us-east-1"`. | If deploying via Terraform from a provider not pinned to `us-east-1`, `aws_region` must be set to match. |
| CloudWatch log retention | Hard-coded to 30 days in the template. | Configurable via `log_retention_days` (default `30`). | With defaults, both produce identical retention. Terraform can be tuned without a template fork. |
| Resource tagging | No `tags` parameter; only resource-specific `Tags` attributes hard-coded in the template. | `tags` variable merged with `provider default_tags` and applied uniformly to all resources. | Deployers who need custom tags on the CFN path must fork the template today. |
| Flexible schedule window | Not supported. | `review_flexible_window_minutes` (default `0` = off). | Only meaningful when the schedule is enabled; useful for spreading many stacks' schedules across a window. |
| Boto3 layer | Built at deploy time by a custom resource that requires Lambda internet access. | Pre-packaged zip shipped in `lambda_layers/`; no internet needed at apply time. | Both produce a functionally equivalent layer. |
| Naming conventions | `PascalCase` parameter names with `'Yes'`/`'No'` string values. | `snake_case` variable names with native `bool`. | 1:1 name mapping: `AmazonConnectInstanceARN`↔`connect_instance_arn`, `AmazonS3ForReports`↔`s3_reporting_bucket`, `EnableGlueCatalog`↔`enable_glue_catalog`, `EnableReviewSchedule`↔`enable_review_schedule`, `ReviewScheduleExpression`↔`review_schedule_expression`, `ReviewScheduleTimezone`↔`review_schedule_timezone`, `EnableHtmlReport`↔`enable_html_report`, `RetainJsonData`↔`retain_json_data`, `DevOpsAgentAgentSpaceId`↔`devops_agent_agent_space_id`. |

## Running a Review

### Manual Execution (Step Functions)

**Simplest invocation (uses SSM config defaults):**

```bash
aws stepfunctions start-execution \
  --state-machine-arn "$(terraform output -raw orchestrator_state_machine_arn)" \
  --input '{}'
```

**With overrides (optional — any field can be omitted):**

```bash
aws stepfunctions start-execution \
  --state-machine-arn "$(terraform output -raw orchestrator_state_machine_arn)" \
  --input '{"instanceArn":"arn:aws:connect:<region>:<account>:instance/<id>","s3ReportingBucket":"<bucket>","daysBack":7}'
```

> **Note:** All input fields are optional. If omitted, values are resolved from the SSM parameter (`/connect-ops-review/config`) or Terraform-deployed environment variables.

The state machine runs all 8 analyzers in parallel, then generates an HTML report at:
`s3://<bucket>/connect-review_<instance-alias>_<region>_<timestamp>.html`

### Automated Execution

When `enable_review_schedule = true`, the deployment includes an EventBridge Scheduler that automatically triggers the review on the configured schedule (default: weekly Mondays at 09:00 UTC). No additional setup is needed.

### Checking Execution Status

```bash
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --query '{status:status,startDate:startDate,stopDate:stopDate}'
```

## Runtime Configuration

After deployment, customize behavior without redeployment by editing the SSM parameter:

```bash
aws ssm put-parameter \
  --name /connect-ops-review/config \
  --type String \
  --overwrite \
  --value '{
    "daysBack": 14,
    "generateHtmlReport": true,
    "analyzers": {
      "security": true,
      "resilience": true,
      "cloudtrail": true,
      "operational_excellence": true,
      "capacity": true,
      "observability": true,
      "cost": true,
      "ai": true
    },
    "analyzerTimeouts": {
      "security": 240,
      "resilience": 240,
      "cloudtrail": 840,
      "operational_excellence": 240,
      "capacity": 540,
      "observability": 540,
      "cost": 240,
      "ai": 540
    }
  }'
```

Set any analyzer to `false` to skip it. Timeout values are in seconds (capped at Lambda timeout − 60s safety margin).

## Report Sections

| Pillar | Checks |
| ------ | ------ |
| Security | Identity Management (SAML), S3/Streaming Encryption, AI Guardrails, AI Domain Encryption |
| Resilience | Multi-AZ Architecture, Global Resiliency (ACGR), Carrier Diversity |
| Operational Excellence | Contact Flow Logging, Missed Calls Analysis, Misconfigured Phone Numbers |
| Capacity | Instance Limits, Concurrency, API Rate Quotas, Cases/Profiles/AI Limits |
| Observability | CloudWatch Alarms, Missed Calls, Log Groups, Contact Flow Logging |
| Cost | Phone Number Distribution, Channel Usage Breakdown, Unused Phone Numbers |
| CloudTrail | API Throttling Analysis (account-level) |
| AI | Agent Inventory, Prompt Configuration, Guardrails, Domain Encryption, Agent Logging |

## Updating

Edit `terraform.tfvars` (or `terraform.tfvars.example` and re-copy) with your new values and apply:

```bash
terraform plan
terraform apply
```

> **Note:** Updating Lambda code from a new release package is a developer/maintainer workflow. See [`docs/developer-guide.md`](../../docs/developer-guide.md) for the `lambda_packages/` sync flow.

## Remote State Backend (Recommended for Teams)

For shared environments or CI/CD pipelines:

```bash
cd bootstrap
terraform init
terraform apply -var="state_bucket_name=my-tf-state-connect-ops-review"
```

Then uncomment the `backend.tf` block with the output values and re-initialize:

```bash
cd ..
terraform init    # Answer 'yes' to migrate state
```

## Rollback

```bash
# Revert a code change
git checkout HEAD~1 -- lambda_packages/
terraform apply

# Revert a configuration change
git revert <commit-sha>
terraform apply
```

## Teardown

```bash
terraform destroy
```

This removes all deployed resources. It does **not** remove:
- The S3 reporting bucket or its contents
- The state backend (if using remote state)
- The Amazon Connect instance

## Outputs

| Name | Description |
| ---- | ----------- |
| `orchestrator_state_machine_arn` | ARN of the Step Functions state machine |
| `prepare_context_function_arn` | ARN of the PrepareContext Lambda |
| `security_analyzer_function_arn` | ARN of the Security Analyzer Lambda |
| `resilience_analyzer_function_arn` | ARN of the Resilience Analyzer Lambda |
| `cloudtrail_analyzer_parallel_function_arn` | ARN of the CloudTrail Analyzer Lambda |
| `opex_analyzer_function_arn` | ARN of the OpEx Analyzer Lambda |
| `capacity_analyzer_function_arn` | ARN of the Capacity Analyzer Lambda |
| `observability_analyzer_function_arn` | ARN of the Observability Analyzer Lambda |
| `cost_analyzer_function_arn` | ARN of the Cost Analyzer Lambda |
| `ai_analyzer_function_arn` | ARN of the AI Analyzer Lambda |
| `report_generator_function_arn` | ARN of the Report Generator Lambda |
| `eventbridge_schedule_arn` | ARN of the EventBridge Scheduler schedule |
| `ssm_config_parameter_name` | Name of the SSM runtime config parameter |
| `shared_lambda_role_arn` | ARN of the shared Lambda execution role |

## Post-Deployment Verification

After `terraform apply` completes successfully, walk this ordered checklist to confirm the deployment actually works end-to-end.

1. **Confirm all Lambda functions were created.** All 10 should be present (PrepareContext, 8 analyzers, ReportGenerator):
   ```bash
   aws lambda list-functions \
     --query "Functions[?starts_with(FunctionName, 'ConnectOpsReview')].FunctionName"
   ```
2. **Find module outputs.** Note the `orchestrator_state_machine_arn` — you'll use it in step 3:
   ```bash
   terraform output
   ```
3. **Trigger the first run manually** (do not wait for the schedule):
   ```bash
   aws stepfunctions start-execution \
     --state-machine-arn "$(terraform output -raw orchestrator_state_machine_arn)" \
     --input '{}'
   ```
   Capture the returned `executionArn`.
4. **Wait for the execution to succeed.** The full run typically completes in 3–15 minutes depending on account size:
   ```bash
   aws stepfunctions describe-execution --execution-arn <executionArn>
   ```
   `status` should be `SUCCEEDED`.
5. **Locate the HTML report** (only if `enable_html_report = true`, the default). It is written to:
   ```
   s3://<s3_reporting_bucket>/connect-review_<instance-alias>_<region>_<timestamp>.html
   ```
   Download and open in any browser.
6. **Locate the raw JSON data** (only if `retain_json_data = true`, or if `enable_html_report = false`). It is written under a Hive-partitioned prefix per component:
   ```
   s3://<s3_reporting_bucket>/data/<component>/year=<YYYY>/month=<MM>/day=<DD>/<file>.json
   ```
7. **Verify Glue tables are non-empty in Athena** (only if `enable_glue_catalog = true`). In the Athena console, select the `connect_ops_review` database (or the name given by the module) and run a simple `SELECT count(*) FROM <table>;` against each of the 8 analyzer tables. Every table should return a non-zero count for at least one partition.

## Cost to Run

This solution is designed to be extremely low-cost. With the default weekly schedule, expected monthly costs are **under $0.50/month** and often fall within AWS Free Tier.

### Monthly Cost Estimate (Weekly Reviews)

| Component | Estimate | Notes |
| --------- | -------- | ----- |
| **Lambda** | ~$0.05 | 10 functions × 4 executions/month; most run 30–120s at 256 MB |
| **Step Functions** | ~$0.001 | ~80 state transitions/month ($0.025 per 1,000 transitions) |
| **CloudWatch Logs** | ~$0.05 | ~20 MB ingestion/month at 30-day retention |
| **S3** | ~$0.01 | HTML reports (~200 KB) + JSON data (~2 MB) per review |
| **EventBridge Scheduler** | $0.00 | Covered by free tier (14M free invocations/month) |
| **SSM Parameter** | $0.00 | Standard parameters are free |
| **Total** | **~$0.10–0.50/month** | |

### How Costs Scale

| Review Frequency | Reviews/Month | Estimated Cost |
| ---------------- | ------------- | -------------- |
| Weekly (default) | 4 | $0.10–0.50 |
| Daily | 30 | $0.50–1.50 |
| Twice daily | 60 | $1.00–3.00 |

### Key Cost Drivers

- **CloudTrail Analyzer** is the most expensive Lambda invocation — it runs up to 15 minutes (900s timeout) at 512 MB when scanning large accounts. Other analyzers complete in 30–120 seconds at 256 MB.
- **Report Generator** uses 1024 MB memory but completes in under 60 seconds.
- **CloudWatch Logs** ingestion is the largest steady-state cost for most deployments.
- Costs increase linearly with review frequency.
- Disabling analyzers via SSM config reduces both execution time and cost proportionally.

### Free Tier Considerations

Most deployments with weekly reviews will stay within AWS Free Tier limits:
- Lambda: 400,000 GB-seconds free/month (this solution uses ~200–500 GB-seconds/month)
- S3: 5 GB standard storage free for 12 months
- CloudWatch Logs: 5 GB ingestion free/month

> **Bottom line:** At the default weekly schedule, this solution costs less than a cup of coffee per year.

## Optional: DevOps Agent Integration

If you use [AWS DevOps Agent](https://docs.aws.amazon.com/devops-agent/latest/userguide/), the HTML report includes per-check "Ask DevOps Agent" buttons. To enable, set the `devops_agent_agent_space_id` variable to your Agent Space ID (find yours via `aws devops-agent list-agent-spaces --region us-east-1`).

Companion DevOps Agent skills (investigation, remediation, proactive evaluation) are available separately — contact your AWS TAM for access.

## Troubleshooting

| Issue | Resolution |
| ----- | ---------- |
| Execution fails at PrepareContext | Check the Connect instance ARN is correct and SSM parameter is valid JSON |
| Single analyzer fails, others succeed | Check that analyzer's CloudWatch log group for specific errors; disable via SSM config if needed |
| `AccessDeniedException` | Verify the Connect instance is in the same region as the deployment |
| Partial results (`"partial": true`) | The analyzer hit its time budget — increase the timeout in SSM config or reduce `daysBack` |
| Empty report | Ensure the S3 bucket exists, is in the same region, and the Lambda role has `s3:PutObject` |
| `NoSuchResourceException` on Service Quotas | Expected for some quota codes in newer regions — non-fatal |
