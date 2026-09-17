# Security Analyzer Lambda Function
#
# Standalone Lambda that performs security analysis for Amazon Connect instances,
# checking identity management, S3 encryption, streaming encryption, and AI guardrails.
# Extracted from the monolithic lambda_function.py as part of the
# split-architecture prototype.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import logging
import time
from botocore.exceptions import ClientError

from analyzer_common import (
    validate_input,
    success_result,
    error_result,
    persist_to_s3,
    read_shared_data,
    fetch_storage_configs,
    STORAGE_RESOURCE_TYPES,
)
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 240


def check_identity_management(
    connect_client, instance_id, start_time, time_budget, event=None
):
    """Check the identity management type for the Connect instance.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        dict: Identity management findings.
    """
    check_time_budget(start_time, time_budget)

    try:
        # Try shared data first
        shared_instance = read_shared_data(event, "instanceData") if event else None
        if shared_instance:
            instance_data = shared_instance
            logger.info("Using pre-fetched instance data for identity management check")
        else:
            response = connect_client.describe_instance(InstanceId=instance_id)
            instance_data = response.get("Instance", {})
        identity_type = instance_data.get("IdentityManagementType", "N/A")

        is_saml = identity_type == "SAML"
        status = "pass" if is_saml else "fail"
        detail = (
            "SAML 2.0 federation configured"
            if is_saml
            else f"Using {identity_type} - SAML 2.0 recommended"
        )

        return {
            "identity_type": identity_type,
            "status": status,
            "detail": detail,
            "recommendation": None
            if is_saml
            else (
                "AWS recommends SAML 2.0 federation with an external identity provider "
                "(IdP) such as Okta, Azure AD, or AWS IAM Identity Center for production "
                "Amazon Connect instances."
            ),
        }
    except ClientError as e:
        logger.error(f"Error checking identity management: {e}")
        return {
            "identity_type": "unknown",
            "status": "error",
            "detail": str(e)[:1024],
            "recommendation": None,
        }


def check_s3_encryption(connect_client, instance_id, start_time, time_budget):
    """Check S3 encryption configuration for all storage types.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: S3 encryption findings with per-resource details.
    """
    s3_encryption_details = []
    unencrypted_buckets = []
    total_configs = 0

    # Fetch all storage configs via shared utility (QB-5)
    all_storage_configs = fetch_storage_configs(
        connect_client, instance_id, start_time, time_budget
    )

    for rtype, cfg in all_storage_configs:
        stype = cfg.get("StorageType", "N/A")
        if stype == "S3" and "S3Config" in cfg:
            total_configs += 1
            s3c = cfg["S3Config"]
            bucket_name = s3c.get("BucketName", "N/A")
            enc = s3c.get("EncryptionConfig")

            if enc:
                key_id = enc.get("KeyId", "")
                if key_id and not key_id.startswith("alias/aws/"):
                    s3_encryption_details.append(
                        {
                            "resource_type": rtype.replace("_", " ").title(),
                            "bucket": bucket_name,
                            "status": "CMK",
                            "key": key_id[-12:],
                        }
                    )
                else:
                    s3_encryption_details.append(
                        {
                            "resource_type": rtype.replace("_", " ").title(),
                            "bucket": bucket_name,
                            "status": "AWS Managed",
                            "key": key_id[-12:] if key_id else "",
                        }
                    )
            else:
                unencrypted_buckets.append(
                    {
                        "resource_type": rtype.replace("_", " ").title(),
                        "bucket": bucket_name,
                    }
                )
                s3_encryption_details.append(
                    {
                        "resource_type": rtype.replace("_", " ").title(),
                        "bucket": bucket_name,
                        "status": "None",
                        "key": "",
                    }
                )

    cmk_count = sum(1 for d in s3_encryption_details if d["status"] == "CMK")
    aws_managed_count = sum(
        1 for d in s3_encryption_details if d["status"] == "AWS Managed"
    )
    none_count = len(unencrypted_buckets)

    if total_configs == 0:
        status = "info"
        detail = "No S3 storage configs found"
    elif none_count > 0:
        status = "fail"
        detail = f"{cmk_count} CMK, {aws_managed_count} AWS Managed, {none_count} missing encryption"
    else:
        status = "pass"
        detail = f"All {total_configs} S3 configs encrypted ({cmk_count} CMK, {aws_managed_count} AWS Managed)"

    return {
        "total_configs": total_configs,
        "cmk_count": cmk_count,
        "aws_managed_count": aws_managed_count,
        "unencrypted_count": none_count,
        "status": status,
        "detail": detail,
        "encryption_details": s3_encryption_details,
        "unencrypted_buckets": unencrypted_buckets,
    }


def check_streaming_encryption(connect_client, instance_id, start_time, time_budget):
    """Check encryption configuration for data streaming (KVS, KDS, Firehose).

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Streaming encryption findings.
    """
    streaming_encryption_info = []

    # Fetch all storage configs via shared utility (QB-5)
    all_storage_configs = fetch_storage_configs(
        connect_client, instance_id, start_time, time_budget
    )

    for rtype, cfg in all_storage_configs:
        stype = cfg.get("StorageType", "N/A")

        if stype == "KINESIS_VIDEO_STREAM" and "KinesisVideoStreamConfig" in cfg:
            kvc = cfg["KinesisVideoStreamConfig"]
            kvs_retention = kvc.get("RetentionPeriodHours", 0)
            enc = kvc.get("EncryptionConfig")
            has_enc = bool(enc)
            streaming_encryption_info.append(
                {
                    "resource_type": rtype.replace("_", " ").title(),
                    "stream_type": "Kinesis Video Stream",
                    "destination": f"KVS prefix: {kvc.get('Prefix', '')} (retention: {kvs_retention}h)",
                    "encrypted": has_enc,
                    "retention_hours": kvs_retention,
                }
            )
        elif stype == "KINESIS_STREAM" and "KinesisStreamConfig" in cfg:
            destination = cfg["KinesisStreamConfig"].get("StreamArn", "")
            streaming_encryption_info.append(
                {
                    "resource_type": rtype.replace("_", " ").title(),
                    "stream_type": "Kinesis Data Stream",
                    "destination": destination.split("/")[-1]
                    if "/" in destination
                    else destination,
                    "encrypted": None,  # Encryption managed at stream level
                }
            )
        elif stype == "KINESIS_FIREHOSE" and "KinesisFirehoseConfig" in cfg:
            destination = cfg["KinesisFirehoseConfig"].get("FirehoseArn", "")
            streaming_encryption_info.append(
                {
                    "resource_type": rtype.replace("_", " ").title(),
                    "stream_type": "Kinesis Firehose",
                    "destination": destination.split("/")[-1]
                    if "/" in destination
                    else destination,
                    "encrypted": None,  # Encryption managed at delivery stream level
                }
            )

    if not streaming_encryption_info:
        return {
            "total_streams": 0,
            "status": "info",
            "detail": "No streaming configurations found",
            "streams": [],
        }

    total_streams = len(streaming_encryption_info)
    kvs_streams = [
        s
        for s in streaming_encryption_info
        if s["stream_type"] == "Kinesis Video Stream"
    ]
    kvs_encrypted = sum(1 for s in kvs_streams if s["encrypted"] is True)
    kvs_unencrypted = sum(1 for s in kvs_streams if s["encrypted"] is False)
    kds_streams = [
        s
        for s in streaming_encryption_info
        if s["stream_type"] == "Kinesis Data Stream"
    ]
    firehose_streams = [
        s for s in streaming_encryption_info if s["stream_type"] == "Kinesis Firehose"
    ]

    summary_parts = []
    if kvs_streams:
        summary_parts.append(f"{kvs_encrypted}/{len(kvs_streams)} KVS encrypted")
    if kds_streams:
        summary_parts.append(f"{len(kds_streams)} KDS (verify at stream level)")
    if firehose_streams:
        summary_parts.append(
            f"{len(firehose_streams)} Firehose (verify at stream level)"
        )

    if kvs_unencrypted > 0:
        status = "fail"
        detail = f"{kvs_unencrypted} KVS stream(s) missing encryption; {', '.join(summary_parts)}"
    elif kds_streams or firehose_streams:
        status = "warn"
        detail = f"{total_streams} stream(s) configured; {', '.join(summary_parts)}"
    else:
        status = "pass"
        detail = f"All {total_streams} stream(s) encrypted"

    return {
        "total_streams": total_streams,
        "kvs_encrypted": kvs_encrypted,
        "kvs_unencrypted": kvs_unencrypted,
        "kds_count": len(kds_streams),
        "firehose_count": len(firehose_streams),
        "status": status,
        "detail": detail,
        "streams": streaming_encryption_info,
    }


def _build_storage_configs(connect_client, instance_id, start_time, time_budget):
    """Build a unified storage configs list covering ALL resource types.

    This produces the data needed for the Data Storage Configuration table,
    including "Not configured" entries for resource types with no storage.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        list: List of dicts with resource_type, storage_type, destination,
              encryption for each configured (and unconfigured) resource type.
    """
    storage_configs = []

    # Fetch all storage configs via shared utility (QB-5)
    all_storage_configs = fetch_storage_configs(
        connect_client, instance_id, start_time, time_budget
    )

    # Build a set of resource types that have configs
    configured_rtypes = set()
    for rtype, cfg in all_storage_configs:
        configured_rtypes.add(rtype)

    for rtype in STORAGE_RESOURCE_TYPES:
        rtype_configs = [(rt, c) for rt, c in all_storage_configs if rt == rtype]

        if not rtype_configs:
            # No storage configured for this resource type
            storage_configs.append(
                {
                    "resource_type": rtype.replace("_", " ").title(),
                    "storage_type": "Not configured",
                    "destination": "-",
                    "encryption": "-",
                }
            )
        else:
            for _rt, cfg in rtype_configs:
                stype = cfg.get("StorageType", "N/A")
                destination = "-"
                encryption = "None"

                if stype == "S3" and "S3Config" in cfg:
                    s3c = cfg["S3Config"]
                    bucket = s3c.get("BucketName", "")
                    prefix = s3c.get("BucketPrefix", "")
                    destination = (
                        f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
                    )
                    enc = s3c.get("EncryptionConfig")
                    if enc:
                        key_id = enc.get("KeyId", "")
                        if key_id and not key_id.startswith("alias/aws/"):
                            encryption = f"KMS: ...{key_id[-12:]}"
                        else:
                            encryption = "AWS Managed"
                elif (
                    stype == "KINESIS_VIDEO_STREAM"
                    and "KinesisVideoStreamConfig" in cfg
                ):
                    kvs = cfg["KinesisVideoStreamConfig"]
                    prefix = kvs.get("Prefix", "")
                    retention = kvs.get("RetentionPeriodHours", 0)
                    destination = f"KVS prefix: {prefix}" if prefix else "KVS"
                    enc = kvs.get("EncryptionConfig")
                    if enc:
                        key_id = enc.get("KeyId", "")
                        if key_id and not key_id.startswith("alias/aws/"):
                            encryption = f"KMS: ...{key_id[-12:]}"
                        else:
                            encryption = "AWS Managed"
                elif stype == "KINESIS_STREAM" and "KinesisStreamConfig" in cfg:
                    kds = cfg["KinesisStreamConfig"]
                    stream_arn = kds.get("StreamArn", "")
                    destination = (
                        stream_arn.split("/")[-1] if "/" in stream_arn else stream_arn
                    )
                elif stype == "KINESIS_FIREHOSE" and "KinesisFirehoseConfig" in cfg:
                    fh = cfg["KinesisFirehoseConfig"]
                    firehose_arn = fh.get("FirehoseArn", "")
                    destination = (
                        firehose_arn.split("/")[-1]
                        if "/" in firehose_arn
                        else firehose_arn
                    )

                storage_configs.append(
                    {
                        "resource_type": rtype.replace("_", " ").title(),
                        "storage_type": stype.replace("_", " ").title()
                        if stype != "N/A"
                        else stype,
                        "destination": destination,
                        "encryption": encryption,
                    }
                )

    return storage_configs


def lambda_handler(event, context):
    """Security Analyzer Lambda handler.

    Validates input using shared validate_input, checks identity management,
    S3 encryption, streaming encryption, and AI guardrails with graceful timeout,
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
    logger.info("Security Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "security"),
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
        f"Analyzing security for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"max_seconds={max_seconds}, time_budget={time_budget}"
    )

    connect_client = boto3.client("connect", region_name=aws_region)
    timed_out = False
    checks_completed = []

    try:
        # Check 1: Identity Management
        identity_findings = check_identity_management(
            connect_client, instance_id, execution_start, time_budget, event=event
        )
        checks_completed.append("identity_management")

        # Check 2: S3 Encryption
        s3_findings = check_s3_encryption(
            connect_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("s3_encryption")

        # Check 3: Streaming Encryption
        streaming_findings = check_streaming_encryption(
            connect_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("streaming_encryption")

        # Check 4: Unified Storage Configuration (all resource types)
        storage_configs = _build_storage_configs(
            connect_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("storage_configs")

        findings = {
            "identity_management": identity_findings,
            "s3_encryption": s3_findings,
            "streaming_encryption": streaming_findings,
            "storage_configs": storage_configs,
            "checks_completed": checks_completed,
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        }

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(event, findings)

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(
            f"Security analysis complete: {len(checks_completed)} checks completed"
        )

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
        )

    except TimeBudgetExceeded:
        timed_out = True
        logger.info(
            f"Security Analyzer reached time budget after completing "
            f"{len(checks_completed)} of 4 checks: {checks_completed}"
        )

        # Build partial findings from whatever checks completed
        findings = {
            "checks_completed": checks_completed,
            "timed_out": True,
            "account_id": account_id,
            "region": aws_region,
        }
        if "identity_management" in checks_completed:
            findings["identity_management"] = identity_findings
        if "s3_encryption" in checks_completed:
            findings["s3_encryption"] = s3_findings
        if "streaming_encryption" in checks_completed:
            findings["streaming_encryption"] = streaming_findings
        if "storage_configs" in checks_completed:
            findings["storage_configs"] = storage_configs

        collected_count = len(checks_completed)
        total_estimated = 4  # Total number of security checks

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
        logger.error(f"AWS API error in Security Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in Security Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
