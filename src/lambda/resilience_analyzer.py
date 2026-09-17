# Resilience Analyzer Lambda Function
#
# Standalone Lambda that performs resilience analysis for Amazon Connect instances,
# checking multi-region configuration (ACGR), DR readiness, carrier diversity,
# and knowledge base sync health.
# Extracted from the monolithic lambda_function.py as part of the
# parallel orchestration architecture.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from botocore.exceptions import ClientError

from analyzer_common import (
    validate_input,
    success_result,
    error_result,
    persist_to_s3,
    discover_assistant_id,
    fetch_phone_numbers,
)
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 240


def check_multi_region_config(
    connect_client, instance_id, start_time, time_budget, event=None
):
    """Check multi-region / Global Resiliency (ACGR) configuration.

    Examines the Connect instance's ReplicationConfiguration to determine
    whether a replica instance exists in another region for DR purposes.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        dict: Multi-region configuration findings.
    """
    check_time_budget(start_time, time_budget)

    try:
        response = connect_client.describe_instance(InstanceId=instance_id)
        instance_data = response.get("Instance", {})

        # Check for replication configuration in the response
        replication_config = response.get("ReplicationConfiguration")

        if replication_config:
            replication_region = replication_config.get("ReplicationRegion", "N/A")
            replication_status = replication_config.get("ReplicationStatus", "N/A")
            replication_message = replication_config.get("ReplicationStatusMessage", "")

            return {
                "has_replica": True,
                "replication_region": replication_region,
                "replication_status": replication_status,
                "replication_status_message": replication_message,
                "status": "pass",
                "detail": f"ACGR replica configured in {replication_region} (status: {replication_status})",
            }
        else:
            return {
                "has_replica": False,
                "replication_region": None,
                "replication_status": None,
                "replication_status_message": None,
                "status": "info",
                "detail": "No ACGR replica configured — consider enabling Global Resiliency for DR",
                "recommendation": (
                    "Amazon Connect Global Resiliency (ACGR) provides geographic telephony "
                    "redundancy, offering a flexible solution to distribute inbound voice "
                    "traffic and agents across linked instances in another Region in the "
                    "event of unplanned Region outages or disruptions."
                ),
            }
    except ClientError as e:
        logger.error(f"Error checking multi-region config: {e}")
        return {
            "has_replica": False,
            "replication_region": None,
            "replication_status": None,
            "status": "error",
            "detail": str(e)[:1024],
        }


def check_carrier_diversity(
    connect_client, instance_id, start_time, time_budget, aws_region, event=None
):
    """Check phone number carrier diversity for redundancy.

    Examines all phone numbers claimed by the instance and groups them by
    country code and carrier to assess whether there is sufficient carrier
    diversity for resilience.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        aws_region (str): AWS region for Pinpoint client.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        dict: Carrier diversity findings.
    """
    check_time_budget(start_time, time_budget)

    phone_numbers = []
    phone_type_counts = {
        "TOLL_FREE": 0,
        "DID": 0,
        "UIFN": 0,
        "SHARED": 0,
        "THIRD_PARTY_TF": 0,
        "THIRD_PARTY_DID": 0,
        "SHORT_CODE": 0,
    }
    country_counts = Counter()

    # Create Pinpoint client once for all carrier lookups (QB-3)
    pinpoint_client = boto3.client("pinpoint", region_name=aws_region)

    # Fetch phone numbers via shared utility (QB-4)
    raw_numbers = fetch_phone_numbers(
        connect_client, instance_id, start_time, time_budget, event
    )

    for number_summary in raw_numbers:
        phone_type = number_summary.get("PhoneNumberType", "UNKNOWN")
        country_code = number_summary.get("PhoneNumberCountryCode", "UNKNOWN")
        if phone_type in phone_type_counts:
            phone_type_counts[phone_type] += 1
        country_counts[country_code] += 1

        carrier = _get_phone_carrier(
            number_summary.get("PhoneNumber", ""),
            country_code,
            pinpoint_client,
        )
        # Rate-limit Pinpoint calls to ~10 TPS (QB-6)
        time.sleep(0.1)

        phone_numbers.append(
            {
                "phone_number_id": number_summary.get("PhoneNumberId", ""),
                "phone_number": number_summary.get("PhoneNumber", ""),
                "phone_number_type": phone_type,
                "phone_number_country_code": country_code,
                "phone_number_carrier": carrier,
            }
        )

    # Group by country code + carrier for diversity analysis
    carrier_groups = defaultdict(list)
    for pn in phone_numbers:
        key = f"{pn['phone_number_country_code']} | {pn['phone_number_carrier']}"
        carrier_groups[key].append(pn["phone_number"])

    num_carriers = len(carrier_groups)
    total_numbers = len(phone_numbers)

    if num_carriers > 1:
        status = "pass"
        detail = f"{total_numbers} numbers across {num_carriers} carrier groups"
    elif total_numbers > 0:
        status = "warn"
        detail = (
            f"All {total_numbers} numbers on a single carrier — consider diversifying"
        )
    else:
        status = "info"
        detail = "No phone numbers configured"

    return {
        "total_numbers": total_numbers,
        "carrier_groups": num_carriers,
        "carrier_group_details": {k: len(v) for k, v in carrier_groups.items()},
        "phone_numbers_by_group": dict(carrier_groups),
        "phone_type_counts": {k: v for k, v in phone_type_counts.items() if v > 0},
        "country_distribution": dict(country_counts),
        "status": status,
        "detail": detail,
    }


def _get_phone_carrier(phone_number, country_code, pinpoint_client):
    """Look up the carrier for a phone number using Pinpoint.

    Args:
        phone_number (str): E.164 phone number.
        country_code (str): ISO country code.
        pinpoint_client: Pre-created Boto3 Pinpoint client.

    Returns:
        str: Carrier name or 'Unknown' on failure.
    """
    if not phone_number:
        return "Unknown"
    try:
        response = pinpoint_client.phone_number_validate(
            NumberValidateRequest={
                "PhoneNumber": phone_number,
                "IsoCountryCode": country_code,
            }
        )
        return response.get("NumberValidateResponse", {}).get("Carrier", "Unknown")
    except Exception as e:
        logger.debug(f"Carrier lookup failed for {phone_number}: {e}")
        return "Unknown"


def check_dr_readiness(
    connect_client, instance_id, start_time, time_budget, aws_region, event=None
):
    """Check DR readiness indicators for the Connect instance.

    Evaluates overall disaster recovery posture by checking:
    - Whether the instance has inbound/outbound calls enabled
    - Multi-AZ architecture (inherent in Connect)
    - Knowledge base sync health (if Q/Wisdom is configured)

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        aws_region (str): AWS region.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        tuple: (dr_readiness_findings, kb_sync_health_detail) where
               dr_readiness_findings is the check result dict and
               kb_sync_health_detail is per-KB detail data (or None).
    """
    check_time_budget(start_time, time_budget)

    dr_checks = []
    kb_sync_detail = None

    # Check 1: Multi-AZ is inherent — always pass
    dr_checks.append(
        {
            "check": "Multi-AZ Architecture",
            "status": "pass",
            "detail": (
                "Amazon Connect instances are deployed across a minimum of 3 AZs "
                "in an active-active-active configuration within each Region."
            ),
        }
    )

    # Check 2: Instance call configuration
    try:
        response = connect_client.describe_instance(InstanceId=instance_id)
        instance_data = response.get("Instance", {})
        inbound = instance_data.get("InboundCallsEnabled", False)
        outbound = instance_data.get("OutboundCallsEnabled", False)

        if inbound and outbound:
            dr_checks.append(
                {
                    "check": "Call Configuration",
                    "status": "pass",
                    "detail": "Both inbound and outbound calls enabled",
                }
            )
        else:
            dr_checks.append(
                {
                    "check": "Call Configuration",
                    "status": "info",
                    "detail": f"Inbound={inbound}, Outbound={outbound}",
                }
            )
    except ClientError as e:
        logger.warning(f"Error checking call configuration: {e}")
        dr_checks.append(
            {
                "check": "Call Configuration",
                "status": "error",
                "detail": str(e)[:1024],
            }
        )

    # Check 3: Knowledge Base Sync Health (if Q/Wisdom is configured)
    check_time_budget(start_time, time_budget)
    kb_check_result, kb_sync_detail = _check_kb_sync_health(
        connect_client, instance_id, start_time, time_budget, aws_region
    )
    if kb_check_result:
        dr_checks.append(kb_check_result)

    # Determine overall DR readiness status
    statuses = [c["status"] for c in dr_checks]
    if "error" in statuses:
        overall_status = "warn"
        overall_detail = "DR readiness check encountered errors"
    elif "warn" in statuses:
        overall_status = "warn"
        overall_detail = "Some DR readiness concerns identified"
    else:
        overall_status = "pass"
        overall_detail = f"All {len(dr_checks)} DR readiness checks passed"

    dr_findings = {
        "checks": dr_checks,
        "total_checks": len(dr_checks),
        "status": overall_status,
        "detail": overall_detail,
    }

    return dr_findings, kb_sync_detail


def _check_kb_sync_health(
    connect_client, instance_id, start_time, time_budget, aws_region
):
    """Check knowledge base sync health for DR readiness.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        aws_region (str): AWS region.

    Returns:
        tuple: (check_dict, kb_sync_detail) where check_dict is the summary
               check result for dr_readiness.checks[] and kb_sync_detail is
               the per-KB detail data for the kb_sync_health top-level key.
               Returns (None, None) if no assistant found.
    """
    # Discover assistant ID from Connect instance integration associations
    assistant_id = discover_assistant_id(connect_client, instance_id)
    if not assistant_id:
        return None, None

    try:
        qconnect_client = boto3.client("qconnect", region_name=aws_region)

        check_time_budget(start_time, time_budget)
        assoc_resp = qconnect_client.list_assistant_associations(
            assistantId=assistant_id, maxResults=100
        )
        all_associations = assoc_resp.get("assistantAssociationSummaries", [])
        kb_associations = [
            a for a in all_associations if a.get("associationType") == "KNOWLEDGE_BASE"
        ]

        if not kb_associations:
            check_result = {
                "check": "Knowledge Base Sync Health",
                "status": "info",
                "detail": "No knowledge bases associated with assistant",
            }
            return check_result, {"kb_associations": [], "review_kbs": []}

        stale_kbs = []
        kb_details = []
        for assoc in kb_associations:
            check_time_budget(start_time, time_budget)
            kb_id = (
                assoc.get("associationData", {})
                .get("knowledgeBaseAssociation", {})
                .get("knowledgeBaseId", "")
            )
            if not kb_id:
                continue
            try:
                kb_resp = qconnect_client.get_knowledge_base(knowledgeBaseId=kb_id)
                kb = kb_resp.get("knowledgeBase", {})
                kb_name = kb.get("name", kb_id)
                kb_status = kb.get("status", "UNKNOWN")
                last_modified = kb.get("lastContentModificationTime")

                if last_modified:
                    now = datetime.now(timezone.utc)
                    hours_since = (now - last_modified).total_seconds() / 3600
                    days_since = hours_since / 24

                    # Determine freshness color
                    if hours_since <= 24:
                        freshness = "Fresh"
                        freshness_color = "#22c55e"
                    elif hours_since <= 168:  # 7 days
                        freshness = "Recent"
                        freshness_color = "#f59e0b"
                    else:
                        freshness = "Stale"
                        freshness_color = "#ef4444"
                        stale_kbs.append(kb_name)

                    last_modified_str = last_modified.strftime("%Y-%m-%d %H:%M UTC")
                    time_since_update = (
                        f"{days_since:.1f} days"
                        if days_since >= 1
                        else f"{hours_since:.1f} hours"
                    )
                else:
                    freshness = "Unknown"
                    freshness_color = "#6b7280"
                    last_modified_str = "Never"
                    time_since_update = "N/A"

                kb_details.append(
                    {
                        "name": kb_name,
                        "kb_id": kb_id,
                        "status": kb_status,
                        "last_modified_str": last_modified_str,
                        "time_since_update": time_since_update,
                        "freshness": freshness,
                        "freshness_color": freshness_color,
                    }
                )
            except Exception as e:
                logger.warning(f"Error checking KB {kb_id}: {e}")
                kb_details.append(
                    {
                        "name": kb_id,
                        "kb_id": kb_id,
                        "status": "ERROR",
                        "last_modified_str": "Error",
                        "time_since_update": "N/A",
                        "freshness": "Unknown",
                        "freshness_color": "#6b7280",
                    }
                )

        if stale_kbs:
            check_result = {
                "check": "Knowledge Base Sync Health",
                "status": "warn",
                "detail": f"{len(stale_kbs)} KB(s) not updated in 7+ days: {', '.join(stale_kbs[:3])}",
            }
        else:
            check_result = {
                "check": "Knowledge Base Sync Health",
                "status": "pass",
                "detail": f"{len(kb_associations)} KB(s) checked — content is fresh",
            }

        kb_sync_detail = {
            "kb_associations": kb_details,
            "review_kbs": stale_kbs,
        }

        return check_result, kb_sync_detail

    except Exception as e:
        logger.warning(f"Error checking KB sync health: {e}")
        check_result = {
            "check": "Knowledge Base Sync Health",
            "status": "error",
            "detail": str(e)[:1024],
        }
        return check_result, None


def lambda_handler(event, context):
    """Resilience Analyzer Lambda handler.

    Validates input using shared validate_input, checks multi-region
    configuration (ACGR), carrier diversity, and DR readiness with graceful
    timeout, persists results to S3 Hive-style path, and returns a
    standardized AnalyzerResult.

    Args:
        event (dict): Input event with reviewId, instanceId, instanceArn,
                      accountId, awsRegion, daysBack, componentType,
                      s3ReportingBucket, and optional maxSeconds.
        context: Lambda context object.

    Returns:
        dict: Standardized AnalyzerResult (success or error).
    """
    logger.info("Resilience Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "resilience"),
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
        f"Analyzing resilience for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"max_seconds={max_seconds}, time_budget={time_budget}"
    )

    connect_client = boto3.client("connect", region_name=aws_region)
    checks_completed = []

    try:
        # Check 1: Multi-Region Configuration (ACGR)
        multi_region_findings = check_multi_region_config(
            connect_client, instance_id, execution_start, time_budget
        )
        checks_completed.append("multi_region")

        # Check 2: Carrier Diversity
        carrier_diversity_findings = check_carrier_diversity(
            connect_client,
            instance_id,
            execution_start,
            time_budget,
            aws_region,
            event=event,
        )
        checks_completed.append("carrier_diversity")

        # Check 3: DR Readiness
        dr_readiness_findings, kb_sync_health_detail = check_dr_readiness(
            connect_client,
            instance_id,
            execution_start,
            time_budget,
            aws_region,
            event=event,
        )
        checks_completed.append("dr_readiness")

        findings = {
            "multi_region": multi_region_findings,
            "carrier_diversity": carrier_diversity_findings,
            "dr_readiness": dr_readiness_findings,
            "checks_completed": checks_completed,
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        }

        # Add KB sync health detail as top-level key if available
        if kb_sync_health_detail:
            findings["kb_sync_health"] = kb_sync_health_detail

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(event, findings)

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(
            f"Resilience analysis complete: {len(checks_completed)} checks completed"
        )

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
        )

    except TimeBudgetExceeded:
        logger.info(
            f"Resilience Analyzer reached time budget after completing "
            f"{len(checks_completed)} of 3 checks: {checks_completed}"
        )

        # Build partial findings from whatever checks completed
        findings = {
            "checks_completed": checks_completed,
            "timed_out": True,
            "account_id": account_id,
            "region": aws_region,
        }
        if "multi_region" in checks_completed:
            findings["multi_region"] = multi_region_findings
        if "carrier_diversity" in checks_completed:
            findings["carrier_diversity"] = carrier_diversity_findings
        if "dr_readiness" in checks_completed:
            findings["dr_readiness"] = dr_readiness_findings
            if kb_sync_health_detail:
                findings["kb_sync_health"] = kb_sync_health_detail

        collected_count = len(checks_completed)
        total_estimated = 3  # Total number of resilience checks

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
        logger.error(f"AWS API error in Resilience Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in Resilience Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
