# Architecture — Connect Ops Review

> **Audience:** For developers and reviewers of the architecture. Deployers should read the deployment READMEs first.

## System Overview

### Monorepo CI/CD Flow

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    GitLab Internal Monorepo                         │
│                                                                     │
│  src/lambda/        deploy/cloudformation/      deploy/terraform/   │
│  (source of truth)  (CFT + embedded code)       (TF + .py copies)   │
└───────┬─────────────────────┬───────────────────────────┬───────────┘
        │                     │                           │
        │  CI: sync stage     │                           │
        │  validates parity   │                           │
        ▼                     ▼                           ▼
   ┌─────────┐       ┌────────────────┐          ┌────────────────┐
   │ Lambda  │       │  CFT Stack     │          │  TF Module     │
   │  .zip   │       │  Package       │          │  Package       │
   └────┬────┘       └──────┬─────────┘          └──────┬─────────┘
        │                   │                           │
        │                   ▼                           ▼
        │           ┌────────────────┐          ┌────────────────┐
        │           │  AWS Account   │          │  AWS Account   │
        │           │  (CFT deploy)  │          │  (TF deploy)   │
        │           └────────────────┘          └────────────────┘
        │                   │                           │
        └───────────────────┴─────────┬─────────────────┘
                                      ▼
                           Identical Runtime Behavior
```

### Single Source of Truth Sync Flow

```text
src/lambda/*.py  ─────────────────────────────────────────────────────┐
       │                                                              │
       │  cp *.py deploy/terraform/lambda_packages/                   │
       │                                                              │
       ├──► deploy/terraform/lambda_packages/*.py                     │
       │                                                              │
       │  scripts/update_cft_lambda.py (per analyzer)                 │
       │  scripts/sync_shared_utils_to_cft.py                         │
       │                                                              │
       └──► deploy/cloudformation/CFT-AmazonConnectOperationsReview.yml
                    (embedded in ZipFile blocks)
```

## Step Functions Parallel Architecture

```text
EventBridge Scheduler
        │
        │  rate(7 days) or cron expression
        ▼
┌────────────────────────────────────────────────────────────────────┐
│  Step Functions State Machine — ConnectOpsReview-Orchestrator      │
│                                                                    │
│  ┌──────────────────┐                                              │
│  │  PrepareContext  │  Read SSM config, resolve Connect instance   │
│  └────────┬─────────┘                                              │
│           │                                                        │
│           ▼  ExecutionContext passed to all analyzers              │
│  ┌────────────────────────────────────────────────────────┐        │
│  │              Parallel Fan-Out (Map state)              │        │
│  │                                                        │        │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐   │        │
│  │  │  Security    │  │  Resilience  │  │  CloudTrail │   │        │
│  │  └──────────────┘  └──────────────┘  └─────────────┘   │        │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐   │        │
│  │  │    OpEx      │  │   Capacity   │  │Observability│   │        │
│  │  └──────────────┘  └──────────────┘  └─────────────┘   │        │
│  │  ┌──────────────┐  ┌──────────────┐                    │        │
│  │  │     Cost     │  │      AI      │                    │        │
│  │  └──────────────┘  └──────────────┘                    │        │
│  └────────────────────────────────────────────────────────┘        │
│           │                                                        │
│           ▼  Each analyzer writes JSON to S3                       │
│  ┌──────────────────┐                                              │
│  │  ReportGenerator │  Reads all JSON, renders HTML, writes to S3  │
│  └──────────────────┘                                              │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
   S3 Bucket
   ├── data/{componentType}/year=YYYY/month=MM/day=DD/{reviewId}.json
   └── reports/connect-review-{timestamp}.html
```

## Executive Summary Status Model

Each analyzer produces a pillar-level status rolled up into the report's executive summary:

| Status | Color | Meaning |
| ------ | ----- | ------- |
| **pass** | 🟢 Green | All checks passed — no action needed |
| **fail** | 🔴 Red | Critical findings requiring immediate attention |
| **warn** | 🟡 Amber | Non-critical findings that should be reviewed |
| **info** | 🔵 Blue | Informational — no issue, but worth noting |
| **error** | ⚪ Grey | Analyzer could not run — check CloudWatch logs |

The executive summary at the top of the HTML report displays one colored badge per pillar, giving instant visibility into overall health.

## Graceful Timeout Pattern

Each analyzer Lambda is configured with a timeout (e.g., 240s for most, 840s for CloudTrail). The graceful timeout pattern ensures partial results are never lost:

```text
┌─────────────────────────────────────────────────────┐
│ Lambda invocation starts                            │
│                                                     │
│  time_budget = lambda_timeout - 60s (safety margin) │
│                                                     │
│  for each check in analyzer:                        │
│    if remaining_time < threshold:                   │
│      break  ← stop gracefully                       │
│    run check, collect findings                      │
│                                                     │
│  persist results to S3 (even if partial)            │
│  return AnalyzerResult with partial=true if needed  │
└─────────────────────────────────────────────────────┘
```

If a Lambda times out unexpectedly (no graceful exit), the Step Functions error handler catches it and the report shows `error` status for that pillar.

## Hive-Style S3 Partitioning

Analyzer results are stored in a partition scheme compatible with AWS Glue/Athena:

```text
s3://<bucket>/data/<componentType>/year=2025/month=01/day=15/<reviewId>.json
```

This enables:
- Time-range queries in Athena without full scans
- Per-analyzer filtering via `componentType` partition
- Optional Glue Catalog integration (enabled via `EnableGlueCatalog` parameter)

## Per-Analyzer Toggle (SSM)

Each analyzer can be enabled/disabled at runtime via the SSM parameter `/connect-ops-review/config`:

```json
{
  "analyzers": {
    "security": true,
    "resilience": true,
    "cloudtrail": false,
    "operational_excellence": true,
    "capacity": true,
    "observability": true,
    "cost": true,
    "ai": true
  }
}
```

Disabled analyzers are skipped during PrepareContext — they don't appear in the parallel fan-out and don't appear in the report.

## Standardized AnalyzerResult Schema

Every analyzer returns a consistent structure:

```json
{
  "componentType": "security",
  "status": "warn",
  "findings": [
    {
      "checkName": "S3 Data Encryption",
      "status": "pass",
      "detail": "Encryption enabled with AWS managed key",
      "recommendation": null
    }
  ],
  "durationMs": 4523,
  "s3ResultKey": "data/security/year=2025/month=01/day=15/abc-123.json",
  "partial": false
}
```

## Design Decisions

| Decision | Rationale |
| -------- | --------- |
| Monorepo over multi-repo | Small team, tightly coupled components, single versioning, simpler CI/CD |
| Dual deploy options (CFT + TF) | Customer choice — some orgs mandate one or the other |
| Step Functions parallel orchestration | Independent analyzers run concurrently, reducing total execution time from ~15min to ~3min |
| Per-analyzer Lambda functions | Independent scaling, isolated failures, least-privilege IAM per function |
| Shared IAM role (consolidated) | Simplified management — all analyzers need similar base permissions |
| Graceful timeout over hard timeout | Partial results are more valuable than no results |
| S3 as inter-step data store | Decouples analyzers from report generator, enables replay/debugging |
| Hive partitioning | Free Athena queryability without ETL |
| SSM for runtime config | Change behavior without redeployment or code change |
| GitLab Package Registry for releases | Internal distribution without S3 bucket management |
| Path-based CI/CD rules | Only run jobs for changed components — fast feedback |
| Sync stage in CI | Catches drift between source and deploy directories before merge |
| CODEOWNERS | Lightweight governance without blocking velocity |
