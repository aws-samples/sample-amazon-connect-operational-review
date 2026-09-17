# Developer Guide

> **Audience:** For developers contributing Lambda code or infra changes. For deployment usage, see [`deploy/cloudformation/README.md`](../deploy/cloudformation/README.md) or [`deploy/terraform/README.md`](../deploy/terraform/README.md).

Documentation for contributors to the Amazon Connect Operational Review repository.

## Prerequisites

- Python 3.12+
- pip (with virtualenv recommended)
- AWS CLI v2
- Terraform >= 1.8 (if working on TF deploy)
- `ruff` (Python linter/formatter)
- `pytest` (test runner)

## Local Development Setup

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-operational-review.git
cd sample-amazon-connect-operational-review

# Set up Python environment
cd src/lambda
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-cov ruff

# Verify setup
pytest tests/
ruff check .
```

## Monorepo Sync Workflow

The single most important rule: **all Lambda code changes start in `src/lambda/`**. After editing source files, you must sync to both deploy directories before committing.

### Step-by-Step Sync Process

#### 1. Edit source code

Make your changes in `src/lambda/`. This is the only place you write Lambda logic.

#### 2. Sync to Terraform

From the repo root:

```bash
cp src/lambda/*.py deploy/terraform/lambda_packages/
```

This is a simple file copy — Terraform's `archive_file` data sources pick up changes via `source_code_hash`.

#### 3. Sync to CloudFormation

The CFT embeds Lambda code inline in `ZipFile` blocks. Each analyzer needs its own sync command:

```bash
# Sync a specific analyzer
python3 deploy/cloudformation/scripts/update_cft_lambda.py \
  --lambda-source src/lambda/security_analyzer.py \
  --cft-template deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml \
  --no-backup

# Sync all analyzers (batch)
for f in prepare_context report_generator \
         security_analyzer resilience_analyzer \
         opex_analyzer capacity_analyzer observability_analyzer cost_analyzer \
         cloudtrail_analyzer ai_analyzer; do
  python3 deploy/cloudformation/scripts/update_cft_lambda.py \
    --lambda-source "src/lambda/${f}.py" \
    --cft-template deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml \
    --no-backup
done
```

#### 4. Sync shared utilities

If you changed `analyzer_common.py` or `graceful_timeout.py`:

```bash
python3 deploy/cloudformation/scripts/sync_shared_utils_to_cft.py \
  --source-dir src/lambda \
  --cft-template deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml
```

#### 5. Commit everything together

```bash
git add src/lambda/ deploy/terraform/lambda_packages/ deploy/cloudformation/
git commit -m "feat(analyzer): Add new check for X"
```

### What If I Forget to Sync?

The CI `sync` stage will fail with a clear error message showing exactly which files are out of sync and the commands to fix it. Fix locally, amend your commit, and push again.

### Updating Lambda code from a release package (Terraform)

Downstream users deploying via a released Terraform module tarball can refresh Lambda code without editing `src/lambda/` — they simply overlay the `lambda_packages/` directory shipped in a newer release and re-apply:

```bash
# From the extracted release tarball
cp -r lambda_packages/*.py <your-terraform-dir>/lambda_packages/
cd <your-terraform-dir>
terraform apply
```

For in-repo contributors, this shortcut is a no-op — the sync steps above (edit `src/lambda/` → copy into `deploy/terraform/lambda_packages/` → sync the CFT) are the authoritative flow, and the release tarball's `lambda_packages/` is produced by CI from exactly those synced files.

## Testing

### Unit Tests (Local)

```bash
cd src/lambda
pytest tests/ --cov=. --cov-report=term-missing
```

### Linting

```bash
cd src/lambda
ruff check .           # Find issues
ruff check . --fix     # Auto-fix what's possible
ruff format --check .  # Check formatting
ruff format .          # Auto-format
```

### Testing a Deployed Stack

After deploying via either CFT or Terraform:

```bash
# Invoke the orchestrator (empty input uses SSM defaults)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:<region>:<account>:stateMachine:ConnectOpsReview-Orchestrator \
  --input '{}'

# With overrides
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:<region>:<account>:stateMachine:ConnectOpsReview-Orchestrator \
  --input '{"instanceArn":"<arn>","s3ReportingBucket":"<bucket>","daysBack":7}'

# Check execution status
aws stepfunctions describe-execution --execution-arn <execution-arn>

# Check S3 for the report
aws s3 ls s3://<bucket>/reports/ | tail -5

# Check individual analyzer output
aws s3 ls s3://<bucket>/data/security/ | tail -5

# Check CloudWatch Logs for errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/ConnectOpsReview-SecurityAnalyzer \
  --filter-pattern "ERROR"
```

## Adding a New Analyzer

To add a new analyzer (e.g., `networking_analyzer`):

1. **Create the source file**: `src/lambda/networking_analyzer.py`
   - Import `analyzer_common` and `graceful_timeout`
   - Implement `lambda_handler(event, context)` returning `AnalyzerResult`
   - Follow the pattern of an existing analyzer (e.g., `security_analyzer.py`)

2. **Add to the Step Functions state machine**: Edit `src/lambda/state-machine-definition.asl.json`
   - Add a new branch in the Parallel state with a Lambda invoke task

3. **Add IAM permissions**: In both deploy directories:
   - `deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml` — add a new Lambda resource + IAM policy statements
   - `deploy/terraform/parallel.tf` — add a new `aws_lambda_function` resource + IAM policy

4. **Add to the sync pipeline**: Edit `.gitlab-ci.yml` `sync:cloudformation-embedded-code` job — add the new analyzer name to the `ANALYZERS` variable

5. **Add to report rendering**: Edit `report_generator.py` to add the section renderer and recommendation text

6. **Add to SSM config schema**: Document the new analyzer in the `analyzers` toggle map

7. **Sync and test**: Run the full sync workflow, add unit tests, verify with a deployed stack

## Adding a Check to an Existing Analyzer

Simpler than adding a whole new analyzer:

1. **Edit the analyzer** (e.g., `src/lambda/security_analyzer.py`):
   - Add your check logic
   - Append findings to the `findings` list
   - Respect the time budget (`graceful_timeout`)

2. **Update report rendering** (if needed): Edit `report_generator.py` to render the new check in the appropriate section

3. **Sync**: Run the sync workflow for the changed analyzer file

4. **Test**: Add a unit test for the new check, run `pytest`

## Report Rendering Pipeline

```text
Analyzer Lambda
    │
    │  Writes AnalyzerResult JSON to S3:
    │  s3://<bucket>/data/<componentType>/year=YYYY/month=MM/day=DD/<reviewId>.json
    │
    ▼
ReportGenerator Lambda
    │
    │  1. Lists all analyzer JSON for this reviewId
    │  2. Reads each JSON file
    │  3. Renders HTML section per pillar (findings → styled table rows)
    │  4. Computes executive summary (worst status per pillar → badge)
    │  5. Writes HTML to s3://<bucket>/reports/connect-review-<timestamp>.html
    │
    ▼
HTML Report (self-contained, no external dependencies)
```

### Status Rollup Logic

Within each analyzer, individual checks have statuses. The pillar status is the **worst** status among its checks:
- Any `fail` → pillar is `fail`
- No fail but any `warn` → pillar is `warn`
- All `pass` → pillar is `pass`
- `error` means the analyzer itself couldn't run

## Debugging

### Analyzer Shows "error" Status

This means the analyzer Lambda failed to execute properly. Check:
1. CloudWatch Logs for the specific analyzer Lambda
2. Step Functions execution history (console → State Machines → execution → events)
3. Common causes: IAM permission denied, timeout exceeded, Connect instance not found

### Analyzer Shows "partial" Results

The graceful timeout fired — the analyzer ran out of time and saved what it had. This is expected for large accounts (especially CloudTrail). The report will indicate which checks were skipped.

### Report Not Generated

If all analyzers ran but no report appeared:
1. Check the ReportGenerator Lambda logs
2. Verify S3 bucket permissions (the report generator needs `s3:PutObject` on the reports prefix)
3. Check if `generateHtmlReport` is `true` in SSM config

## CloudFormation vs Terraform Differences

Both deploy directories produce identical runtime behavior, but differ in packaging:

| Aspect | CloudFormation | Terraform |
| ------ | -------------- | --------- |
| Lambda code | Inline `ZipFile` blocks in YAML | External `.py` files zipped via `archive_file` |
| Shared utils | Lambda Layer built by custom resource | Lambda Layer from `archive_file` zip |
| Boto3 layer | Built at deploy time (needs internet) | Pre-packaged zip (no internet needed) |
| State management | CloudFormation stack | Terraform state file (S3 + DynamoDB) |

## Key Files Reference

| Path | Purpose |
| ---- | ------- |
| `.gitlab-ci.yml` | CI/CD pipeline definition |
| `src/lambda/state-machine-definition.asl.json` | Step Functions ASL definition |
| `deploy/cloudformation/scripts/update_cft_lambda.py` | Embeds .py source into CFT ZipFile blocks |
| `deploy/cloudformation/scripts/sync_shared_utils_to_cft.py` | Embeds shared utils into CFT layer builder |
| `deploy/terraform/parallel.tf` | Terraform resources for parallel analyzer Lambdas |
| `deploy/terraform/lambda_packages/` | Synced .py copies consumed by Terraform |

## Deploying for Testing

### CloudFormation

```bash
# Upload template to S3 (exceeds 51KB direct upload limit)
aws s3 cp deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml \
  s3://<your-bucket>/cfn-templates/

# Deploy
aws cloudformation create-stack \
  --stack-name ConnectOpsReview-Dev \
  --template-url https://s3.amazonaws.com/<your-bucket>/cfn-templates/CFT-AmazonConnectOperationsReview.yml \
  --capabilities CAPABILITY_IAM \
  --parameters \
    ParameterKey=AmazonConnectInstanceARN,ParameterValue="<instance-arn>" \
    ParameterKey=AmazonS3ForReports,ParameterValue="<bucket-name>"

# Wait for completion
aws cloudformation wait stack-create-complete --stack-name ConnectOpsReview-Dev
```

### Terraform

```bash
cd deploy/terraform

# Configure for your test account
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with test instance ARN, S3 bucket, region

# Deploy
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### Invoke and Verify

After deploying via either method:

```bash
# Start an execution (empty input uses SSM/env defaults)
aws stepfunctions start-execution \
  --state-machine-arn <state-machine-arn-from-outputs> \
  --input '{}'

# With explicit overrides (all fields optional)
aws stepfunctions start-execution \
  --state-machine-arn <state-machine-arn-from-outputs> \
  --input '{"instanceArn":"<arn>","s3ReportingBucket":"<bucket>","daysBack":7}'

# Check execution status
aws stepfunctions describe-execution --execution-arn <execution-arn>

# Check S3 for the report
aws s3 ls s3://<bucket>/reports/ | tail -5

# Check CloudWatch Logs for errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/ConnectOpsReview-SecurityAnalyzer \
  --filter-pattern "ERROR"
```

### Teardown

```bash
# CloudFormation
aws cloudformation delete-stack --stack-name ConnectOpsReview-Dev

# Terraform
cd deploy/terraform && terraform destroy
```

## Terraform-Specific Cautions

### Variable Changes That Trigger Resource Replacement

Be careful modifying these Terraform variables — they cause destroy + recreate (downtime):

- `lambda_function_name` — changing destroys and recreates all Lambda functions
- `aws_region` — changing region requires full destroy and redeploy

Safe to change (updates environment variables only):
- `connect_instance_arn`
- `s3_reporting_bucket`

### The Boto3 Layer

The Terraform deployment includes a pre-packaged `boto3-layer.zip` that provides a newer boto3 than the Lambda runtime bundles. This is needed because the AI Analyzer requires `qconnect` API fields not available in the default Python 3.12 runtime.

| Aspect | CloudFormation | Terraform |
| ------ | -------------- | --------- |
| Boto3 layer | Built at deploy time via custom resource (needs internet) | Pre-packaged zip (no internet needed at apply time) |

There is no mechanism to pin the boto3 layer to a specific version in the TF variant. A breaking boto3 update could affect Lambda behavior at next `terraform apply`.

### Terraform State

- Never share `.tfstate` files — they contain sensitive resource identifiers
- Always use remote state (S3 + DynamoDB) for shared environments
- `terraform.tfvars` is gitignored — it contains customer-specific values

## CloudFormation-Specific Cautions

### Do Not Edit ZipFile Blocks Directly

The `ZipFile: |` blocks in `CFT-AmazonConnectOperationsReview.yml` are generated by the sync scripts. Never edit them directly — your changes will be overwritten on the next sync. Always edit in `src/lambda/` and run the sync workflow.

### SharedUtils Layer — BuildVersion Requirement

The `SharedUtilsLayerBuilder` custom resource embeds `analyzer_common.py` and `graceful_timeout.py` as inline constants. CloudFormation only triggers a rebuild when resource properties change.

**When you update shared utility code, you MUST increment `BuildVersion`** in the `SharedUtilsLayerBuilder` resource:

```yaml
SharedUtilsLayerBuilder:
  Type: Custom::LayerBuilder
  Properties:
    ServiceToken: !GetAtt LayerBuilderFunction.Arn
    BuildVersion: "4"          # ← Increment this on every shared code change
    ...
```

Increment when:
- Any change to `analyzer_common.py`
- Any change to `graceful_timeout.py`

The sync scripts do not auto-increment this value. The developer must do it manually when syncing shared utility changes.

### Template Size Limits

The CFT template with all embedded Lambda code is ~700KB. CloudFormation limits:
- Direct upload: 51,200 bytes (must use S3 URL instead)
- S3-referenced: 1MB max

Adding many more analyzers with inline code could approach the limit.

### Stack Update Behavior

- IAM role changes: in-place update (no downtime)
- Lambda code changes: in-place update (no downtime)
- Lambda function name change: **replacement** (creates new, deletes old)
- Resource logical ID changes: **replacement** (dangerous — can cause deletion)

## Known Limitations

- **Single-instance only**: Each deployment targets one Connect instance. Multi-instance requires separate CFT stacks or TF state files/workspaces.
- **No drift detection** (Terraform): If someone modifies resources in the console, Terraform won't know until next `plan`.
- **CloudTrail analyzer timeout**: Hard 900s Lambda timeout. Large accounts with high API event volume may see partial results.
- **No automatic rollback** (Terraform): Unlike CloudFormation, a failed `apply` leaves partial state that must be manually resolved.
- **CFT layer rebuild requires internet**: Stack updates that trigger the boto3 layer custom resource need Lambda internet access (NAT gateway or VPC endpoint).
- **Report rendering is all-or-nothing**: If the ReportGenerator Lambda fails, no HTML is produced even if all analyzers succeeded. The raw JSON data in S3 is still available.
