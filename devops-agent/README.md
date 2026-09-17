# Amazon Connect Operational Review - DevOps Agent

A comprehensive agent skill set for the AWS DevOps Agent that provides investigation, remediation, and proactive evaluation capabilities based on the automated Amazon Connect Operational Review findings.

## Quick Start

### Option A: CloudFormation (Recommended)

```bash
aws cloudformation create-stack \
  --stack-name DevOpsAgent-ConnectOpsReview \
  --template-body file://CFT-DevOpsAgent-Setup.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=ConnectInstanceArn,ParameterValue=arn:aws:connect:us-east-1:123456789012:instance/your-id
```

### Option B: Manual Setup

See [AWS_DEVOPS_AGENT_SETUP_GUIDE.md](./AWS_DEVOPS_AGENT_SETUP_GUIDE.md) for the complete step-by-step guide.

## Repository Structure

```
amazonConnectOperationalreview-DevOpsAgent/
├── AWS_DEVOPS_AGENT_SETUP_GUIDE.md          # Step-by-step setup guide
├── CFT-DevOpsAgent-Setup.yml                # CloudFormation template for Agent Space
├── README.md                                 # This file
├── .kiro/
│   ├── skills/                              # Kiro IDE agent skills
│   │   ├── amazon-connect-ops-review.md     # Recommendations reference
│   │   └── ops-review-actions.md            # Remediation playbooks
│   └── steering/
│       └── devops-agent.md                  # Agent behavior configuration
└── skills/                                  # AWS DevOps Agent skills (upload-ready)
    ├── connect-ops-review-investigation/
    │   ├── SKILL.md                         # Investigation procedures
    │   └── references/
    │       └── checks-reference.md          # 36 checks quick reference
    ├── connect-ops-remediation-actions/
    │   ├── SKILL.md                         # Remediation playbooks with CLI/CFT
    │   └── references/
    │       └── cli-commands.md              # CLI quick reference
    └── connect-ops-proactive-evaluation/
        ├── SKILL.md                         # Proactive evaluation workflow
        └── references/
            └── evaluation-thresholds.md     # Scoring thresholds
```

## Skills Overview

### For AWS DevOps Agent (Upload to Agent Space)

| Skill | Agent Type | Purpose |
|-------|------------|---------|
| `connect-ops-review-investigation` | Generic | Investigate findings from operational review reports |
| `connect-ops-remediation-actions` | Generic | Execute remediation with CLI commands and CFT snippets |
| `connect-ops-proactive-evaluation` | Evaluation | Scheduled proactive health assessments |

### For Kiro IDE (Local development)

| Skill | Purpose |
|-------|---------|
| `amazon-connect-ops-review.md` | Complete reference of all 36 recommendations/information |
| `ops-review-actions.md` | Step-by-step remediation playbooks |

## Coverage

36 checks across 7 areas:

| Area | Checks | Types |
|------|--------|-------|
| Security | 4 | Recommendations |
| Resilience | 3 | 1 Information + 2 Recommendations |
| Operational Excellence | 3 | Recommendations |
| Capacity Analysis | 7 | Recommendations |
| Observability | 4 | Recommendations |
| Cost | 9 | Recommendations |
| AI Agents | 6 | 3 Information + 3 Recommendations |

## How to Upload Skills to AWS DevOps Agent

### Option 1: Upload as ZIP (Recommended - includes references)

1. ZIP each skill directory (e.g., `connect-ops-review-investigation/`)
2. Navigate to Skills page in your Agent Space Web App
3. Click "Add skill" → "Upload skill"
4. Upload the ZIP file
5. Select Agent Type and click "Upload"

### Option 2: Create via UI (SKILL.md content only)

1. Navigate to Skills page in your Agent Space Web App
2. Click "Add skill" → "Create skill"
3. Copy the name, description, and instructions from SKILL.md
4. Click "Create"

## Related Resources

- **Repository:** https://github.com/aws-samples/sample-amazon-connect-operational-review
  - Lambda source: `src/lambda/`
  - CloudFormation template: `deploy/cloudformation/`
  - Terraform module: `deploy/terraform/`

## Usage Examples

Once the skills are uploaded to your AWS DevOps Agent, ask questions like:

```
My operational review report shows 5 contact flows without logging. How do I fix them?
```

```
We're at 87% of our concurrent calls quota. What should I do?
```

```
Create CloudWatch alarms for our Connect instance based on best practices.
```

```
Evaluate the security posture of our Amazon Connect instance.
```

```
Our KVS retention is 0 hours. What are the risks and how do I fix it?
```

```
Help me request a quota increase for routing profiles.
```
