# Cleanup & Redeployment Guide

> **Audience:** For deployers tearing down or redeploying the stack.

This guide covers complete removal of the Amazon Connect Operational Review solution from an AWS account, as well as preparation for clean redeployment. It applies to **both CloudFormation and Terraform** deployments since both create the same underlying resources.

---

## Quick Removal (Standard Uninstall)

### CloudFormation

```bash
aws cloudformation delete-stack --stack-name ConnectOpsReview
aws cloudformation wait stack-delete-complete --stack-name ConnectOpsReview
```

### Terraform

```bash
cd deploy/terraform
terraform destroy -var="connect_instance_arn=arn:aws:connect:REGION:ACCOUNT:instance/ID"
```

These commands remove all stack-managed resources (Lambda functions, IAM roles, Step Functions, EventBridge Scheduler, SSM parameter, Glue tables). However, **some resources persist** because they were created at runtime rather than by the IaC tool.

---

## Orphaned Resources (Must Be Cleaned Manually)

After stack/module deletion, the following resources may remain in the account. These are created **at runtime by Lambda invocations** and are not tracked by CloudFormation or Terraform state.

### 1. CloudWatch Log Groups

Each Lambda function auto-creates its own log group on first invocation. These persist after stack deletion.

```bash
# List orphaned log groups
aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/ConnectOpsReview" \
  --query "logGroups[*].logGroupName" --output table

# Delete all orphaned log groups
for lg in $(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/ConnectOpsReview" \
  --query "logGroups[*].logGroupName" --output text); do
  echo "Deleting: $lg"
  aws logs delete-log-group --log-group-name "$lg"
done
```

**Expected log groups** (up to 13 if all analyzers ran):

| Log Group | Source |
|-----------|--------|
| `/aws/lambda/ConnectOpsReview-PrepareContext` | PrepareContext Lambda |
| `/aws/lambda/ConnectOpsReview-SecurityAnalyzer` | Security analyzer |
| `/aws/lambda/ConnectOpsReview-ResilienceAnalyzer` | Resilience analyzer |
| `/aws/lambda/ConnectOpsReview-CloudTrailAnalyzer` | CloudTrail analyzer |
| `/aws/lambda/ConnectOpsReview-OpExAnalyzer` | Operational Excellence analyzer |
| `/aws/lambda/ConnectOpsReview-CapacityAnalyzer` | Capacity analyzer |
| `/aws/lambda/ConnectOpsReview-ObservabilityAnalyzer` | Observability analyzer |
| `/aws/lambda/ConnectOpsReview-CostAnalyzer` | Cost analyzer |
| `/aws/lambda/ConnectOpsReview-AIAnalyzer` | AI analyzer |
| `/aws/lambda/ConnectOpsReview-ReportGenerator` | Report generator |
| `/aws/lambda/ConnectOpsReview-DeleteJsonData` | JSON cleanup |
| `/aws/lambda/ConnectOpsReview-Boto3LayerBuilder` | Layer builder (CFT only) |
| `/aws/lambda/ConnectOpsReview-SharedUtilsLayerBuilder` | Layer builder (CFT only) |

### 2. Lambda Layers

Custom Lambda layers created by the layer builder functions persist after deletion.

```bash
# List orphaned layers
aws lambda list-layers \
  --query "Layers[?contains(LayerName,'ConnectOpsReview')].[LayerName,LatestMatchingVersion.Version]" \
  --output table

# Delete each layer version (must delete all versions)
LAYER_NAME="ConnectOpsReview-SharedUtils"
for v in $(aws lambda list-layer-versions --layer-name "$LAYER_NAME" \
  --query "LayerVersions[*].Version" --output text); do
  aws lambda delete-layer-version --layer-name "$LAYER_NAME" --version-number "$v"
done

# Repeat for Boto3 layer (CFT deployments)
LAYER_NAME="ConnectOpsReview-Boto3LayerBuilder-layer"
for v in $(aws lambda list-layer-versions --layer-name "$LAYER_NAME" \
  --query "LayerVersions[*].Version" --output text 2>/dev/null); do
  aws lambda delete-layer-version --layer-name "$LAYER_NAME" --version-number "$v"
done
```

### 3. S3 Data (Reports and Analyzer JSON)

The S3 bucket is **not created by the stack** (it's a pre-existing bucket you provide), so it's never deleted. However, the solution writes data to it:

```bash
# List report files
aws s3 ls s3://YOUR-BUCKET/ --recursive | grep -E "connect-review|data/"

# Remove all solution data (optional — keeps the bucket)
aws s3 rm s3://YOUR-BUCKET/data/ --recursive
aws s3 rm s3://YOUR-BUCKET/reports/ --recursive
```

### 4. SSM Parameter (Legacy rc.3 and Earlier Only)

> **Note:** This manual step is required **only for stacks originally deployed on rc.3 or earlier**, which shipped the `/connect-ops-review/config` SSM parameter with `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`. Under those templates the parameter persisted after every stack deletion and had to be removed manually before redeployment.
>
> For **rc.4 and later** deployments, the retain policies were removed and the SSM parameter is deleted as part of normal stack deletion (CloudFormation) or `terraform destroy` (Terraform). No manual SSM cleanup is needed.
>
> If you are unsure which release your stack was originally deployed from, or if a previous rc.3-or-earlier deployment left the parameter behind, it is safe to run the command below — it is idempotent and succeeds whether or not the parameter exists.

```bash
aws ssm delete-parameter --name "/connect-ops-review/config" 2>/dev/null || true
```

---

## Full Cleanup Script

Run this to ensure all traces are removed after stack/module deletion:

```bash
#!/usr/bin/env bash
# cleanup-connect-ops-review.sh
# Run AFTER deleting the CloudFormation stack or running terraform destroy.
# Usage: ./cleanup-connect-ops-review.sh [AWS_PROFILE]

set -e
PROFILE="${1:+--profile $1}"

echo "=== Cleaning up orphaned Connect Ops Review resources ==="

# 1. CloudWatch Log Groups
echo "Deleting orphaned log groups..."
for lg in $(aws logs describe-log-groups $PROFILE \
  --log-group-name-prefix "/aws/lambda/ConnectOpsReview" \
  --query "logGroups[*].logGroupName" --output text 2>/dev/null); do
  echo "  Deleting: $lg"
  aws logs delete-log-group $PROFILE --log-group-name "$lg"
done

# Also check for state machine log group
aws logs delete-log-group $PROFILE \
  --log-group-name "/aws/states/ConnectOpsReview" 2>/dev/null || true

# 2. Lambda Layers
echo "Deleting orphaned Lambda layers..."
for layer in $(aws lambda list-layers $PROFILE \
  --query "Layers[?contains(LayerName,'ConnectOpsReview')].LayerName" \
  --output text 2>/dev/null); do
  for v in $(aws lambda list-layer-versions $PROFILE --layer-name "$layer" \
    --query "LayerVersions[*].Version" --output text); do
    echo "  Deleting: $layer version $v"
    aws lambda delete-layer-version $PROFILE --layer-name "$layer" --version-number "$v"
  done
done

# 3. SSM Parameter
echo "Removing SSM parameter (if orphaned)..."
aws ssm delete-parameter $PROFILE --name "/connect-ops-review/config" 2>/dev/null && \
  echo "  Deleted: /connect-ops-review/config" || \
  echo "  Not found (already clean)"

echo ""
echo "=== Cleanup complete ==="
echo "Note: S3 report data is NOT deleted automatically."
echo "To remove reports: aws s3 rm s3://YOUR-BUCKET/data/ --recursive"
echo "                    aws s3 rm s3://YOUR-BUCKET/reports/ --recursive"
```

---

## Preparing for Redeployment

If you plan to redeploy the solution (e.g., upgrading from v1 to v2, or redeploying after testing):

1. **Delete the existing stack** (CloudFormation) or **run `terraform destroy`**

2. **Delete the SSM parameter** (only for rc.3 or earlier stacks)

   > **Note:** This step is required **only if the stack you just deleted was originally deployed from rc.3 or earlier**. Those releases shipped the `/connect-ops-review/config` SSM parameter with `DeletionPolicy: Retain`, so the parameter persisted after every stack delete and would cause redeployment to fail with:
   >
   > ```
   > ParameterAlreadyExists: The parameter already exists. To overwrite this value, set the overwrite option in the request to true.
   > ```
   >
   > **rc.4 and later** deployments remove the retain policy — the SSM parameter is deleted along with the stack, so this step is a no-op. If you are unsure of the original release, the command below is idempotent (`|| true` on the not-found case) and safe to run either way.

   ```bash
   aws ssm delete-parameter --name "/connect-ops-review/config" 2>/dev/null || true
   ```

3. **Run the cleanup script above** to remove other orphaned resources (log groups, layers)

4. **Verify no conflicts remain:**
   ```bash
   # Should return empty results for all three:
   aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/ConnectOpsReview" \
     --query "logGroups[*].logGroupName"
   aws ssm get-parameter --name "/connect-ops-review/config" 2>&1 | grep -c "ParameterNotFound"
   aws lambda list-functions --query "Functions[?contains(FunctionName,'ConnectOpsReview')].FunctionName"
   ```

5. **Deploy fresh:**
   ```bash
   # CFT
   aws s3 cp CFT-AmazonConnectOperationsReview.yml s3://YOUR-STAGING-BUCKET/
   aws cloudformation create-stack --stack-name ConnectOpsReview \
     --template-url https://YOUR-STAGING-BUCKET.s3.REGION.amazonaws.com/CFT-AmazonConnectOperationsReview.yml \
     --capabilities CAPABILITY_NAMED_IAM \
     --parameters \
       ParameterKey=AmazonConnectInstanceARN,ParameterValue=YOUR-INSTANCE-ARN \
       ParameterKey=AmazonS3ForReports,ParameterValue=YOUR-BUCKET

   # Terraform
   terraform init && terraform apply
   ```

---

## Why This Cleanup Is Needed

CloudFormation's **pre-deployment validation** (introduced late 2025) includes an `AWS::EarlyValidation::ResourceExistenceCheck` that verifies resources the template creates don't already exist. When Lambda functions with hardcoded `FunctionName` values run, they auto-create CloudWatch Log Groups. If the stack is deleted and redeployed, these orphaned log groups trigger a validation failure:

```
Validation failed with 1 error(s). Call DescribeEvents to retrieve the full list 
of issues with resource and property details, resolve each error, then retry.
```

The cleanup script prevents this by removing all runtime-created resources that fall outside of CloudFormation/Terraform state management.

---

## Template Size Note (CloudFormation Only)

The CFT template exceeds 51,200 bytes (it embeds all Lambda code inline). You **must** upload it to S3 before deployment — the `--template-body file://` method won't work. Use either:

- `aws cloudformation deploy --s3-bucket YOUR-STAGING-BUCKET --template-file ...`
- Upload manually then use `--template-url https://BUCKET.s3.REGION.amazonaws.com/FILE`

Ensure the S3 staging bucket is in the **same region** as the CloudFormation stack.
