# Amazon Connect Automated Operational Review

Automated Amazon Connect operational health report. This solution generates a self-contained HTML findings report across seven pillars — Security, Resilience, Operational Excellence, Capacity, Observability, Cost, and AI — using a parallel AWS Step Functions orchestration of independent analyzer Lambda functions.

Amazon Connect contact centers grow in complexity over time: phone numbers accumulate, encryption settings drift, quotas approach limits, and logging gaps appear silently. This solution automates the operational health assessment that would otherwise take days of manual API calls and console checks. After a single deployment, you get a repeatable HTML report delivered to an S3 bucket and viewable in any browser.

> **Audience:** This is the entry-point README for both developers and deployers. Developers: continue here and see [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`docs/`](docs/). Deployers: skip to [`deploy/cloudformation/README.md`](deploy/cloudformation/README.md) or [`deploy/terraform/README.md`](deploy/terraform/README.md).

## What You Get

A self-contained HTML report (~200 KB) with:

- Executive summary dashboard showing pass / fail / warn counts per pillar at a glance
- Detailed findings per check with specific recommendations
- Color-coded status badges (green = pass, red = fail, amber = warn, blue = review, grey = error)
- No external dependencies — open directly in any browser, or share via email or an S3 pre-signed URL

## Architecture

The solution deploys a Step Functions state machine that runs eight analyzer Lambda functions in parallel, then aggregates their output into a single HTML report uploaded to S3.

```text
                          ┌─────────────────┐
                          │ prepare_context │
                          └────────┬────────┘
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ▼              ▼           ▼           ▼              ▼
   security      resilience   cloudtrail     opex      capacity / observability
   analyzer       analyzer     analyzer    analyzer     / cost / ai analyzers
        └──────────────┴───────────┼───────────┴──────────────┘
                                   ▼
                          ┌─────────────────┐
                          │ report_generator│  ──►  HTML report in S3
                          └─────────────────┘
```

See [`docs/architecture.md`](docs/architecture.md) for the full parallel execution model, data flow, and design decisions.

## Repository Structure

```text
amazon-connect-operational-review/
├── LICENSE                             ← MIT-0
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── .github/workflows/                  ← GitHub Actions CI (lint / test)
├── src/
│   └── lambda/                         ← Python source (SINGLE SOURCE OF TRUTH)
│       ├── prepare_context.py
│       ├── security_analyzer.py
│       ├── resilience_analyzer.py
│       ├── cloudtrail_analyzer.py
│       ├── opex_analyzer.py
│       ├── capacity_analyzer.py
│       ├── observability_analyzer.py
│       ├── cost_analyzer.py
│       ├── ai_analyzer.py
│       ├── report_generator.py
│       ├── analyzer_common.py          ← Shared utilities
│       ├── graceful_timeout.py         ← Time budget pattern
│       └── tests/
├── deploy/
│   ├── cloudformation/
│   │   ├── CFT-AmazonConnectOperationsReview.yml
│   │   └── scripts/                    ← Sync scripts (update_cft_lambda.py, etc.)
│   └── terraform/
│       ├── main.tf
│       ├── parallel.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── lambda_packages/            ← Synced .py copies from src/lambda/
│       └── state-machine-definition.asl.json
├── devops-agent/                       ← Optional DevOps Agent skills (YAML)
└── docs/
    ├── architecture.md
    └── developer-guide.md
```

> **Source of truth:** All Lambda code changes are made in `src/lambda/`. Changes propagate to both `deploy/cloudformation/` and `deploy/terraform/` via the sync scripts. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the sync workflow.

## Deployment Options

Both options deploy identical runtime behavior — a parallel Step Functions workflow orchestrating eight analyzer Lambdas.

| Option | Directory | Deploy Command |
| ------ | --------- | -------------- |
| **CloudFormation** | `deploy/cloudformation/` | `aws cloudformation deploy --template-file CFT-AmazonConnectOperationsReview.yml --s3-bucket YOUR-STAGING-BUCKET ...` |
| **Terraform** | `deploy/terraform/` | `terraform init && terraform apply` |

Per-method parameter references:

- **CloudFormation:** [Parameters](deploy/cloudformation/README.md#parameters) · [Parameter Dependencies](deploy/cloudformation/README.md#parameter-dependencies)
- **Terraform:** [Parameters](deploy/terraform/README.md#parameters) · [Parameter Dependencies](deploy/terraform/README.md#parameter-dependencies)

For the canonical cross-parameter rules shared by both deployment methods, see [`docs/parameter-dependencies.md`](docs/parameter-dependencies.md).

> **CloudFormation CLI note:** The template embeds Lambda code inline and exceeds CloudFormation's 51,200-byte inline `--template-body` limit. `aws cloudformation deploy` handles this transparently when you pass `--s3-bucket` (it auto-stages the template to that bucket). If you use `create-stack` directly, upload the template to S3 first and reference it via `--template-url`. See [`deploy/cloudformation/README.md`](deploy/cloudformation/README.md) for full CLI details.

## Getting Started (Development)

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-operational-review.git
cd sample-amazon-connect-operational-review

# Lambda development
cd src/lambda
pip install -r requirements.txt
pytest tests/
ruff check .
```

See [`docs/developer-guide.md`](docs/developer-guide.md) for the full local development and sync workflow.

## Prerequisites

- An AWS account with an existing Amazon Connect instance
- Permissions to deploy the stack (IAM roles, Lambda, Step Functions, S3, EventBridge Scheduler, and optionally Glue)
- AWS CLI configured, and Terraform installed if using the Terraform path
- Python 3.12 for local development

## Security

This solution provisions IAM roles scoped to least-privilege for the analyzer Lambdas and reads operational metadata from your Amazon Connect instance. Review the IAM policies in the deployment templates before deploying to any account. The generated report may contain configuration details about your Connect instance — treat the S3 report bucket and any shared report files accordingly.

See [CONTRIBUTING.md](CONTRIBUTING.md#security-issue-notifications) for how to report a potential security issue. Do not open a public GitHub issue for security findings.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch workflow, sync requirements, and pull request process. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

---

> This content is provided as an example and starting point for development. Before deploying to production, you are responsible for conducting appropriate testing and validation, implementing safeguards for your use case, and ensuring compliance with your organization's requirements. See [`deploy/cloudformation/NOTICE`](deploy/cloudformation/NOTICE) for the full notice.
