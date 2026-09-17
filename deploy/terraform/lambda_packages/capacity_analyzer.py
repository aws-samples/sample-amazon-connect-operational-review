# Capacity Analyzer Lambda Function
#
# Standalone Lambda that performs capacity analysis for Amazon Connect instances,
# checking instance resource limits, concurrency limits, and API rate quotas.
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

from analyzer_common import (
    validate_input,
    success_result,
    error_result,
    persist_to_s3,
    read_shared_data,
)
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 540

# Service code for Amazon Connect quotas
CONNECT_SERVICE_CODE = "connect"

# Instance resource quotas: (label, quota_code, metric_name)
INSTANCE_QUOTAS = [
    ("Contact Flows per instance", "L-22922690", "ContactFlows"),
    ("Queues per instance", "L-19A87C94", "Queues"),
    ("Routing Profiles per instance", "L-D3E7BE26", "RoutingProfiles"),
    ("Security Profiles per instance", "L-F325A715", "SecurityProfiles"),
    ("Users per instance", "L-9A46857E", "Users"),
    ("Lex Bots V1 per instance", "L-B93A6612", "LexBotsV1"),
    ("Lex Bots V2 per instance", "L-CCEA7427", "LexBotsV2"),
    ("PhoneNumbers per instance", "L-8F812903", "PhoneNumbers"),
    ("QuickConnects per instance", "L-68BBE2E8", "QuickConnects"),
    ("HoursOfOperation per instance", "L-20CD02F7", "HoursOfOperation"),
    ("User Hierarchy Groups per instance", "L-D68AAAE4", "UserHierarchyGroups"),
    ("AWS Lambda functions per instance", "L-E3D2F503", "LambdaFunctions"),
    (
        "Application integration associations per instance",
        "L-FC6A5030",
        "IntegrationAssociations",
    ),
    ("Prompts per instance", "L-0865B754", "Prompts"),
]

# Optional quotas that may not be available in all boto3 versions
INSTANCE_QUOTAS_OPTIONAL = [
    ("Data tables per instance", "L-D492D362", "DataTables"),
    ("Email addresses per instance", "L-F4C86B27", "_email"),
    ("Notifications per instance", "L-DFA239E1", "Notifications"),
    ("Queues per routing profile per instance", "L-516BC0EB", "_queues_per_rp"),
    ("Workspaces per instance", "L-6402A996", "Workspaces"),
]

# Quotas where we can only report the limit (no usage API)
INSTANCE_QUOTAS_NOUSAGE = [
    ("Proficiencies per agent", "L-50375162"),
    ("Campaigns per instance", "L-7F7B4C39"),
]

# Concurrency quotas: (label, quota_code, metric_name, pct_metric, metric_group, is_decimal)
CONCURRENCY_QUOTAS = [
    (
        "Concurrent active calls per instance",
        "L-12AB7C57",
        "ConcurrentCalls",
        "ConcurrentCallsPercentage",
        "VoiceCalls",
        True,
    ),
    (
        "Concurrent active chats per instance",
        "L-D4BA6F6E",
        "ConcurrentActiveChats",
        "ConcurrentActiveChatsPercentage",
        "Chats",
        False,
    ),
    (
        "Concurrent active emails per instance",
        "L-B117F12F",
        "ConcurrentEmails",
        "ConcurrentEmailsPercentage",
        "Email",
        False,
    ),
    (
        "Concurrent active tasks per instance",
        "L-60553137",
        "ConcurrentTasks",
        "ConcurrentTasksPercentage",
        "Tasks",
        False,
    ),
    (
        "Concurrent campaign calls per instance",
        "L-E908C3A1",
        "ConcurrentCalls",
        "ConcurrentCallsPercentage",
        "OutboundCampaigns",
        True,
    ),
]

# Cases service quotas: (label, quota_code)
CASES_QUOTAS = [
    ("Domains", "L-C2B81BC3"),
    ("Fields per domain", "L-C5B69356"),
    ("Templates per domain", "L-0482161A"),
    ("Layouts per domain", "L-D0ED993F"),
    ("Case rules per domain", "L-228D6D2D"),
    ("Attached files per case", "L-930905B5"),
    ("Parent field values per field options case rule", "L-F43DCB55"),
    ("Related items per case", "L-C1AF8D37"),
    ("Field options per field", "L-E52A0E46"),
    ("Child field values per field options case rule", "L-435DBDE3"),
    ("Field options case rules per template", "L-8F7DFC0D"),
    ("Attached SLAs per case", "L-A7158118"),
    ("Fields per related item", "L-7D21A319"),
]

# Application Integrations quotas: (label, quota_code)
APPINT_QUOTAS = [
    ("Data integrations per Region", "L-013E1287"),
    ("Event integrations per Region", "L-152D3E9E"),
    ("Applications per Region", "L-8C721859"),
    ("Data integration associations per data integration (max)", "L-3DEFA101"),
    ("Event integration associations per event integration (max)", "L-C1BC25C8"),
]

# Customer Profiles quotas: (label, quota_code)
PROFILES_QUOTAS = [
    ("Amazon Connect Customer Profiles domain count", "L-6603B252"),
    ("Object types per domain", "L-14092FF4"),
    ("Maximum number of integrations", "L-4A5ECB8E"),
    ("Maximum number of event triggers per domain", "L-0A1E1791"),
    ("Keys per object type", "L-A7ED412C"),
    ("Maximum number of recommenders per domain", "L-B6E9F054"),
    ("Maximum number of segment snapshots per day", "L-B59352A0"),
    ("Objects per profile", "L-E17DC7C3"),
    ("Maximum size of all objects for a profile", "L-63975AF3"),
    ("Maximum number of profile history records per profile", "L-DFAEAED3"),
    ("Maximum expiration in days", "L-3217D1F1"),
]

# AI Agents / Amazon Q in Connect quotas: (label, quota_code)
AI_AGENTS_QUOTAS = [
    ("AI Agents per instance", "L-3B582C30"),
    ("AI Agent versions per AI Agent", "L-1DCF2B6E"),
    ("AI Guardrails per instance", "L-C19BE493"),
    ("Knowledge bases per AI Agent", "L-B7D0D7FE"),
    ("Self-service AI Agents per instance", "L-F55FD50A"),
    ("AI Prompts per instance", "L-7E8C6FC7"),
]


def _get_service_quota(
    service_quotas_client, service_code, quota_code, context_id=None
):
    """Get a single service quota name and value with retry-on-throttle.

    Falls back to default if no instance-specific quota exists.

    Args:
        service_quotas_client: Boto3 Service Quotas client.
        service_code (str): AWS service code (e.g., 'connect').
        quota_code (str): Quota code (e.g., 'L-22922690').
        context_id (str, optional): Instance ARN for instance-specific quotas.

    Returns:
        tuple: (quota_name, quota_value) where quota_value may be None on error.
    """
    params = {"ServiceCode": service_code, "QuotaCode": quota_code}
    if context_id:
        params["ContextId"] = context_id

    for attempt in range(4):
        try:
            time.sleep(0.25)
            response = service_quotas_client.get_service_quota(**params)
            quota_name = response["Quota"]["QuotaName"]
            quota_value = response["Quota"]["Value"]
            return quota_name, quota_value
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "TooManyRequestsException":
                wait = 0.5 * (2**attempt)
                logger.debug(
                    f"Throttled on {quota_code}, retrying in {wait}s (attempt {attempt + 1})"
                )
                time.sleep(wait)
                continue
            if (
                error_code in ("NoSuchResourceException", "IllegalArgumentException")
                and context_id
            ):
                try:
                    response = service_quotas_client.get_service_quota(
                        ServiceCode=service_code, QuotaCode=quota_code
                    )
                    return response["Quota"]["QuotaName"], response["Quota"]["Value"]
                except ClientError:
                    return quota_code, None
            logger.debug(f"Error for {quota_code}: {error_code}")
            return quota_code, None

    return quota_code, None


def _count_paginated(client, method, list_key, **kwargs):
    """Generic paginator counter for any Connect list_* API.

    Args:
        client: Boto3 client.
        method (str): Paginator method name.
        list_key (str): Key in response containing the items list.
        **kwargs: Additional arguments to pass to the paginator.

    Returns:
        int: Total count of items across all pages.
    """
    total = 0
    paginator = client.get_paginator(method)
    for page in paginator.paginate(**kwargs):
        total += len(page.get(list_key, []))
    return total


def _count_manual(client, method, list_key, **kwargs):
    """Manual pagination counter for newer Connect APIs.

    Args:
        client: Boto3 client.
        method (str): API method name.
        list_key (str): Key in response containing the items list.
        **kwargs: Additional arguments to pass to the API call.

    Returns:
        int: Total count of items across all pages.
    """
    total = 0
    api_call = getattr(client, method)
    while True:
        resp = api_call(**kwargs)
        total += len(resp.get(list_key, []))
        token = resp.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    return total


def _current_utilization(connect_client, instance_id, metric, event=None):
    """Return the current utilization count for a single metric.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.
        metric (str): Metric name matching INSTANCE_QUOTAS entries.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        int: Current count for the metric.

    Raises:
        ValueError: If metric is unknown.
    """
    # For PhoneNumbers, try shared data first (just need the count)
    if metric == "PhoneNumbers" and event:
        shared_phone_data = read_shared_data(event, "phoneNumbers")
        if shared_phone_data:
            logger.info(
                f"Using pre-fetched phone numbers count ({len(shared_phone_data)})"
            )
            return len(shared_phone_data)

    metric_map = {
        "Users": lambda: _count_paginated(
            connect_client, "list_users", "UserSummaryList", InstanceId=instance_id
        ),
        "ContactFlows": lambda: _count_paginated(
            connect_client,
            "list_contact_flows",
            "ContactFlowSummaryList",
            InstanceId=instance_id,
        ),
        "PhoneNumbers": lambda: _count_paginated(
            connect_client,
            "list_phone_numbers_v2",
            "ListPhoneNumbersSummaryList",
            InstanceId=instance_id,
        ),
        "Queues": lambda: _count_paginated(
            connect_client,
            "list_queues",
            "QueueSummaryList",
            InstanceId=instance_id,
            QueueTypes=["STANDARD"],
        ),
        "RoutingProfiles": lambda: _count_paginated(
            connect_client,
            "list_routing_profiles",
            "RoutingProfileSummaryList",
            InstanceId=instance_id,
        ),
        "SecurityProfiles": lambda: _count_paginated(
            connect_client,
            "list_security_profiles",
            "SecurityProfileSummaryList",
            InstanceId=instance_id,
        ),
        "HoursOfOperation": lambda: _count_paginated(
            connect_client,
            "list_hours_of_operations",
            "HoursOfOperationSummaryList",
            InstanceId=instance_id,
        ),
        "QuickConnects": lambda: _count_paginated(
            connect_client,
            "list_quick_connects",
            "QuickConnectSummaryList",
            InstanceId=instance_id,
        ),
        "LexBotsV1": lambda: _count_paginated(
            connect_client,
            "list_bots",
            "LexBots",
            InstanceId=instance_id,
            LexVersion="V1",
        ),
        "LexBotsV2": lambda: _count_paginated(
            connect_client,
            "list_bots",
            "LexBots",
            InstanceId=instance_id,
            LexVersion="V2",
        ),
        "UserHierarchyGroups": lambda: _count_paginated(
            connect_client,
            "list_user_hierarchy_groups",
            "UserHierarchyGroupSummaryList",
            InstanceId=instance_id,
        ),
        "LambdaFunctions": lambda: _count_paginated(
            connect_client,
            "list_lambda_functions",
            "LambdaFunctions",
            InstanceId=instance_id,
        ),
        "IntegrationAssociations": lambda: _count_paginated(
            connect_client,
            "list_integration_associations",
            "IntegrationAssociationSummaryList",
            InstanceId=instance_id,
            IntegrationType="APPLICATION",
        ),
        "Prompts": lambda: _count_paginated(
            connect_client, "list_prompts", "PromptSummaryList", InstanceId=instance_id
        ),
        "DataTables": lambda: _count_manual(
            connect_client,
            "list_data_tables",
            "DataTableSummaryList",
            InstanceId=instance_id,
        ),
        "Notifications": lambda: _count_manual(
            connect_client,
            "list_notifications",
            "NotificationSummaryList",
            InstanceId=instance_id,
        ),
        "Workspaces": lambda: _count_manual(
            connect_client,
            "list_workspaces",
            "WorkspaceSummaryList",
            InstanceId=instance_id,
        ),
    }

    if metric not in metric_map:
        raise ValueError(f"Unknown metric '{metric}'")

    return metric_map[metric]()


def _count_email_addresses(connect_client, instance_id):
    """Count email addresses using SearchEmailAddresses API.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.

    Returns:
        int: Total email address count.
    """
    total = 0
    kwargs = {"InstanceId": instance_id, "MaxResults": 100}
    while True:
        resp = connect_client.search_email_addresses(**kwargs)
        total += len(resp.get("EmailAddresses", []))
        token = resp.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    return total


def _max_queues_per_routing_profile(connect_client, instance_id):
    """Find the maximum number of queues across all routing profiles.

    Args:
        connect_client: Boto3 Connect client.
        instance_id (str): Connect instance ID.

    Returns:
        int: Maximum queue count across all routing profiles.
    """
    max_count = 0
    paginator = connect_client.get_paginator("list_routing_profiles")
    for page in paginator.paginate(InstanceId=instance_id):
        for rp in page.get("RoutingProfileSummaryList", []):
            rp_id = rp["Id"]
            q_count = _count_paginated(
                connect_client,
                "list_routing_profile_queues",
                "RoutingProfileQueueConfigSummaryList",
                InstanceId=instance_id,
                RoutingProfileId=rp_id,
            )
            if q_count > max_count:
                max_count = q_count
    return max_count


def check_instance_quotas(
    connect_client,
    service_quotas_client,
    instance_id,
    instance_arn,
    start_time,
    time_budget,
    event=None,
):
    """Check instance resource quotas against current utilization.

    Fetches quota limits from Service Quotas API and current usage from
    Connect list APIs, computing utilization percentages.

    Args:
        connect_client: Boto3 Connect client.
        service_quotas_client: Boto3 Service Quotas client.
        instance_id (str): Connect instance ID.
        instance_arn (str): Connect instance ARN (used as context_id).
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.
        event (dict, optional): Full event payload for shared data lookup.

    Returns:
        dict: Instance resource quota findings with per-resource details.
    """
    check_time_budget(start_time, time_budget)

    resource_data = []  # list of dicts with label, current, limit, percentage

    # Core quotas
    for label, qcode, metric in INSTANCE_QUOTAS:
        check_time_budget(start_time, time_budget)
        try:
            _, limit_val = _get_service_quota(
                service_quotas_client, CONNECT_SERVICE_CODE, qcode, instance_arn
            )
            cur = _current_utilization(connect_client, instance_id, metric, event=event)
            limit_num = int(float(limit_val)) if limit_val else 0
            pct = round(cur / limit_num * 100, 1) if limit_num > 0 else 0
            resource_data.append(
                {
                    "label": label,
                    "current": cur,
                    "limit": limit_num,
                    "percentage": pct,
                }
            )
        except Exception as e:
            logger.warning(f"Error fetching quota {label}: {e}")
            resource_data.append(
                {
                    "label": label,
                    "current": -1,
                    "limit": -1,
                    "percentage": -1,
                }
            )

    # Optional quotas (may not be available in all boto3 versions)
    for label, qcode, metric in INSTANCE_QUOTAS_OPTIONAL:
        check_time_budget(start_time, time_budget)
        try:
            _, limit_val = _get_service_quota(
                service_quotas_client, CONNECT_SERVICE_CODE, qcode, instance_arn
            )
            limit_num = int(float(limit_val)) if limit_val else 0
            if metric == "_email":
                cur = _count_email_addresses(connect_client, instance_id)
            elif metric == "_queues_per_rp":
                cur = _max_queues_per_routing_profile(connect_client, instance_id)
            else:
                cur = _current_utilization(
                    connect_client, instance_id, metric, event=event
                )
            pct = round(cur / limit_num * 100, 1) if limit_num > 0 else 0
            resource_data.append(
                {
                    "label": label,
                    "current": cur,
                    "limit": limit_num,
                    "percentage": pct,
                }
            )
        except Exception as e:
            logger.debug(f"Could not fetch optional quota {label}: {e}")
            try:
                _, limit_val = _get_service_quota(
                    service_quotas_client, CONNECT_SERVICE_CODE, qcode, instance_arn
                )
                limit_num = int(float(limit_val)) if limit_val else -1
            except Exception:
                limit_num = -1
            resource_data.append(
                {
                    "label": label,
                    "current": -1,
                    "limit": limit_num,
                    "percentage": -1,
                }
            )

    # No-usage quotas (limit only, no current usage API)
    for label, qcode in INSTANCE_QUOTAS_NOUSAGE:
        check_time_budget(start_time, time_budget)
        try:
            _, limit_val = _get_service_quota(
                service_quotas_client, CONNECT_SERVICE_CODE, qcode, instance_arn
            )
            limit_num = int(float(limit_val)) if limit_val else -1
        except Exception:
            limit_num = -1
        resource_data.append(
            {
                "label": label,
                "current": -1,
                "limit": limit_num,
                "percentage": -1,
            }
        )

    # Compute summary statistics
    measurable = [r for r in resource_data if r["percentage"] >= 0]
    cap_pass = sum(1 for r in measurable if r["percentage"] < 80)
    cap_warn = sum(1 for r in measurable if 80 <= r["percentage"] < 98)
    cap_fail = sum(1 for r in measurable if r["percentage"] >= 98)
    unmeasurable_count = sum(1 for r in resource_data if r["percentage"] < 0)

    if cap_fail > 0:
        fail_names = [r["label"] for r in measurable if r["percentage"] >= 98]
        status = "fail"
        detail = f"{cap_pass}/{len(measurable)} passed, {cap_fail} critical ({', '.join(fail_names)})"
    elif cap_warn > 0:
        warn_names = [r["label"] for r in measurable if 80 <= r["percentage"] < 98]
        status = "warn"
        detail = f"{cap_pass}/{len(measurable)} passed, {cap_warn} nearing limit ({', '.join(warn_names)})"
    else:
        status = "pass"
        detail = f"{len(measurable)}/{len(measurable)} resources within safe limits"

    if unmeasurable_count > 0:
        detail += f", {unmeasurable_count} to review"

    return {
        "resources": resource_data,
        "total_checked": len(resource_data),
        "measurable_count": len(measurable),
        "unmeasurable_count": unmeasurable_count,
        "pass_count": cap_pass,
        "warn_count": cap_warn,
        "fail_count": cap_fail,
        "status": status,
        "detail": detail,
    }


def check_concurrency_limits(
    connect_client,
    cloudwatch_client,
    service_quotas_client,
    instance_id,
    instance_arn,
    days_back,
    start_time,
    time_budget,
):
    """Check concurrency limits using CloudWatch peak metrics.

    Retrieves peak concurrent usage from CloudWatch metrics and compares
    against Service Quotas limits to identify capacity pressure.

    Args:
        connect_client: Boto3 Connect client.
        cloudwatch_client: Boto3 CloudWatch client.
        service_quotas_client: Boto3 Service Quotas client.
        instance_id (str): Connect instance ID.
        instance_arn (str): Connect instance ARN.
        days_back (int): Number of days to look back for peak metrics.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Concurrency limit findings with per-metric details.
    """
    check_time_budget(start_time, time_budget)

    end_time = datetime.now(timezone.utc)
    cw_start_time = end_time - timedelta(days=days_back)

    concurrency_data = []

    for label, qcode, metric, pct_metric, group, is_decimal in CONCURRENCY_QUOTAS:
        check_time_budget(start_time, time_budget)

        try:
            _, limit_val = _get_service_quota(
                service_quotas_client, CONNECT_SERVICE_CODE, qcode, instance_arn
            )
            limit_num = int(float(limit_val)) if limit_val else 0
        except Exception:
            limit_num = 0

        peak_cur = _get_peak_metric(
            cloudwatch_client, instance_id, metric, group, cw_start_time, end_time
        )
        peak_pct = _get_peak_percentage(
            cloudwatch_client,
            instance_id,
            pct_metric,
            group,
            cw_start_time,
            end_time,
            is_decimal,
        )

        concurrency_data.append(
            {
                "label": label,
                "peak_current": peak_cur,
                "limit": limit_num,
                "peak_percentage": peak_pct,
            }
        )

    # Compute summary
    measurable = [c for c in concurrency_data if c["peak_percentage"] >= 0]
    conc_warn = [c["label"] for c in measurable if c["peak_percentage"] >= 80]
    conc_fail = [c["label"] for c in measurable if c["peak_percentage"] >= 98]

    if conc_fail:
        status = "fail"
        detail = f"{len(measurable)} metrics evaluated; {len(conc_fail)} critical ({', '.join(conc_fail)})"
    elif conc_warn:
        status = "warn"
        detail = f"{len(measurable)} metrics evaluated; {len(conc_warn)} nearing limit ({', '.join(conc_warn)})"
    else:
        status = "pass"
        detail = f"{len(measurable)} concurrency metrics within safe limits"

    return {
        "metrics": concurrency_data,
        "total_metrics": len(concurrency_data),
        "measurable_count": len(measurable),
        "status": status,
        "detail": detail,
    }


def _get_peak_metric(
    cloudwatch_client,
    instance_id,
    metric_name,
    metric_group,
    start_time_dt,
    end_time_dt,
):
    """Get peak value for a CloudWatch metric over the given period.

    Args:
        cloudwatch_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        metric_name (str): CloudWatch metric name.
        metric_group (str): MetricGroup dimension value.
        start_time_dt (datetime): Start of the period.
        end_time_dt (datetime): End of the period.

    Returns:
        int: Peak value, or 0 on error.
    """
    try:
        resp = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/Connect",
            MetricName=metric_name,
            Dimensions=[
                {"Name": "InstanceId", "Value": instance_id},
                {"Name": "MetricGroup", "Value": metric_group},
            ],
            StartTime=start_time_dt,
            EndTime=end_time_dt,
            Period=86400,
            Statistics=["Maximum"],
        )
        dps = resp.get("Datapoints", [])
        return int(max((dp.get("Maximum", 0) for dp in dps), default=0)) if dps else 0
    except Exception:
        return 0


def _get_peak_percentage(
    cloudwatch_client,
    instance_id,
    metric_name,
    metric_group,
    start_time_dt,
    end_time_dt,
    is_decimal=False,
):
    """Get peak percentage value for a CloudWatch metric.

    Args:
        cloudwatch_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        metric_name (str): CloudWatch metric name (percentage metric).
        metric_group (str): MetricGroup dimension value.
        start_time_dt (datetime): Start of the period.
        end_time_dt (datetime): End of the period.
        is_decimal (bool): If True, multiply by 100 (metric is 0-1 range).

    Returns:
        float: Peak percentage value, or 0.0 on error.
    """
    try:
        resp = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/Connect",
            MetricName=metric_name,
            Dimensions=[
                {"Name": "InstanceId", "Value": instance_id},
                {"Name": "MetricGroup", "Value": metric_group},
            ],
            StartTime=start_time_dt,
            EndTime=end_time_dt,
            Period=86400,
            Statistics=["Maximum"],
        )
        dps = resp.get("Datapoints", [])
        val = max((dp.get("Maximum", 0) for dp in dps), default=0) if dps else 0
        return round(val * 100, 1) if is_decimal else round(val, 1)
    except Exception:
        return 0.0


def check_growth_trends(
    cloudwatch_client, instance_id, days_back, start_time, time_budget
):
    """Analyze growth trends using CloudWatch concurrency metrics.

    Compares the first half vs second half of the analysis period to
    identify growth or decline in concurrent usage.

    Args:
        cloudwatch_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        days_back (int): Number of days to analyze.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: Growth trend findings with per-channel analysis.
    """
    check_time_budget(start_time, time_budget)

    end_time = datetime.now(timezone.utc)
    full_start = end_time - timedelta(days=days_back)
    midpoint = end_time - timedelta(days=days_back // 2)

    growth_metrics = [
        ("Voice Calls", "ConcurrentCalls", "VoiceCalls"),
        ("Active Chats", "ConcurrentActiveChats", "Chats"),
        ("Active Tasks", "ConcurrentTasks", "Tasks"),
    ]

    trends = []

    for label, metric_name, metric_group in growth_metrics:
        check_time_budget(start_time, time_budget)

        first_half_avg = _get_average_metric(
            cloudwatch_client,
            instance_id,
            metric_name,
            metric_group,
            full_start,
            midpoint,
        )
        second_half_avg = _get_average_metric(
            cloudwatch_client,
            instance_id,
            metric_name,
            metric_group,
            midpoint,
            end_time,
        )

        if first_half_avg > 0:
            growth_pct = round(
                ((second_half_avg - first_half_avg) / first_half_avg) * 100, 1
            )
        else:
            growth_pct = 0.0 if second_half_avg == 0 else 100.0

        trends.append(
            {
                "channel": label,
                "first_half_avg": round(first_half_avg, 1),
                "second_half_avg": round(second_half_avg, 1),
                "growth_percentage": growth_pct,
            }
        )

    # Determine overall growth status
    significant_growth = [t for t in trends if t["growth_percentage"] > 20]
    significant_decline = [t for t in trends if t["growth_percentage"] < -20]

    if significant_growth:
        status = "warn"
        detail = f"Significant growth detected in {len(significant_growth)} channel(s)"
    elif significant_decline:
        status = "info"
        detail = f"Declining usage in {len(significant_decline)} channel(s)"
    else:
        status = "pass"
        detail = "Usage trends stable across all channels"

    return {
        "trends": trends,
        "days_analyzed": days_back,
        "status": status,
        "detail": detail,
    }


def _get_average_metric(
    cloudwatch_client,
    instance_id,
    metric_name,
    metric_group,
    start_time_dt,
    end_time_dt,
):
    """Get average value for a CloudWatch metric over the given period.

    Args:
        cloudwatch_client: Boto3 CloudWatch client.
        instance_id (str): Connect instance ID.
        metric_name (str): CloudWatch metric name.
        metric_group (str): MetricGroup dimension value.
        start_time_dt (datetime): Start of the period.
        end_time_dt (datetime): End of the period.

    Returns:
        float: Average value, or 0.0 on error.
    """
    try:
        resp = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/Connect",
            MetricName=metric_name,
            Dimensions=[
                {"Name": "InstanceId", "Value": instance_id},
                {"Name": "MetricGroup", "Value": metric_group},
            ],
            StartTime=start_time_dt,
            EndTime=end_time_dt,
            Period=86400,
            Statistics=["Average"],
        )
        dps = resp.get("Datapoints", [])
        if not dps:
            return 0.0
        return sum(dp.get("Average", 0) for dp in dps) / len(dps)
    except Exception:
        return 0.0


def check_account_level_api_quotas(
    service_quotas_client, cloudwatch_client, days_back, start_time, time_budget
):
    """Check account-level Amazon Connect API rate quotas.

    Enumerates all Connect API rate quotas, identifies any with custom (non-default)
    values, and fetches CloudWatch utilization for modified quotas.

    Returns:
        dict or None: Findings with api_rate_count, modified_count, modified_rows[],
            default_rows[], status, detail. Returns None if unavailable.
    """
    check_time_budget(start_time, time_budget)
    logger.info("Checking account-level API rate quotas...")

    try:
        # Fetch all applied quotas (with retry on throttle)
        applied_quotas = {}
        for attempt in range(3):
            try:
                paginator_applied = service_quotas_client.get_paginator(
                    "list_service_quotas"
                )
                for page in paginator_applied.paginate(
                    ServiceCode=CONNECT_SERVICE_CODE
                ):
                    for q in page.get("Quotas", []):
                        applied_quotas[q["QuotaCode"]] = q
                break
            except ClientError as retry_e:
                if "TooManyRequests" in str(retry_e):
                    logger.info(
                        f"Throttled on list_service_quotas, retry {attempt + 1}"
                    )
                    time.sleep(3 * (attempt + 1))
                else:
                    raise
        logger.info(f"Fetched {len(applied_quotas)} applied quotas")

        time.sleep(3)

        # Fetch all default quotas (with retry on throttle)
        default_quotas = {}
        for attempt in range(3):
            try:
                paginator_default = service_quotas_client.get_paginator(
                    "list_aws_default_service_quotas"
                )
                for page in paginator_default.paginate(
                    ServiceCode=CONNECT_SERVICE_CODE
                ):
                    for q in page.get("Quotas", []):
                        default_quotas[q["QuotaCode"]] = q
                break
            except ClientError as retry_e:
                if "TooManyRequests" in str(retry_e):
                    logger.info(
                        f"Throttled on list_aws_default_service_quotas, retry {attempt + 1}"
                    )
                    time.sleep(3 * (attempt + 1))
                else:
                    raise
        logger.info(f"Fetched {len(default_quotas)} default quotas")

        api_rate_count = 0
        modified_rows = []
        default_rows = []

        for qcode in sorted(default_quotas.keys()):
            check_time_budget(start_time, time_budget)
            default = default_quotas.get(qcode)
            if not default:
                continue
            qname = default.get("QuotaName", qcode)
            if not qname.startswith("Rate of"):
                continue

            applied = applied_quotas.get(qcode)
            current_val = applied["Value"] if applied else default["Value"]
            default_val = default["Value"]
            api_rate_count += 1

            if applied and current_val != default_val:
                utilization = None
                usage_metric = applied.get("UsageMetric")
                if usage_metric:
                    try:
                        dims = [
                            {"Name": k, "Value": v}
                            for k, v in usage_metric.get("MetricDimensions", {}).items()
                        ]
                        cw_resp = cloudwatch_client.get_metric_statistics(
                            Namespace=usage_metric.get("MetricNamespace", "AWS/Usage"),
                            MetricName=usage_metric.get("MetricName", "CallCount"),
                            Dimensions=dims,
                            StartTime=datetime.now(timezone.utc)
                            - timedelta(days=days_back),
                            EndTime=datetime.now(timezone.utc),
                            Period=86400,
                            Statistics=["Maximum"],
                        )
                        dps = cw_resp.get("Datapoints", [])
                        if dps:
                            utilization = (
                                f"{max(dp.get('Maximum', 0) for dp in dps):.0f}"
                            )
                    except Exception:
                        pass
                modified_rows.append(
                    {
                        "name": qname,
                        "default_value": default_val,
                        "current_value": current_val,
                        "utilization": utilization,
                    }
                )
            else:
                default_rows.append(
                    {
                        "name": qname,
                        "default_value": default_val,
                    }
                )

        modified_count = len(modified_rows)
        logger.info(
            f"Account level API: {api_rate_count} rate quotas, {modified_count} modified"
        )

        return {
            "api_rate_count": api_rate_count,
            "modified_count": modified_count,
            "modified_rows": modified_rows,
            "default_rows": default_rows,
            "status": "info",
            "detail": f"{api_rate_count} API rate quotas evaluated, {modified_count} with custom increases",
        }

    except Exception as e:
        logger.warning(f"Could not fetch account-level API quotas: {e}")
        return None


def check_cases_limits(service_quotas_client, aws_region, start_time, time_budget):
    """Check Amazon Connect Cases service limits."""
    check_time_budget(start_time, time_budget)
    logger.info("Checking Amazon Connect Cases limits...")

    try:
        cases_client = boto3.client("connectcases", region_name=aws_region)
        cases_domains = cases_client.list_domains(maxResults=10).get("domains", [])
        domain_count = len(cases_domains)

        current_values = {"Domains": domain_count}

        if cases_domains:
            domain_id = cases_domains[0].get("domainId", "")
            if domain_id:
                try:
                    current_values["Fields per domain"] = len(
                        cases_client.list_fields(
                            domainId=domain_id, maxResults=100
                        ).get("fields", [])
                    )
                except Exception:
                    pass
                try:
                    current_values["Templates per domain"] = len(
                        cases_client.list_templates(
                            domainId=domain_id, maxResults=100
                        ).get("templates", [])
                    )
                except Exception:
                    pass
                try:
                    current_values["Layouts per domain"] = len(
                        cases_client.list_layouts(
                            domainId=domain_id, maxResults=100
                        ).get("layouts", [])
                    )
                except Exception:
                    pass
                try:
                    current_values["Case rules per domain"] = len(
                        cases_client.list_case_rules(
                            domainId=domain_id, maxResults=100
                        ).get("caseRules", [])
                    )
                except Exception:
                    pass

        quotas = []
        for label, qcode in CASES_QUOTAS:
            check_time_budget(start_time, time_budget)
            current = current_values.get(label)
            try:
                _, limit_val = _get_service_quota(service_quotas_client, "cases", qcode)
            except Exception:
                limit_val = None
            percentage = None
            if current is not None and limit_val and limit_val > 0:
                percentage = round(current / limit_val * 100, 1)
            quotas.append(
                {
                    "label": label,
                    "current": current,
                    "limit": limit_val,
                    "percentage": percentage,
                }
            )

        pcts = [
            (q["label"], q["percentage"]) for q in quotas if q["percentage"] is not None
        ]
        pass_count = sum(1 for _, p in pcts if p < 80)
        warn_count = sum(1 for _, p in pcts if 80 <= p < 98)
        fail_count = sum(1 for _, p in pcts if p >= 98)
        measurable = len(pcts)
        unmeasurable = len(CASES_QUOTAS) - measurable
        review_note = f", {unmeasurable} to review" if unmeasurable > 0 else ""

        if fail_count > 0:
            fail_names = [n for n, p in pcts if p >= 98]
            status, detail = (
                "fail",
                f"{pass_count}/{measurable} passed, {fail_count} critical ({', '.join(fail_names)}){review_note}",
            )
        elif warn_count > 0:
            warn_names = [n for n, p in pcts if 80 <= p < 98]
            status, detail = (
                "warn",
                f"{pass_count}/{measurable} passed, {warn_count} nearing limit ({', '.join(warn_names)}){review_note}",
            )
        elif measurable > 0:
            status, detail = (
                "pass",
                f"{measurable}/{measurable} resources within safe limits{review_note}",
            )
        else:
            status, detail = (
                "info",
                f"{domain_count} domain(s), {len(CASES_QUOTAS)} quotas to review",
            )

        return {
            "domain_count": domain_count,
            "quotas": quotas,
            "status": status,
            "detail": detail,
        }

    except Exception as e:
        logger.warning(f"Could not fetch Cases limits: {e}")
        return None


def check_appint_limits(service_quotas_client, aws_region, start_time, time_budget):
    """Check Amazon Connect Application Integrations limits."""
    check_time_budget(start_time, time_budget)
    logger.info("Checking Application Integrations limits...")

    try:
        appint_client = boto3.client("appintegrations", region_name=aws_region)

        data_integrations = []
        next_token = None
        while True:
            kwargs = {"MaxResults": 50}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = appint_client.list_data_integrations(**kwargs)
            data_integrations.extend(resp.get("DataIntegrations", []))
            next_token = resp.get("NextToken")
            if not next_token:
                break

        event_integrations = []
        next_token = None
        while True:
            kwargs = {"MaxResults": 50}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = appint_client.list_event_integrations(**kwargs)
            event_integrations.extend(resp.get("EventIntegrations", []))
            next_token = resp.get("NextToken")
            if not next_token:
                break

        applications = []
        next_token = None
        while True:
            kwargs = {"MaxResults": 50}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = appint_client.list_applications(**kwargs)
            applications.extend(resp.get("Applications", []))
            next_token = resp.get("NextToken")
            if not next_token:
                break

        data_int_count = len(data_integrations)
        event_int_count = len(event_integrations)
        app_count = len(applications)

        max_data_assoc = 0
        for di in data_integrations[:50]:
            check_time_budget(start_time, time_budget)
            di_id = di.get("DataIntegrationArn", "") or di.get("Name", "")
            if di_id:
                try:
                    time.sleep(0.1)
                    count = len(
                        appint_client.list_data_integration_associations(
                            DataIntegrationIdentifier=di_id, MaxResults=50
                        ).get("DataIntegrationAssociations", [])
                    )
                    if count > max_data_assoc:
                        max_data_assoc = count
                except Exception:
                    pass

        max_event_assoc = 0
        for ei in event_integrations[:50]:
            check_time_budget(start_time, time_budget)
            ei_name = ei.get("Name", "")
            if ei_name:
                try:
                    time.sleep(0.1)
                    count = len(
                        appint_client.list_event_integration_associations(
                            EventIntegrationName=ei_name, MaxResults=50
                        ).get("EventIntegrationAssociations", [])
                    )
                    if count > max_event_assoc:
                        max_event_assoc = count
                except Exception:
                    pass

        current_values = {
            "Data integrations per Region": data_int_count,
            "Event integrations per Region": event_int_count,
            "Applications per Region": app_count,
            "Data integration associations per data integration (max)": max_data_assoc,
            "Event integration associations per event integration (max)": max_event_assoc,
        }

        quotas = []
        for label, qcode in APPINT_QUOTAS:
            current = current_values.get(label)
            try:
                _, limit_val = _get_service_quota(
                    service_quotas_client, "app-integrations", qcode
                )
            except Exception:
                limit_val = None
            percentage = None
            if current is not None and limit_val and limit_val > 0:
                percentage = round(current / limit_val * 100, 1)
            quotas.append(
                {
                    "label": label,
                    "current": current,
                    "limit": limit_val,
                    "percentage": percentage,
                }
            )

        pcts = [
            (q["label"], q["percentage"]) for q in quotas if q["percentage"] is not None
        ]
        pass_count = sum(1 for _, p in pcts if p < 80)
        warn_count = sum(1 for _, p in pcts if 80 <= p < 98)
        fail_count = sum(1 for _, p in pcts if p >= 98)
        measurable = len(pcts)
        unmeasurable = len(APPINT_QUOTAS) - measurable
        review_note = f", {unmeasurable} to review" if unmeasurable > 0 else ""

        if fail_count > 0:
            fail_names = [n for n, p in pcts if p >= 98]
            status, detail = (
                "fail",
                f"{pass_count}/{measurable} passed, {fail_count} critical ({', '.join(fail_names)}){review_note}",
            )
        elif warn_count > 0:
            warn_names = [n for n, p in pcts if 80 <= p < 98]
            status, detail = (
                "warn",
                f"{pass_count}/{measurable} passed, {warn_count} nearing limit ({', '.join(warn_names)}){review_note}",
            )
        elif measurable > 0:
            status, detail = (
                "pass",
                f"{measurable}/{measurable} resources within safe limits{review_note}",
            )
        else:
            status, detail = (
                "info",
                f"{data_int_count} data, {event_int_count} event, {app_count} apps, {len(APPINT_QUOTAS)} quotas to review",
            )

        return {
            "data_int_count": data_int_count,
            "event_int_count": event_int_count,
            "app_count": app_count,
            "quotas": quotas,
            "status": status,
            "detail": detail,
        }

    except Exception as e:
        logger.warning(f"Could not fetch Application Integrations limits: {e}")
        return None


def check_profiles_limits(service_quotas_client, aws_region, start_time, time_budget):
    """Check Amazon Connect Customer Profiles limits."""
    check_time_budget(start_time, time_budget)
    logger.info("Checking Customer Profiles limits...")

    try:
        profiles_client = boto3.client("customer-profiles", region_name=aws_region)
        domains_resp = profiles_client.list_domains(MaxResults=100)
        profile_domains = domains_resp.get("Items", [])
        domain_count = len(profile_domains)

        current_values = {"Amazon Connect Customer Profiles domain count": domain_count}

        if profile_domains:
            domain_name = profile_domains[0].get("DomainName", "")
            if domain_name:
                try:
                    current_values["Object types per domain"] = len(
                        profiles_client.list_profile_object_types(
                            DomainName=domain_name, MaxResults=100
                        ).get("Items", [])
                    )
                except Exception:
                    pass
                try:
                    current_values["Maximum number of integrations"] = len(
                        profiles_client.list_integrations(
                            DomainName=domain_name, MaxResults=100
                        ).get("Items", [])
                    )
                except Exception:
                    pass
                try:
                    current_values["Maximum number of event triggers per domain"] = len(
                        profiles_client.list_event_triggers(
                            DomainName=domain_name, MaxResults=100
                        ).get("Items", [])
                    )
                except Exception:
                    pass

        quotas = []
        for label, qcode in PROFILES_QUOTAS:
            check_time_budget(start_time, time_budget)
            current = current_values.get(label)
            try:
                _, limit_val = _get_service_quota(
                    service_quotas_client, "profile", qcode
                )
            except Exception:
                limit_val = None
            percentage = None
            if current is not None and limit_val and limit_val > 0:
                percentage = round(current / limit_val * 100, 1)
            quotas.append(
                {
                    "label": label,
                    "current": current,
                    "limit": limit_val,
                    "percentage": percentage,
                }
            )

        pcts = [
            (q["label"], q["percentage"]) for q in quotas if q["percentage"] is not None
        ]
        pass_count = sum(1 for _, p in pcts if p < 80)
        warn_count = sum(1 for _, p in pcts if 80 <= p < 98)
        fail_count = sum(1 for _, p in pcts if p >= 98)
        measurable = len(pcts)

        if fail_count > 0:
            fail_names = [n for n, p in pcts if p >= 98]
            status, detail = (
                "fail",
                f"{pass_count}/{measurable} passed, {fail_count} critical ({', '.join(fail_names)})",
            )
        elif warn_count > 0:
            warn_names = [n for n, p in pcts if 80 <= p < 98]
            status, detail = (
                "warn",
                f"{pass_count}/{measurable} passed, {warn_count} nearing limit ({', '.join(warn_names)})",
            )
        elif measurable > 0:
            status, detail = (
                "pass",
                f"{measurable}/{measurable} resources within safe limits",
            )
        else:
            status, detail = "info", f"{domain_count} domain(s), quotas reviewed"

        return {
            "domain_count": domain_count,
            "quotas": quotas,
            "status": status,
            "detail": detail,
        }

    except Exception as e:
        logger.warning(f"Could not fetch Customer Profiles limits: {e}")
        return None


def check_ai_agents_limits(service_quotas_client, aws_region, start_time, time_budget):
    """Check Amazon Q in Connect / AI Agent service quota limits.

    Queries Service Quotas for Amazon Q in Connect limits and returns
    quota information including applied values, defaults, and adjustability.

    Args:
        service_quotas_client: Boto3 Service Quotas client.
        aws_region (str): AWS region.
        start_time (float): Epoch timestamp when execution began.
        time_budget (int): Time budget in seconds.

    Returns:
        dict: AI Agents limit findings with quotas list, status, detail.
              Returns None on failure.
    """
    check_time_budget(start_time, time_budget)
    logger.info("Checking AI Agents limits...")

    try:
        quotas = []
        for label, qcode in AI_AGENTS_QUOTAS:
            check_time_budget(start_time, time_budget)
            try:
                # Get full quota response for default_value and adjustable fields
                params = {"ServiceCode": "amazon-q-connect", "QuotaCode": qcode}
                for attempt in range(4):
                    try:
                        time.sleep(0.25)
                        response = service_quotas_client.get_service_quota(**params)
                        quota_info = response["Quota"]
                        applied_value = quota_info.get("Value")
                        default_value = quota_info.get("DefaultValue")
                        adjustable = quota_info.get("Adjustable", False)
                        break
                    except ClientError as e:
                        error_code = e.response["Error"]["Code"]
                        if error_code == "TooManyRequestsException":
                            wait = 0.5 * (2**attempt)
                            logger.debug(
                                f"Throttled on {qcode}, retrying in {wait}s (attempt {attempt + 1})"
                            )
                            time.sleep(wait)
                            continue
                        elif error_code == "NoSuchResourceException":
                            # Service may not be available in this region
                            applied_value = None
                            default_value = None
                            adjustable = None
                            break
                        else:
                            applied_value = None
                            default_value = None
                            adjustable = None
                            break
                else:
                    # All retries exhausted
                    applied_value = None
                    default_value = None
                    adjustable = None
            except Exception:
                applied_value = None
                default_value = None
                adjustable = None

            quotas.append(
                {
                    "name": label,
                    "quota_code": qcode,
                    "applied_value": applied_value,
                    "default_value": default_value,
                    "adjustable": adjustable,
                    "percentage_used": None,
                }
            )

        status = "info"
        detail = f"{len(AI_AGENTS_QUOTAS)} AI Agent quotas reviewed"

        return {"quotas": quotas, "status": status, "detail": detail}

    except Exception as e:
        logger.warning(f"Could not fetch AI Agents limits: {e}")
        return None


def lambda_handler(event, context):
    """Capacity Analyzer Lambda handler.

    Validates input using shared validate_input, checks instance resource
    quotas, concurrency limits, and growth trends with graceful timeout,
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
    logger.info("Capacity Analyzer invoked")
    execution_start = time.time()

    # Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(
            event.get("componentType", "capacity"),
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
        f"Analyzing capacity for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"max_seconds={max_seconds}, time_budget={time_budget}"
    )

    connect_client = boto3.client("connect", region_name=aws_region)
    cloudwatch_client_regional = boto3.client("cloudwatch", region_name=aws_region)
    service_quotas_client = boto3.client("service-quotas", region_name=aws_region)
    checks_completed = []

    try:
        # Check 1: Instance Resource Quotas
        instance_quota_findings = check_instance_quotas(
            connect_client,
            service_quotas_client,
            instance_id,
            instance_arn,
            execution_start,
            time_budget,
            event=event,
        )
        checks_completed.append("instance_quotas")

        # Check 2: Concurrency Limits
        concurrency_findings = check_concurrency_limits(
            connect_client,
            cloudwatch_client_regional,
            service_quotas_client,
            instance_id,
            instance_arn,
            days_back,
            execution_start,
            time_budget,
        )
        checks_completed.append("concurrency_limits")

        # Check 3: Growth Trends
        growth_findings = check_growth_trends(
            cloudwatch_client_regional,
            instance_id,
            days_back,
            execution_start,
            time_budget,
        )
        checks_completed.append("growth_trends")

        # Check 4: Account-level API rate quotas
        api_quota_findings = check_account_level_api_quotas(
            service_quotas_client,
            cloudwatch_client_regional,
            days_back,
            execution_start,
            time_budget,
        )
        if api_quota_findings:
            checks_completed.append("account_level_api")

        # Check 5: Cases limits
        cases_findings = check_cases_limits(
            service_quotas_client,
            aws_region,
            execution_start,
            time_budget,
        )
        if cases_findings:
            checks_completed.append("cases_limits")

        # Check 6: Application Integrations limits
        appint_findings = check_appint_limits(
            service_quotas_client,
            aws_region,
            execution_start,
            time_budget,
        )
        if appint_findings:
            checks_completed.append("appint_limits")

        # Check 7: Customer Profiles limits
        profiles_findings = check_profiles_limits(
            service_quotas_client,
            aws_region,
            execution_start,
            time_budget,
        )
        if profiles_findings:
            checks_completed.append("profiles_limits")

        # Check 8: AI Agents limits
        ai_agents_findings = check_ai_agents_limits(
            service_quotas_client,
            aws_region,
            execution_start,
            time_budget,
        )
        if ai_agents_findings:
            checks_completed.append("ai_agents_limits")

        findings = {
            "instance_quotas": instance_quota_findings,
            "concurrency_limits": concurrency_findings,
            "growth_trends": growth_findings,
            "checks_completed": checks_completed,
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        }
        if api_quota_findings:
            findings["account_level_api"] = api_quota_findings
        if cases_findings:
            findings["cases_limits"] = cases_findings
        if appint_findings:
            findings["appint_limits"] = appint_findings
        if profiles_findings:
            findings["profiles_limits"] = profiles_findings
        if ai_agents_findings:
            findings["ai_agents_limits"] = ai_agents_findings

        # Persist results to S3 Hive-style path before returning
        s3_key = persist_to_s3(event, findings)

        duration_ms = int((time.time() - execution_start) * 1000)

        logger.info(
            f"Capacity analysis complete: {len(checks_completed)} checks completed"
        )

        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
        )

    except TimeBudgetExceeded:
        logger.info(
            f"Capacity Analyzer reached time budget after completing "
            f"{len(checks_completed)} of 8 checks: {checks_completed}"
        )

        # Build partial findings from whatever checks completed
        findings = {
            "checks_completed": checks_completed,
            "timed_out": True,
            "account_id": account_id,
            "region": aws_region,
        }
        if "instance_quotas" in checks_completed:
            findings["instance_quotas"] = instance_quota_findings
        if "concurrency_limits" in checks_completed:
            findings["concurrency_limits"] = concurrency_findings
        if "growth_trends" in checks_completed:
            findings["growth_trends"] = growth_findings
        if "account_level_api" in checks_completed:
            findings["account_level_api"] = api_quota_findings
        if "cases_limits" in checks_completed:
            findings["cases_limits"] = cases_findings
        if "appint_limits" in checks_completed:
            findings["appint_limits"] = appint_findings
        if "profiles_limits" in checks_completed:
            findings["profiles_limits"] = profiles_findings
        if "ai_agents_limits" in checks_completed:
            findings["ai_agents_limits"] = ai_agents_findings

        collected_count = len(checks_completed)
        total_estimated = 8  # Total number of capacity checks

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
        logger.error(f"AWS API error in Capacity Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in Capacity Analyzer: {error_msg}")
        return error_result(component_type, error_msg)
