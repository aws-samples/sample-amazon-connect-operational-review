---
name: connect-ops-review-investigation
description: Investigation procedures for Amazon Connect operational health findings
  from the automated operational review Lambda function. Use this skill when analyzing
  operational review reports, investigating security gaps, capacity warnings, observability
  gaps, resilience configuration, cost optimization opportunities, or AI agent configuration
  issues across 7 areas covering 36 checks.
---

# Amazon Connect Operational Review - Investigation Skill

Use this skill when investigating findings from the Amazon Connect Operational Review
report or when users ask about the health of their Amazon Connect instance.

## Overview

The Amazon Connect Operational Review is an automated Lambda function that generates
an HTML report covering 7 areas with 36 checks. This skill provides investigation
procedures for each finding.

## Areas and Checks

### Security (4 checks)

#### Identity Management
- **Trigger:** Instance identity type is not SAML
- **Severity:** Warning
- **Investigation Steps:**
  1. Check the Connect instance identity type via `connect:DescribeInstance`
  2. Determine current authentication method (Connect-managed, SAML, or IAM)
  3. Assess user count and federation readiness
  4. Review existing IdP infrastructure (Okta, Azure AD, IAM Identity Center)
- **Key Question:** Is the organization using centralized identity management elsewhere?

#### S3 Data Encryption
- **Trigger:** S3 storage configurations missing customer-managed KMS keys
- **Severity:** Warning (CMK recommended), Pass (AWS-managed keys acceptable)
- **Investigation Steps:**
  1. List storage configs via `connect:ListInstanceStorageConfigs`
  2. Check each storage type: Call Recordings, Chat Transcripts, Exported Reports
  3. Verify KMS key type (AWS-managed vs customer-managed)
  4. Review key policies and rotation status
- **Key Question:** Does the organization require CMK for compliance (HIPAA, PCI)?

#### Streaming Encryption
- **Trigger:** KVS, KDS, or Firehose streams without encryption
- **Severity:** Warning
- **Investigation Steps:**
  1. Check KVS configuration in Connect storage settings
  2. Describe KDS streams for SSE status
  3. Check Firehose delivery stream encryption
  4. Verify KMS key associations
- **Key Question:** Is media streaming enabled and are streams encrypted at rest?

#### AI Domain Encryption
- **Trigger:** Connect AI domain using AWS-owned key instead of CMK
- **Severity:** Warning
- **Investigation Steps:**
  1. Check AI domain configuration via Wisdom APIs
  2. Verify encryption key type
  3. Assess compliance requirements for AI data
- **Key Question:** Does the AI domain handle sensitive customer data requiring CMK?

---

### Resilience (3 checks)

#### Global Resiliency (ACGR)
- **Trigger:** No replica instance configured
- **Type:** Information (not a recommendation)
- **Investigation Steps:**
  1. Check for replica instances via `connect:ListInstances`
  2. Verify if ACGR is available in the instance's region
  3. Assess business continuity requirements
- **Key Question:** Does the business require geographic telephony redundancy?

#### Carrier Diversity
- **Trigger:** Phone number inventory analysis
- **Severity:** Warning (single carrier risk)
- **Investigation Steps:**
  1. List phone numbers via `connect:ListPhoneNumbersV2`
  2. Categorize by type (DID, Toll-Free)
  3. Assess geographic distribution
  4. Check if toll-free numbers provide multi-carrier distribution
- **Key Question:** Would a single carrier outage impact all inbound calls?

#### Knowledge Base Sync
- **Trigger:** Knowledge base content freshness
- **Severity:** Review
- **Investigation Steps:**
  1. List knowledge bases via Wisdom APIs
  2. Check last sync timestamp
  3. Verify sync interval configuration (1h, 3h, daily)
  4. Review source data freshness
- **Key Question:** Is the knowledge base content current with source material?

---

### Operational Excellence (3 checks)

#### API Throttling
- **Trigger:** CloudTrail shows throttled Connect API calls
- **Severity:** Warning
- **Investigation Steps:**
  1. Query CloudTrail for `ThrottlingException` events on Connect APIs
  2. Identify which APIs are being throttled
  3. Check call patterns (burst vs sustained)
  4. Review integration code for retry logic
- **Key Question:** Which integrations are causing throttling and do they implement backoff?

#### Contact Flow Logging
- **Trigger:** Contact flows missing "Set logging behavior" block
- **Severity:** Warning
- **Investigation Steps:**
  1. List all contact flows via `connect:SearchContactFlows`
  2. Describe each flow to check for logging blocks
  3. Identify flows handling sensitive data (may intentionally skip logging)
  4. Count affected flows vs total
- **Key Question:** Are there legitimate reasons some flows skip logging (PCI compliance)?

#### KVS Retention Period
- **Trigger:** Kinesis Video Streams retention set to 0 hours
- **Severity:** Warning
- **Investigation Steps:**
  1. Check KVS storage configuration
  2. Verify retention period setting
  3. Identify consumers (Contact Lens, Amazon Transcribe, custom consumers)
  4. Assess maximum consumer processing delay
- **Key Question:** What is the maximum time a consumer needs to process the stream?

---

### Capacity Analysis (7 checks)

#### Instance Resource Limits
- **Trigger:** Any resource at or above 80% utilization
- **Severity:** Warning (>=80%), Fail (>=95%)
- **Investigation Steps:**
  1. Query Service Quotas for Connect instance limits
  2. Compare current resource counts against quota values
  3. Check quotas: Routing Profiles, Queues, Phone Numbers, Users, Quick Connects, etc.
  4. Identify growth trends
- **Key Question:** Which resources are approaching limits and what is the growth rate?

#### Concurrency Limits
- **Trigger:** Peak concurrent calls/chats approaching quota
- **Severity:** Warning (>=80%)
- **Investigation Steps:**
  1. Get current concurrent calls quota
  2. Query CloudWatch for peak ConcurrentCallsPercentage
  3. Analyze time-of-day patterns
  4. Project future capacity needs
- **Key Question:** When will current capacity be insufficient based on growth trends?

#### API Rate Limits
- **Trigger:** Modified (non-default) API rate quotas detected
- **Severity:** Information
- **Investigation Steps:**
  1. List Connect API quotas via Service Quotas
  2. Identify quotas that differ from defaults
  3. Cross-reference with throttling events
- **Key Question:** Were these quotas increased due to past throttling issues?

#### Cases Limits
- **Trigger:** Cases domain resource utilization >=80%
- **Severity:** Warning
- **Investigation Steps:**
  1. Query Service Quotas for Connect Cases
  2. Check: Domains, Fields, Templates, Layouts, Rules, Related Items
  3. Compare current counts against quota values
- **Key Question:** Which Cases resources are growing fastest?

#### Customer Profiles Limits
- **Trigger:** Profiles domain resource utilization >=80%
- **Severity:** Warning
- **Investigation Steps:**
  1. Query Service Quotas for Customer Profiles
  2. Check: Object types, Keys, Integrations, Recommenders, Event Triggers
  3. Compare current counts against quota values
- **Key Question:** Are profile object counts growing with customer base?

#### App Integrations Limits
- **Trigger:** Integration resource utilization >=80%
- **Severity:** Warning
- **Investigation Steps:**
  1. Query Service Quotas for App Integrations
  2. Check: Data integrations, Event integrations, Applications, Associations
  3. Compare current counts against quota values
- **Key Question:** Are new integrations being added without quota planning?

#### Campaigns Limits
- **Trigger:** Outbound campaigns quota utilization
- **Severity:** Warning
- **Investigation Steps:**
  1. Query Service Quotas for Campaigns (L-7F7B4C39)
  2. Check current campaign count
  3. Assess seasonal campaign patterns
- **Key Question:** Do campaign volumes spike seasonally?

---

### Observability (4 checks)

#### CloudWatch Alarms - Connect
- **Trigger:** Missing recommended CloudWatch alarms for Connect metrics
- **Severity:** Critical (missing critical alarms), Warning (missing standard alarms)
- **Investigation Steps:**
  1. List existing CloudWatch alarms for the Connect instance
  2. Compare against recommended alarm set
  3. Check alarm actions (SNS topics configured?)
  4. Verify alarm thresholds are appropriate
- **Recommended Alarms:**
  - ConcurrentCallsPercentage (Critical)
  - ThrottledCalls (Critical)
  - MissedCalls (Warning)
  - CallsPerInterval (Warning)
  - ContactFlowErrors (Warning)

#### CloudWatch Alarms - KDS
- **Trigger:** Missing Kinesis Data Stream alarms
- **Severity:** Warning
- **Investigation Steps:**
  1. Identify KDS streams used by Connect
  2. Check for existing alarms on those streams
  3. Verify coverage of key metrics
- **Recommended Alarms:**
  - IteratorAgeMilliseconds
  - GetRecords.Success
  - PutRecord.Success
  - ReadProvisionedThroughputExceeded
  - WriteProvisionedThroughputExceeded

#### Missed Calls
- **Trigger:** Daily average missed calls > 5
- **Severity:** Warning
- **Investigation Steps:**
  1. Query CloudWatch for MissedCalls metric
  2. Analyze time-of-day distribution
  3. Correlate with agent availability
  4. Check queue configurations and timeouts
- **Key Question:** Are missed calls concentrated during specific hours or queues?

#### Contact Flow Logging (Observability)
- **Trigger:** Flows without logging (same check, observability perspective)
- **Severity:** Warning
- **Investigation Steps:**
  1. Same as Operational Excellence check
  2. Focus on troubleshooting and incident response impact
  3. Assess log group configuration and retention
- **Key Question:** Can you troubleshoot contact issues without flow logs?

---

### Cost (9 checks)

#### Phone Number Analysis
- **Triggers:**
  - No toll-free numbers (missed resilience + international access)
  - DID-heavy inventory (potential cost optimization)
  - Toll-free dominant (verify cost vs resilience tradeoff)
- **Investigation Steps:**
  1. List all phone numbers by type
  2. Calculate monthly cost by number type
  3. Assess usage patterns per number
  4. Identify unused or low-traffic numbers
- **Key Question:** Is the phone number mix optimized for cost and resilience?

#### Channel Mix Analysis
- **Triggers:**
  - Voice > 80% of contacts (deflection opportunity)
  - No chat enabled (missing cost-efficient channel)
  - No tasks enabled (missing automation opportunity)
  - High voice handle time > 10 min (complexity indicator)
  - Multi-channel active (optimization opportunity)
  - Email active (AI draft opportunity)
- **Investigation Steps:**
  1. Query contact metrics by channel
  2. Calculate cost per contact by channel
  3. Identify routine inquiries suitable for chat/task deflection
  4. Assess agent utilization across channels
- **Key Question:** What percentage of voice contacts could be handled via chat or tasks?

---

### AI Agents (6 checks)

#### Connect AI Agent Inventory
- **Type:** Information
- **Investigation Steps:**
  1. List AI agents via Wisdom APIs
  2. Check tool count per agent (flag if >15 MCP tools)
  3. Review agent configurations
- **Key Question:** Are agents scoped appropriately with minimal required tools?

#### Connect AI Prompt Configuration
- **Type:** Information
- **Investigation Steps:**
  1. List AI prompts
  2. Review prompt purposes (self-service vs agent-assist)
  3. Check prompt versioning
- **Key Question:** Are prompts configured for both self-service and agent-assist?

#### Connect AI Guardrails
- **Type:** Information
- **Investigation Steps:**
  1. List guardrail configurations
  2. Check filter types: Content, PII, Denied Topics, Word Filter, Contextual Grounding
  3. Verify max 3 guardrails per assistant
- **Key Question:** Are appropriate guardrails in place for the use case?

#### Connect AI Agent Logging
- **Trigger:** No event logging configured
- **Severity:** Warning
- **Investigation Steps:**
  1. Check CloudWatch Vended Logs configuration for AI agents
  2. Verify log group exists and is receiving events
  3. Check retention period
- **Key Question:** Can you audit AI agent interactions and troubleshoot issues?

#### Connect AI Domain Encryption
- **Trigger:** AWS-owned key instead of CMK
- **Severity:** Warning
- **Investigation Steps:**
  1. Check domain encryption configuration
  2. Verify key type and rotation
- **Key Question:** Does compliance require customer-managed encryption for AI data?

#### Connect AI Knowledge Base
- **Trigger:** No knowledge base associated with AI agents
- **Severity:** Warning
- **Investigation Steps:**
  1. List knowledge bases
  2. Check associations with AI agents
  3. Verify content freshness and sync status
- **Key Question:** Do AI agents have access to current organizational knowledge?

---

## Priority Framework

When multiple findings exist, prioritize investigation in this order:

1. **Critical** - Security gaps, capacity at >95%, missing critical alarms
2. **Warning** - Capacity at >80%, missing encryption, no logging
3. **Review** - Capacity items without measurable data
4. **Information** - Configuration awareness items (ACGR, AI inventory)

## Report Integration

The operational review Lambda function generates an HTML report with:
- Executive summary with pass/warn/fail counts per area
- Area execution duration tracking
- Detailed findings with specific resource identifiers
- Recommendations from a centralized `get_recommendation()` function

When users share report findings, map them to the appropriate investigation procedure above.
