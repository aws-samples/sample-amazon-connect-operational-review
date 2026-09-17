# Amazon Connect Automated Operational Review — CloudFormation

## Audience

This document is for **deployers** — customers or TAMs deploying the stack into an AWS account. For repository development and contribution flow, see [`README.md`](../../README.md) and [`docs/developer-guide.md`](../../docs/developer-guide.md) in the repo root.

## Why Use This

Amazon Connect contact centers grow in complexity over time — phone numbers accumulate, encryption settings drift, quotas approach limits, and logging gaps appear silently. This solution automates the operational health assessment that would otherwise take days of manual API calls and console checks.

After a single CloudFormation deployment, you get a weekly HTML report covering Security, Resilience, Operational Excellence, Capacity, Observability, Cost, and AI — delivered to your S3 bucket, viewable in any browser.

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
| Lambda Layers | Shared utilities + boto3 (built automatically at deploy time) |
| Step Functions state machine | Orchestrates parallel execution of all analyzers |
| IAM roles | Shared execution role (least-privilege), state machine role, scheduler role, layer builder role |
| SSM Parameter | Runtime configuration (edit to customize without redeployment) |
| EventBridge Scheduler | Automated recurring execution (default: weekly) |
| CloudWatch Log Group | State machine execution logs (30-day retention) |
| Glue Catalog (optional) | Database + tables for Athena querying of analyzer results |

## Prerequisites

Before deploying, confirm the following.

### Required AWS services

The stack provisions resources in the following services; they must be available (and not opted-out) in the target region:

- Amazon Connect (an existing instance must already be provisioned)
- AWS Lambda
- AWS IAM
- AWS Step Functions
- Amazon EventBridge Scheduler
- AWS Systems Manager Parameter Store (SSM)
- Amazon CloudWatch Logs
- Amazon S3
- AWS Glue (only if `EnableGlueCatalog=Yes`)

### Deployer IAM permissions

The identity running `create-stack` / `create-change-set` must be able to create Lambda functions, IAM roles and policies, Step Functions state machines, EventBridge schedules, SSM parameters, CloudWatch log groups, and (optionally) Glue databases and tables. Attach `AdministratorAccess` for simplicity, or scope down to the equivalent least-privilege deploy policy documented in [`deploy/terraform/README.md`](../terraform/README.md#least-privilege-deploy-permissions) — the same IAM surface applies for either deployment method.

### Stack-runtime IAM permissions

The stack itself creates a shared execution role that the analyzer Lambdas assume at runtime. It is granted read access to the target Amazon Connect instance, Amazon S3 (for report and JSON data upload), Amazon CloudWatch, CloudTrail, Amazon Q in Connect (AI analyzer), and the Cases/Customer Profiles APIs. The full runtime IAM surface mirrors the Terraform module's [least-privilege deploy permissions block](../terraform/README.md#least-privilege-deploy-permissions).

### Required Amazon Connect instance state

- The instance must exist and be in the **same AWS region** as the CloudFormation stack.
- You must have the instance ARN in the form `arn:aws:connect:<region>:<account-id>:instance/<instance-id>`.
- The identity used at runtime must be entitled to `DescribeInstance`, `ListPhoneNumbers`, and the other Connect read APIs used by the analyzers (all covered by the stack-runtime role above).

### Supported/tested regions

The stack has been tested primarily in `us-east-1`, `us-west-2`, `eu-west-1`, and `ap-southeast-2`. Any region in which Amazon Connect and the services listed above are all generally available should work; the stack and the Connect instance must be in the same region.

### Account-level quotas

Default account quotas are typically sufficient. Confirm the following if you have a heavily-used account:

- Lambda: at least 10 concurrent executions (the analyzers run in parallel).
- Step Functions: at least 1 additional state machine per stack.
- IAM: at least 4 additional roles per stack.
- CloudWatch Logs: no per-account limit typically applies.
- S3: an existing bucket for reports (in the same region as the stack).

## Quick Start

### Option A: AWS Console

1. Open the [CloudFormation Console](https://console.aws.amazon.com/cloudformation)
2. Click **Create stack** → **With new resources (standard)**
3. Select **Upload a template file** and upload `CFT-AmazonConnectOperationsReview.yml`
   - If upload fails due to size, first upload the template to an S3 bucket, then provide the S3 URL
4. Fill in the parameters (see below)
5. Check **I acknowledge that AWS CloudFormation might create IAM resources**
6. Click **Submit**

The stack takes about 2–3 minutes to create (includes building Lambda layers).

### Option B: AWS CLI

> **Template size — you must stage the template in S3 first.** The template is
> ~765 KB (it embeds every analyzer Lambda's code inline via `Code.ZipFile`),
> well over CloudFormation's 51,200-byte limit for inline `--template-body`
> uploads. All CLI examples below use `--template-url` after an `aws s3 cp`
> to stage the file. `aws cloudformation deploy` can stage automatically if
> you pass `--s3-bucket` — see the alternative one-liner near the bottom of
> this section.

```bash
# Upload template to S3 first — 765 KB > 51,200-byte inline API limit
aws s3 cp CFT-AmazonConnectOperationsReview.yml s3://<your-bucket>/cfn-templates/

# Deploy using change set (required — see note below)
aws cloudformation create-change-set \
  --stack-name ConnectOpsReview \
  --change-set-name initial-deploy \
  --change-set-type CREATE \
  --template-url https://s3.<region>.amazonaws.com/<your-bucket>/cfn-templates/CFT-AmazonConnectOperationsReview.yml \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=AmazonConnectInstanceARN,ParameterValue="arn:aws:connect:<region>:<account-id>:instance/<instance-id>" \
    ParameterKey=AmazonS3ForReports,ParameterValue="<your-s3-bucket-name>"

# Wait for change set to be ready
aws cloudformation wait change-set-create-complete \
  --stack-name ConnectOpsReview \
  --change-set-name initial-deploy

# Execute the change set to create the stack
aws cloudformation execute-change-set \
  --stack-name ConnectOpsReview \
  --change-set-name initial-deploy
```

> **Why change set instead of `create-stack`?** CloudFormation's resource-type
> schema validation (rolled out mid-2026) rejects `Code.ZipFile` payloads that
> exceed the documented 4096-byte limit — even though the Lambda service accepts
> larger inline payloads at runtime. This template embeds Lambda code inline
> (required for single-file `ZipFile` deployments without S3 packaging). The
> change set path bypasses pre-create validation while still deploying correctly.
> Stack **updates** via `update-stack` are unaffected and work normally.

### Verify

```bash
aws cloudformation describe-stacks --stack-name ConnectOpsReview --query 'Stacks[0].StackStatus'
```

## Parameters

The template exposes **9** parameters. Two are required; seven are optional and have sensible defaults.

### Required parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `AmazonConnectInstanceARN` | `String` | ARN of your Amazon Connect instance. Must match the pattern `arn:aws:connect:<region>:<account-id>:instance/<instance-id>` and be in the same region as the stack. |
| `AmazonS3ForReports` | `String` | Name (not ARN) of an **existing** S3 bucket in the same region as the stack. HTML reports and (optionally) raw JSON data are written to this bucket. Must be 3–63 characters. |

### Optional parameters

| Parameter | Type | Default | Allowed Values | Behavior When Set to Default |
|-----------|------|---------|----------------|------------------------------|
| `EnableGlueCatalog` | `String` | `'No'` | `'Yes'` \| `'No'` | No Glue database or tables are created. Analyzer JSON output is still written to `s3://<bucket>/data/…` (subject to `RetainJsonData`), but Athena is not queryable out of the box. See [D1](../../docs/parameter-dependencies.md#d1). |
| `EnableReviewSchedule` | `String` | `'No'` | `'Yes'` \| `'No'` | **No EventBridge Scheduler is created.** The state machine can only be triggered manually (see "Running a Review" below). See [D3](../../docs/parameter-dependencies.md#d3). |
| `ReviewScheduleExpression` | `String` | `'cron(0 9 ? * MON *)'` | Any valid `rate(...)` or `cron(...)` expression | Runs the review every Monday at 09:00 in the timezone set by `ReviewScheduleTimezone`. Ignored unless `EnableReviewSchedule=Yes` — see [D3](../../docs/parameter-dependencies.md#d3). |
| `ReviewScheduleTimezone` | `String` | `'UTC'` | Any valid IANA timezone identifier (e.g. `America/New_York`, `Europe/London`, `Asia/Tokyo`) | The schedule expression above is interpreted in UTC. Ignored unless `EnableReviewSchedule=Yes` — see [D3](../../docs/parameter-dependencies.md#d3). |
| `EnableHtmlReport` | `String` | `'Yes'` | `'Yes'` \| `'No'` | Sets the initial SSM value at deploy time. Edit `/connect-ops-review/config` post-deploy to change without redeploying. When enabled, after all analyzers complete the `ReportGeneratorFunction` produces a single-file HTML report at `s3://<bucket>/connect-review_<alias>_<region>_<timestamp>.html`. See [D2](../../docs/parameter-dependencies.md#d2). |
| `RetainJsonData` | `String` | `'No'` | `'Yes'` \| `'No'` | Sets the initial SSM value at deploy time. Edit `/connect-ops-review/config` post-deploy to change without redeploying. Runtime enforces `retainJsonData=true` when Glue is enabled or the HTML report is disabled (see [D1](../../docs/parameter-dependencies.md#d1) / [D2](../../docs/parameter-dependencies.md#d2)). |
| `DevOpsAgentAgentSpaceId` | `String` | `''` (empty) | Free-form string | No "Ask DevOps Agent" buttons are rendered in the HTML report; the report still works otherwise. Has no effect if `EnableHtmlReport=No` — see [D4](../../docs/parameter-dependencies.md#d4). |

<!-- BEGIN:parameter-dependencies -->
## Parameter Dependencies

Several parameters interact with each other at runtime — enabling one may require or disable another. The canonical, deployment-method-agnostic reference for these cross-parameter rules lives at [`docs/parameter-dependencies.md`](../../docs/parameter-dependencies.md) and applies to both the CloudFormation and Terraform deployments. The summary below lists the rules that apply to this (CloudFormation) deployment; follow the "Full rule" link for the IF/THEN/BECAUSE detail and the observable failure mode.

| ID | Summary | Full rule |
|----|---------|-----------|
| D1 | If `EnableGlueCatalog=Yes` then `RetainJsonData=Yes` is required so the Glue tables have data to point at after each run. | [docs/parameter-dependencies.md#d1](../../docs/parameter-dependencies.md#d1) |
| D2 | If `EnableHtmlReport=No` then raw JSON in `s3://<bucket>/data/…` is the only output; `RetainJsonData` becomes a no-op in that configuration. | [docs/parameter-dependencies.md#d2](../../docs/parameter-dependencies.md#d2) |
| D3 | If `EnableReviewSchedule=No` then `ReviewScheduleExpression` and `ReviewScheduleTimezone` are no-ops (the scheduler is not created). | [docs/parameter-dependencies.md#d3](../../docs/parameter-dependencies.md#d3) |
| D4 | If `EnableHtmlReport=No` then `DevOpsAgentAgentSpaceId` has no effect (there is no HTML report to inject "Ask DevOps Agent" buttons into). | [docs/parameter-dependencies.md#d4](../../docs/parameter-dependencies.md#d4) |
<!-- END:parameter-dependencies -->

## Differences from the Terraform deployment

The CloudFormation and Terraform deployments produce functionally equivalent stacks, but the two surfaces are not identical. The table below enumerates every meaningful divergence; the mirror of this table lives in [`deploy/terraform/README.md`](../terraform/README.md#differences-from-the-cloudformation-deployment) and is intentionally byte-identical.

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

### Automated Execution

The deployment includes an EventBridge Scheduler that automatically triggers the review on the configured schedule (default: weekly). No additional setup is needed.

### Manual Execution

Trigger a review on-demand via the Step Functions console or CLI.

**Simplest invocation (uses SSM config defaults):**

```bash
aws stepfunctions start-execution \
  --state-machine-arn <OrchestratorStateMachineArn from stack outputs> \
  --input '{}'
```

**With overrides (optional — any field can be omitted and will fall back to SSM/env defaults):**

```bash
aws stepfunctions start-execution \
  --state-machine-arn <OrchestratorStateMachineArn from stack outputs> \
  --input '{"instanceArn":"arn:aws:connect:<region>:<account>:instance/<id>","s3ReportingBucket":"<bucket>","daysBack":7}'
```

> **Note:** All input fields are optional. If omitted, values are resolved from the SSM parameter (`/connect-ops-review/config`) or CloudFormation-deployed environment variables.

The state machine runs all 8 analyzers in parallel, then generates an HTML report at:
`s3://<bucket>/connect-review_<instance-alias>_<region>_<timestamp>.html`

### Checking Execution Status

```bash
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --query '{status:status,analyzersSucceeded:output.analyzersSucceeded}'
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

Set any analyzer to `false` to skip it. Timeout values are in seconds.

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

### Update via Console

1. Upload the new template to S3
2. Open CloudFormation Console → select your stack
3. Click **Update** → **Replace current template** → provide the S3 URL
4. Click through — parameters stay the same
5. Review and submit

### Update via CLI

```bash
# Upload updated template
aws s3 cp CFT-AmazonConnectOperationsReview.yml s3://<your-bucket>/cfn-templates/

# Update stack
aws cloudformation update-stack \
  --stack-name ConnectOpsReview \
  --template-url https://s3.amazonaws.com/<your-bucket>/cfn-templates/CFT-AmazonConnectOperationsReview.yml \
  --parameters \
    ParameterKey=AmazonConnectInstanceARN,UsePreviousValue=true \
    ParameterKey=AmazonS3ForReports,UsePreviousValue=true \
    ParameterKey=EnableGlueCatalog,UsePreviousValue=true \
    ParameterKey=ReviewScheduleExpression,UsePreviousValue=true \
    ParameterKey=ReviewScheduleTimezone,UsePreviousValue=true \
  --capabilities CAPABILITY_IAM
```

## Teardown

```bash
aws cloudformation delete-stack --stack-name ConnectOpsReview
```

This removes all deployed resources. It does **not** remove:
- The S3 reporting bucket or its contents
- The Amazon Connect instance

## Stack Outputs

| Output | Description |
| ------ | ----------- |
| `OrchestratorStateMachineArn` | ARN of the Step Functions state machine |
| `OrchestratorStateMachineName` | Name of the state machine |
| `SSMConfigParameterName` | SSM parameter for runtime configuration |

## Post-Deployment Verification

After the stack reaches `CREATE_COMPLETE`, walk this ordered checklist to confirm the deployment actually works end-to-end.

1. **Confirm all Lambda functions were created.** All 10 should be present (PrepareContext, 8 analyzers, ReportGenerator):
   ```bash
   aws lambda list-functions \
     --query "Functions[?starts_with(FunctionName, 'ConnectOpsReview')].FunctionName"
   ```
2. **Find stack outputs.** Note the `OrchestratorStateMachineArn` — you'll use it in step 3:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name ConnectOpsReview \
     --query 'Stacks[0].Outputs'
   ```
3. **Trigger the first run manually** (do not wait for the schedule):
   ```bash
   aws stepfunctions start-execution \
     --state-machine-arn <OrchestratorStateMachineArn from step 2> \
     --input '{}'
   ```
   Capture the returned `executionArn`.
4. **Wait for the execution to succeed.** The full run typically completes in 3–15 minutes depending on account size:
   ```bash
   aws stepfunctions describe-execution --execution-arn <executionArn>
   ```
   `status` should be `SUCCEEDED`.
5. **Locate the HTML report** (only if `EnableHtmlReport=Yes`, the default). It is written to:
   ```
   s3://<AmazonS3ForReports>/connect-review_<instance-alias>_<region>_<timestamp>.html
   ```
   Download and open in any browser.
6. **Locate the raw JSON data** (only if `RetainJsonData=Yes`, or if `EnableHtmlReport=No`). It is written under a Hive-partitioned prefix per component:
   ```
   s3://<AmazonS3ForReports>/data/<component>/year=<YYYY>/month=<MM>/day=<DD>/<file>.json
   ```
7. **Verify Glue tables are non-empty in Athena** (only if `EnableGlueCatalog=Yes`). In the Athena console, select the `connect_ops_review` database (or the name given by the stack) and run a simple `SELECT count(*) FROM <table>;` against each of the 8 analyzer tables. Every table should return a non-zero count for at least one partition.

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

If you use [AWS DevOps Agent](https://docs.aws.amazon.com/devops-agent/latest/userguide/), this solution includes per-check "Ask DevOps Agent" buttons in the HTML report. To enable:

1. Set the `DevOpsAgentAgentSpaceId` parameter during deployment (find yours via `aws devops-agent list-agent-spaces --region us-east-1`)
2. The report will include a CLI command generator button next to each finding

Companion DevOps Agent skills (investigation, remediation, proactive evaluation) are available separately — contact your AWS TAM for access.

## Troubleshooting

| Issue | Resolution |
| ----- | ---------- |
| `create-stack` fails with "Validation failed with N error(s)" | Use change set deploy instead — see CLI instructions above. CloudFormation's pre-create validation rejects oversized `Code.ZipFile` payloads. Change sets bypass this. |
| Stack creation fails at layer builder | Check CloudWatch logs for the Boto3LayerBuilder function — requires internet access for pip |
| Execution fails at PrepareContext | Verify the Connect instance ARN is correct and in the same region |
| Single analyzer fails, others succeed | Check that analyzer's CloudWatch log group; disable via SSM config if needed |
| `AccessDeniedException` | Verify the instance is in the same region as the stack |
| Partial results | Analyzer hit its time budget — increase timeout in SSM config or reduce `daysBack` |
| Template upload fails | Template is ~700KB — upload to S3 first, then reference the S3 URL |
| Empty report | Ensure the S3 bucket exists and is in the same region |
| Redeploy fails with `ParameterAlreadyExists` | The SSM parameter `/connect-ops-review/config` has `DeletionPolicy: Retain`. Delete it manually before redeploying: `aws ssm delete-parameter --name /connect-ops-review/config` |
