# Evaluation Thresholds Reference

## Capacity Utilization Thresholds

| Utilization | Status | Color | Action |
|-------------|--------|-------|--------|
| 0% - 79% | Pass | Green | No action needed |
| 80% - 94% | Warning | Yellow | Plan quota increase within 30 days |
| 95% - 100% | Fail | Red | Immediate quota increase required |
| N/A | Review | Blue | Manual verification needed |

## Security Scoring

| Check | Pass | Warn | Fail |
|-------|------|------|------|
| Identity Management | SAML configured | - | Non-SAML |
| S3 Encryption | CMK or AWS-managed KMS | - | No encryption |
| Streaming Encryption | SSE enabled | - | No encryption |
| AI Domain Encryption | CMK configured | AWS-owned key | - |

## Observability Scoring

| Alarm Category | Critical | Standard |
|---------------|----------|----------|
| ConcurrentCallsPercentage | Yes | - |
| ThrottledCalls | Yes | - |
| MissedCalls | - | Yes |
| CallsPerInterval | - | Yes |
| ContactFlowErrors | - | Yes |
| CallRecordingUploadError | - | Yes |

## Cost Optimization Indicators

| Indicator | Threshold | Opportunity |
|-----------|-----------|-------------|
| Voice percentage | > 80% | Chat/Task deflection |
| No chat channel | 0 chat contacts | Enable chat |
| No tasks channel | 0 task contacts | Enable tasks |
| Voice handle time | > 10 minutes avg | Step-by-step guides, AI agents |
| Unused phone numbers | 0 contacts in 30 days | Release numbers |

## AI Agent Health Indicators

| Indicator | Healthy | Needs Attention |
|-----------|---------|-----------------|
| Tools per agent | <= 15 | > 15 (latency risk) |
| Guardrails | Configured | None configured |
| Logging | Enabled | Disabled |
| Knowledge base | Associated | None associated |
| Domain encryption | CMK | AWS-owned key |

## Evaluation Cadence

| Area | Recommended Frequency | Trigger Events |
|------|----------------------|----------------|
| Security | Weekly | IAM changes, new integrations |
| Resilience | Monthly | New phone numbers, region expansion |
| Operational Excellence | Weekly | Deployment events, traffic spikes |
| Capacity | Weekly | Growth milestones, seasonal peaks |
| Observability | Bi-weekly | New resources, alarm modifications |
| Cost | Monthly | Billing cycle, new channels |
| AI Agents | Monthly | New agent deployments, prompt changes |
