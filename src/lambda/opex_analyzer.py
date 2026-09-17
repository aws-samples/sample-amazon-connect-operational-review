# Operational Excellence Analyzer Lambda Function
#
# Standalone Lambda that performs operational excellence analysis for Amazon
# Connect instances, checking contact flow logging hygiene, KVS retention
# configuration, and misconfigured phone numbers.
# Extracted from the monolithic lambda_function.py as part of the
# parallel orchestration architecture.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import json
import logging
import time
from botocore.exceptions import ClientError

from analyzer_common import (
    validate_input,
    success_result,
    error_result,
    persist_to_s3,
    fetch_phone_numbers,
    fetch_storage_configs,
)
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 240

# Maximum number of contact flows to analyze before capping
MAX_FLOWS_TO_ANALYZE = 1000

# Prefixes for default/sample flows that should be skipped
SKIP_PREFIXES = ("default ", "sample ")


def check_contact_flow_logging(connect_client, instance_id, start_time, time_budget):
    """Check contact flows for explicit logging behavior blocks.

    Analyzes non-default/sample contact flows to determine whether they have
    an explicit 'Set logging behavior' block enabled. Flows without explicit
    logging may lose logs if instance-level settings change.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Contact flow logging findings.
    """
    check_time_budget(start_time, time_budget)

    flows_without_logging = []
    skipped_flows = []
    analyzed_count = 0
    was_capped = False

    try:
        paginator = connect_client.get_paginator("list_contact_flows")
        page_iterator = paginator.paginate(InstanceId=instance_id)

        for page in page_iterator:
            check_time_budget(start_time, time_budget)

            for flow_summary in page.get("ContactFlowSummaryList", []):
                flow_id = flow_summary["Id"]
                flow_name = flow_summary["Name"]
                flow_type = flow_summary.get("ContactFlowType", "UNKNOWN")

                # Skip default and sample flows
                if flow_name.lower().startswith(SKIP_PREFIXES):
                    skipped_flows.append(
                        {
                            "flow_id": flow_id,
                            "flow_name": flow_name,
                            "flow_type": flow_type,
                        }
                    )
                    continue

                # Cap at MAX_FLOWS_TO_ANALYZE to avoid throttling
                if analyzed_count >= MAX_FLOWS_TO_ANALYZE:
                    was_capped = True
                    continue

                check_time_budget(start_time, time_budget)

                try:
                    # Rate limit: ~8 TPS to stay under 10 TPS DescribeContactFlow limit
                    time.sleep(0.12)
                    flow_details = connect_client.describe_contact_flow(
                        InstanceId=instance_id,
                        ContactFlowId=flow_id,
                    )

                    # Parse the flow content (JSON string)
                    flow_content_str = flow_details.get("ContactFlow", {}).get(
                        "Content", "{}"
                    )
                    flow_content = json.loads(flow_content_str)

                    # Check if logging is enabled in this flow
                    logging_enabled = _check_logging_behavior_in_flow(flow_content)
                    analyzed_count += 1

                    if not logging_enabled:
                        flows_without_logging.append(
                            {
                                "flow_id": flow_id,
                                "flow_name": flow_name,
                                "flow_type": flow_type,
                                "flow_arn": flow_summary.get("Arn", ""),
                                "status": flow_summary.get("ContactFlowStatus", ""),
                                "state": flow_summary.get("ContactFlowState", ""),
                            }
                        )
                except ClientError as e:
                    logger.warning(f"Error analyzing flow {flow_name}: {e}")
                    analyzed_count += 1
                    continue
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Error parsing flow content for {flow_name}: {e}")
                    analyzed_count += 1
                    continue

    except ClientError as e:
        logger.error(f"Error listing contact flows: {e}")
        return {
            "total_analyzed": 0,
            "flows_without_logging": [],
            "skipped_flows_count": 0,
            "was_capped": False,
            "status": "error",
            "detail": str(e)[:1024],
        }

    # Determine status
    if analyzed_count == 0:
        status = "info"
        detail = "No non-default contact flows found to analyze"
    elif len(flows_without_logging) == 0:
        status = "pass"
        detail = f"All {analyzed_count} analyzed flows have logging enabled"
    else:
        status = "warn"
        detail = (
            f"{len(flows_without_logging)} of {analyzed_count} flows "
            f"missing explicit logging block"
        )

    return {
        "total_analyzed": analyzed_count,
        "flows_without_logging": flows_without_logging,
        "flows_without_logging_count": len(flows_without_logging),
        "skipped_flows_count": len(skipped_flows),
        "was_capped": was_capped,
        "status": status,
        "detail": detail,
    }


def _check_logging_behavior_in_flow(flow_content):
    """Check if a flow contains a 'Set logging behavior' block with logging enabled.

    Args:
        flow_content (dict): Parsed contact flow JSON content.

    Returns:
        bool: True if an enabled logging behavior block is found.
    """
    actions = flow_content.get("Actions", [])

    for action in actions:
        action_type = action.get("Type", "")
        if action_type == "UpdateFlowLoggingBehavior":
            parameters = action.get("Parameters", {})
            logging_behavior = parameters.get("FlowLoggingBehavior", "")
            if logging_behavior == "Enabled":
                return True

    return False


def check_kvs_retention(connect_client, instance_id, start_time, time_budget):
    """Check Kinesis Video Stream retention period configuration.

    Identifies KVS streams with zero retention (no persistence), which means
    media data is not stored and cannot be replayed after the stream ends.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: KVS retention findings.
    """
    check_time_budget(start_time, time_budget)

    kvs_streams = []

    # Fetch all storage configs via shared utility (QB-5)
    all_storage_configs = fetch_storage_configs(
        connect_client, instance_id, start_time, time_budget
    )

    for rtype, cfg in all_storage_configs:
        stype = cfg.get("StorageType", "")
        if stype == "KINESIS_VIDEO_STREAM" and "KinesisVideoStreamConfig" in cfg:
            kvc = cfg["KinesisVideoStreamConfig"]
            retention_hours = kvc.get("RetentionPeriodHours", 0)
            enc_config = kvc.get("EncryptionConfig")
            kvs_streams.append(
                {
                    "resource_type": rtype.replace("_", " ").title(),
                    "prefix": kvc.get("Prefix", ""),
                    "retention_hours": retention_hours,
                    "has_encryption": bool(enc_config),
                }
            )

    if not kvs_streams:
        return {
            "total_streams": 0,
            "zero_retention_count": 0,
            "streams": [],
            "status": "info",
            "detail": "No Kinesis Video Stream configurations found",
        }

    zero_retention = [s for s in kvs_streams if s["retention_hours"] == 0]

    if zero_retention:
        status = "warn"
        detail = (
            f"{len(zero_retention)}/{len(kvs_streams)} KVS stream(s) "
            f"with 0h retention (no persistence)"
        )
    else:
        status = "pass"
        detail = f"All {len(kvs_streams)} KVS stream(s) have retention > 0h"

    return {
        "total_streams": len(kvs_streams),
        "zero_retention_count": len(zero_retention),
        "streams": kvs_streams,
        "status": status,
        "detail": detail,
    }


def check_misconfigured_phone_numbers(
    connect_client, instance_id, start_time, time_budget, event=None
):
    """Check for phone numbers with FAILED or IN_PROGRESS claim/port status.

    Simplified check that only identifies numbers with non-functional status.
    Numbers with FAILED or IN_PROGRESS status incur charges without delivering
    value and should be investigated or released.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        dict: Misconfigured phone numbers findings.
    """
    check_time_budget(start_time, time_budget)

    phone_numbers = []
    misconfigured = []
    failed_count = 0

    # Fetch phone numbers via shared utility (QB-4)
    raw_numbers = fetch_phone_numbers(
        connect_client, instance_id, start_time, time_budget, event
    )

    for number_summary in raw_numbers:
        phone_number = number_summary.get("PhoneNumber", "")
        country_code = number_summary.get("PhoneNumberCountryCode", "UNKNOWN")
        phone_type = number_summary.get("PhoneNumberType", "UNKNOWN")
        phone_number_status = number_summary.get("PhoneNumberStatus", {})
        status_value = (
            phone_number_status.get("Status", "")
            if isinstance(phone_number_status, dict)
            else ""
        )

        phone_numbers.append(number_summary)

        # Only flag FAILED or IN_PROGRESS status
        if status_value in ("FAILED", "IN_PROGRESS"):
            misconfigured.append(
                {
                    "phone_number": phone_number,
                    "phone_number_country_code": country_code,
                    "phone_number_type": phone_type,
                    "phone_number_status": status_value,
                }
            )
            failed_count += 1

    total_phone_numbers = len(phone_numbers)

    if misconfigured:
        status = "warn"
        detail = f"{len(misconfigured)} number(s) with FAILED/IN_PROGRESS status"
    elif total_phone_numbers > 0:
        status = "pass"
        detail = f"All {total_phone_numbers} numbers have healthy CLAIMED status"
    else:
        status = "info"
        detail = "No phone numbers to analyze"

    return {
        "total_phone_numbers": total_phone_numbers,
        "misconfigured": misconfigured,
        "failed_count": failed_count,
        "status": status,
        "detail": detail,
    }


def lambda_handler(event, context):
    """Operational Excellence Analyzer Lambda handler.

    Validates input using shared validate_input, checks contact flow logging
    hygiene, KVS retention configuration, and misconfigured phone numbers
    with graceful timeout, persists results to S3 Hive-style path, and
    returns a standardized AnalyzerResult.

    Args:
        event (dict): Input event with reviewId, instanceId, instanceArn,
                      accountId, awsRegion, daysBack, componentType,
                      s3ReportingBucket, and optional maxSeconds.
        context: Lambda context object.

    Returns:
        dict: Standardized AnalyzerResult (success or error).
    """
    logger.info("Operational Excellence Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "operational_excellence"),
            str(e),
        )

    instance_id = event["instanceId"]
    account_id = event["accountId"]
    aws_region = event["awsRegion"]
    component_type = event["componentType"]

    # Accept maxSeconds from event payload (passed via analyzerTimeouts from PrepareContext)
    max_seconds = event.get("maxSeconds", DEFAULT_MAX_SECONDS)

    # Compute internal time budget using shared graceful timeout utility
    time_budget = compute_time_budget(max_seconds)

    logger.info(
        f"Analyzing operational excellence for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"max_seconds={max_seconds}, time_budget={time_budget}"
    )

    connect_client = boto3.client("connect", region_name=aws_region)
    checks_completed = []

    try:
        # Check 1: Contact Flow Logging Hygiene
        contact_flow_findings = check_contact_flow_logging(
            connect_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("contact_flow_logging")

        # Check 2: KVS Retention Period
        kvs_retention_findings = check_kvs_retention(
            connect_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("kvs_retention")

        # Check 3: Misconfigured Phone Numbers
        misconfig_phone_findings = check_misconfigured_phone_numbers(
            connect_client, instance_id, execution_start, time_budget, event=event
        )
        checks_completed.append("misconfigured_phone_numbers")

        findings = {
            "contact_flow_logging": contact_flow_findings,
            "kvs_retention": kvs_retention_findings,
            "misconfigured_phone_numbers": misconfig_phone_findings,
            "checks_completed": checks_completed,
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        }

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(event, findings)

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(
            f"Operational excellence analysis complete: "
            f"{len(checks_completed)} checks completed"
        )

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
        )

    except TimeBudgetExceeded:
        logger.info(
            f"Operational Excellence Analyzer reached time budget after completing "
            f"{len(checks_completed)} of 3 checks: {checks_completed}"
        )

        # Build partial findings from whatever checks completed
        findings = {
            "checks_completed": checks_completed,
            "timed_out": True,
            "account_id": account_id,
            "region": aws_region,
        }
        if "contact_flow_logging" in checks_completed:
            findings["contact_flow_logging"] = contact_flow_findings
        if "kvs_retention" in checks_completed:
            findings["kvs_retention"] = kvs_retention_findings
        if "misconfigured_phone_numbers" in checks_completed:
            findings["misconfigured_phone_numbers"] = misconfig_phone_findings

        collected_count = len(checks_completed)
        total_estimated = 3  # Total number of operational excellence checks

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
        logger.error(f"AWS API error in Operational Excellence Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Unexpected error in Operational Excellence Analyzer: {error_msg}"
        )
        return error_result(component_type, error_msg)
