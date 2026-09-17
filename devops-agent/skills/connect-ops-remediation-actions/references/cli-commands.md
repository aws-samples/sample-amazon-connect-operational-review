# CLI Commands Quick Reference

## Service Quotas Commands

```bash
# List all Connect quotas
aws service-quotas list-service-quotas --service-code connect

# Get specific quota
aws service-quotas get-service-quota --service-code connect --quota-code <code>

# Request increase
aws service-quotas request-service-quota-increase \
  --service-code connect --quota-code <code> --desired-value <value>

# Check request history
aws service-quotas list-requested-service-quota-change-history --service-code connect
```

## Connect Instance Commands

```bash
# Describe instance
aws connect describe-instance --instance-id <id>

# List storage configs
aws connect list-instance-storage-configs --instance-id <id> --resource-type CALL_RECORDINGS

# List phone numbers
aws connect list-phone-numbers-v2 --target-arn <instance-arn>

# List contact flows
aws connect search-contact-flows --instance-id <id> --search-criteria '{}'

# Describe contact flow
aws connect describe-contact-flow --instance-id <id> --contact-flow-id <flow-id>
```

## CloudWatch Commands

```bash
# List Connect alarms
aws cloudwatch describe-alarms --alarm-name-prefix "Connect-"

# Get metric data
aws cloudwatch get-metric-data --metric-data-queries file://query.json \
  --start-time <start> --end-time <end>

# Create alarm
aws cloudwatch put-metric-alarm --cli-input-json file://alarm.json
```

## CloudTrail Commands

```bash
# Check for throttling events
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=connect.amazonaws.com \
  --start-time <start-time>
```

## KMS Commands

```bash
# Create key
aws kms create-key --description "Connect Encryption" --key-usage ENCRYPT_DECRYPT

# Enable rotation
aws kms enable-key-rotation --key-id <key-id>

# Describe key
aws kms describe-key --key-id <key-id>
```

## Kinesis Commands

```bash
# Describe stream
aws kinesis describe-stream --stream-name <name>

# Enable encryption
aws kinesis start-stream-encryption --stream-name <name> --encryption-type KMS --key-id <key>
```
