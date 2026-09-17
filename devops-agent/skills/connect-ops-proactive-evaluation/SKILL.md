---
name: connect-ops-proactive-evaluation
description: Proactive evaluation procedures for Amazon Connect instances. Use this
  skill during scheduled evaluations to assess instance health across security posture,
  resilience configuration, capacity utilization, observability coverage, cost efficiency,
  and AI agent configuration. Generates prioritized recommendations based on severity
  levels (Critical, Warning, Review, Information) aligned with the automated operational
  review Lambda function output.
---

# Amazon Connect Operational Review - Proactive Evaluation

Use this skill during scheduled evaluations or when proactively assessing the
operational health of an Amazon Connect instance. This skill guides systematic
evaluation across all 7 areas.

## Evaluation Workflow

### Phase 1: Discovery

Before evaluating, gather the following context:

1. **Instance Information**
   - Instance ID and ARN
   - Region and account
   - Instance alias
   - Identity type (SAML, Connect-managed)

2. **Infrastructure Context**
   - CloudFormation stacks associated with Connect
   - Kinesis streams (KVS, KDS)
   - S3 buckets for storage
   - KMS keys in use

3. **Operational Context**
   - Current contact volume (daily/weekly)
   - Peak hours and seasonal patterns
   - Number of agents and queues
   - Channels in use (voice, chat, tasks, email)

---

### Phase 2: Security Evaluation

Run these checks in order:

| # | Check | API/Method | Pass Criteria |
|---|-------|-----------|---------------|
| 1 | Identity Management | `connect:DescribeInstance` | IdentityManagementType = SAML |
| 2 | S3 Encryption | `connect:ListInstanceStorageConfigs` | All storage types have KMS (AWS-managed acceptable) |
| 3 | Streaming Encryption | `kinesis:DescribeStream` | SSE enabled on all streams |
| 4 | AI Domain Encryption | Wisdom APIs | CMK configured (not AWS-owned) |

**Scoring:**
- 4/4 Pass = Security: Healthy
- 3/4 Pass = Security: Needs Attention
- <3/4 Pass = Security: Action Required

---

### Phase 3: Resilience Evaluation

| # | Check | API/Method | Pass Criteria |
|---|-------|-----------|---------------|
| 1 | Global Resiliency | `connect:ListInstances` | Replica exists (Information only) |
| 2 | Carrier Diversity | `connect:ListPhoneNumbersV2` | Multiple number types or carriers |
| 3 | Knowledge Base Sync | Wisdom APIs | Last sync within configured interval |

**Note:** ACGR is informational only - absence is not a failure.

---

### Phase 4: Operational Excellence Evaluation

| # | Check | API/Method | Pass Criteria |
|---|-------|-----------|---------------|
| 1 | API Throttling | CloudTrail lookup | Zero ThrottlingException in last 7 days |
| 2 | Contact Flow Logging | `connect:SearchContactFlows` | All flows have logging block |
| 3 | KVS Retention | Storage config | Retention > 0 hours |

**Scoring:**
- API Throttling: Check last 7 days of CloudTrail events
- Contact Flow Logging: Calculate percentage of flows with logging
- KVS Retention: Binary check (0 = fail, >0 = pass)

---

### Phase 5: Capacity Evaluation

This is the most data-intensive phase. Evaluate each sub-area:

#### 5a. Instance Resource Limits

For each resource type, calculate: `(current_count / quota_limit) * 100`

| Resource | Quota API | Count API |
|----------|-----------|-----------|
| Routing Profiles | Service Quotas | `connect:ListRoutingProfiles` |
| Queues | Service Quotas | `connect:ListQueues` |
| Phone Numbers | Service Quotas | `connect:ListPhoneNumbersV2` |
| Users | Service Quotas | `connect:ListUsers` |
| Quick Connects | Service Quotas | `connect:ListQuickConnects` |
| Hours of Operation | Service Quotas | `connect:ListHoursOfOperations` |
| Prompts | Service Quotas | `connect:ListPrompts` |
| Security Profiles | Service Quotas | `connect:ListSecurityProfiles` |
| Agent Status | Service Quotas | `connect:ListAgentStatuses` |
| Contact Flows | Service Quotas | `connect:SearchContactFlows` |
| Contact Flow Modules | Service Quotas | `connect:SearchContactFlowModules` |

**Thresholds:**
- < 80%: Pass
- >= 80% and < 95%: Warning
- >= 95%: Fail
- Unable to measure: Review

#### 5b. Concurrency Limits

Check peak concurrent calls against quota using CloudWatch metrics.

#### 5c. API Rate Limits

Identify any non-default API quotas (indicates past throttling issues).

#### 5d. Cases Limits

Query Service Quotas for Connect Cases service and compare against current usage.

#### 5e. Customer Profiles Limits

Query Service Quotas for Customer Profiles and compare against current usage.

#### 5f. App Integrations Limits

Query Service Quotas for App Integrations and compare against current usage.

#### 5g. Campaigns Limits

Query Service Quotas for Campaigns (L-7F7B4C39) and compare against current usage.

---

### Phase 6: Observability Evaluation

| # | Check | Method | Pass Criteria |
|---|-------|--------|---------------|
| 1 | Connect Alarms | `cloudwatch:DescribeAlarms` | All recommended alarms exist with SNS actions |
| 2 | KDS Alarms | `cloudwatch:DescribeAlarms` | All KDS alarms exist (if streams configured) |
| 3 | Missed Calls | CloudWatch metrics | Daily average <= 5 |
| 4 | Flow Logging | Same as OpEx check | All flows have logging |

**Recommended Connect Alarms:**
- ConcurrentCallsPercentage (Critical)
- ThrottledCalls (Critical)
- MissedCalls (Warning)
- CallsPerInterval (Warning)
- ContactFlowErrors (Warning)
- CallRecordingUploadError (Warning)

**Recommended KDS Alarms:**
- IteratorAgeMilliseconds
- GetRecords.Success
- PutRecord.Success
- ReadProvisionedThroughputExceeded
- WriteProvisionedThroughputExceeded

---

### Phase 7: Cost Evaluation

| # | Check | Method | Observation |
|---|-------|--------|-------------|
| 1 | Phone Number Mix | List phone numbers | Balance DID vs Toll-Free |
| 2 | Channel Distribution | Contact metrics | Voice vs Chat vs Tasks vs Email |
| 3 | Voice Handle Time | Contact metrics | Average > 10 min = optimization opportunity |

**Channel Cost Comparison:**
- Voice: Per-minute billing, single contact per agent
- Chat: Per-message billing, multiple concurrent per agent
- Tasks: Per-task billing, automated follow-up
- Email: Per-email billing, AI-assisted drafting

---

### Phase 8: AI Agents Evaluation

| # | Check | Method | Observation |
|---|-------|--------|-------------|
| 1 | Agent Inventory | Wisdom APIs | Count agents, check tool count (<= 15) |
| 2 | Prompt Configuration | Wisdom APIs | Prompts exist for self-service + agent-assist |
| 3 | Guardrails | Wisdom APIs | Appropriate filters configured |
| 4 | Logging | CloudWatch config | Event logging enabled |
| 5 | Domain Encryption | Wisdom APIs | CMK configured |
| 6 | Knowledge Base | Wisdom APIs | At least one KB associated |

---

## Evaluation Report Template

After completing all phases, generate a summary:

```
## Amazon Connect Operational Review - Proactive Evaluation

**Instance:** [instance-alias]
**Date:** [evaluation-date]
**Region:** [region]

### Executive Summary

| Area | Checks | Pass | Warn | Fail | Review |
|------|--------|------|------|------|--------|
| Security | 4 | X | X | X | - |
| Resilience | 3 | X | X | X | - |
| Operational Excellence | 3 | X | X | X | - |
| Capacity Analysis | 7 | X | X | X | X |
| Observability | 4 | X | X | X | - |
| Cost | 9 | X | X | X | - |
| AI Agents | 6 | X | X | X | - |

### Priority Actions

1. [Critical findings - immediate action required]
2. [Warning findings - plan remediation]
3. [Review items - manual verification needed]
4. [Information items - awareness only]

### Detailed Findings

[Per-area breakdown with specific resources and recommendations]
```

---

## Evaluation Frequency

| Evaluation Type | Frequency | Scope |
|----------------|-----------|-------|
| Full evaluation | Monthly | All 7 areas, all 36 checks |
| Security focused | Weekly | Security + AI encryption |
| Capacity check | Weekly | All capacity sub-areas |
| Observability audit | Bi-weekly | Alarms + logging coverage |
| Cost review | Monthly | Phone numbers + channel mix |

---

## Integration with Automated Review

The proactive evaluation complements the automated Lambda function:

- **Lambda function** runs on schedule (EventBridge) and generates HTML report
- **DevOps Agent evaluation** provides deeper investigation and context
- **Together** they provide continuous operational health monitoring

When the Lambda report identifies issues, use the Investigation skill to dig deeper.
When proactive evaluation finds gaps, use the Remediation skill to fix them.
