#!/usr/bin/env bash
# =============================================================================
# smoke-test-deployment.sh
# Post-deployment integration smoke test for Connect Ops Review
#
# Validates the end-to-end wiring between CFT/TF parameters, SSM config,
# IAM policies, Step Functions execution, and S3 output. Catches the class of
# bugs that live in the seams between components (QB-38, IAM bucket mismatches,
# orphaned config, etc.)
#
# Usage:
#   ./scripts/smoke-test-deployment.sh [OPTIONS]
#
# Options:
#   --stack-name NAME       CloudFormation stack name (default: ConnectOpsReview)
#   --profile PROFILE       AWS CLI profile (default: none / env credentials)
#   --region REGION         AWS region (default: us-west-2)
#   --skip-execution        Skip triggering the state machine (validate config only)
#   --timeout SECONDS       Max wait for execution (default: 300)
#   --help                  Show this help
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
#   2  Missing prerequisites (stack not found, etc.)
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
STACK_NAME="ConnectOpsReview"
PROFILE=""
REGION="us-west-2"
SKIP_EXECUTION=false
TIMEOUT=300

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case $1 in
    --stack-name)  STACK_NAME="$2"; shift 2 ;;
    --profile)     PROFILE="$2"; shift 2 ;;
    --region)      REGION="$2"; shift 2 ;;
    --skip-execution) SKIP_EXECUTION=true; shift ;;
    --timeout)     TIMEOUT="$2"; shift 2 ;;
    --help)
      sed -n '2,/^# ====/p' "$0" | grep '^#' | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

# Build AWS CLI base command
AWS_CMD="aws"
[[ -n "$PROFILE" ]] && AWS_CMD="$AWS_CMD --profile $PROFILE"
AWS_CMD="$AWS_CMD --region $REGION"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS=0
FAIL=0
WARN=0

pass() { PASS=$((PASS + 1)); echo "  ✅ PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ❌ FAIL: $1"; }
warn() { WARN=$((WARN + 1)); echo "  ⚠️  WARN: $1"; }
info() { echo "  ℹ️  $1"; }
header() { echo ""; echo "━━━ $1 ━━━"; }

# ---------------------------------------------------------------------------
# Preflight: verify stack exists
# ---------------------------------------------------------------------------
header "Preflight"

STACK_STATUS=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null) || {
  echo "ERROR: Stack '$STACK_NAME' not found in $REGION"
  exit 2
}

if [[ "$STACK_STATUS" == *"COMPLETE" ]]; then
  pass "Stack '$STACK_NAME' exists (status: $STACK_STATUS)"
else
  fail "Stack '$STACK_NAME' is in unexpected state: $STACK_STATUS"
  exit 2
fi

# ---------------------------------------------------------------------------
# 1. Collect stack outputs and parameters
# ---------------------------------------------------------------------------
header "1. Stack Configuration"

# Get outputs as key=value pairs
OUTPUTS=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" --output text)

STATE_MACHINE_ARN=$(echo "$OUTPUTS" | grep "OrchestratorStateMachineArn" | awk '{print $2}')
S3_BUCKET=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Parameters[?ParameterKey=='AmazonS3ForReports'].ParameterValue" --output text)
CONNECT_ARN=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Parameters[?ParameterKey=='AmazonConnectInstanceARN'].ParameterValue" --output text)
RETAIN_JSON=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Parameters[?ParameterKey=='RetainJsonData'].ParameterValue" --output text)
ENABLE_GLUE=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Parameters[?ParameterKey=='EnableGlueCatalog'].ParameterValue" --output text)

info "State Machine: $STATE_MACHINE_ARN"
info "S3 Bucket: $S3_BUCKET"
info "Connect ARN: $CONNECT_ARN"
info "RetainJsonData: $RETAIN_JSON"
info "EnableGlueCatalog: $ENABLE_GLUE"

[[ -n "$STATE_MACHINE_ARN" ]] && pass "State machine ARN found in outputs" || fail "State machine ARN missing from stack outputs"
[[ -n "$S3_BUCKET" ]] && pass "S3 bucket parameter found" || fail "S3 bucket parameter missing"

# ---------------------------------------------------------------------------
# 2. SSM Parameter validation
# ---------------------------------------------------------------------------
header "2. SSM Parameter Config"

SSM_VALUE=$($AWS_CMD ssm get-parameter \
  --name "/connect-ops-review/config" \
  --query "Parameter.Value" --output text 2>/dev/null) || {
  fail "SSM parameter /connect-ops-review/config does not exist"
  SSM_VALUE=""
}

if [[ -n "$SSM_VALUE" ]]; then
  pass "SSM parameter exists"

  # Check instance ARN matches
  SSM_INSTANCE=$(echo "$SSM_VALUE" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('instanceArn',''))" 2>/dev/null)
  if [[ "$SSM_INSTANCE" == "$CONNECT_ARN" ]]; then
    pass "SSM instanceArn matches stack parameter"
  else
    fail "SSM instanceArn mismatch: SSM='$SSM_INSTANCE' vs Stack='$CONNECT_ARN'"
  fi

  # Check bucket matches
  SSM_BUCKET=$(echo "$SSM_VALUE" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('s3ReportingBucket',''))" 2>/dev/null)
  if [[ "$SSM_BUCKET" == "$S3_BUCKET" ]]; then
    pass "SSM s3ReportingBucket matches stack parameter"
  else
    fail "SSM s3ReportingBucket mismatch: SSM='$SSM_BUCKET' vs Stack='$S3_BUCKET'"
  fi

  # Check retainJsonData (QB-38 — this will fail until the bug is fixed)
  SSM_RETAIN=$(echo "$SSM_VALUE" | /usr/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('retainJsonData', 'KEY_MISSING'))" 2>/dev/null)
  if [[ "$RETAIN_JSON" == "Yes" ]]; then
    if [[ "$SSM_RETAIN" == "True" || "$SSM_RETAIN" == "true" ]]; then
      pass "SSM retainJsonData=true (matches RetainJsonData=Yes)"
    elif [[ "$SSM_RETAIN" == "KEY_MISSING" ]]; then
      warn "SSM config missing 'retainJsonData' key (QB-38: manual executions will delete JSON)"
    else
      fail "SSM retainJsonData='$SSM_RETAIN' but stack has RetainJsonData=Yes"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 3. IAM Policy validation — S3 bucket reference
# ---------------------------------------------------------------------------
header "3. IAM Policy Validation"

SHARED_ROLE=$($AWS_CMD iam list-roles \
  --query "Roles[?starts_with(RoleName,'ConnectOpsReview-SharedLambdaRole-')].RoleName" \
  --output text 2>/dev/null)

if [[ -n "$SHARED_ROLE" ]]; then
  pass "SharedLambdaRole found: $SHARED_ROLE"

  # Check S3WriteAnalyzerResults policy references correct bucket
  S3_POLICY_RESOURCES=$($AWS_CMD iam get-role-policy \
    --role-name "$SHARED_ROLE" \
    --policy-name "S3WriteAnalyzerResults" \
    --query "PolicyDocument.Statement[0].Resource" --output text 2>/dev/null)

  if echo "$S3_POLICY_RESOURCES" | grep -q "$S3_BUCKET"; then
    pass "S3WriteAnalyzerResults policy references correct bucket ($S3_BUCKET)"
  else
    fail "S3WriteAnalyzerResults policy does NOT reference '$S3_BUCKET'"
    info "Policy references: $(echo "$S3_POLICY_RESOURCES" | head -2)"
  fi
else
  fail "SharedLambdaRole not found (expected ConnectOpsReview-SharedLambdaRole-*)"
fi

# ---------------------------------------------------------------------------
# 4. EventBridge Scheduler input validation
# ---------------------------------------------------------------------------
header "4. EventBridge Scheduler Input"

SCHEDULER_INPUT=$($AWS_CMD scheduler get-schedule \
  --name "ConnectOpsReview-RecurringSchedule" \
  --query "Target.Input" --output text 2>/dev/null) || SCHEDULER_INPUT=""

if [[ -n "$SCHEDULER_INPUT" ]]; then
  pass "Scheduler exists"

  SCHED_BUCKET=$(echo "$SCHEDULER_INPUT" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('s3ReportingBucket',''))" 2>/dev/null)
  SCHED_RETAIN=$(echo "$SCHEDULER_INPUT" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('retainJsonData','MISSING'))" 2>/dev/null)

  if [[ "$SCHED_BUCKET" == "$S3_BUCKET" ]]; then
    pass "Scheduler input references correct bucket"
  else
    fail "Scheduler input bucket mismatch: '$SCHED_BUCKET' vs '$S3_BUCKET'"
  fi

  if [[ "$RETAIN_JSON" == "Yes" && ("$SCHED_RETAIN" == "True" || "$SCHED_RETAIN" == "true") ]]; then
    pass "Scheduler input has retainJsonData=true"
  elif [[ "$RETAIN_JSON" == "No" && ("$SCHED_RETAIN" == "False" || "$SCHED_RETAIN" == "false") ]]; then
    pass "Scheduler input has retainJsonData=false (matches RetainJsonData=No)"
  else
    warn "Scheduler retainJsonData='$SCHED_RETAIN' vs stack RetainJsonData='$RETAIN_JSON'"
  fi
else
  warn "Scheduler not found (may be expected if EnableReviewSchedule=No)"
fi

# ---------------------------------------------------------------------------
# 5. Lambda function existence check
# ---------------------------------------------------------------------------
header "5. Lambda Functions"

EXPECTED_FUNCTIONS=(
  "ConnectOpsReview-PrepareContext"
  "ConnectOpsReview-SecurityAnalyzer"
  "ConnectOpsReview-ResilienceAnalyzer"
  "ConnectOpsReview-CloudTrailAnalyzer"
  "ConnectOpsReview-OpExAnalyzer"
  "ConnectOpsReview-CapacityAnalyzer"
  "ConnectOpsReview-ObservabilityAnalyzer"
  "ConnectOpsReview-CostAnalyzer"
  "ConnectOpsReview-AIAnalyzer"
  "ConnectOpsReview-ReportGenerator"
  "ConnectOpsReview-DeleteJsonData"
)

for fn in "${EXPECTED_FUNCTIONS[@]}"; do
  if $AWS_CMD lambda get-function --function-name "$fn" --query "Configuration.FunctionName" --output text &>/dev/null; then
    pass "Lambda exists: $fn"
  else
    fail "Lambda missing: $fn"
  fi
done

# ---------------------------------------------------------------------------
# 6. Glue Catalog (if enabled)
# ---------------------------------------------------------------------------
if [[ "$ENABLE_GLUE" == "Yes" ]]; then
  header "6. Glue Catalog"

  if $AWS_CMD glue get-database --name "connect_ops_review" &>/dev/null; then
    pass "Glue database 'connect_ops_review' exists"
  else
    fail "Glue database 'connect_ops_review' missing (EnableGlueCatalog=Yes)"
  fi

  EXPECTED_TABLES=("cloudtrail" "security" "resilience" "operational_excellence" "capacity" "observability" "cost" "ai")
  for table in "${EXPECTED_TABLES[@]}"; do
    if $AWS_CMD glue get-table --database-name "connect_ops_review" --name "$table" &>/dev/null; then
      pass "Glue table: $table"
    else
      fail "Glue table missing: $table"
    fi
  done
fi

# ---------------------------------------------------------------------------
# 7. Execution test (unless --skip-execution)
# ---------------------------------------------------------------------------
if [[ "$SKIP_EXECUTION" == false ]]; then
  header "7. State Machine Execution"

  # Use scheduler-style input to test the full path
  EXEC_INPUT=$(cat <<EOF
{
  "instanceArn": "$CONNECT_ARN",
  "s3ReportingBucket": "$S3_BUCKET",
  "generateHtmlReport": true,
  "retainJsonData": true
}
EOF
)

  EXECUTION_ARN=$($AWS_CMD stepfunctions start-execution \
    --state-machine-arn "$STATE_MACHINE_ARN" \
    --input "$EXEC_INPUT" \
    --query "executionArn" --output text 2>&1)

  if [[ "$EXECUTION_ARN" == arn:* ]]; then
    pass "Execution started: ${EXECUTION_ARN##*:}"
  else
    fail "Failed to start execution: $EXECUTION_ARN"
    SKIP_EXECUTION=true  # skip remaining execution checks
  fi

  if [[ "$SKIP_EXECUTION" == false ]]; then
    info "Waiting for completion (timeout: ${TIMEOUT}s)..."
    ELAPSED=0
    while [[ $ELAPSED -lt $TIMEOUT ]]; do
      STATUS=$($AWS_CMD stepfunctions describe-execution \
        --execution-arn "$EXECUTION_ARN" \
        --query "status" --output text)
      if [[ "$STATUS" != "RUNNING" ]]; then
        break
      fi
      sleep 10
      ELAPSED=$((ELAPSED + 10))
    done

    if [[ "$STATUS" == "SUCCEEDED" ]]; then
      pass "Execution completed: SUCCEEDED (${ELAPSED}s)"
    elif [[ "$STATUS" == "RUNNING" ]]; then
      fail "Execution timed out after ${TIMEOUT}s (still RUNNING)"
    else
      fail "Execution finished with status: $STATUS"
      # Get error details
      ERROR=$($AWS_CMD stepfunctions describe-execution \
        --execution-arn "$EXECUTION_ARN" \
        --query "[error,cause]" --output text 2>/dev/null)
      info "Error: $ERROR"
    fi
  fi

  # ---------------------------------------------------------------------------
  # 8. S3 Output validation
  # ---------------------------------------------------------------------------
  if [[ "${STATUS:-}" == "SUCCEEDED" ]]; then
    header "8. S3 Output Validation"

    # Extract reviewId from execution name for filtering
    EXEC_NAME="${EXECUTION_ARN##*:}"

    # Check HTML report
    HTML_COUNT=$($AWS_CMD s3 ls "s3://$S3_BUCKET/connect-review_" --recursive 2>/dev/null | wc -l | tr -d ' ')
    if [[ $HTML_COUNT -gt 0 ]]; then
      pass "HTML report(s) found in S3 ($HTML_COUNT total)"
    else
      fail "No HTML report found in S3 (expected connect-review_*.html)"
    fi

    # Check per-analyzer JSON (retainJsonData=true was passed)
    EXPECTED_COMPONENT_TYPES=("ai" "capacity" "cloudtrail" "cost" "observability" "operational_excellence" "resilience" "security")
    JSON_FOUND=0
    JSON_MISSING=()
    for ct in "${EXPECTED_COMPONENT_TYPES[@]}"; do
      COUNT=$($AWS_CMD s3 ls "s3://$S3_BUCKET/data/$ct/" --recursive 2>/dev/null | wc -l | tr -d ' ')
      if [[ $COUNT -gt 0 ]]; then
        JSON_FOUND=$((JSON_FOUND + 1))
      else
        JSON_MISSING+=("$ct")
      fi
    done

    if [[ $JSON_FOUND -eq 8 ]]; then
      pass "All 8 analyzer JSON files retained in S3"
    elif [[ $JSON_FOUND -gt 0 ]]; then
      warn "Only $JSON_FOUND/8 analyzer JSON files found (missing: ${JSON_MISSING[*]})"
    else
      fail "No analyzer JSON retained in S3 (retainJsonData=true was passed — possible QB-38)"
    fi

    # Check shared context data
    SHARED_COUNT=$($AWS_CMD s3 ls "s3://$S3_BUCKET/data/shared/" --recursive 2>/dev/null | wc -l | tr -d ' ')
    if [[ $SHARED_COUNT -gt 0 ]]; then
      pass "Shared context data found ($SHARED_COUNT files)"
    else
      warn "No shared context data in data/shared/ (may be expected if disabled)"
    fi

    # Check review-metadata
    META_COUNT=$($AWS_CMD s3 ls "s3://$S3_BUCKET/review-metadata/" --recursive 2>/dev/null | wc -l | tr -d ' ')
    if [[ $META_COUNT -gt 0 ]]; then
      pass "Review metadata found ($META_COUNT files)"
    else
      warn "No review-metadata found"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
header "Summary"
TOTAL=$((PASS + FAIL + WARN))
echo ""
echo "  Results: $PASS passed, $FAIL failed, $WARN warnings ($TOTAL total checks)"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "  ❌ SMOKE TEST FAILED — $FAIL issue(s) need attention"
  exit 1
else
  echo "  ✅ SMOKE TEST PASSED"
  exit 0
fi
