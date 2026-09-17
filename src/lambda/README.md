# Amazon Connect Operational Review — Lambda Source

This directory is the **single source of truth** for all Lambda function code. Changes here propagate to both deployment directories via sync scripts and are validated by CI.

## Architecture

### Parallel Orchestration (Step Functions)

```text
EventBridge Scheduler (rate/cron)
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  Step Functions — ConnectOpsReview-Orchestrator       │
│                                                       │
│  PrepareContext ──► Parallel Fan-Out ──► ReportGen    │
│                     ┌───────────────┐                 │
│                     │ Security      │                 │
│                     │ Resilience    │                 │
│                     │ CloudTrail    │                 │
│                     │ OpEx          │                 │
│                     │ Capacity      │                 │
│                     │ Observability │                 │
│                     │ Cost          │                 │
│                     │ AI            │                 │
│                     └───────────────┘                 │
└───────────────────────────────────────────────────────┘
        │
        ▼
   S3 (HTML report + structured JSON per analyzer)
```

### Data Flow

1. **EventBridge** triggers Step Functions on a schedule (default: `rate(7 days)`)
2. **PrepareContext** reads SSM config, resolves Connect instance, produces `ExecutionContext`
3. **Parallel fan-out** invokes all enabled analyzers concurrently — each writes findings to S3
4. **ReportGenerator** reads all analyzer JSON from S3, renders HTML report with executive summary

## Source Files

| File | Purpose |
| ---- | ------- |
| `prepare_context.py` | Reads SSM config, normalizes input into ExecutionContext |
| `security_analyzer.py` | Identity management, S3/streaming encryption, AI guardrails |
| `resilience_analyzer.py` | Multi-region (ACGR), carrier diversity, DR readiness |
| `cloudtrail_analyzer.py` | CloudTrail API throttling analysis with time budget |
| `opex_analyzer.py` | Contact flow logging hygiene, KVS retention, AI agent inventory |
| `capacity_analyzer.py` | Service quotas, concurrency limits, growth trends |
| `observability_analyzer.py` | CloudWatch alarm validation, missed calls, log groups |
| `cost_analyzer.py` | Channel usage mix, telephony inventory, usage patterns |
| `ai_analyzer.py` | AI agent inventory, prompt config, guardrails, domain encryption |
| `report_generator.py` | Assembles HTML report from analyzer JSON results in S3 |
| `analyzer_common.py` | Shared utilities: input validation, result formatting, S3 persistence |
| `graceful_timeout.py` | Internal time budget pattern for graceful partial results |
| `lambda_function.py` | Legacy monolithic entry point (kept for backward compatibility) |

## Report Sections

| Pillar | Checks |
| ------ | ------ |
| **Security** | Identity Management (SAML), S3 Data Encryption, Streaming Encryption, AI Guardrails, AI Domain Encryption |
| **Resilience** | Multi-AZ Architecture, Global Resiliency (ACGR), Carrier Diversity, KB Sync Health |
| **Operational Excellence** | API Throttling, Contact Flow Logging, KVS Retention, AI Agent Inventory, AI Prompt Configuration, Misconfigured Phone Numbers |
| **Capacity** | Instance Resource Limits, Concurrency Limits, Account Level API Limits, Cases/AppInt/Profiles Limits, AI Agents Limits |
| **Observability** | CloudWatch Alarm Validation (Connect & KDS), Missed Calls Analysis, AI Agent Logging |
| **Cost** | Phone Number Mix Analysis, Channel Usage Breakdown, Unused Phone Numbers, Phone Number Distribution |
| **AI** | Agent Inventory & Configuration, Prompt Configuration, Guardrails, Domain Encryption, Agent Logging, Hard Limits |

## Key Design Patterns

- **Graceful timeout**: Each analyzer has an internal time budget (Lambda timeout − 60s safety margin). If the budget is exceeded, partial results are persisted to S3 before the Lambda terminates.
- **Hive-style S3 partitioning**: Results are written to `data/{componentType}/year=YYYY/month=MM/day=DD/{reviewId}.json` for Athena queryability.
- **Per-analyzer toggle**: Each analyzer can be independently enabled/disabled via SSM Parameter Store without redeployment.
- **Standardized result schema**: All analyzers return an `AnalyzerResult` dict with `componentType`, `status`, `findings`, `durationMs`, `s3ResultKey`, and optional `partial` fields.
- **5-status executive summary**: Each pillar gets a status — pass (green), fail (red), warn (amber), info (blue), error (grey).

## How Changes Flow (Monorepo Sync Workflow)

1. **Edit files** in `src/lambda/` — this is the only place you write Lambda code
2. **Sync to Terraform**: `cp src/lambda/*.py deploy/terraform/lambda_packages/`
3. **Sync to CloudFormation**: run sync scripts per changed file:
   ```bash
   python3 deploy/cloudformation/scripts/update_cft_lambda.py \
     --lambda-source src/lambda/<analyzer>.py \
     --cft-template deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml \
     --no-backup
   ```
4. **Sync shared utils** (if `analyzer_common.py` or `graceful_timeout.py` changed):
   ```bash
   python3 deploy/cloudformation/scripts/sync_shared_utils_to_cft.py \
     --source-dir src/lambda \
     --cft-template deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml
   ```
5. **Commit** source + synced artifacts together
6. **Push** to feature branch → open MR → CI validates lint + test + sync → merge

> If you forget to sync, the CI `sync` stage will fail with exact instructions on how to fix it.

## Development Workflow

```bash
# Start from the current development branch
git checkout parallel-orchestration-architecture && git pull

# Create a feature branch
git checkout -b feature/my-change

# Make changes to the relevant analyzer file(s)
# Run tests
cd src/lambda
pytest tests/
ruff check .

# Sync to deploy directories (from repo root)
cp src/lambda/*.py deploy/terraform/lambda_packages/
# Run CFT sync for each changed analyzer (see above)

# Commit everything together
git add src/lambda/ deploy/terraform/lambda_packages/ deploy/cloudformation/
git commit -m "feat(analyzer): Add new check for X"

# Push and open MR
git push -u origin feature/my-change
```

## Testing

### Locally

```bash
cd src/lambda
pip install -r requirements.txt
pip install pytest pytest-cov ruff

# Unit tests with coverage
pytest tests/ --cov=. --cov-report=term-missing

# Lint
ruff check .
ruff format --check .
```

### Against a Deployed Stack

```bash
# Step Functions invocation (empty input uses SSM/env defaults)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:<region>:<account>:stateMachine:ConnectOpsReview-Orchestrator \
  --input '{}'

# With overrides (all fields optional)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:<region>:<account>:stateMachine:ConnectOpsReview-Orchestrator \
  --input '{"instanceArn":"arn:aws:connect:<region>:<account>:instance/<id>","s3ReportingBucket":"<bucket>","daysBack":7}'

# Check execution status
aws stepfunctions describe-execution --execution-arn <arn>

# Check the report output
aws s3 ls s3://<bucket>/ | grep connect-review

# Check logs for errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/ConnectOpsReview-SecurityAnalyzer \
  --filter-pattern "ERROR"
```

## Runtime Configuration

Edit the SSM parameter `/connect-ops-review/config` to customize behavior without redeployment:

```json
{
  "instanceArn": "arn:aws:connect:us-east-1:123456789012:instance/abc-123",
  "s3ReportingBucket": "my-bucket",
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
  }
}
```

## IAM Permissions

All analyzer Lambdas share a consolidated IAM role (`ConnectOpsReview-SharedLambdaRole`) with least-privilege permissions scoped per analyzer. Key permission groups include Connect read, CloudTrail lookup, Service Quotas read, CloudWatch read, Cost Explorer read, KMS describe, S3 read/write (scoped to bucket prefixes), and SSM parameter read.

See the full policy breakdown in `deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml` or `deploy/terraform/main.tf`.

## Deployment

Deploy using either option from the monorepo:

### CloudFormation

The template is ~765 KB (Lambda code embedded inline). It exceeds CloudFormation's
51,200-byte limit for inline `--template-body` uploads, so `aws cloudformation deploy`
must stage it in S3 first via `--s3-bucket`:

```bash
aws cloudformation deploy \
  --template-file deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml \
  --stack-name amazon-connect-ops-review \
  --s3-bucket <your-staging-bucket> \
  --s3-prefix cfn-staging \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    AmazonConnectInstanceARN=arn:aws:connect:<region>:<account-id>:instance/<instance-id> \
    AmazonS3ForReports=<your-s3-bucket-name>
```

See `deploy/cloudformation/README.md` for the `create-stack` / change-set path and
full parameter reference.

### Terraform

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init && terraform apply
```

## Sync Requirement

After editing any file in `src/lambda/`, you **must** also sync the changes to the deploy directories before committing. The CI pipeline will reject pushes where deploy directories are out of sync.

See [docs/developer-guide.md](../../docs/developer-guide.md) for the detailed sync workflow.
