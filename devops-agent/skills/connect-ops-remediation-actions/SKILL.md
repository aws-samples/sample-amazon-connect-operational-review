---
name: connect-ops-remediation-actions
description: Remediation playbooks for Amazon Connect operational review findings.
  Use this skill when users need to fix security issues (SAML, KMS, encryption),
  improve resilience (carrier diversity, ACGR), resolve operational problems (logging,
  retention, throttling), manage capacity (quota increases), set up observability
  (alarms, monitoring), or optimize costs (channel mix, phone numbers). Includes
  CLI commands and CloudFormation snippets for each remediation action.
---

# Amazon Connect Operational Review - Remediation Actions

Use this skill when users need to take action on operational review findings.
Provide specific CLI commands, CloudFormation snippets, and step-by-step procedures.

## Security Remediation

### Fix: Enable SAML 2.0 Federation

**When:** Identity Management check shows non-SAML authentication

**Steps:**
1. Configure your IdP (Okta, Azure AD, or IAM Identity Center) with Amazon Connect SAML metadata
2. Create a SAML application in your IdP with the Connect ACS URL
3. Update Connect instance identity management settings
4. Migrate users from Connect-managed to SAML-federated
5. Test SSO login flow before disabling direct authentication

**Reference:** https://docs.aws.amazon.com/connect/latest/adminguide/configure-saml.html

**Important:** This is a one-way migration. Once converted to SAML, you cannot revert to Connect-managed identity.

---

### Fix: Enable S3 KMS Encryption (Customer-Managed Key)

**When:** S3 storage configs missing CMK encryption

**CLI - Create KMS Key:**
```bash
aws kms create-key \
  --description "Amazon Connect Storage Encryption Key" \
  --key-usage ENCRYPT_DECRYPT \
  --origin AWS_KMS \
  --tags TagKey=Service,TagValue=AmazonConnect

# Enable automatic key rotation
aws kms enable-key-rotation --key-id <key-id>
```

**CLI - Update Connect Storage Config:**
```bash
aws connect update-instance-storage-config \
  --instance-id <instance-id> \
  --association-id <association-id> \
  --resource-type CALL_RECORDINGS \
  --storage-config '{
    "StorageType": "S3",
    "S3Config": {
      "BucketName": "<bucket-name>",
      "BucketPrefix": "connect/recordings",
      "EncryptionConfig": {
        "EncryptionType": "KMS",
        "KeyId": "arn:aws:kms:<region>:<account>:key/<key-id>"
      }
    }
  }'
```

**CloudFormation Snippet:**
```yaml
ConnectStorageKMSKey:
  Type: AWS::KMS::Key
  Properties:
    Description: Amazon Connect Storage Encryption Key
    EnableKeyRotation: true
    KeyPolicy:
      Version: '2012-10-17'
      Statement:
        - Sid: AllowKeyAdministration
          Effect: Allow
          Principal:
            AWS: !Sub 'arn:aws:iam::${AWS::AccountId}:root'
          Action: 'kms:*'
          Resource: '*'
        - Sid: AllowConnectAccess
          Effect: Allow
          Principal:
            Service: connect.amazonaws.com
          Action:
            - kms:GenerateDataKey
            - kms:Decrypt
          Resource: '*'
```

---

### Fix: Enable KDS Server-Side Encryption

**When:** Kinesis Data Streams without encryption

**CLI:**
```bash
aws kinesis start-stream-encryption \
  --stream-name <stream-name> \
  --encryption-type KMS \
  --key-id alias/aws/kinesis
```

**For customer-managed key:**
```bash
aws kinesis start-stream-encryption \
  --stream-name <stream-name> \
  --encryption-type KMS \
  --key-id arn:aws:kms:<region>:<account>:key/<key-id>
```

---

### Fix: Enable AI Domain Encryption (CMK)

**When:** AI domain using AWS-owned key

**Steps:**
1. Create or identify a KMS key for AI domain encryption
2. Update the Wisdom domain encryption configuration
3. Verify key policy allows Wisdom service access

**Note:** Changing domain encryption may require domain recreation depending on current state.

---

## Resilience Remediation

### Fix: Improve Carrier Diversity

**When:** Phone number inventory shows single-carrier risk

**Steps:**
1. Review current phone number inventory:
```bash
aws connect list-phone-numbers-v2 \
  --target-arn arn:aws:connect:<region>:<account>:instance/<instance-id>
```
2. Request additional toll-free numbers (auto-distributed across carriers in US):
```bash
aws connect search-available-phone-numbers \
  --target-arn arn:aws:connect:<region>:<account>:instance/<instance-id> \
  --phone-number-country-code US \
  --phone-number-type TOLL_FREE
```
3. Claim a toll-free number:
```bash
aws connect claim-phone-number \
  --target-arn arn:aws:connect:<region>:<account>:instance/<instance-id> \
  --phone-number <phone-number> \
  --phone-number-description "Carrier diversity - TF"
```

**Best Practice:** Maintain at least 2 toll-free numbers for US operations to ensure multi-carrier distribution.

---

## Operational Excellence Remediation

### Fix: Add Contact Flow Logging

**When:** Contact flows missing "Set logging behavior" block

**Steps:**
1. Open the contact flow in the Connect designer
2. Add a "Set logging behavior" block at the start of the flow
3. Set LoggingBehavior to "Enabled"
4. For flows with sensitive data (PCI), add a second block to disable logging before the sensitive section
5. Save and publish the flow

**CLI - Verify logging is enabled:**
```bash
aws connect describe-contact-flow \
  --instance-id <instance-id> \
  --contact-flow-id <flow-id> \
  --query 'ContactFlow.Content'
```

**Note:** Contact flow logs go to CloudWatch Logs group: `/aws/connect/<instance-name>`

---

### Fix: Set KVS Retention Period

**When:** KVS retention is 0 hours

**Steps:**
1. Navigate to Connect console → Data storage → Media streams
2. Set retention period based on requirements:
   - Minimum: Retention must exceed maximum consumer processing delay
   - Compliance: Set based on organizational and regulatory requirements
   - Cost consideration: Retention proportionally increases storage cost

**Consumers to consider:**
- Amazon Transcribe (real-time transcription)
- Contact Lens (post-call analytics)
- Custom consumers (third-party integrations)

---

### Fix: Resolve API Throttling

**When:** CloudTrail shows throttled Connect API calls

**Steps:**
1. Identify throttled APIs:
```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=connect.amazonaws.com \
  --start-time $(date -d '7 days ago' --iso-8601) \
  --query 'Events[?contains(CloudTrailEvent, `ThrottlingException`)].{Time:EventTime,Event:EventName}'
```

2. Implement exponential backoff in integration code:
```python
import time
import random

def call_with_backoff(func, max_retries=5):
    for attempt in range(max_retries):
        try:
            return func()
        except ClientError as e:
            if e.response['Error']['Code'] == 'TooManyRequestsException':
                wait = (2 ** attempt) * 0.25 + random.uniform(0, 0.1)
                time.sleep(wait)
            else:
                raise
    raise Exception("Max retries exceeded")
```

3. Request quota increase for frequently throttled APIs:
```bash
aws service-quotas request-service-quota-increase \
  --service-code connect \
  --quota-code <quota-code> \
  --desired-value <new-value>
```

**Reference:** https://docs.aws.amazon.com/connect/latest/APIReference/best-practices-connect-apis.html

---

## Capacity Remediation

### Fix: Request Quota Increase

**When:** Any resource at >=80% utilization

**CLI - Check current quota:**
```bash
aws service-quotas get-service-quota \
  --service-code connect \
  --quota-code <quota-code>
```

**CLI - Request increase:**
```bash
aws service-quotas request-service-quota-increase \
  --service-code connect \
  --quota-code <quota-code> \
  --desired-value <new-value>
```

**CLI - Check request status:**
```bash
aws service-quotas list-requested-service-quota-change-history \
  --service-code connect
```

**Best Practice:** Request increases proactively when utilization reaches 80%. Include business justification with expected growth timeline.

### Common Quota Codes

| Resource | Service | Quota Code |
|----------|---------|-----------|
| Concurrent active calls | connect | L-CONCURRENT |
| Routing profiles per instance | connect | L-RP |
| Queues per instance | connect | L-QUEUE |
| Phone numbers per instance | connect | L-PHONE |
| Users per instance | connect | L-USER |
| Cases domains | cases | L-C2B81BC3 |
| Fields per domain | cases | L-C5B69356 |
| Templates per domain | cases | L-0482161A |
| Data integrations per region | app-integrations | L-013E1287 |
| Event integrations per region | app-integrations | L-152D3E9E |
| Object types per domain | profile | L-14092FF4 |
| Integrations | profile | L-4A5ECB8E |
| Campaigns | connect-campaigns | L-7F7B4C39 |

---

## Observability Remediation

### Fix: Create Missing CloudWatch Alarms

**When:** Recommended alarms are not configured

**CLI - Create ConcurrentCallsPercentage alarm (Critical):**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Connect-ConcurrentCallsPercentage-High" \
  --namespace "AWS/Connect" \
  --metric-name "ConcurrentCallsPercentage" \
  --dimensions Name=InstanceId,Value=<instance-id> Name=MetricGroup,Value=VoiceCalls \
  --statistic Maximum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 0.8 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions <sns-topic-arn> \
  --treat-missing-data notBreaching
```

**CLI - Create ThrottledCalls alarm (Critical):**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Connect-ThrottledCalls" \
  --namespace "AWS/Connect" \
  --metric-name "ThrottledCalls" \
  --dimensions Name=InstanceId,Value=<instance-id> Name=MetricGroup,Value=CallsPerInterval \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions <sns-topic-arn> \
  --treat-missing-data notBreaching
```

**CLI - Create MissedCalls alarm (Warning):**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Connect-MissedCalls-High" \
  --namespace "AWS/Connect" \
  --metric-name "MissedCalls" \
  --dimensions Name=InstanceId,Value=<instance-id> Name=MetricGroup,Value=CallsPerInterval \
  --statistic Sum \
  --period 86400 \
  --evaluation-periods 1 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions <sns-topic-arn> \
  --treat-missing-data notBreaching
```

**CloudFormation - Complete Alarm Set:**
```yaml
Parameters:
  ConnectInstanceId:
    Type: String
  SNSTopicArn:
    Type: String

Resources:
  ConcurrentCallsAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: Connect-ConcurrentCallsPercentage-High
      Namespace: AWS/Connect
      MetricName: ConcurrentCallsPercentage
      Dimensions:
        - Name: InstanceId
          Value: !Ref ConnectInstanceId
        - Name: MetricGroup
          Value: VoiceCalls
      Statistic: Maximum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 0.8
      ComparisonOperator: GreaterThanOrEqualToThreshold
      AlarmActions:
        - !Ref SNSTopicArn
      TreatMissingData: notBreaching

  ThrottledCallsAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: Connect-ThrottledCalls
      Namespace: AWS/Connect
      MetricName: ThrottledCalls
      Dimensions:
        - Name: InstanceId
          Value: !Ref ConnectInstanceId
        - Name: MetricGroup
          Value: CallsPerInterval
      Statistic: Sum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 0
      ComparisonOperator: GreaterThanThreshold
      AlarmActions:
        - !Ref SNSTopicArn
      TreatMissingData: notBreaching

  MissedCallsAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: Connect-MissedCalls-High
      Namespace: AWS/Connect
      MetricName: MissedCalls
      Dimensions:
        - Name: InstanceId
          Value: !Ref ConnectInstanceId
        - Name: MetricGroup
          Value: CallsPerInterval
      Statistic: Sum
      Period: 86400
      EvaluationPeriods: 1
      Threshold: 5
      ComparisonOperator: GreaterThanThreshold
      AlarmActions:
        - !Ref SNSTopicArn
      TreatMissingData: notBreaching
```

---

### Fix: Create KDS Alarms

**When:** Kinesis Data Stream alarms missing

**CLI - IteratorAge alarm:**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "KDS-Connect-IteratorAge-High" \
  --namespace "AWS/Kinesis" \
  --metric-name "GetRecords.IteratorAgeMilliseconds" \
  --dimensions Name=StreamName,Value=<stream-name> \
  --statistic Maximum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 60000 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions <sns-topic-arn>
```

---

### Fix: Address Missed Calls

**When:** Daily average missed calls > 5

**Steps:**
1. Analyze missed call patterns:
```bash
aws cloudwatch get-metric-statistics \
  --namespace "AWS/Connect" \
  --metric-name "MissedCalls" \
  --dimensions Name=InstanceId,Value=<instance-id> Name=MetricGroup,Value=CallsPerInterval \
  --start-time $(date -d '7 days ago' --iso-8601) \
  --end-time $(date --iso-8601) \
  --period 3600 \
  --statistics Sum
```

2. Review agent staffing during peak missed-call hours
3. Implement queued callbacks:
   - Reference: https://docs.aws.amazon.com/connect/latest/adminguide/setup-queued-cb.html
4. Adjust agent answer timeout if agents need more time
5. Review routing profiles and queue priorities for efficient distribution

---

## Cost Remediation

### Fix: Optimize Channel Mix

**When:** Voice-heavy contact distribution (>80% voice)

**Steps:**
1. Identify routine inquiries suitable for chat:
   - FAQ responses
   - Status checks
   - Simple requests (password resets, account lookups)

2. Enable Amazon Connect Chat:
   - Configure chat in the Connect console
   - Set up routing profiles for concurrent chats (3-5 per agent)
   - Deploy chat widget on website/app

3. Enable Tasks for automation:
   - Create task templates for follow-up actions
   - Configure task routing to appropriate queues
   - Integrate with CRM for automated task creation

4. Cost comparison:
   - Voice: billed per minute
   - Chat: billed per message
   - Tasks: billed per task
   - Agents can handle multiple concurrent chats vs single voice call

**Reference:** https://aws.amazon.com/connect/pricing/

---

### Fix: Optimize Phone Number Inventory

**When:** Phone number mix analysis shows optimization opportunity

**Steps:**
1. Audit current numbers:
```bash
aws connect list-phone-numbers-v2 \
  --target-arn arn:aws:connect:<region>:<account>:instance/<instance-id> \
  --query 'ListPhoneNumbersSummaryList[].{Number:PhoneNumber,Type:PhoneNumberType,Country:PhoneNumberCountryCode}'
```

2. Identify unused numbers (no traffic in 30+ days)
3. Release unused numbers to reduce monthly costs
4. Consider toll-free for:
   - International access
   - Multi-carrier resilience (US)
   - Customer perception (free to call)

---

## AI Agents Remediation

### Fix: Enable AI Agent Logging

**When:** No event logging configured for AI agents

**Steps:**
1. Enable CloudWatch Vended Logs for AI agent events
2. Configure log group with appropriate retention
3. Set up log-based metrics for monitoring

**Benefits:**
- Audit AI agent interactions
- Troubleshoot conversation failures
- Monitor guardrail activations
- Track knowledge base retrieval quality

---

### Fix: Associate Knowledge Base

**When:** AI agents have no knowledge base

**Steps:**
1. Create a knowledge base in the Connect console
2. Configure data source (S3, web crawler, etc.)
3. Set sync interval (1 hour, 3 hours, or daily)
4. Associate the knowledge base with AI agents
5. Test retrieval quality with sample queries

**Reference:** https://docs.aws.amazon.com/connect/latest/adminguide/amazon-q-connect.html
