# Cost Analyzer Lambda Function
#
# Standalone Lambda that performs cost analysis for Amazon Connect instances,
# analyzing telephony usage patterns, channel mix, and cost optimization
# opportunities. Extracted from the monolithic lambda_function.py as part of
# the parallel orchestration architecture.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import logging
import time
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError

from analyzer_common import (
    validate_input,
    success_result,
    error_result,
    persist_to_s3,
    fetch_phone_numbers,
)
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 240

# Channel ordering for consistent output
CHANNEL_ORDER = ["VOICE", "CHAT", "EMAIL", "TASK", "GUIDE"]


def get_channel_usage(
    connect_client, instance_id, instance_arn, days_back, start_time, time_budget
):
    """Get contact counts by channel using GetMetricDataV2.

    Queries Amazon Connect metrics API for contact volume, handle time,
    and channel distribution over the specified period.

    Args:
        connect_client: Boto3 Connect client (regional).
        instance_id (str): Connect instance ID.
        instance_arn (str): Connect instance ARN.
        days_back (int): Number of days to look back.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Channel usage data keyed by channel name, each containing
              CONTACTS_CREATED, CONTACTS_HANDLED, AVG_HANDLE_TIME, SUM_HANDLE_TIME.
    """
    check_time_budget(start_time, time_budget)

    end_time_dt = datetime.now(timezone.utc)
    start_time_dt = end_time_dt - timedelta(days=days_back)

    # Need at least one queue filter for GetMetricDataV2
    try:
        queues_resp = connect_client.list_queues(
            InstanceId=instance_id, QueueTypes=["STANDARD"]
        )
        queue_arns = [q["Arn"] for q in queues_resp.get("QueueSummaryList", [])]
    except ClientError as e:
        logger.warning(f"Error listing queues: {e}")
        return {}

    if not queue_arns:
        logger.info("No queues found — cannot query channel usage")
        return {}

    results = {}
    next_token = None

    while True:
        check_time_budget(start_time, time_budget)

        kwargs = dict(
            ResourceArn=instance_arn,
            StartTime=start_time_dt,
            EndTime=end_time_dt,
            Interval={"TimeZone": "UTC", "IntervalPeriod": "TOTAL"},
            Filters=[{"FilterKey": "QUEUE", "FilterValues": queue_arns}],
            Groupings=["CHANNEL"],
            Metrics=[
                {"Name": "CONTACTS_CREATED"},
                {"Name": "CONTACTS_HANDLED"},
                {"Name": "AVG_HANDLE_TIME"},
                {"Name": "SUM_HANDLE_TIME"},
            ],
        )
        if next_token:
            kwargs["NextToken"] = next_token

        try:
            resp = connect_client.get_metric_data_v2(**kwargs)
        except ClientError as e:
            logger.error(f"Error calling get_metric_data_v2: {e}")
            break

        for mr in resp.get("MetricResults", []):
            channel = mr.get("Dimensions", {}).get("CHANNEL", "UNKNOWN")
            if channel not in results:
                results[channel] = {}
            for col in mr.get("Collections", []):
                metric_name = col["Metric"]["Name"]
                results[channel][metric_name] = col.get("Value", 0)

        next_token = resp.get("NextToken")
        if not next_token:
            break

    return results


def analyze_channel_mix(channel_usage):
    """Analyze channel mix and generate cost optimization recommendations.

    Evaluates the distribution of contacts across channels and identifies
    opportunities to reduce costs through channel deflection, multi-channel
    routing, and handle time optimization.

    Args:
        channel_usage (dict): Channel usage data from get_channel_usage.

    Returns:
        dict: Channel mix analysis with per-channel metrics, totals,
              and cost optimization recommendations.
    """
    if not channel_usage:
        return {
            "channels": [],
            "total_created": 0,
            "total_handled": 0,
            "recommendations": [],
            "status": "info",
            "detail": "No contact data available for analysis",
        }

    # Order channels consistently
    all_channels = list(channel_usage.keys())
    ordered = [ch for ch in CHANNEL_ORDER if ch in all_channels]
    ordered += [ch for ch in all_channels if ch not in ordered]

    channels = []
    total_created = 0
    total_handled = 0
    channel_created = {}
    channel_sum_ht = {}

    for ch in ordered:
        metrics = channel_usage[ch]
        created = int(metrics.get("CONTACTS_CREATED", 0))
        handled = int(metrics.get("CONTACTS_HANDLED", 0))
        avg_ht = metrics.get("AVG_HANDLE_TIME", 0)
        sum_ht = metrics.get("SUM_HANDLE_TIME", 0)

        total_created += created
        total_handled += handled
        channel_created[ch] = created
        channel_sum_ht[ch] = sum_ht

        channels.append(
            {
                "channel": ch,
                "contacts_created": created,
                "contacts_handled": handled,
                "avg_handle_time_seconds": round(avg_ht, 1) if avg_ht else 0,
                "sum_handle_time_seconds": round(sum_ht, 1) if sum_ht else 0,
            }
        )

    # Generate cost optimization recommendations
    recommendations = []

    voice_created = channel_created.get("VOICE", 0)
    chat_created = channel_created.get("CHAT", 0)
    task_created = channel_created.get("TASK", 0)
    email_created = channel_created.get("EMAIL", 0)

    voice_pct = (voice_created / total_created * 100) if total_created > 0 else 0
    chat_pct = (chat_created / total_created * 100) if total_created > 0 else 0

    voice_sum_ht = channel_sum_ht.get("VOICE", 0)
    voice_avg_ht = (voice_sum_ht / voice_created) if voice_created > 0 else 0

    if voice_pct > 80 and total_created > 100:
        recommendations.append(
            {
                "type": "channel_voice_heavy",
                "severity": "warn",
                "detail": (
                    f"Voice contacts represent {voice_pct:.0f}% of total volume. "
                    "Voice is billed per minute — consider deflecting routine inquiries "
                    "to chat or tasks where applicable."
                ),
            }
        )

    if chat_created == 0 and voice_created > 100:
        recommendations.append(
            {
                "type": "channel_no_chat",
                "severity": "info",
                "detail": (
                    "No chat contacts detected. Enabling chat can reduce costs for "
                    "routine inquiries — agents can handle multiple concurrent chats "
                    "vs. one voice call at a time."
                ),
            }
        )

    if task_created == 0 and total_created > 100:
        recommendations.append(
            {
                "type": "channel_no_tasks",
                "severity": "info",
                "detail": (
                    "No task contacts detected. Tasks can automate follow-up work items "
                    "at a lower cost than voice (billed per task rather than per minute)."
                ),
            }
        )

    if voice_avg_ht > 600 and voice_created > 50:
        recommendations.append(
            {
                "type": "channel_high_voice_ht",
                "severity": "warn",
                "detail": (
                    f"Average voice handle time is {int(voice_avg_ht // 60)}m "
                    f"{int(voice_avg_ht % 60)}s. Long handle times increase per-minute "
                    "voice costs. Consider step-by-step guides or AI agent assist to "
                    "reduce handle time."
                ),
            }
        )

    if chat_pct > 0 and voice_pct > 0:
        recommendations.append(
            {
                "type": "channel_multi_channel",
                "severity": "info",
                "detail": (
                    f"Multi-channel active (voice {voice_pct:.0f}%, chat {chat_pct:.0f}%). "
                    "Ensure routing profiles allow agents to handle concurrent chats "
                    "alongside voice to maximize utilization and reduce cost per contact."
                ),
            }
        )

    if email_created > 0:
        recommendations.append(
            {
                "type": "channel_email_active",
                "severity": "info",
                "detail": (
                    f"Email channel active with {email_created} contacts. "
                    "Consider auto-responses and AI agent assist to draft replies "
                    "and reduce agent handling time for email."
                ),
            }
        )

    # General pricing reminder
    recommendations.append(
        {
            "type": "channel_usage_general",
            "severity": "info",
            "detail": (
                "Amazon Connect pricing varies by channel: Voice is per-minute, "
                "Chat is per-message, Tasks are per-task, and Email is per-email. "
                "Review your channel mix regularly to optimize costs."
            ),
        }
    )

    # Determine overall status
    warn_count = sum(1 for r in recommendations if r["severity"] == "warn")
    if warn_count > 0:
        status = "warn"
        detail = f"{warn_count} cost optimization opportunity(ies) identified"
    else:
        status = "pass"
        detail = "Channel mix reviewed — no significant cost concerns"

    return {
        "channels": channels,
        "total_created": total_created,
        "total_handled": total_handled,
        "voice_percentage": round(voice_pct, 1),
        "chat_percentage": round(chat_pct, 1),
        "recommendations": recommendations,
        "status": status,
        "detail": detail,
    }


def analyze_telephony_usage(
    connect_client, instance_id, start_time, time_budget, event=None
):
    """Analyze telephony number inventory and usage patterns.

    Examines phone number types (DID, toll-free, etc.), country distribution,
    and provides recommendations for telephony cost optimization.

    Args:
        connect_client: Boto3 Connect client (regional).
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        dict: Telephony usage findings with number inventory and recommendations.
    """
    check_time_budget(start_time, time_budget)

    phone_type_counts = {
        "TOLL_FREE": 0,
        "DID": 0,
        "UIFN": 0,
        "SHARED": 0,
        "THIRD_PARTY_TF": 0,
        "THIRD_PARTY_DID": 0,
        "SHORT_CODE": 0,
    }
    country_counts = {}
    total_numbers = 0
    phone_numbers = []
    unused_phone_numbers = []

    # Fetch phone numbers via shared utility (QB-4)
    raw_numbers = fetch_phone_numbers(
        connect_client, instance_id, start_time, time_budget, event
    )

    for number_summary in raw_numbers:
        total_numbers += 1
        phone_type = number_summary.get("PhoneNumberType", "UNKNOWN")
        country_code = number_summary.get("PhoneNumberCountryCode", "UNKNOWN")
        target_arn = number_summary.get("TargetArn", "")
        phone_number_status = number_summary.get("PhoneNumberStatus", {})
        status_value = (
            phone_number_status.get("Status", "")
            if isinstance(phone_number_status, dict)
            else ""
        )

        if phone_type in phone_type_counts:
            phone_type_counts[phone_type] += 1
        country_counts[country_code] = country_counts.get(country_code, 0) + 1

        phone_record = {
            "phone_number": number_summary.get("PhoneNumber", ""),
            "phone_number_country_code": country_code,
            "phone_number_type": phone_type,
            "target_arn": target_arn,
            "phone_number_status": status_value,
        }
        phone_numbers.append(phone_record)

        # Identify unused phone numbers: no target ARN or failed/in-progress status
        if not target_arn or status_value in ("FAILED", "IN_PROGRESS"):
            unused_phone_numbers.append(phone_record)

    # Generate telephony recommendations
    recommendations = []

    toll_free_count = phone_type_counts.get("TOLL_FREE", 0)
    did_count = phone_type_counts.get("DID", 0)

    if total_numbers > 0:
        toll_free_pct = toll_free_count / total_numbers * 100
        did_pct = did_count / total_numbers * 100
    else:
        toll_free_pct = 0
        did_pct = 0

    if toll_free_pct > 70 and total_numbers > 5:
        recommendations.append(
            {
                "type": "telephony_toll_free_dominant",
                "severity": "info",
                "detail": (
                    f"Toll-free numbers represent {toll_free_pct:.0f}% of inventory. "
                    "While toll-free provides carrier redundancy, it comes at higher "
                    "per-minute cost compared to DIDs. Evaluate whether all numbers "
                    "require toll-free resiliency."
                ),
            }
        )

    if did_count > 3 and len(country_counts) == 1:
        recommendations.append(
            {
                "type": "telephony_single_country",
                "severity": "info",
                "detail": (
                    f"All {total_numbers} numbers are in a single country. "
                    "If serving international customers, consider local DIDs in "
                    "target countries to reduce caller costs."
                ),
            }
        )

    if total_numbers > 20:
        recommendations.append(
            {
                "type": "telephony_large_inventory",
                "severity": "info",
                "detail": (
                    f"Large phone number inventory ({total_numbers} numbers). "
                    "Each claimed number incurs a daily charge. Review whether all "
                    "numbers are actively used and release unused numbers."
                ),
            }
        )

    if total_numbers == 0:
        recommendations.append(
            {
                "type": "telephony_no_numbers",
                "severity": "info",
                "detail": "No phone numbers claimed for this instance.",
            }
        )

    # QB-65 / R2: emit warn when DIDs exist with zero toll-free (single-carrier
    # dependency, no redundancy). Analyzer now owns this status decision so the
    # renderer can flow the analyzer's status field verbatim to the badge — the
    # retained JSON and the HTML report agree on every input.
    if did_count > 0 and toll_free_count == 0:
        recommendations.append(
            {
                "type": "telephony_no_toll_free_backup",
                "severity": "warn",
                "detail": (
                    f"{did_count} DIDs, 0 toll-free - consider toll-free for redundancy"
                ),
            }
        )

    # Determine status — surface the strongest severity present in
    # recommendations so the renderer can flow status verbatim.
    if not recommendations:
        status = "pass"
        detail = f"Telephony inventory reviewed: {total_numbers} numbers"
    else:
        severities = {r["severity"] for r in recommendations}
        if "warn" in severities:
            status = "warn"
        else:
            status = "pass"
        detail = f"{total_numbers} numbers reviewed, {len(recommendations)} recommendation(s)"

    return {
        "total_numbers": total_numbers,
        "type_distribution": phone_type_counts,
        "country_distribution": country_counts,
        "countries_count": len(country_counts),
        "toll_free_percentage": round(toll_free_pct, 1),
        "did_percentage": round(did_pct, 1),
        "phone_numbers": phone_numbers,
        "unused_phone_numbers": unused_phone_numbers,
        "recommendations": recommendations,
        "status": status,
        "detail": detail,
    }


def analyze_usage_patterns(
    connect_client, cloudwatch_client, instance_id, days_back, start_time, time_budget
):
    """Analyze usage patterns to identify cost optimization opportunities.

    Examines daily contact volume patterns and peak/off-peak distribution
    to identify scheduling optimization opportunities.

    Args:
        connect_client: Boto3 Connect client (regional).
        cloudwatch_client: Boto3 CloudWatch client (regional).
        instance_id (str): Connect instance ID.
        days_back (int): Number of days to analyze.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Usage pattern findings with daily volumes and recommendations.
    """
    check_time_budget(start_time, time_budget)

    end_time_dt = datetime.now(timezone.utc)
    start_time_dt = end_time_dt - timedelta(days=days_back)

    # Get daily concurrent call metrics to understand usage patterns
    daily_volumes = []
    try:
        resp = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/Connect",
            MetricName="ConcurrentCalls",
            Dimensions=[
                {"Name": "InstanceId", "Value": instance_id},
                {"Name": "MetricGroup", "Value": "VoiceCalls"},
            ],
            StartTime=start_time_dt,
            EndTime=end_time_dt,
            Period=86400,  # Daily granularity
            Statistics=["Maximum", "Average", "Sum"],
        )
        for dp in sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"]):
            daily_volumes.append(
                {
                    "date": dp["Timestamp"].strftime("%Y-%m-%d"),
                    "peak_concurrent": int(dp.get("Maximum", 0)),
                    "avg_concurrent": round(dp.get("Average", 0), 1),
                }
            )
    except ClientError as e:
        logger.warning(f"Error fetching usage patterns: {e}")

    # Analyze patterns
    recommendations = []

    if daily_volumes:
        peaks = [d["peak_concurrent"] for d in daily_volumes]
        avgs = [d["avg_concurrent"] for d in daily_volumes]
        max_peak = max(peaks) if peaks else 0
        avg_peak = sum(peaks) / len(peaks) if peaks else 0
        overall_avg = sum(avgs) / len(avgs) if avgs else 0

        # High peak-to-average ratio suggests bursty traffic
        if avg_peak > 0 and max_peak > avg_peak * 3:
            recommendations.append(
                {
                    "type": "usage_bursty_traffic",
                    "severity": "info",
                    "detail": (
                        f"Peak concurrent calls ({max_peak}) is {max_peak / avg_peak:.1f}x "
                        "the average peak. Bursty traffic patterns may benefit from "
                        "callback queuing to smooth demand and reduce concurrent capacity needs."
                    ),
                }
            )

        # Very low utilization
        if overall_avg < 2 and len(daily_volumes) >= 7:
            recommendations.append(
                {
                    "type": "usage_low_volume",
                    "severity": "info",
                    "detail": (
                        f"Average concurrent calls is {overall_avg:.1f}. "
                        "Low volume instances may benefit from consolidation with "
                        "other instances or reduced phone number inventory."
                    ),
                }
            )

        status = "pass"
        detail = f"Analyzed {len(daily_volumes)} days of usage data"
    else:
        max_peak = 0
        avg_peak = 0
        overall_avg = 0
        status = "info"
        detail = "No usage pattern data available"

    return {
        "daily_volumes": daily_volumes,
        "days_analyzed": len(daily_volumes),
        "max_peak_concurrent": max_peak,
        "avg_peak_concurrent": round(avg_peak, 1),
        "overall_avg_concurrent": round(overall_avg, 1),
        "recommendations": recommendations,
        "status": status,
        "detail": detail,
    }


def lambda_handler(event, context):
    """Cost Analyzer Lambda handler.

    Validates input using shared validate_input, analyzes channel usage,
    telephony inventory, and usage patterns with graceful timeout,
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
    logger.info("Cost Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "cost"),
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
        f"Analyzing cost for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"days_back={days_back}, max_seconds={max_seconds}, time_budget={time_budget}"
    )

    connect_client = boto3.client("connect", region_name=aws_region)
    cloudwatch_client = boto3.client("cloudwatch", region_name=aws_region)
    checks_completed = []

    try:
        # Check 1: Channel Usage and Cost Mix
        channel_usage_raw = get_channel_usage(
            connect_client,
            instance_id,
            instance_arn,
            days_back,
            execution_start,
            time_budget,
        )
        channel_mix_findings = analyze_channel_mix(channel_usage_raw)
        checks_completed.append("channel_mix")

        # Check 2: Telephony Number Inventory
        telephony_findings = analyze_telephony_usage(
            connect_client,
            instance_id,
            execution_start,
            time_budget,
            event=event,
        )
        checks_completed.append("telephony_usage")

        # Check 3: Usage Patterns
        usage_pattern_findings = analyze_usage_patterns(
            connect_client,
            cloudwatch_client,
            instance_id,
            days_back,
            execution_start,
            time_budget,
        )
        checks_completed.append("usage_patterns")

        findings = {
            "channel_mix": channel_mix_findings,
            "telephony_usage": telephony_findings,
            "usage_patterns": usage_pattern_findings,
            "checks_completed": checks_completed,
            "days_back": days_back,
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        }

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(event, findings)

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(f"Cost analysis complete: {len(checks_completed)} checks completed")

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
        )

    except TimeBudgetExceeded:
        logger.info(
            f"Cost Analyzer reached time budget after completing "
            f"{len(checks_completed)} of 3 checks: {checks_completed}"
        )

        # Build partial findings from whatever checks completed
        findings = {
            "checks_completed": checks_completed,
            "timed_out": True,
            "account_id": account_id,
            "region": aws_region,
        }
        if "channel_mix" in checks_completed:
            findings["channel_mix"] = channel_mix_findings
        if "telephony_usage" in checks_completed:
            findings["telephony_usage"] = telephony_findings
        if "usage_patterns" in checks_completed:
            findings["usage_patterns"] = usage_pattern_findings

        collected_count = len(checks_completed)
        total_estimated = 3  # Total number of cost checks

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
        logger.error(f"AWS API error in Cost Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in Cost Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
