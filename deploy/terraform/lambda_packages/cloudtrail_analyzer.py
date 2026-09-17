# CloudTrail Analyzer Lambda Function
#
# Standalone Lambda that looks up CloudTrail events for Amazon Connect,
# analyzes API throttling, and returns structured JSON results.
# Extracted from the monolithic lambda_function.py as part of the
# split-architecture prototype.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import json
import logging
import time
from botocore.exceptions import ClientError
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from analyzer_common import validate_input, success_result, error_result, persist_to_s3
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 120


def lookup_connect_cloudtrail_events(
    account_id, days_back, aws_region, time_budget, start_time, cloudtrail_client
):
    """
    Lookup CloudTrail events for Amazon Connect with a time budget.
    Returns whatever events have been collected when the time budget expires.

    Args:
        account_id (str): AWS account ID
        days_back (int): Days back for event lookup
        aws_region (str): AWS region
        time_budget (int): Time budget in seconds (from compute_time_budget)
        start_time (float): Epoch timestamp when execution began
        cloudtrail_client: Boto3 CloudTrail client (region-specific)

    Returns:
        tuple: (connect_events list, timed_out bool, total_estimated int or None)
    """
    end_time = datetime.now(timezone.utc)
    lookup_start_time = end_time - timedelta(hours=24 * days_back)

    logger.debug(
        f"Start Time for lookup_connect_cloudtrail_events: {lookup_start_time}"
    )
    logger.debug(f"End Time for lookup_connect_cloudtrail_events: {end_time}")
    logger.info(f"CloudTrail lookup time budget: {time_budget}s")

    connect_events = []
    timed_out = False
    # CloudTrail lookup_events does not expose a total event count;
    # kept as None for protocol consistency with persist_to_s3/success_result.
    total_estimated = None
    pages_processed = 0

    try:
        lookup_attributes = [
            {"AttributeKey": "EventSource", "AttributeValue": "connect.amazonaws.com"}
        ]

        paginator = cloudtrail_client.get_paginator("lookup_events")

        for page in paginator.paginate(
            LookupAttributes=lookup_attributes,
            StartTime=lookup_start_time,
            EndTime=end_time,
        ):
            # Check time budget at the start of each page
            try:
                check_time_budget(start_time, time_budget)
            except TimeBudgetExceeded:
                timed_out = True
                logger.info(
                    f"CloudTrail lookup reached time budget "
                    f"after {pages_processed} pages, "
                    f"{len(connect_events)} events collected"
                )
                break

            pages_processed += 1
            for event in page["Events"]:
                event_data = parse_connect_event(event)
                if event_data:
                    connect_events.append(event_data)

        return connect_events, timed_out, total_estimated

    except ClientError as e:
        logger.debug(f"Error looking up CloudTrail events: {e}")
        raise


def parse_connect_event(event):
    """Parse and enrich a Connect CloudTrail event.

    Args:
        event (dict): Raw CloudTrail event dictionary.

    Returns:
        dict or None: Parsed event data, or None if parsing fails.
    """
    try:
        cloud_trail_event = json.loads(event.get("CloudTrailEvent", "{}"))

        event_data = {
            "eventId": event.get("EventId"),
            "eventName": event.get("EventName"),
            "eventTime": event.get("EventTime"),
            "username": event.get("Username"),
            "eventSource": cloud_trail_event.get("eventSource"),
            "awsRegion": cloud_trail_event.get("awsRegion"),
            "sourceIPAddress": cloud_trail_event.get("sourceIPAddress"),
            "userAgent": cloud_trail_event.get("userAgent"),
            "requestId": cloud_trail_event.get("requestID"),
            "errorCode": cloud_trail_event.get("errorCode"),
            "errorMessage": cloud_trail_event.get("errorMessage"),
        }

        return event_data

    except Exception as e:
        logger.warning(f"Error parsing event: {e}")
        return None


def analyze_api_throttles(events, aws_region):
    """Analyze parsed CloudTrail events for API throttling in the target region.

    Args:
        events (list): List of parsed event dicts from parse_connect_event().
        aws_region (str): Target AWS region to filter throttle events.

    Returns:
        dict: Analysis results with total_throttled, throttled_by_api list
              sorted descending by count.
    """
    category_counts = defaultdict(int)
    total_throttled = 0

    for item in events:
        if (
            item.get("errorCode") == "TooManyRequestsException"
            and item.get("awsRegion") == aws_region
        ):
            category = item.get("eventName")
            category_counts[category] += 1
            total_throttled += 1

    # Sort descending by count
    throttled_by_api = [
        {"event_name": name, "count": count}
        for name, count in sorted(
            category_counts.items(), key=lambda x: x[1], reverse=True
        )
    ]

    return {
        "total_throttled": total_throttled,
        "throttled_by_api": throttled_by_api,
    }


def lambda_handler(event, context):
    """CloudTrail Analyzer Lambda handler.

    Validates input using shared validate_input, looks up CloudTrail events
    for Amazon Connect with graceful timeout, analyzes API throttling,
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
    logger.info("CloudTrail Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "cloudtrail"),
            str(e),
        )

    instance_id = event["instanceId"]
    account_id = event["accountId"]
    aws_region = event["awsRegion"]
    days_back = event["daysBack"]
    component_type = event["componentType"]

    # Accept maxSeconds from event payload (passed via analyzerTimeouts from PrepareContext)
    # Defaults to DEFAULT_MAX_SECONDS (120) if not provided
    max_seconds = event.get("maxSeconds", DEFAULT_MAX_SECONDS)

    # Compute internal time budget using shared graceful timeout utility
    time_budget = compute_time_budget(max_seconds)

    logger.info(
        f"Analyzing CloudTrail for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"days_back={days_back}, max_seconds={max_seconds}, "
        f"time_budget={time_budget}"
    )

    try:
        # Create CloudTrail client with the correct region from event payload (QB-10)
        cloudtrail_client = boto3.client("cloudtrail", region_name=aws_region)

        events, timed_out, total_estimated = lookup_connect_cloudtrail_events(
            account_id,
            days_back,
            aws_region,
            time_budget=time_budget,
            start_time=execution_start,
            cloudtrail_client=cloudtrail_client,
        )

        throttle_analysis = analyze_api_throttles(events, aws_region)

        findings = {
            "total_events_analyzed": len(events),
            "total_throttled": throttle_analysis["total_throttled"],
            "throttled_by_api": throttle_analysis["throttled_by_api"],
            "timed_out": timed_out,
            "max_seconds": max_seconds,
            "account_id": account_id,
            "region": aws_region,
            "days_back": days_back,
        }

        # Determine if this is a partial result
        partial = timed_out
        collected_count = len(events) if partial else None
        # total_estimated comes from pagination if available
        partial_total_estimated = total_estimated if partial else None

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(
            event,
            findings,
            partial=partial,
            collected_count=collected_count,
            total_estimated=partial_total_estimated,
        )

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(
            f"Analysis complete: {findings['total_events_analyzed']} events, "
            f"{findings['total_throttled']} throttled, timed_out={timed_out}"
        )

        # Return standardized AnalyzerResult with partial support
        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
            partial=partial,
            collected_count=collected_count,
            total_estimated=partial_total_estimated,
        )

    except ClientError as e:
        error_msg = str(e)
        logger.error(f"CloudTrail API error: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in CloudTrail Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
