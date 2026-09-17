# Observability Analyzer Lambda Function
#
# Standalone Lambda that performs observability analysis for Amazon Connect
# instances, checking CloudWatch alarms, CloudWatch metrics (missed calls),
# and CloudWatch Logs delivery configuration.
# Extracted from the monolithic lambda_function.py as part of the
# parallel orchestration architecture.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import logging
import time
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError

from analyzer_common import validate_input, success_result, error_result, persist_to_s3
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 540

# ── Recommended Alarms for Amazon Connect ──
# These define the best-practice CloudWatch alarms that should exist for
# any production Amazon Connect instance.
RECOMMENDED_CONNECT_ALARMS = [
    {
        "metric_name": "ConcurrentCallsPercentage",
        "namespace": "AWS/Connect",
        "statistic": "Maximum",
        "comparison": "GreaterThanOrEqualToThreshold",
        "threshold": 0.8,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when concurrent voice calls reach 80% of quota",
        "severity": "CRITICAL",
        "metric_group": "VoiceCalls",
    },
    {
        "metric_name": "ConcurrentActiveChatsPercentage",
        "namespace": "AWS/Connect",
        "statistic": "Maximum",
        "comparison": "GreaterThanOrEqualToThreshold",
        "threshold": 0.8,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when concurrent active chats reach 80% of quota",
        "severity": "CRITICAL",
        "metric_group": "Chats",
    },
    {
        "metric_name": "ConcurrentTasksPercentage",
        "namespace": "AWS/Connect",
        "statistic": "Maximum",
        "comparison": "GreaterThanOrEqualToThreshold",
        "threshold": 0.8,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when concurrent tasks reach 80% of quota",
        "severity": "HIGH",
        "metric_group": "Tasks",
    },
    {
        "metric_name": "CallsBreachingConcurrencyQuota",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when any voice calls breach the concurrency quota",
        "severity": "CRITICAL",
        "metric_group": "VoiceCalls",
    },
    {
        "metric_name": "ChatsBreachingActiveChatQuota",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when any chats breach the active chat quota",
        "severity": "CRITICAL",
        "metric_group": "Chats",
    },
    {
        "metric_name": "TasksBreachingConcurrencyQuota",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when any tasks breach the concurrency quota",
        "severity": "HIGH",
        "metric_group": "Tasks",
    },
    {
        "metric_name": "ThrottledCalls",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when voice calls are being throttled",
        "severity": "CRITICAL",
        "metric_group": "VoiceCalls",
    },
    {
        "metric_name": "MisconfiguredPhoneNumbers",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when calls fail due to misconfigured phone numbers",
        "severity": "HIGH",
        "metric_group": "VoiceCalls",
    },
    {
        "metric_name": "CallRecordingUploadError",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when call recordings fail to upload to S3",
        "severity": "HIGH",
        "metric_group": "CallRecordings",
    },
    {
        "metric_name": "ContactFlowErrors",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when contact flow error branches are triggered",
        "severity": "MEDIUM",
        "metric_group": "ContactFlow",
    },
    {
        "metric_name": "ContactFlowFatalErrors",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when contact flows fail due to system errors",
        "severity": "CRITICAL",
        "metric_group": "ContactFlow",
    },
    {
        "metric_name": "MissedCalls",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 10,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when missed calls exceed threshold",
        "severity": "MEDIUM",
        "metric_group": "VoiceCalls",
    },
    {
        "metric_name": "ToInstancePacketLossRate",
        "namespace": "AWS/Connect",
        "statistic": "Average",
        "comparison": "GreaterThanThreshold",
        "threshold": 0.05,
        "period": 300,
        "evaluation_periods": 3,
        "description": "Alarm when packet loss rate exceeds 5% sustained over 15 min",
        "severity": "HIGH",
        "metric_group": "VoiceCalls",
    },
    {
        "metric_name": "QueueCapacityExceededError",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when calls are rejected because a queue is full",
        "severity": "HIGH",
        "metric_group": "Queue",
    },
    {
        "metric_name": "CallBackNotDialableNumber",
        "namespace": "AWS/Connect",
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when queued callbacks cannot be dialed",
        "severity": "MEDIUM",
        "metric_group": "ContactFlow",
    },
]

# ── Recommended Alarms for Amazon Kinesis Data Streams ──
# These define the best-practice CloudWatch alarms that should exist for
# Kinesis Data Streams associated with a Connect instance (CTR streaming,
# agent event streaming).
RECOMMENDED_KINESIS_ALARMS = [
    {
        "metric_name": "GetRecords.IteratorAgeMilliseconds",
        "namespace": "AWS/Kinesis",
        "statistic": "Maximum",
        "comparison": "GreaterThanThreshold",
        "threshold": 60000,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when consumer iterator age exceeds 60 seconds (consumer lag)",
        "severity": "CRITICAL",
    },
    {
        "metric_name": "ReadProvisionedThroughputExceeded",
        "namespace": "AWS/Kinesis",
        "statistic": "Average",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when read throughput is being throttled",
        "severity": "HIGH",
    },
    {
        "metric_name": "WriteProvisionedThroughputExceeded",
        "namespace": "AWS/Kinesis",
        "statistic": "Average",
        "comparison": "GreaterThanThreshold",
        "threshold": 0,
        "period": 300,
        "evaluation_periods": 1,
        "description": "Alarm when write throughput is being throttled",
        "severity": "HIGH",
    },
    {
        "metric_name": "GetRecords.Success",
        "namespace": "AWS/Kinesis",
        "statistic": "Average",
        "comparison": "LessThanThreshold",
        "threshold": 1,
        "period": 300,
        "evaluation_periods": 3,
        "description": "Alarm when successful read rate drops below 100% sustained",
        "severity": "MEDIUM",
    },
    {
        "metric_name": "PutRecord.Success",
        "namespace": "AWS/Kinesis",
        "statistic": "Average",
        "comparison": "LessThanThreshold",
        "threshold": 1,
        "period": 300,
        "evaluation_periods": 3,
        "description": "Alarm when single-record put success rate drops",
        "severity": "MEDIUM",
    },
    {
        "metric_name": "PutRecords.Success",
        "namespace": "AWS/Kinesis",
        "statistic": "Average",
        "comparison": "LessThanThreshold",
        "threshold": 1,
        "period": 300,
        "evaluation_periods": 3,
        "description": "Alarm when batch put success rate drops",
        "severity": "MEDIUM",
    },
]


def validate_connect_alarms(cw_client, instance_id, start_time, time_budget):
    """Validate recommended CloudWatch alarms for a Connect instance.

    Checks existing CloudWatch alarms against the recommended set, identifying
    which recommended alarms are configured, which are missing, and any extra
    Connect alarms that exist beyond the recommendations.

    Args:
        cw_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Alarm validation findings with found, missing, and extra lists.
    """
    check_time_budget(start_time, time_budget)

    # Collect all existing Connect alarms for this instance
    existing = []
    try:
        paginator = cw_client.get_paginator("describe_alarms")
        for page in paginator.paginate():
            check_time_budget(start_time, time_budget)
            for alarm in page.get("MetricAlarms", []):
                if alarm.get("Namespace") != "AWS/Connect":
                    continue
                for dim in alarm.get("Dimensions", []):
                    if dim["Name"] == "InstanceId" and dim["Value"] == instance_id:
                        existing.append(alarm)
                        break
    except ClientError as e:
        logger.error(f"Error fetching CloudWatch alarms: {e}")
        return {
            "found": [],
            "missing": [],
            "extra": [],
            "total_recommended": len(RECOMMENDED_CONNECT_ALARMS),
            "coverage_pct": 0,
            "triggered_count": 0,
            "status": "error",
            "detail": str(e)[:1024],
        }

    found = []
    missing = []
    recommended_metrics = set()

    for rec in RECOMMENDED_CONNECT_ALARMS:
        recommended_metrics.add(rec["metric_name"])
        matched = None
        for alarm in existing:
            if (
                alarm.get("MetricName") == rec["metric_name"]
                and alarm.get("Namespace") == rec["namespace"]
            ):
                has_instance = any(
                    d["Name"] == "InstanceId" and d["Value"] == instance_id
                    for d in alarm.get("Dimensions", [])
                )
                if has_instance:
                    matched = alarm
                    break

        entry = {
            "metric_name": rec["metric_name"],
            "description": rec["description"],
            "severity": rec["severity"],
            "rec_statistic": rec["statistic"],
            "rec_threshold": rec["threshold"],
            "rec_period": rec["period"],
            "rec_comparison": rec["comparison"],
        }
        if matched:
            entry["alarm_name"] = matched["AlarmName"]
            entry["alarm_state"] = matched.get("StateValue", "UNKNOWN")
            entry["actual_statistic"] = matched.get("Statistic", "N/A")
            entry["actual_threshold"] = matched.get("Threshold", "N/A")
            entry["actual_period"] = matched.get("Period", "N/A")
            entry["has_actions"] = bool(matched.get("AlarmActions"))
            found.append(entry)
        else:
            missing.append(entry)

    # Identify extra alarms not in recommendations
    extra = []
    for alarm in existing:
        if alarm.get("MetricName") not in recommended_metrics:
            extra.append(
                {
                    "alarm_name": alarm["AlarmName"],
                    "metric_name": alarm.get("MetricName"),
                    "state": alarm.get("StateValue"),
                }
            )

    total_recommended = len(RECOMMENDED_CONNECT_ALARMS)
    found_count = len(found)
    coverage_pct = (
        round((found_count / total_recommended) * 100, 1) if total_recommended else 0
    )
    triggered_count = sum(1 for f in found if f.get("alarm_state") == "ALARM")

    # Determine status
    if len(missing) > 0 and triggered_count > 0:
        status = "fail"
        detail = f"{len(missing)} recommended alarms missing, {triggered_count} in ALARM state"
    elif len(missing) > 0:
        status = "warn"
        detail = f"{len(missing)} of {total_recommended} recommended alarms missing"
    elif triggered_count > 0:
        status = "fail"
        detail = f"All configured, {triggered_count} in ALARM state"
    else:
        status = "pass"
        detail = f"All {found_count} recommended alarms configured"

    return {
        "found": found,
        "missing": missing,
        "extra": extra,
        "total_recommended": total_recommended,
        "found_count": found_count,
        "missing_count": len(missing),
        "coverage_pct": coverage_pct,
        "triggered_count": triggered_count,
        "status": status,
        "detail": detail,
    }


def check_missed_calls_metrics(
    cw_client, instance_id, days_back, start_time, time_budget
):
    """Analyze missed calls CloudWatch metrics over the specified period.

    Retrieves daily missed call statistics and computes summary metrics
    including totals, averages, peaks, and week-over-week trends.

    Args:
        cw_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        days_back (int): Number of days to analyze.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Missed calls metrics findings.
    """
    check_time_budget(start_time, time_budget)

    end_time = datetime.now(timezone.utc)
    cw_start_time = end_time - timedelta(days=days_back)

    try:
        response = cw_client.get_metric_statistics(
            Namespace="AWS/Connect",
            MetricName="MissedCalls",
            Dimensions=[
                {"Name": "InstanceId", "Value": instance_id},
                {"Name": "MetricGroup", "Value": "VoiceCalls"},
            ],
            StartTime=cw_start_time,
            EndTime=end_time,
            Period=86400,  # Daily statistics
            Statistics=["Sum"],
        )
    except ClientError as e:
        logger.error(f"Error fetching missed calls metrics: {e}")
        return {
            "total_missed_calls": 0,
            "daily_average": 0,
            "peak_day_count": 0,
            "peak_day_date": None,
            "days_analyzed": days_back,
            "status": "error",
            "detail": str(e)[:1024],
        }

    # Process daily data
    daily_data = []
    total_missed_calls = 0

    for datapoint in sorted(
        response.get("Datapoints", []), key=lambda x: x["Timestamp"]
    ):
        date_str = datapoint["Timestamp"].strftime("%Y-%m-%d")
        missed_calls = int(datapoint["Sum"])
        total_missed_calls += missed_calls
        daily_data.append({"date": date_str, "count": missed_calls})

    if not daily_data:
        return {
            "total_missed_calls": 0,
            "daily_average": 0,
            "peak_day_count": 0,
            "peak_day_date": None,
            "daily_data": [],
            "days_analyzed": days_back,
            "status": "info",
            "detail": "No missed calls data available for the specified period",
        }

    counts = [d["count"] for d in daily_data]
    daily_average = round(sum(counts) / len(counts), 1)
    peak_idx = counts.index(max(counts))
    peak_day_count = counts[peak_idx]
    peak_day_date = daily_data[peak_idx]["date"]

    # Week-over-week comparison
    midpoint = len(daily_data) // 2
    first_half = counts[:midpoint] if midpoint > 0 else []
    second_half = counts[midpoint:] if midpoint > 0 else counts

    week_over_week_change = None
    if first_half and sum(first_half) > 0:
        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)
        week_over_week_change = round(((second_avg - first_avg) / first_avg) * 100, 1)

    # Determine status
    if total_missed_calls > 50:
        status = "warn"
        detail = f"{total_missed_calls:,} missed calls in last {days_back} days"
    elif total_missed_calls > 0:
        status = "pass"
        detail = f"{total_missed_calls:,} missed calls in last {days_back} days"
    else:
        status = "pass"
        detail = "No missed calls detected"

    return {
        "total_missed_calls": total_missed_calls,
        "daily_average": daily_average,
        "peak_day_count": peak_day_count,
        "peak_day_date": peak_day_date,
        "week_over_week_change": week_over_week_change,
        "daily_data": daily_data,
        "days_analyzed": days_back,
        "status": status,
        "detail": detail,
    }


def check_connect_log_groups(
    logs_client, instance_id, aws_region, start_time, time_budget
):
    """Check CloudWatch Log Groups related to the Amazon Connect instance.

    Identifies Connect-related log groups (contact flow logs, contact lens,
    etc.) and checks their retention configuration.

    Args:
        logs_client: Boto3 CloudWatch Logs client.
        instance_id (str): Connect instance ID.
        aws_region (str): AWS region.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Log group findings.
    """
    check_time_budget(start_time, time_budget)

    # Connect-related log group prefixes/patterns
    connect_log_prefixes = [
        f"/aws/connect/{instance_id}",
        "/aws/connect/",
    ]

    connect_log_groups = []
    no_retention_groups = []

    try:
        for prefix in connect_log_prefixes:
            check_time_budget(start_time, time_budget)
            paginator = logs_client.get_paginator("describe_log_groups")
            for page in paginator.paginate(logGroupNamePrefix=prefix):
                check_time_budget(start_time, time_budget)
                for lg in page.get("logGroups", []):
                    log_group_name = lg.get("logGroupName", "")
                    # Avoid duplicates from overlapping prefixes
                    if any(
                        g["log_group_name"] == log_group_name
                        for g in connect_log_groups
                    ):
                        continue
                    retention_days = lg.get("retentionInDays")
                    stored_bytes = lg.get("storedBytes", 0)
                    entry = {
                        "log_group_name": log_group_name,
                        "retention_days": retention_days,
                        "stored_bytes": stored_bytes,
                        "has_retention_policy": retention_days is not None,
                    }
                    connect_log_groups.append(entry)
                    if retention_days is None:
                        no_retention_groups.append(entry)
    except ClientError as e:
        logger.error(f"Error describing log groups: {e}")
        return {
            "total_log_groups": 0,
            "no_retention_count": 0,
            "log_groups": [],
            "status": "error",
            "detail": str(e)[:1024],
        }

    if not connect_log_groups:
        return {
            "total_log_groups": 0,
            "no_retention_count": 0,
            "log_groups": [],
            "status": "info",
            "detail": "No Connect-related CloudWatch Log Groups found",
        }

    if no_retention_groups:
        status = "warn"
        detail = (
            f"{len(no_retention_groups)}/{len(connect_log_groups)} "
            f"log group(s) have no retention policy (logs retained indefinitely)"
        )
    else:
        status = "pass"
        detail = f"All {len(connect_log_groups)} log group(s) have retention policies configured"

    return {
        "total_log_groups": len(connect_log_groups),
        "no_retention_count": len(no_retention_groups),
        "log_groups": connect_log_groups,
        "status": status,
        "detail": detail,
    }


def validate_kinesis_stream_alarms(
    connect_client, cw_client, instance_id, instance_arn, start_time, time_budget
):
    """Validate CloudWatch alarms for Kinesis Data Streams associated with Connect.

    Discovers Kinesis streams configured for the Connect instance (CTR streaming,
    agent event streaming) via instance storage configs, then checks whether
    recommended CloudWatch alarms exist for each stream.

    Args:
        connect_client: Boto3 Connect client.
        cw_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        instance_arn (str): Connect instance ARN.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Kinesis alarm validation findings with found, missing, coverage_pct, status, detail.
    """
    check_time_budget(start_time, time_budget)

    # Discover Kinesis streams associated with this Connect instance
    kinesis_stream_arns = []
    storage_resource_types = [
        "CONTACT_TRACE_RECORDS",
        "AGENT_EVENTS",
    ]

    try:
        for resource_type in storage_resource_types:
            check_time_budget(start_time, time_budget)
            try:
                response = connect_client.list_instance_storage_configs(
                    InstanceId=instance_id,
                    ResourceType=resource_type,
                )
                for config in response.get("StorageConfigs", []):
                    if config.get("StorageType") == "KINESIS_STREAM":
                        stream_config = config.get("KinesisStreamConfig", {})
                        stream_arn = stream_config.get("StreamArn", "")
                        if stream_arn and stream_arn not in kinesis_stream_arns:
                            kinesis_stream_arns.append(stream_arn)
            except ClientError as e:
                # Some resource types may not be configured; continue checking others
                logger.warning(
                    f"Could not list storage configs for {resource_type}: {e}"
                )
                continue
    except ClientError as e:
        logger.error(f"Error discovering Kinesis streams for Connect instance: {e}")
        return {
            "streams_found": [],
            "found": [],
            "missing": [],
            "total_recommended": 0,
            "coverage_pct": 0,
            "status": "error",
            "detail": str(e)[:1024],
        }

    # Extract stream names from ARNs
    stream_names = []
    for arn in kinesis_stream_arns:
        # ARN format: arn:aws:kinesis:region:account:stream/stream-name
        parts = arn.split("/")
        if len(parts) >= 2:
            stream_names.append(parts[-1])

    if not stream_names:
        return {
            "streams_found": [],
            "found": [],
            "missing": [],
            "total_recommended": 0,
            "coverage_pct": 0,
            "status": "info",
            "detail": "No Kinesis Data Streams associated with this Connect instance",
        }

    # Check CloudWatch alarms for each stream
    found = []
    missing = []

    try:
        for stream_name in stream_names:
            check_time_budget(start_time, time_budget)

            # Get all alarms in AWS/Kinesis namespace for this stream
            existing_alarms = []
            paginator = cw_client.get_paginator("describe_alarms")
            for page in paginator.paginate():
                check_time_budget(start_time, time_budget)
                for alarm in page.get("MetricAlarms", []):
                    if alarm.get("Namespace") != "AWS/Kinesis":
                        continue
                    for dim in alarm.get("Dimensions", []):
                        if dim["Name"] == "StreamName" and dim["Value"] == stream_name:
                            existing_alarms.append(alarm)
                            break

            # Check each recommended alarm against existing alarms
            for rec in RECOMMENDED_KINESIS_ALARMS:
                matched = None
                for alarm in existing_alarms:
                    if alarm.get("MetricName") == rec["metric_name"]:
                        matched = alarm
                        break

                entry = {
                    "stream_name": stream_name,
                    "metric_name": rec["metric_name"],
                    "description": rec["description"],
                    "severity": rec["severity"],
                    "rec_statistic": rec["statistic"],
                    "rec_threshold": rec["threshold"],
                    "rec_period": rec["period"],
                    "rec_comparison": rec["comparison"],
                }
                if matched:
                    entry["alarm_name"] = matched["AlarmName"]
                    entry["alarm_state"] = matched.get("StateValue", "UNKNOWN")
                    entry["has_actions"] = bool(matched.get("AlarmActions"))
                    found.append(entry)
                else:
                    missing.append(entry)
    except ClientError as e:
        logger.error(f"Error checking Kinesis stream alarms: {e}")
        return {
            "streams_found": stream_names,
            "found": found,
            "missing": missing,
            "total_recommended": len(RECOMMENDED_KINESIS_ALARMS) * len(stream_names),
            "coverage_pct": 0,
            "status": "error",
            "detail": str(e)[:1024],
        }

    total_recommended = len(RECOMMENDED_KINESIS_ALARMS) * len(stream_names)
    found_count = len(found)
    coverage_pct = (
        round((found_count / total_recommended) * 100, 1) if total_recommended else 0
    )

    # Determine status
    if len(missing) > 0:
        status = "warn"
        detail = (
            f"{len(missing)} of {total_recommended} recommended Kinesis alarms missing "
            f"across {len(stream_names)} stream(s)"
        )
    else:
        status = "pass"
        detail = f"All {found_count} recommended Kinesis alarms configured across {len(stream_names)} stream(s)"

    return {
        "streams_found": stream_names,
        "found": found,
        "missing": missing,
        "total_recommended": total_recommended,
        "found_count": found_count,
        "missing_count": len(missing),
        "coverage_pct": coverage_pct,
        "status": status,
        "detail": detail,
    }


def lambda_handler(event, context):
    """Observability Analyzer Lambda handler.

    Validates input using shared validate_input, checks CloudWatch alarms,
    missed calls metrics, and log group configuration with graceful timeout,
    persists results to S3 Hive-style path, and returns a standardized
    AnalyzerResult.

    Args:
        event (dict): Input event with reviewId, instanceId, instanceArn,
                      accountId, awsRegion, daysBack, componentType,
                      s3ReportingBucket, and optional maxSeconds.
        context: Lambda context object.

    Returns:
        dict: Standardized AnalyzerResult (success or error).
    """
    logger.info("Observability Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "observability"),
            str(e),
        )

    instance_id = event["instanceId"]
    instance_arn = event["instanceArn"]
    account_id = event["accountId"]
    aws_region = event["awsRegion"]
    days_back = event["daysBack"]
    component_type = event["componentType"]

    # Accept maxSeconds from event payload (passed via analyzerTimeouts from PrepareContext)
    max_seconds = event.get("maxSeconds", DEFAULT_MAX_SECONDS)

    # Compute internal time budget using shared graceful timeout utility
    time_budget = compute_time_budget(max_seconds)

    logger.info(
        f"Analyzing observability for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"max_seconds={max_seconds}, time_budget={time_budget}"
    )

    cloudwatch_client = boto3.client("cloudwatch", region_name=aws_region)
    logs_client = boto3.client("logs", region_name=aws_region)
    connect_client = boto3.client("connect", region_name=aws_region)
    checks_completed = []

    try:
        # Check 1: CloudWatch Alarm Validation
        alarm_findings = validate_connect_alarms(
            cloudwatch_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("cloudwatch_alarms")

        # Check 2: Missed Calls Metrics
        missed_calls_findings = check_missed_calls_metrics(
            cloudwatch_client,
            instance_id,
            days_back,
            execution_start,
            time_budget,
        )
        checks_completed.append("missed_calls_metrics")

        # Check 3: CloudWatch Log Groups
        log_group_findings = check_connect_log_groups(
            logs_client,
            instance_id,
            aws_region,
            execution_start,
            time_budget,
        )
        checks_completed.append("log_groups")

        # Check 4: Kinesis Data Streams Alarm Validation
        kinesis_findings = validate_kinesis_stream_alarms(
            connect_client,
            cloudwatch_client,
            instance_id,
            instance_arn,
            execution_start,
            time_budget,
        )
        checks_completed.append("kinesis_data_streams")

        findings = {
            "cloudwatch_alarms": alarm_findings,
            "missed_calls_metrics": missed_calls_findings,
            "log_groups": log_group_findings,
            "kinesis_data_streams": kinesis_findings,
            "checks_completed": checks_completed,
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        }

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(event, findings)

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(
            f"Observability analysis complete: {len(checks_completed)} checks completed"
        )

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
        )

    except TimeBudgetExceeded:
        logger.info(
            f"Observability Analyzer reached time budget after completing "
            f"{len(checks_completed)} of 4 checks: {checks_completed}"
        )

        # Build partial findings from whatever checks completed
        findings = {
            "checks_completed": checks_completed,
            "timed_out": True,
            "account_id": account_id,
            "region": aws_region,
        }
        if "cloudwatch_alarms" in checks_completed:
            findings["cloudwatch_alarms"] = alarm_findings
        if "missed_calls_metrics" in checks_completed:
            findings["missed_calls_metrics"] = missed_calls_findings
        if "log_groups" in checks_completed:
            findings["log_groups"] = log_group_findings
        if "kinesis_data_streams" in checks_completed:
            findings["kinesis_data_streams"] = kinesis_findings

        collected_count = len(checks_completed)
        total_estimated = 4  # Total number of observability checks

        # Persist partial results to S3 before returning
        s3_key = persist_to_s3(
            event,
            findings,
            partial=True,
            collected_count=collected_count,
            total_estimated=total_estimated,
        )

        duration_ms = int((time.time() - execution_start) * 1000)

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
            partial=True,
            collected_count=collected_count,
            total_estimated=total_estimated,
        )

    except ClientError as e:
        error_msg = str(e)
        logger.error(f"AWS API error in Observability Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in Observability Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
