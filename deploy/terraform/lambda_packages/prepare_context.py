# prepare_context.py — PrepareContext Lambda for parallel orchestration
"""
PrepareContext Lambda handler that reads runtime configuration from SSM
Parameter Store, applies safe defaults, and normalizes the input event
into the ExecutionContext schema for downstream analyzer invocations.

This is a Lambda (not a Pass state) because it must perform an SSM API
call at runtime to read customer-configurable operational settings.
"""

import json
import logging
import os
import re
import time

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SSM_PARAM_NAME = os.environ.get("CONFIG_SSM_PARAM", "/connect-ops-review/config")

# Phone number prefetch safety caps (QB-25)
MAX_PREFETCH_PHONE_NUMBERS = 200
MAX_PREFETCH_PHONE_SECONDS = 60

# IaC-deployed defaults (used when SSM param is missing or fields are omitted)
DEFAULT_CONFIG = {
    "instanceArn": os.environ.get("CONNECT_INSTANCE_ARN", ""),
    "s3ReportingBucket": os.environ.get("S3_REPORTING_BUCKET", ""),
    "daysBack": 14,
    "generateHtmlReport": True,
    "retainJsonData": False,
    "enableGlueCatalog": False,
    "analyzers": {
        "security": True,
        "resilience": True,
        "cloudtrail": True,
        "operational_excellence": True,
        "capacity": True,
        "observability": True,
        "cost": True,
        "ai": True,
    },
    "analyzerTimeouts": {
        "security": 240,
        "resilience": 240,
        "cloudtrail": 840,
        "operational_excellence": 240,
        "capacity": 540,
        "observability": 540,
        "cost": 240,
        "ai": 540,
    },
}

# Hard ceiling for all analyzer timeouts: Lambda timeout (900s) - safety margin (60s) = 840s.
# Any customer-configured analyzerTimeouts value exceeding this is capped.
HARD_CEILING = 840

# Set of recognized analyzer names for validation.
# Entries not in this set are discarded by _cap_analyzer_timeouts().
KNOWN_ANALYZERS = {
    "security",
    "resilience",
    "cloudtrail",
    "operational_excellence",
    "capacity",
    "observability",
    "cost",
    "ai",
}

# Regex for parsing Connect instance ARN components
_ARN_PATTERN = re.compile(
    r"^arn:aws:connect:(?P<region>[^:]+):(?P<account>\d{12}):instance/(?P<instance_id>.+)$"
)


def lambda_handler(event, context):
    """PrepareContext Lambda handler.

    Reads runtime configuration from SSM Parameter Store, merges with
    IaC-deployed defaults, and normalizes the input event into the full
    ExecutionContext schema.

    Args:
        event: Input event from Step Functions. Contains:
            - rawInput (dict): The original execution input (may be {} for
              default behavior). Supports optional overrides:
              instanceArn, instanceId, accountId, awsRegion, daysBack,
              s3ReportingBucket.
            - executionStartTime, executionName (from Step Functions context)
        context: Lambda context object.

    Returns:
        ExecutionContext dict with all fields resolved and validated.
    """
    logger.info("PrepareContext invoked")

    # Read runtime config from SSM (falls back to defaults on any failure)
    config = _read_runtime_config()

    # Unwrap rawInput if present (Step Functions wraps execution input under rawInput key)
    raw_input = event.get("rawInput", {}) or {}

    # Build reviewId from execution metadata
    execution_start_time = event.get("executionStartTime", "")
    execution_name = event.get("executionName", "")
    review_id = f"{execution_start_time}-{execution_name}"

    # Resolve instanceArn with priority: rawInput > SSM > defaults
    instance_arn = (
        raw_input.get("instanceArn")
        or config.get("instanceArn")
        or DEFAULT_CONFIG["instanceArn"]
    )

    # Resolve s3ReportingBucket with priority: rawInput > SSM > defaults
    s3_bucket = (
        raw_input.get("s3ReportingBucket")
        or config.get("s3ReportingBucket")
        or DEFAULT_CONFIG["s3ReportingBucket"]
    )

    # Resolve daysBack with priority: rawInput > SSM > defaults
    days_back = (
        raw_input.get("daysBack")
        or config.get("daysBack")
        or DEFAULT_CONFIG["daysBack"]
    )

    # Build enabledAnalyzers map from config
    enabled_analyzers = config.get("analyzers", DEFAULT_CONFIG["analyzers"])

    # Resolve generateHtmlReport with priority: rawInput > SSM config > default (true)
    generate_html_report = raw_input.get(
        "generateHtmlReport",
        config.get("generateHtmlReport", DEFAULT_CONFIG["generateHtmlReport"]),
    )

    # Resolve retainJsonData with priority: rawInput > SSM config > default (false)
    retain_json_data = raw_input.get(
        "retainJsonData", config.get("retainJsonData", DEFAULT_CONFIG["retainJsonData"])
    )

    # Resolve enableGlueCatalog with priority: rawInput > SSM config > default (false)
    enable_glue_catalog = raw_input.get(
        "enableGlueCatalog",
        config.get("enableGlueCatalog", DEFAULT_CONFIG["enableGlueCatalog"]),
    )

    # Runtime enforcement of D1 / D2 (see docs/parameter-dependencies.md).
    # D1: enableGlueCatalog=true requires retainJsonData=true — otherwise DeleteJsonData
    #     empties the S3 prefixes that back Glue tables on every run.
    # D2: generateHtmlReport=false requires retainJsonData=true — otherwise the run
    #     produces no output at all.
    # Coercion is idempotent and additive: both conditions can fire on the same
    # invocation; the final value is retainJsonData=True.
    if enable_glue_catalog and not retain_json_data:
        logger.info(
            "Forcing retainJsonData=true because enableGlueCatalog=true (D1). "
            "Original request would have deleted Glue-backing data."
        )
        retain_json_data = True
    if not generate_html_report and not retain_json_data:
        logger.info(
            "Forcing retainJsonData=true because generateHtmlReport=false (D2). "
            "Original request would have produced no output."
        )
        retain_json_data = True

    # Cap analyzerTimeouts at hard ceilings
    raw_timeouts = config.get("analyzerTimeouts", DEFAULT_CONFIG["analyzerTimeouts"])
    analyzer_timeouts = _cap_analyzer_timeouts(raw_timeouts)

    # Parse instance components from ARN
    instance_id = raw_input.get("instanceId") or _parse_instance_id(instance_arn)
    account_id = raw_input.get("accountId") or _parse_account_id(instance_arn)
    aws_region = raw_input.get("awsRegion") or _parse_region(instance_arn)

    execution_context = {
        "reviewId": review_id,
        "instanceArn": instance_arn,
        "instanceId": instance_id,
        "accountId": account_id,
        "awsRegion": aws_region,
        "daysBack": days_back,
        "s3ReportingBucket": s3_bucket,
        "generateHtmlReport": generate_html_report,
        "retainJsonData": retain_json_data,
        "enabledAnalyzers": enabled_analyzers,
        "analyzerTimeouts": analyzer_timeouts,
    }

    # Pre-fetch shared data for downstream analyzers
    shared_data = _prefetch_shared_data(
        instance_id=instance_id,
        instance_arn=instance_arn,
        aws_region=aws_region,
        s3_bucket=s3_bucket,
        review_id=review_id,
    )
    if shared_data:
        execution_context["sharedData"] = shared_data

    logger.info(
        "ExecutionContext built: reviewId=%s, instanceId=%s, region=%s, "
        "enabledAnalyzers=%s, generateHtmlReport=%s, retainJsonData=%s",
        review_id,
        instance_id,
        aws_region,
        enabled_analyzers,
        generate_html_report,
        retain_json_data,
    )

    return execution_context


def _prefetch_shared_data(instance_id, instance_arn, aws_region, s3_bucket, review_id):
    """Pre-fetch shared data used by multiple analyzers and persist to S3.

    Fetches instance data, phone numbers, storage configs, and integration
    associations once and writes them to S3 under data/shared/{reviewId}/.
    Analyzers can then read from S3 instead of making duplicate API calls.

    Phone number prefetch is capped at MAX_PREFETCH_PHONE_NUMBERS and
    MAX_PREFETCH_PHONE_SECONDS to prevent runaway API calls on large
    instances (QB-25).

    Args:
        instance_id: Connect instance ID.
        instance_arn: Connect instance ARN.
        aws_region: AWS region.
        s3_bucket: S3 bucket for reports.
        review_id: Unique review execution ID.

    Returns:
        dict: S3 keys for each shared data file, or empty dict on total failure.
    """
    if not s3_bucket:
        logger.warning("No s3ReportingBucket — skipping shared data pre-fetch")
        return {}

    shared_data = {}
    s3_client = boto3.client("s3")
    connect_client = boto3.client("connect", region_name=aws_region)
    prefix = f"data/shared/{review_id}"

    # 1. DescribeInstance
    step_start = time.time()
    try:
        response = connect_client.describe_instance(InstanceId=instance_id)
        instance_data = response.get("Instance", {})
        key = f"{prefix}/instance_data.json"
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(instance_data, default=str),
            ContentType="application/json",
        )
        shared_data["instanceData"] = key
        logger.info(
            "Pre-fetched instance_data in %.1fs",
            time.time() - step_start,
        )
    except Exception as e:
        logger.warning("Failed to pre-fetch instance_data: %s", str(e))

    # 2. ListPhoneNumbersV2 + DescribePhoneNumber for each
    step_start = time.time()
    try:
        phone_numbers = []
        capped = False
        paginator = connect_client.get_paginator("list_phone_numbers_v2")
        for page in paginator.paginate(InstanceId=instance_id):
            for summary in page.get("ListPhoneNumbersSummaryList", []):
                # QB-25: enforce count and time caps
                if len(phone_numbers) >= MAX_PREFETCH_PHONE_NUMBERS:
                    capped = True
                    break
                if time.time() - step_start > MAX_PREFETCH_PHONE_SECONDS:
                    capped = True
                    break

                phone_number_id = summary.get("PhoneNumberId", "")
                # Describe each phone number for TargetArn and status
                if phone_number_id:
                    try:
                        detail = connect_client.describe_phone_number(
                            PhoneNumberId=phone_number_id
                        )
                        claimed = detail.get("ClaimedPhoneNumberSummary", {})
                        summary["TargetArn"] = claimed.get("TargetArn", "")
                        summary["PhoneNumberStatus"] = claimed.get(
                            "PhoneNumberStatus", {}
                        )
                    except Exception as desc_err:
                        logger.debug(
                            "DescribePhoneNumber failed for %s: %s",
                            phone_number_id,
                            str(desc_err),
                        )
                phone_numbers.append(summary)
            if capped:
                break

        if capped:
            logger.warning(
                "Phone number prefetch capped at %d numbers / %.0fs "
                "(MAX_PREFETCH_PHONE_NUMBERS=%d, MAX_PREFETCH_PHONE_SECONDS=%d)",
                len(phone_numbers),
                time.time() - step_start,
                MAX_PREFETCH_PHONE_NUMBERS,
                MAX_PREFETCH_PHONE_SECONDS,
            )

        key = f"{prefix}/phone_numbers.json"
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(phone_numbers, default=str),
            ContentType="application/json",
        )
        shared_data["phoneNumbers"] = key
        logger.info(
            "Pre-fetched phone_numbers (%d numbers) in %.1fs",
            len(phone_numbers),
            time.time() - step_start,
        )
    except Exception as e:
        logger.warning("Failed to pre-fetch phone_numbers: %s", str(e))

    # 3. ListInstanceStorageConfigs (all resource types)
    step_start = time.time()
    try:
        resource_types = [
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
        all_storage_configs = {}
        for rtype in resource_types:
            configs = []
            kwargs = {"InstanceId": instance_id, "ResourceType": rtype}
            while True:
                resp = connect_client.list_instance_storage_configs(**kwargs)
                configs.extend(resp.get("StorageConfigs", []))
                token = resp.get("NextToken")
                if not token:
                    break
                kwargs["NextToken"] = token
            all_storage_configs[rtype] = configs

        key = f"{prefix}/storage_configs.json"
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(all_storage_configs, default=str),
            ContentType="application/json",
        )
        shared_data["storageConfigs"] = key
        logger.info(
            "Pre-fetched storage_configs (%d resource types) in %.1fs",
            len(resource_types),
            time.time() - step_start,
        )
    except Exception as e:
        logger.warning("Failed to pre-fetch storage_configs: %s", str(e))

    # 4. ListIntegrationAssociations (all integration types)
    step_start = time.time()
    try:
        integration_types = [
            "EVENT",
            "VOICE_ID",
            "PINPOINT_APP",
            "WISDOM_ASSISTANT",
            "WISDOM_KNOWLEDGE_BASE",
            "WISDOM_QUICK_RESPONSES",
            "CASES_DOMAIN",
            "APPLICATION",
        ]
        all_integrations = {}
        for itype in integration_types:
            try:
                associations = []
                kwargs = {
                    "InstanceId": instance_id,
                    "IntegrationType": itype,
                }
                while True:
                    resp = connect_client.list_integration_associations(**kwargs)
                    associations.extend(
                        resp.get("IntegrationAssociationSummaryList", [])
                    )
                    token = resp.get("NextToken")
                    if not token:
                        break
                    kwargs["NextToken"] = token
                all_integrations[itype] = associations
            except Exception as itype_err:
                logger.debug(
                    "ListIntegrationAssociations failed for %s: %s",
                    itype,
                    str(itype_err),
                )
                all_integrations[itype] = []

        key = f"{prefix}/integration_associations.json"
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(all_integrations, default=str),
            ContentType="application/json",
        )
        shared_data["integrationAssociations"] = key
        logger.info(
            "Pre-fetched integration_associations (%d types) in %.1fs",
            len(integration_types),
            time.time() - step_start,
        )
    except Exception as e:
        logger.warning("Failed to pre-fetch integration_associations: %s", str(e))

    return shared_data


def _read_runtime_config() -> dict:
    """Read operational config from SSM Parameter Store.

    Falls back to DEFAULT_CONFIG on any failure (missing parameter,
    invalid JSON, permission error, network issue).

    Returns:
        Parsed configuration dict, or DEFAULT_CONFIG on failure.
    """
    try:
        ssm = boto3.client("ssm")
        response = ssm.get_parameter(Name=SSM_PARAM_NAME)
        value = response["Parameter"]["Value"]
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            logger.warning(
                "SSM parameter %s is not a JSON object, using defaults",
                SSM_PARAM_NAME,
            )
            return DEFAULT_CONFIG
        logger.info("Successfully read runtime config from SSM: %s", SSM_PARAM_NAME)
        return parsed
    except Exception as e:
        logger.warning(
            "Failed to read SSM parameter %s: %s — using defaults",
            SSM_PARAM_NAME,
            str(e),
        )
        return DEFAULT_CONFIG


def _cap_analyzer_timeouts(raw_timeouts: dict) -> dict:
    """Cap analyzer timeout values at the uniform HARD_CEILING.

    Iterates over the input dict, discards entries whose key is not in
    KNOWN_ANALYZERS (with a warning), and caps any value exceeding
    HARD_CEILING to 840 seconds.

    Args:
        raw_timeouts: Dict of analyzer name to timeout seconds.

    Returns:
        Dict with recognized analyzers only, all values ≤ HARD_CEILING.
    """
    capped_timeouts = {}
    for name, budget in raw_timeouts.items():
        if name not in KNOWN_ANALYZERS:
            logger.warning(
                "analyzerTimeouts contains unrecognized analyzer '%s', discarding",
                name,
            )
            continue
        if budget > HARD_CEILING:
            logger.warning(
                "analyzerTimeouts.%s=%d exceeds ceiling %d, capping",
                name,
                budget,
                HARD_CEILING,
            )
            capped_timeouts[name] = HARD_CEILING
        else:
            capped_timeouts[name] = budget
    return capped_timeouts


def _parse_instance_id(instance_arn: str) -> str:
    """Extract the instance ID from a Connect instance ARN.

    Args:
        instance_arn: Full ARN like
            arn:aws:connect:us-east-1:123456789012:instance/abc-123

    Returns:
        The instance ID portion, or empty string if ARN is invalid.
    """
    match = _ARN_PATTERN.match(instance_arn)
    if match:
        return match.group("instance_id")
    logger.warning("Could not parse instanceId from ARN: %s", instance_arn)
    return ""


def _parse_account_id(instance_arn: str) -> str:
    """Extract the AWS account ID from a Connect instance ARN.

    Args:
        instance_arn: Full ARN like
            arn:aws:connect:us-east-1:123456789012:instance/abc-123

    Returns:
        The 12-digit account ID, or empty string if ARN is invalid.
    """
    match = _ARN_PATTERN.match(instance_arn)
    if match:
        return match.group("account")
    logger.warning("Could not parse accountId from ARN: %s", instance_arn)
    return ""


def _parse_region(instance_arn: str) -> str:
    """Extract the AWS region from a Connect instance ARN.

    Args:
        instance_arn: Full ARN like
            arn:aws:connect:us-east-1:123456789012:instance/abc-123

    Returns:
        The region string, or empty string if ARN is invalid.
    """
    match = _ARN_PATTERN.match(instance_arn)
    if match:
        return match.group("region")
    logger.warning("Could not parse awsRegion from ARN: %s", instance_arn)
    return ""
