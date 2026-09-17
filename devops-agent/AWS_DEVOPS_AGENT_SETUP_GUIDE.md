# AWS DevOps Agent Setup Guide - Amazon Connect Operational Review

This guide walks you through creating an AWS DevOps Agent with skills tailored to the Amazon Connect Operational Review findings. The agent will be able to investigate, remediate, and proactively recommend improvements based on the 36 checks across 7 areas.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Option A: Deploy via CloudFormation (Recommended)](#option-a-deploy-via-cloudformation-recommended)
3. [Option B: Manual Setup](#option-b-manual-setup)
   - [Step 1: Create an Agent Space](#step-1-create-an-agent-space)
   - [Step 2: Configure Primary Account Access](#step-2-configure-primary-account-access)
   - [Step 3: Enable the Web App](#step-3-enable-the-web-app)
   - [Step 4: Connect Secondary Accounts (Optional)](#step-4-connect-secondary-accounts-optional)
4. [Upload Operational Review Skills](#step-5-upload-operational-review-skills)
5. [Verify Topology Discovery](#step-6-verify-topology-discovery)
6. [Test the Agent](#step-7-test-the-agent)
7. [Skill Reference](#skill-reference)
8. [Architecture Overview](#architecture-overview)

---

## Prerequisites

Before starting, ensure you have:

- An AWS account with the Amazon Connect Operational Review CloudFormation stack deployed
  - Stack name: `AmazonConnectOperationalReview`
  - Repository: https://github.com/aws-samples/sample-amazon-connect-operational-review (see `deploy/cloudformation/`)
- IAM permissions to create roles and access the AWS DevOps Agent console
- An Amazon Connect instance in a supported region
- AWS DevOps Agent available in one of the supported regions:
  - US East (N. Virginia) - `us-east-1`
  - US West (Oregon) - `us-west-2`
  - Asia Pacific (Sydney) - `ap-southeast-2`
  - Asia Pacific (Tokyo) - `ap-northeast-1`
  - Europe (Frankfurt) - `eu-central-1`
  - Europe (Ireland) - `eu-west-1`

> **Note:** AWS DevOps Agent can monitor resources in any region regardless of where the Agent Space is created. Choose a region based on data residency or proximity to your operations team.

---

## Option A: Deploy via CloudFormation (Recommended)

The fastest way to set up the DevOps Agent is using the provided CloudFormation template.

### Deploy the Stack

```bash
aws cloudformation create-stack \
  --stack-name DevOpsAgent-ConnectOpsReview \
  --template-body file://CFT-DevOpsAgent-Setup.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=AgentSpaceName,ParameterValue=amazon-connect-ops-review \
    ParameterKey=ConnectInstanceArn,ParameterValue=arn:aws:connect:us-east-1:123456789012:instance/your-instance-id \
    ParameterKey=EnableKMSEncryption,ParameterValue=false
```

### What the Template Creates

| Resource | Type | Purpose |
|----------|------|---------|
| `DevOpsAgentSpace` | AWS::DevOpsAgent::AgentSpace | The Agent Space container |
| `DevOpsAgentResourceRole` | AWS::IAM::Role | Agent access to Connect, CloudWatch, Service Quotas, KMS, S3, Lambda |
| `DevOpsAgentOperatorRole` | AWS::IAM::Role | Web App access for operators |
| `PrimaryAccountAssociation` | AWS::DevOpsAgent::Association | Links the primary AWS account for resource discovery |
| `DevOpsAgentKMSKey` | AWS::KMS::Key | (Optional) Customer-managed encryption key |

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `AgentSpaceName` | amazon-connect-ops-review | Name for the Agent Space |
| `AgentSpaceDescription` | (pre-filled) | Description of the Agent Space |
| `AgentLocale` | en-US | Language for agent responses |
| `ConnectInstanceArn` | (required) | ARN of your Amazon Connect instance |
| `EnableKMSEncryption` | false | Enable CMK for Agent Space data |
| `EnableGitHubIntegration` | false | Flag for GitHub (requires manual OAuth post-deploy) |

### After Deployment

1. Wait for stack to reach `CREATE_COMPLETE`
2. Note the outputs: `AgentSpaceId`, `OperatorRoleArn`
3. Open the DevOps Agent console → click **Operator access** to verify the Web App
4. Proceed to [Upload Operational Review Skills](#step-5-upload-operational-review-skills)

---

## Option B: Manual Setup

If you prefer to set up the Agent Space manually through the console, follow these steps.

---

## Step 1: Create an Agent Space

An Agent Space is the logical container that defines what the DevOps Agent can access and investigate.

1. Sign in to the **AWS Management Console**
2. Navigate to the **AWS DevOps Agent** console
3. Click **Create Agent Space**
4. Fill in the Agent Space details:

| Field | Value |
|-------|-------|
| Name | `amazon-connect-ops-review` |
| Description | Agent Space for Amazon Connect operational health monitoring, incident investigation, and proactive recommendations based on automated operational review findings. |
| Agent response language | English (UK) or your preferred language |

5. Click **Next** to proceed to account access configuration

---

## Step 2: Configure Primary Account Access

The primary account is where your Amazon Connect instance and the Operational Review Lambda function are deployed.

### Option A: Auto-create a new role (Recommended)

1. Select **Auto-create a new AWS DevOps Agent role**
2. Accept the default role name or customize it (e.g., `DevOpsAgent-ConnectOpsReview-Role`)
3. The auto-created role includes permissions for resource discovery and investigation

### Option B: Use a custom role with Connect-specific permissions

If you need tighter access control, create a role with this policy template that aligns with the Operational Review Lambda permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ConnectReadAccess",
      "Effect": "Allow",
      "Action": [
        "connect:Describe*",
        "connect:List*",
        "connect:GetCurrentMetricData",
        "connect:GetMetricDataV2",
        "connect:SearchContactFlowModules",
        "connect:SearchContactFlows"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ConnectCasesProfilesAppInt",
      "Effect": "Allow",
      "Action": [
        "cases:List*",
        "cases:GetDomain",
        "profile:List*",
        "profile:GetDomain",
        "app-integrations:List*"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ObservabilityAccess",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:DescribeAlarms",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "cloudtrail:LookupEvents",
        "logs:DescribeLogGroups",
        "logs:GetLogEvents",
        "logs:StartQuery",
        "logs:GetQueryResults"
      ],
      "Resource": "*"
    },
    {
      "Sid": "InfrastructureDiscovery",
      "Effect": "Allow",
      "Action": [
        "cloudformation:DescribeStacks",
        "cloudformation:ListStackResources",
        "servicequotas:GetServiceQuota",
        "servicequotas:ListServiceQuotas",
        "kinesis:DescribeStream",
        "kinesis:ListStreams",
        "kms:DescribeKey",
        "kms:ListKeys",
        "s3:GetBucketEncryption",
        "lambda:GetFunction",
        "lambda:ListFunctions"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AIAgentsAccess",
      "Effect": "Allow",
      "Action": [
        "wisdom:List*",
        "wisdom:Get*"
      ],
      "Resource": "*"
    }
  ]
}
```

### Trust Policy

If creating a custom role, use this trust policy (replace `AGENT_SPACE_ID` after creation):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "aidevops.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "YOUR_ACCOUNT_ID"
        }
      }
    }
  ]
}
```

---

## Step 3: Enable the Web App

The Web App is where your operations team interacts with the DevOps Agent.

1. In the **Enable Agent Space Web App** section, select **Auto-create a new AWS DevOps Agent role**
2. This creates an operator role for web app access
3. Click **Create** to finalize the Agent Space

After creation, the **Operator access** button appears on the Agent Space details page. Click it to open the Web App.

---

## Step 4: Connect Secondary Accounts (Optional)

If your Amazon Connect deployment spans multiple accounts (e.g., separate accounts for production, staging, or shared services):

1. Navigate to your Agent Space settings
2. Click **Add secondary account**
3. Provide the AWS account ID and configure cross-account IAM role access
4. The agent will discover resources across all connected accounts

This is useful when:
- Your Connect instance is in one account but CloudWatch/KDS streams are in another
- You have separate accounts for telephony vs. contact center operations
- Your CFT stack deploys resources across accounts

---

## Step 5: Upload Operational Review Skills

This is the key step where you add the Amazon Connect Operational Review knowledge to the DevOps Agent. We provide **3 skills** that cover all 36 checks.

### Skill 1: Amazon Connect Operational Review (Investigation)

**Purpose:** Teaches the agent how to investigate findings from the operational review report.

1. Navigate to the **Skills** page in your Agent Space Web App
2. Click **Add skill** → **Upload skill**
3. Upload the zip file: `skills/connect-ops-review-investigation.zip`
4. Set Agent Type to: **Generic** (available to all agent types)
5. Click **Upload**

Alternatively, create via UI:

| Field | Value |
|-------|-------|
| Name | `connect-ops-review-investigation` |
| Description | Investigation procedures for Amazon Connect operational health findings. Use this skill when analyzing operational review reports, investigating security gaps, capacity warnings, observability gaps, or AI agent configuration issues across 7 areas: Security, Resilience, Operational Excellence, Capacity Analysis, Observability, Cost, and AI Agents. |
| Status | Active |
| Agent Type | Generic |

### Skill 2: Amazon Connect Remediation Actions

**Purpose:** Provides step-by-step remediation playbooks for each finding.

1. Click **Add skill** → **Upload skill**
2. Upload: `skills/connect-ops-remediation-actions.zip`
3. Set Agent Type to: **Generic**
4. Click **Upload**

Alternatively, create via UI:

| Field | Value |
|-------|-------|
| Name | `connect-ops-remediation-actions` |
| Description | Remediation playbooks for Amazon Connect operational review findings. Use this skill when users need to fix security issues (SAML, KMS, encryption), improve resilience (carrier diversity, ACGR), resolve operational problems (logging, retention, throttling), manage capacity (quota increases), set up observability (alarms, monitoring), or optimize costs (channel mix, phone numbers). Includes CLI commands and CloudFormation snippets. |
| Status | Active |
| Agent Type | Generic |

### Skill 3: Amazon Connect Proactive Evaluation

**Purpose:** Enables the agent to proactively evaluate Connect instances and make preventative recommendations.

1. Click **Add skill** → **Upload skill**
2. Upload: `skills/connect-ops-proactive-evaluation.zip`
3. Set Agent Type to: **Evaluation**
4. Click **Upload**

Alternatively, create via UI:

| Field | Value |
|-------|-------|
| Name | `connect-ops-proactive-evaluation` |
| Description | Proactive evaluation procedures for Amazon Connect instances. Use this skill during scheduled evaluations to assess instance health across security posture, resilience configuration, capacity utilization, observability coverage, cost efficiency, and AI agent configuration. Generates prioritized recommendations based on severity (Critical, Warning, Review, Information). |
| Status | Active |
| Agent Type | Evaluation |

---

## Step 6: Verify Topology Discovery

After creating the Agent Space and connecting your account, the DevOps Agent automatically discovers your infrastructure.

1. Navigate to the **Topology** page in the Web App
2. Wait for initial discovery to complete (may take several minutes)
3. Verify that the following resources are discovered:
   - Amazon Connect instance
   - Lambda function (`amazonConnectOperationalReview-auto`)
   - CloudWatch Log Groups (Connect contact flow logs)
   - Kinesis Data Streams (if configured)
   - S3 buckets (call recordings, chat transcripts)
   - KMS keys (encryption configuration)
   - CloudFormation stack (`AmazonConnectOperationalReview`)

4. The **Agent Space Understanding** learned skill will be auto-generated from the topology

### Verify with Chat

Open the Chat interface and ask:

```
Show me all resources related to my Amazon Connect instance
```

The agent should list your Connect instance, associated Lambda functions, streams, and storage resources.

---

## Step 7: Test the Agent

Test the agent with queries that exercise the operational review skills:

### Investigation Queries

```
My operational review report shows 5 contact flows without logging enabled. 
Which flows are affected and how do I fix them?
```

```
We're at 87% of our concurrent calls quota. What should I do?
```

```
The report shows our KVS retention is set to 0 hours. What are the risks?
```

### Remediation Queries

```
Help me enable SAML 2.0 federation for our Connect instance
```

```
Create CloudWatch alarms for our Connect instance based on the operational review recommendations
```

```
Generate a quota increase request for routing profiles - we're at 80% utilization
```

### Proactive Evaluation Queries

```
Evaluate the security posture of our Amazon Connect instance
```

```
What capacity risks should we address before our peak season?
```

```
Review our AI agent configuration for best practices
```

---

## Skill Reference

### Complete Check Mapping (36 Checks → 7 Areas)

| Area | Check | Type | Skill Coverage |
|------|-------|------|----------------|
| **Security** | Identity Management | Recommendation | Investigation + Remediation |
| **Security** | S3 Data Encryption | Recommendation | Investigation + Remediation |
| **Security** | Streaming Encryption | Recommendation | Investigation + Remediation |
| **Security** | AI Domain Encryption | Recommendation | Investigation + Remediation |
| **Resilience** | Global Resiliency (ACGR) | Information | Investigation |
| **Resilience** | Carrier Diversity | Recommendation | Investigation + Remediation |
| **Resilience** | Knowledge Base Sync | Recommendation | Investigation + Remediation |
| **Operational Excellence** | API Throttling | Recommendation | Investigation + Remediation |
| **Operational Excellence** | Contact Flow Logging | Recommendation | Investigation + Remediation |
| **Operational Excellence** | KVS Retention Period | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | Instance Resource Limits | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | Concurrency Limits | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | API Rate Limits | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | Cases Limits | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | Customer Profiles Limits | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | App Integrations Limits | Recommendation | Investigation + Remediation |
| **Capacity Analysis** | Campaigns Limits | Recommendation | Investigation + Remediation |
| **Observability** | CloudWatch Alarms - Connect | Recommendation | Investigation + Remediation |
| **Observability** | CloudWatch Alarms - KDS | Recommendation | Investigation + Remediation |
| **Observability** | Missed Calls | Recommendation | Investigation + Remediation |
| **Observability** | Contact Flow Logging | Recommendation | Investigation + Remediation |
| **Cost** | Phone Numbers - No Toll-Free | Recommendation | Investigation + Remediation |
| **Cost** | Phone Numbers - DID Heavy | Recommendation | Investigation + Remediation |
| **Cost** | Phone Numbers - TF Dominant | Recommendation | Investigation + Remediation |
| **Cost** | Channel - Voice Heavy | Recommendation | Investigation + Remediation |
| **Cost** | Channel - No Chat | Recommendation | Investigation + Remediation |
| **Cost** | Channel - No Tasks | Recommendation | Investigation + Remediation |
| **Cost** | Channel - High Voice HT | Recommendation | Investigation + Remediation |
| **Cost** | Channel - Multi-Channel | Recommendation | Investigation + Remediation |
| **Cost** | Channel - Email Active | Recommendation | Investigation + Remediation |
| **AI Agents** | Connect AI Agent Inventory | Information | Investigation |
| **AI Agents** | Connect AI Prompt Configuration | Information | Investigation |
| **AI Agents** | Connect AI Guardrails | Information | Investigation |
| **AI Agents** | Connect AI Agent Logging | Recommendation | Investigation + Remediation |
| **AI Agents** | Connect AI Domain Encryption | Recommendation | Investigation + Remediation |
| **AI Agents** | Connect AI Knowledge Base | Recommendation | Investigation + Remediation |

### Skill → Agent Type Mapping

| Skill | Agent Types | Purpose |
|-------|-------------|---------|
| `connect-ops-review-investigation` | Generic (all) | Investigate findings from operational review reports |
| `connect-ops-remediation-actions` | Generic (all) | Execute remediation steps with CLI/CFT |
| `connect-ops-proactive-evaluation` | Evaluation | Scheduled proactive health assessments |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     AWS DevOps Agent Space                        │
│                  "amazon-connect-ops-review"                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │   Investigation  │  │   Remediation   │  │    Proactive    │ │
│  │      Skill       │  │     Skill       │  │   Evaluation    │ │
│  │                  │  │                 │  │     Skill       │ │
│  │  36 checks       │  │  CLI commands   │  │  Scheduled      │ │
│  │  7 areas         │  │  CFT snippets   │  │  assessments    │ │
│  │  Decision trees  │  │  Playbooks      │  │  Prioritized    │ │
│  └────────┬─────────┘  └────────┬────────┘  └────────┬────────┘ │
│           │                      │                     │          │
│  ┌────────┴──────────────────────┴─────────────────────┴────────┐│
│  │              Learned Skills (Auto-generated)                   ││
│  │  • Agent Space Understanding (topology map)                   ││
│  │  • Tool Use Best Practices (investigation patterns)           ││
│  └───────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌───────────────────────────────────────────────────────────────┐│
│  │                    Topology Discovery                          ││
│  │  • Amazon Connect Instance                                    ││
│  │  • Lambda (amazonConnectOperationalReview-auto)               ││
│  │  • CloudWatch Alarms & Logs                                   ││
│  │  • Kinesis Data Streams                                       ││
│  │  • S3 Buckets (recordings, transcripts)                       ││
│  │  • KMS Keys                                                   ││
│  │  • CloudFormation Stack                                       ││
│  └───────────────────────────────────────────────────────────────┘│
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│  Primary Account: [Your AWS Account with Connect Instance]        │
│  Secondary Accounts: [Optional - shared services, telephony]      │
└─────────────────────────────────────────────────────────────────┘
```

### Integration with Operational Review Lambda

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  EventBridge     │────▶│  Lambda Function  │────▶│  HTML Report     │
│  (Scheduled/     │     │  (Operational     │     │  (S3 or inline)  │
│   Manual)        │     │   Review)         │     │                  │
└──────────────────┘     └────────┬──────────┘     └────────┬─────────┘
                                  │                          │
                                  ▼                          ▼
                         ┌──────────────────┐     ┌──────────────────┐
                         │  CloudWatch Logs  │     │  DevOps Agent    │
                         │  (Execution logs) │────▶│  (Investigates   │
                         └──────────────────┘     │   & Remediates)  │
                                                  └──────────────────┘
```

---

## Appendix: Packaging Skills as ZIP Files

If you prefer to upload skills as ZIP files (supports reference materials and assets), use the following structure:

### Skill 1 ZIP Structure

```
connect-ops-review-investigation/
├── SKILL.md
└── references/
    └── checks-reference.md
```

### Skill 2 ZIP Structure

```
connect-ops-remediation-actions/
├── SKILL.md
└── references/
    ├── cli-commands.md
    └── cft-snippets.md
```

### Skill 3 ZIP Structure

```
connect-ops-proactive-evaluation/
├── SKILL.md
└── references/
    └── evaluation-thresholds.md
```

See the `skills/` directory in this repository for ready-to-upload ZIP files.

---

## References

- [AWS DevOps Agent User Guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/)
- [Creating an Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html)
- [DevOps Agent Skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html)
- [DevOps Agent Topology](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-what-is-a-devops-agent-topology.html)
- [Learned Skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-learned-skills.html)
- [Amazon Connect Operational Review](https://github.com/aws-samples/sample-amazon-connect-operational-review) (Lambda source in `src/lambda/`, CFT in `deploy/cloudformation/`)
