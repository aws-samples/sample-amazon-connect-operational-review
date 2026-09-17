# Amazon Connect Operational Review - Checks Reference

## Quick Reference Table

| # | Area | Check | Type | Severity |
|---|------|-------|------|----------|
| 1 | Security | Identity Management | Recommendation | Warning |
| 2 | Security | S3 Data Encryption | Recommendation | Warning |
| 3 | Security | Streaming Encryption | Recommendation | Warning |
| 4 | Security | AI Domain Encryption | Recommendation | Warning |
| 5 | Resilience | Global Resiliency (ACGR) | Information | Info |
| 6 | Resilience | Carrier Diversity | Recommendation | Warning |
| 7 | Resilience | Knowledge Base Sync | Recommendation | Review |
| 8 | Operational Excellence | API Throttling | Recommendation | Warning |
| 9 | Operational Excellence | Contact Flow Logging | Recommendation | Warning |
| 10 | Operational Excellence | KVS Retention Period | Recommendation | Warning |
| 11 | Capacity Analysis | Instance Resource Limits | Recommendation | Warning/Fail |
| 12 | Capacity Analysis | Concurrency Limits | Recommendation | Warning |
| 13 | Capacity Analysis | API Rate Limits | Recommendation | Info |
| 14 | Capacity Analysis | Cases Limits | Recommendation | Warning |
| 15 | Capacity Analysis | Customer Profiles Limits | Recommendation | Warning |
| 16 | Capacity Analysis | App Integrations Limits | Recommendation | Warning |
| 17 | Capacity Analysis | Campaigns Limits | Recommendation | Warning |
| 18 | Observability | CloudWatch Alarms - Connect | Recommendation | Critical |
| 19 | Observability | CloudWatch Alarms - KDS | Recommendation | Warning |
| 20 | Observability | Missed Calls | Recommendation | Warning |
| 21 | Observability | Contact Flow Logging | Recommendation | Warning |
| 22 | Cost | Phone Numbers - No Toll-Free | Recommendation | Info |
| 23 | Cost | Phone Numbers - DID Heavy | Recommendation | Info |
| 24 | Cost | Phone Numbers - TF Dominant | Recommendation | Info |
| 25 | Cost | Channel - Voice Heavy | Recommendation | Warning |
| 26 | Cost | Channel - No Chat | Recommendation | Info |
| 27 | Cost | Channel - No Tasks | Recommendation | Info |
| 28 | Cost | Channel - High Voice HT | Recommendation | Warning |
| 29 | Cost | Channel - Multi-Channel | Recommendation | Info |
| 30 | Cost | Channel - Email Active | Recommendation | Info |
| 31 | AI Agents | Connect AI Agent Inventory | Information | Info |
| 32 | AI Agents | Connect AI Prompt Configuration | Information | Info |
| 33 | AI Agents | Connect AI Guardrails | Information | Info |
| 34 | AI Agents | Connect AI Agent Logging | Recommendation | Warning |
| 35 | AI Agents | Connect AI Domain Encryption | Recommendation | Warning |
| 36 | AI Agents | Connect AI Knowledge Base | Recommendation | Warning |

## Capacity Analysis Thresholds

| Threshold | Status | Action |
|-----------|--------|--------|
| < 80% | Pass | No action needed |
| >= 80% and < 95% | Warning | Plan quota increase |
| >= 95% | Fail | Immediate quota increase required |
| No measurable data | Review | Manual verification needed |

## Instance Resource Quotas Monitored

| Resource | Quota Code | Default |
|----------|-----------|---------|
| Routing Profiles | L-4B location | 500 |
| Queues | L-QUEUE | 500 |
| Phone Numbers | L-PHONE | 500 |
| Users | L-USER | 500 |
| Quick Connects | L-QC | 500 |
| Hours of Operation | L-HOP | 100 |
| Prompts | L-PROMPT | 500 |
| Security Profiles | L-SP | 100 |
| Agent Status | L-AS | 50 |
| Contact Flows | L-CF | 500 |
| Contact Flow Modules | L-CFM | 500 |

## CloudWatch Alarms - Recommended Set

### Connect Instance Alarms (Critical)
- ConcurrentCallsPercentage >= 0.8
- ThrottledCalls > 0

### Connect Instance Alarms (Warning)
- MissedCalls > 5 (daily)
- CallsPerInterval anomaly detection
- ContactFlowErrors > 0
- CallRecordingUploadError > 0

### Kinesis Data Stream Alarms
- IteratorAgeMilliseconds > 60000
- GetRecords.Success < 1
- PutRecord.Success < 1
- ReadProvisionedThroughputExceeded > 0
- WriteProvisionedThroughputExceeded > 0

## Environment Variable Toggles

The Lambda function supports enabling/disabling areas via environment variables:

| Variable | Default | Controls |
|----------|---------|----------|
| ENABLE_SECURITY_CHECKS | true | Security area |
| ENABLE_RESILIENCE_CHECKS | true | Resilience area |
| ENABLE_OPERATIONAL_EXCELLENCE | true | Operational Excellence area |
| ENABLE_CAPACITY_ANALYSIS | true | Capacity Analysis area |
| ENABLE_OBSERVABILITY_CHECKS | true | Observability area |
| ENABLE_COST_CHECKS | true | Cost area |
| ENABLE_AI_AGENTS | true | AI Agents area |
