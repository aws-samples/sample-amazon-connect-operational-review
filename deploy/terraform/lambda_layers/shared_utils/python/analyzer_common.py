# analyzer_common.py — shared across all analyzer Lambdas via Lambda Layer
"""
Shared utility module for all analyzer Lambda functions in the parallel
orchestration architecture. Provides input validation, standardized result
formatting, and S3 persistence with Hive-style partitioning.
"""

import json
import logging
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Required fields in every analyzer invocation payload
REQUIRED_FIELDS = [
    "reviewId",
    "instanceId",
    "instanceArn",
    "accountId",
    "awsRegion",
    "daysBack",
    "componentType",
]


def validate_input(event: dict) -> None:
    """Validate that all required fields are present in the event payload.

    Raises:
        ValueError: If one or more required fields are missing from the event.
    """
    missing = [f for f in REQUIRED_FIELDS if f not in event]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")


def success_result(
    component_type: str,
    findings: dict,
    duration_ms: int = 0,
    s3_key: str = "",
    partial: bool = False,
    collected_count: int = None,
    total_estimated: int = None,
) -> dict:
    """Build a standardized success response for an analyzer.

    Args:
        component_type: The analyzer's componentType identifier.
        findings: The analyzer-specific structured findings data.
        duration_ms: Execution duration in milliseconds.
        s3_key: The S3 key where results were persisted.
        partial: Whether the results are incomplete due to time budget.
        collected_count: Number of items collected before stopping (partial only).
        total_estimated: Estimated total items if known (partial only).

    Returns:
        A standardized AnalyzerResult dict with status "success".
    """
    result = {
        "componentType": component_type,
        "status": "success",
        "findings": findings,
        "durationMs": duration_ms,
        "s3ResultKey": s3_key,
    }
    if partial:
        result["partial"] = True
        result["collectedCount"] = collected_count
        result["totalEstimated"] = total_estimated
        result["timeoutReason"] = "time_budget_exceeded"
    return result


def error_result(component_type: str, error: str) -> dict:
    """Build a standardized error response for an analyzer.

    Args:
        component_type: The analyzer's componentType identifier.
        error: Error description (truncated to 1024 characters max).

    Returns:
        A standardized AnalyzerResult dict with status "error".
    """
    return {
        "componentType": component_type,
        "status": "error",
        "error": error[:1024],
    }


def read_shared_data(event: dict, key: str):
    """Read pre-fetched shared data from S3.

    Looks up the S3 key from event['sharedData'][key], reads the object,
    and returns parsed JSON. Returns None if shared data is not available
    or if any error occurs (callers should fall back to direct API calls).

    Args:
        event: The analyzer event payload (may contain 'sharedData' dict).
        key: The shared data key name (e.g., 'phoneNumbers', 'instanceData',
             'storageConfigs', 'integrationAssociations').

    Returns:
        Parsed JSON data, or None if unavailable or on error.
    """
    s3_key = event.get("sharedData", {}).get(key)
    if not s3_key:
        return None

    bucket = event.get("s3ReportingBucket")
    if not bucket:
        return None

    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        body = response["Body"].read().decode("utf-8")
        data = json.loads(body)
        logger.info("Read shared data '%s' from s3://%s/%s", key, bucket, s3_key)
        return data
    except Exception as e:
        logger.warning(
            "Failed to read shared data '%s' from S3: %s — falling back to API",
            key,
            str(e),
        )
        return None


def persist_to_s3(
    event: dict,
    findings: dict,
    partial: bool = False,
    collected_count: int = None,
    total_estimated: int = None,
) -> str:
    """Persist findings to S3 using Hive-style partitioned path.

    Writes to: data/{componentType}/year=YYYY/month=MM/day=DD/{reviewId}.json

    The output includes a metadata envelope for Athena queryability.
    When partial=True, includes partial collection metadata for downstream
    filtering.

    Args:
        event: The full analyzer event payload (must contain s3ReportingBucket,
               componentType, reviewId, instanceId, accountId, awsRegion, daysBack).
        findings: The analyzer-specific structured findings data.
        partial: Whether the results are incomplete due to time budget.
        collected_count: Number of items collected before stopping (partial only).
        total_estimated: Estimated total items if known (partial only).

    Returns:
        The S3 key where the document was written, or empty string if no bucket
        is configured.
    """
    bucket = event.get("s3ReportingBucket")
    if not bucket:
        logger.warning("No s3ReportingBucket configured — skipping S3 persistence")
        return ""

    now = datetime.now(timezone.utc)
    component_type = event["componentType"]
    review_id = event["reviewId"]

    key = (
        f"data/{component_type}"
        f"/year={now.year:04d}"
        f"/month={now.month:02d}"
        f"/day={now.day:02d}"
        f"/{review_id}.json"
    )

    document = {
        "reviewId": review_id,
        "instanceId": event["instanceId"],
        "accountId": event["accountId"],
        "awsRegion": event["awsRegion"],
        "componentType": component_type,
        "timestamp": now.isoformat(),
        "daysBack": event["daysBack"],
        "partial": partial,
        "findings": findings,
    }

    if partial:
        document["collectedCount"] = collected_count
        document["totalEstimated"] = total_estimated

    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(document),
            ContentType="application/json",
        )
        logger.info(
            "Persisted %s results to s3://%s/%s (partial=%s)",
            component_type,
            bucket,
            key,
            partial,
        )
    except Exception as e:
        logger.error("Failed to persist results to S3: %s", str(e))
        raise

    return key


# ---------------------------------------------------------------------------
# QB-1: Shared discover_assistant_id utility
# ---------------------------------------------------------------------------


def discover_assistant_id(
    connect_client, instance_id, start_time=None, time_budget=None, event=None
):
    """Discover the Amazon Q in Connect assistant ID via integration associations.

    Consolidated implementation that handles:
    - Shared data lookup (avoids redundant API calls when pre-fetched data available)
    - Direct API fallback via list_integration_associations
    - ARN parsing for both '/assistant/' format and generic '/' suffix format
    - Optional time budget checking between API calls

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float, optional): Epoch timestamp when execution began.
            If provided with time_budget, checks budget before API call.
        time_budget (int, optional): Allowed time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.
            If present and contains 'integrationAssociations' shared data,
            avoids the API call entirely.

    Returns:
        str or None: The assistant ID if found, else None.

    Raises:
        TimeBudgetExceeded: If time budget is exceeded (only when start_time
            and time_budget are provided).
    """
    # Import here to avoid circular dependency at module load time
    from graceful_timeout import TimeBudgetExceeded, check_time_budget as _check_budget

    # Try shared data first (avoids redundant API calls)
    if event:
        shared_integrations = read_shared_data(event, "integrationAssociations")
        if shared_integrations:
            associations = shared_integrations.get("WISDOM_ASSISTANT", [])
            if associations:
                integration_arn = associations[0].get("IntegrationArn", "")
                assistant_id = _parse_assistant_id_from_arn(integration_arn)
                if assistant_id:
                    logger.info(
                        "Discovered assistant ID from shared data: %s", assistant_id
                    )
                    return assistant_id
            # Shared data present but no WISDOM_ASSISTANT found
            logger.info(
                "Shared data available but no WISDOM_ASSISTANT integration found"
            )
            return None

    # Fall back to direct API call
    try:
        if start_time is not None and time_budget is not None:
            _check_budget(start_time, time_budget)
        response = connect_client.list_integration_associations(
            InstanceId=instance_id,
            IntegrationType="WISDOM_ASSISTANT",
        )
        associations = response.get("IntegrationAssociationSummaryList", [])
        if associations:
            integration_arn = associations[0].get("IntegrationArn", "")
            assistant_id = _parse_assistant_id_from_arn(integration_arn)
            if assistant_id:
                logger.info("Discovered assistant ID via API: %s", assistant_id)
                return assistant_id
        return None
    except TimeBudgetExceeded:
        raise
    except ClientError as e:
        logger.warning("Could not discover assistant ID (ClientError): %s", e)
        return None
    except Exception as e:
        logger.warning("Could not discover assistant ID: %s", e)
        return None


def _parse_assistant_id_from_arn(integration_arn):
    """Parse assistant ID from an integration ARN.

    Handles ARN formats:
    - arn:aws:wisdom:region:account:assistant/assistant-id
    - Any ARN with '/assistant/' segment
    - Fallback: last segment after '/'

    Args:
        integration_arn (str): The integration ARN string.

    Returns:
        str or None: The extracted assistant ID, or None if empty/unparseable.
    """
    if not integration_arn:
        return None
    if "/assistant/" in integration_arn:
        return integration_arn.split("/assistant/")[-1] or None
    elif "/" in integration_arn:
        result = integration_arn.split("/")[-1]
        return result or None
    return None


# ---------------------------------------------------------------------------
# QB-2: Shared pagination utilities
# ---------------------------------------------------------------------------


def paginate_api_call(
    client_method, result_key, start_time=None, time_budget=None, **kwargs
):
    """Paginate any AWS API call that uses 'NextToken'/'nextToken' pattern.

    General-purpose pagination helper for Connect and other AWS APIs.
    Re-raises TimeBudgetExceeded to allow graceful timeout handling.
    Optionally checks time budget between pages when start_time and time_budget
    are provided.

    Args:
        client_method: Boto3 client method to call.
        result_key (str): Key in response containing the items list.
        start_time (float, optional): Epoch timestamp when execution began.
            If provided with time_budget, checks budget between pages.
        time_budget (int, optional): Allowed time budget in seconds.
        **kwargs: Additional arguments to pass to the API call.

    Returns:
        list: All items collected across pages.

    Raises:
        TimeBudgetExceeded: Re-raised if time budget is exceeded during pagination.
    """
    # Import here to avoid circular dependency at module load time
    from graceful_timeout import TimeBudgetExceeded, check_time_budget as _check_budget

    all_items = []
    try:
        if start_time is not None and time_budget is not None:
            _check_budget(start_time, time_budget)
        response = client_method(**kwargs)
        all_items.extend(response.get(result_key, []))

        # Handle both 'NextToken' (Connect) and 'nextToken' (QConnect) patterns
        next_token = response.get("NextToken") or response.get("nextToken")
        while next_token:
            if start_time is not None and time_budget is not None:
                _check_budget(start_time, time_budget)
            # Try both token key formats
            if "NextToken" in response:
                kwargs["NextToken"] = next_token
            else:
                kwargs["nextToken"] = next_token
            response = client_method(**kwargs)
            all_items.extend(response.get(result_key, []))
            next_token = response.get("NextToken") or response.get("nextToken")
    except TimeBudgetExceeded:
        raise
    except ClientError as e:
        logger.error("ClientError paginating %s: %s", result_key, e)
    except Exception as e:
        logger.error("Unexpected error paginating %s: %s", result_key, e)
    return all_items


def paginate_qconnect(
    client_method, result_key, start_time=None, time_budget=None, **kwargs
):
    """Paginate a QConnect list API call.

    QConnect APIs use 'nextToken' (camelCase) for pagination tokens.
    This helper handles that specific format and re-raises TimeBudgetExceeded.
    Optionally checks time budget between pages when start_time and time_budget
    are provided.

    Args:
        client_method: Boto3 QConnect client method to call.
        result_key (str): Key in response containing the items list.
        start_time (float, optional): Epoch timestamp when execution began.
            If provided with time_budget, checks budget between pages.
        time_budget (int, optional): Allowed time budget in seconds.
        **kwargs: Additional arguments to pass to the API call.

    Returns:
        list: All items collected across pages.

    Raises:
        TimeBudgetExceeded: Re-raised if time budget is exceeded during pagination.
    """
    # Import here to avoid circular dependency at module load time
    from graceful_timeout import TimeBudgetExceeded, check_time_budget as _check_budget

    all_items = []
    try:
        if start_time is not None and time_budget is not None:
            _check_budget(start_time, time_budget)
        response = client_method(**kwargs)
        all_items.extend(response.get(result_key, []))
        while response.get("nextToken"):
            if start_time is not None and time_budget is not None:
                _check_budget(start_time, time_budget)
            kwargs["nextToken"] = response["nextToken"]
            response = client_method(**kwargs)
            all_items.extend(response.get(result_key, []))
    except TimeBudgetExceeded:
        raise
    except ClientError as e:
        logger.error("ClientError paginating %s: %s", result_key, e)
    except Exception as e:
        logger.error("Unexpected error paginating %s: %s", result_key, e)
    return all_items


# ---------------------------------------------------------------------------
# QB-4: Shared phone number fetching
# ---------------------------------------------------------------------------


def fetch_phone_numbers(
    connect_client, instance_id, start_time, time_budget, event=None
):
    """Fetch all phone numbers from shared data or directly via Connect API.

    Shared utility to avoid duplicating the phone number fetching pattern
    across cost, opex, and resilience analyzers.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        list: List of phone number summary dicts with keys: PhoneNumber,
              PhoneNumberType, PhoneNumberCountryCode, TargetArn,
              PhoneNumberStatus, PhoneNumberId. Returns empty list on error.
    """
    from graceful_timeout import check_time_budget as _check_budget

    # Try shared data first
    shared_phone_data = read_shared_data(event, "phoneNumbers") if event else None
    if shared_phone_data:
        logger.info(
            "Using pre-fetched phone numbers (%d numbers)", len(shared_phone_data)
        )
        return shared_phone_data

    # Fall back to paginator
    try:
        all_numbers = []
        paginator = connect_client.get_paginator("list_phone_numbers_v2")
        for page in paginator.paginate(InstanceId=instance_id):
            _check_budget(start_time, time_budget)
            for number_summary in page.get("ListPhoneNumbersSummaryList", []):
                all_numbers.append(number_summary)
        return all_numbers
    except ClientError as e:
        logger.warning("Error listing phone numbers: %s", e)
        return []


# ---------------------------------------------------------------------------
# QB-5: Shared storage config fetching
# ---------------------------------------------------------------------------

import time as _time

# Standard Connect storage resource types
STORAGE_RESOURCE_TYPES = [
    "CHAT_TRANSCRIPTS",
    "CALL_RECORDINGS",
    "SCHEDULED_REPORTS",
    "MEDIA_STREAMS",
    "CONTACT_TRACE_RECORDS",
    "AGENT_EVENTS",
    "REAL_TIME_CONTACT_ANALYSIS_SEGMENTS",
    "ATTACHMENTS",
    "CONTACT_EVALUATIONS",
    "SCREEN_RECORDINGS",
    "REAL_TIME_CONTACT_ANALYSIS_CHAT_SEGMENTS",
    "REAL_TIME_CONTACT_ANALYSIS_VOICE_SEGMENTS",
    "EMAIL_MESSAGES",
]


def fetch_storage_configs(connect_client, instance_id, start_time, time_budget):
    """Fetch all storage configurations across all resource types.

    Shared utility to avoid duplicating the storage config iteration pattern
    across security and opex analyzers.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        list: List of (resource_type, storage_config) tuples where each
              storage_config is the raw dict from the API response.
    """
    from graceful_timeout import check_time_budget as _check_budget

    all_configs = []

    for rtype in STORAGE_RESOURCE_TYPES:
        _check_budget(start_time, time_budget)
        try:
            _time.sleep(0.5)  # Rate limiting
            kwargs = {"InstanceId": instance_id, "ResourceType": rtype}
            while True:
                resp = connect_client.list_instance_storage_configs(**kwargs)
                for cfg in resp.get("StorageConfigs", []):
                    all_configs.append((rtype, cfg))
                token = resp.get("NextToken")
                if not token:
                    break
                kwargs["NextToken"] = token
        except ClientError as e:
            logger.warning("Could not fetch storage config for %s: %s", rtype, e)

    return all_configs
