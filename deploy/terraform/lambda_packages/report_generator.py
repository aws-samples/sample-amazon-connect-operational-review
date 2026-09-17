# Report Generator Lambda Function
#
# Assembles HTML report from analyzer results stored in Hive-style S3 partitions.
# Reads detailed findings from S3, renders sections in fixed order, handles
# partial results with amber indicators, failed sections with error placeholders,
# and omits skipped analyzers entirely.
#
# Part of the parallel orchestration architecture for Amazon Connect Operational Review.
#
# This is a self-contained single-file module (all renderers, recommendations,
# and orchestration in one file) required by CFT Code.ZipFile inline deployment.

import boto3
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from html import escape

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ════════════════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS
# ════════════════════════════════════════════════════════════════════════════════


def get_recommendation(key, **kwargs):
    """Return HTML recommendation block by key. Pass dynamic values via kwargs."""

    recommendations = {
        "identity_management": lambda: (
            """<h4>Recommendation</h4>
            <ul>
                <li>This instance is not using <strong>SAML 2.0-based authentication</strong>. AWS recommends SAML 2.0 federation with an external identity provider (IdP) such as Okta, Azure AD, or AWS IAM Identity Center for production Amazon Connect instances.</li>
                <li><strong>SAML 2.0 federation</strong> provides centralized identity management, single sign-on (SSO), multi-factor authentication (MFA) enforcement, and automated user provisioning/deprovisioning through your corporate IdP.</li>
                <li><strong>CONNECT_MANAGED</strong> identity stores user credentials within Amazon Connect, which limits integration with enterprise identity governance, does not support MFA natively, and requires separate credential management.</li>
                <li><strong>EXISTING_DIRECTORY</strong> (AWS Directory Service) provides Active Directory integration but SAML 2.0 is preferred for its broader IdP compatibility and SSO capabilities.</li>
                <li>Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/configure-saml.html" target="_blank">Configure SAML for Amazon Connect</a> and <a href="https://docs.aws.amazon.com/connect/latest/adminguide/security-best-practices.html" target="_blank">Security Best Practices</a> for migration guidance.</li>
            </ul>"""
        ),
        "global_resiliency": lambda: (
            """<h4>Information</h4>
            <ul>
                <li>Amazon Connect Global Resiliency (ACGR) provides geographic telephony redundancy, offering a flexible solution to distribute inbound voice traffic and agents across linked instances with the same reserved capacity limit, in another Region in the event of unplanned Region outages or disruptions.</li>
                <li>Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/disaster-recovery-resiliency.html" target="_blank">documentation</a> for more information.</li>
            </ul>"""
        ),
        "carrier_diversity": lambda: (
            """<h4>Recommendation</h4>
            <ul> In the US, you should use Amazon Connect telephony services for US toll-free numbers, allowing you to route toll-free traffic across multiple suppliers in an active-active fashion at no additional charge. In situations where you are forwarding inbound traffic to an Amazon Connect phone number, you should request redundant DID or Toll-Free numbers across multiple telephony providers. If you are claiming or porting multiple DID or Toll-Free numbers outside of the US, you should request that those numbers be claimed or ported to a variety of telephony providers for increased resiliency.
            Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/operational-excellence.html#prepare" target="_blank"> documentation </a> for more information</ul>"""
        ),
        "contact_flow_logging": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Use a Set logging behavior block to enable or disable logging for segments of the flow where sensitive information is collected and can't be stored in CloudWatch.</li>
                <li>Learn more about contact flow <a href="https://docs.aws.amazon.com/connect/latest/adminguide/about-contact-flow-logs.html" target="_blank">logging</a>.</li>
            </ul>"""
        ),
        "api_throttling": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Review <a href="https://docs.aws.amazon.com/connect/latest/APIReference/best-practices-connect-apis.html" target="_blank">Best Practices for Amazon Connect APIs</a>.</li>
            </ul>"""
        ),
        "alarm_validation": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Create CloudWatch alarms for all missing metrics listed above, especially CRITICAL severity items.</li>
                <li>Ensure all alarms have SNS actions configured for notification delivery.</li>
                <li>For breach/throttle alarms, trigger on any occurrence (threshold &gt; 0) to detect issues immediately.</li>
            </ul>"""
        ),
        "phone_number_no_tollfree": lambda: (
            """<ul>
                <li>Consider using toll-free numbers for international toll-free access.</li>
                <li>Learn more about <a href="https://docs.aws.amazon.com/connect/latest/adminguide/ag-overview-numbers.html" target="_blank">phone number types</a>.</li>
            </ul>"""
        ),
        "phone_number_more_did": lambda: (
            """<ul>
                <li>In the US, use toll-free phone numbers wherever possible to load balance across multiple carriers for additional route and carrier redundancy.</li>
                <li>In situations where you use DIDs, load balance across numbers from multiple carriers, when possible, to increase reliability. This level of service does come at an additional cost.</li>
            </ul>"""
        ),
        "phone_number_tollfree_dominant": lambda: (
            """<ul>
            Though Toll free numbers provide additional resiliency, it comes with additional cost when compared with DIDs. Apply your workload's
            availability and resiliency requirements in choosing right telephony numbers. Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/ag-overview-numbers.html" target="_blank">documentation</a> for additional details on DIDs and TFNs in
            Amazon Connect.
            </ul>"""
        ),
        "channel_usage_general": lambda: (
            """Amazon Connect pricing varies by channel: Voice is per-minute, Chat is per-message, Tasks are per-task, and Email is per-email. Review your channel mix regularly to optimize costs. Refer to <a href="https://aws.amazon.com/connect/pricing/" target="_blank">Amazon Connect pricing</a> for current rates."""
        ),
        "channel_voice_heavy": lambda: (
            f"""Voice contacts represent {kwargs.get("voice_pct", 0):.0f}% of total volume. Amazon Connect voice usage is billed per minute. Consider deflecting routine inquiries to <strong>chat or tasks</strong> where applicable - chat contacts are billed per message and can be significantly cheaper for simple Q&amp;A interactions. Refer to <a href="https://aws.amazon.com/connect/pricing/" target="_blank">Amazon Connect pricing</a>."""
        ),
        "channel_no_chat": lambda: (
            """No chat contacts detected. Enabling <strong>Amazon Connect Chat</strong> can reduce costs for routine inquiries - agents can handle multiple concurrent chats vs. one voice call at a time, improving agent utilization. Chat is billed per message rather than per minute. Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/enable-chat-in-app.html" target="_blank">chat setup documentation</a>."""
        ),
        "channel_no_tasks": lambda: (
            """No task contacts detected. <strong>Amazon Connect Tasks</strong> can automate follow-up work items (e.g., callbacks, case updates) at a lower cost than voice. Tasks are billed per task rather than per minute. Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/tasks.html" target="_blank">tasks documentation</a>."""
        ),
        "channel_high_voice_ht": lambda: (
            f"""Average voice handle time is <strong>{int(kwargs.get("avg_ht", 0) // 60)}m {int(kwargs.get("avg_ht", 0) % 60)}s</strong>. Long handle times increase per-minute voice costs. Consider using <strong>Amazon Connect step-by-step guides</strong> or <strong>Amazon Connect AI Agents (agent assist)</strong> to reduce handle time. Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/step-by-step-guided-experiences.html" target="_blank">step-by-step guides</a>."""
        ),
        "channel_multi_channel": lambda: (
            f"""Multi-channel usage detected (Voice: {kwargs.get("voice_pct", 0):.0f}%, Chat: {kwargs.get("chat_pct", 0):.0f}%). Ensure routing profiles are configured to allow agents to handle <strong>multiple concurrent chats</strong> alongside voice to maximize agent utilization and reduce cost per contact. Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/concepts-routing-profiles-priority.html" target="_blank">routing profile configuration</a>."""
        ),
        "channel_email_active": lambda: (
            f"""Email channel is active with {kwargs.get("email_count", 0)} contacts. Email contacts are billed per email processed. Consider using <strong>auto-responses</strong> and <strong>Amazon Connect AI Agents</strong> to draft replies and reduce agent handling time for email. Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/email.html" target="_blank">email channel documentation</a>."""
        ),
        "missed_calls": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Review agent staffing levels during peak missed-call periods to ensure adequate coverage.</li>
                <li>Consider adjusting the agent answer timeout (default 20 seconds) if agents need more time to accept calls.</li>
                <li>Implement queued callbacks so customers don't have to wait on hold. Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/setup-queued-cb.html" target="_blank">queued callbacks</a>.</li>
                <li>Monitor the <code>MissedCalls</code> metric trend - a sustained increase may indicate understaffing or routing issues.</li>
                <li>Review routing profiles and queue priorities to ensure calls reach available agents efficiently.</li>
            </ul>"""
        ),
        "s3_encryption": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Use customer-managed KMS keys (CMK) for all S3 storage types for full control over key rotation and access auditing.</li>
                <li>AWS-managed keys provide encryption but do not allow custom key policies or cross-account access control.</li>
                <li>Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/encryption-at-rest.html" target="_blank">encryption at rest documentation</a>.</li>
            </ul>"""
        ),
        "streaming_encryption": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Enable server-side encryption (SSE) on all Kinesis Data Streams using AWS KMS keys. Refer to <a href="https://docs.aws.amazon.com/streams/latest/dev/server-side-encryption.html" target="_blank">KDS encryption documentation</a>.</li>
                <li>Enable encryption on Kinesis Firehose delivery streams. Refer to <a href="https://docs.aws.amazon.com/firehose/latest/dev/encryption.html" target="_blank">Firehose encryption documentation</a>.</li>
                <li>For Kinesis Video Streams, ensure KMS encryption is configured in the Amazon Connect instance storage settings.</li>
            </ul>"""
        ),
        "kvs_retention": lambda: (
            """<h4>Recommendations</h4>
            <ul>
                <li>Setting the retention period of a Kinesis Video Stream to zero hours means data is not stored after it is produced. The stream operates in real-time-only mode.</li>
                <li>While this reduces storage costs, it introduces significant issues around data availability and consumer processing. If a consumer falls behind or disconnects, data is permanently lost.</li>
                <li>Set a retention period of at least 24 hours to allow consumers to catch up after failures or delays.</li>
                <li>For compliance or audit requirements, consider longer retention periods (e.g., 72-168 hours) to ensure recordings are available for review.</li>
                <li>Refer to <a href="https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/how-it-works.html" target="_blank">Kinesis Video Streams documentation</a> for retention configuration details.</li>
            </ul>"""
        ),
        "kvs_retention_info": lambda: (
            """<h4>Information</h4>
            <ul>
                <li><strong>Recommended retention:</strong> Set retention based on your operational and compliance requirements. For real-time-only processing, minimal retention may suffice. For post-call analytics and quality monitoring, allow enough time for all processing to complete. For regulatory or audit requirements, align retention with your organization's data retention policies.</li>
                <li><strong>Cost implications:</strong> KVS charges per GB of data stored per hour of retention. Increasing retention proportionally increases storage cost. Evaluate the trade-off between data availability and storage expense based on your call volume. Refer to <a href="https://aws.amazon.com/kinesis/video-streams/pricing/" target="_blank">KVS pricing</a>.</li>
                <li><strong>Retention and downstream consumers:</strong> KVS retention must exceed the maximum processing delay of all consumers reading from the stream. If a consumer falls behind due to throttling, errors, or scaling delays, data is permanently lost once it ages past the retention window. Set retention to comfortably exceed your expected maximum consumer lag.</li>
            </ul>"""
        ),
        "ai_agent_none": lambda: (
            """<h4>Information</h4>
            <ul>
                <li>No AI agents are configured for this assistant. Self-service and/or agent-assist AI agents can be created in the Connect AI agent designer.</li>
                <li>Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/ai-agents.html" target="_blank">documentation</a>.</li>
            </ul>"""
        ),
        "ai_prompt_none": lambda: (
            """<h4>Information</h4>
            <ul>
                <li>No AI prompts are configured. AI prompts can be created for self-service and agent-assist orchestration.</li>
            </ul>"""
        ),
        "ai_guardrails": lambda: (
            """<h4>Information</h4>
            <ul>
                <li>AI guardrails can be deployed with five filter types: Content Filter, PII Filter, Denied Topics, Word Filter, and Contextual Grounding.</li>
                <li>Guardrails can be associated with AI agents and published to activate protection.</li>
                <li>Maximum 3 custom guardrails per assistant.</li>
                <li>Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/ai-guardrails.html" target="_blank">documentation</a>.</li>
            </ul>"""
        ),
        "ai_domain_encryption": lambda: (
            """<h4>Recommendation</h4>
            <ul>
                <li>Configure a customer-managed KMS key for the Amazon Connect AI Agents domain to enable key rotation auditing and access control.</li>
                <li>Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/encryption-at-rest.html" target="_blank">documentation</a>.</li>
            </ul>"""
        ),
        "ai_agent_logging": lambda: (
            """<h4>Recommendation</h4>
            <ul>
                <li>Enable AI Agent event logging via CloudWatch Vended Logs delivery for the assistant ARN.</li>
                <li>This provides granular AI agent observability: tool invocation traces, LLM token usage, session analytics, intent detection, and recommendation tracking.</li>
            </ul>"""
        ),
        "kb_none": lambda: (
            """<h4>Recommendation</h4>
            <ul>
                <li>Associate at least one knowledge base with the assistant for AI agent retrieval capabilities.</li>
            </ul>"""
        ),
        "kb_sync_review": lambda: (
            """<h4>Recommendation</h4>
            <ul>
                <li>Verify knowledge base content is up to date with your source.</li>
                <li>Default sync interval is 1 hour. Options: every 1 hour, every 3 hours, or daily. For S3 sources, sync is triggered by S3 object events.</li>
            </ul>"""
        ),
        "phone_number_health": lambda: (
            f"""<h4>Recommendation</h4>
            <ul>
                <li><strong>Why detected:</strong> {kwargs.get("count", 0)} of {kwargs.get("total", 0)} phone number(s) have a FAILED or IN_PROGRESS claim/port status. These numbers are not functional and incur a monthly recurring fee without delivering value.</li>
                <li><strong>What to do:</strong>
                    <ul>
                        <li>For FAILED numbers, re-attempt the claim or contact AWS Support to resolve the port issue.</li>
                        <li>For IN_PROGRESS numbers stuck for an extended period, contact AWS Support to check the status of the port request.</li>
                        <li>If numbers are no longer needed, release them to eliminate recurring charges and free up quota capacity.</li>
                    </ul>
                </li>
                <li>Refer to <a href="https://aws.amazon.com/connect/pricing/" target="_blank">Amazon Connect pricing</a> for current per-number fees by type and country.</li>
            </ul>"""
        ),
        "misconfigured_phone_numbers": lambda: (
            f"""<h4>Recommendation</h4>
            <ul>
                <li><strong>Why detected:</strong> {kwargs.get("count", 0)} phone number(s) are misconfigured and may result in callers hearing errors, fast busy signals, or being unable to reach your contact center.</li>
                <li><strong>What to do:</strong>
                    <ul>
                        <li><strong>Orphaned target ({kwargs.get("orphaned_count", 0)}):</strong> These numbers point to a contact flow that no longer exists. Reassign them to a valid, published contact flow.</li>
                        <li><strong>Unpublished flow ({kwargs.get("unpublished_count", 0)}):</strong> These numbers route to a contact flow in SAVED (draft) state. Publish the flow or reassign the number to a published flow.</li>
                        <li><strong>Failed/stuck status ({kwargs.get("failed_count", 0)}):</strong> These numbers have a claim or port that failed. Re-attempt the claim or contact AWS Support for port issues.</li>
                    </ul>
                </li>
                <li>Misconfigured numbers directly impact customer experience - callers may be unable to reach agents.</li>
                <li>Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/ag-overview-numbers.html" target="_blank">phone number management documentation</a>.</li>
            </ul>"""
        ),
    }

    fn = recommendations.get(key)
    if fn:
        return fn()
    logger.warning(f"Unknown recommendation key: {key}")
    return ""


# ════════════════════════════════════════════════════════════════════════════════
# CONSTANTS AND RENDERERS
# ════════════════════════════════════════════════════════════════════════════════


# Fixed section order for the HTML report
SECTION_ORDER = [
    "security",
    "resilience",
    "operational_excellence",
    "capacity",
    "observability",
    "cost",
]

# Human-readable display names for each section
SECTION_DISPLAY_NAMES = {
    "security": "Security",
    "resilience": "Resilience",
    "operational_excellence": "Operational Excellence",
    "capacity": "Capacity Analysis",
    "observability": "Observability",
    "cost": "Cost Considerations",
}

# DevOps Agent Space ID from environment
DEVOPS_AGENT_SPACE_ID = os.environ.get("DEVOPS_AGENT_AGENT_SPACE_ID", "").strip()

# Valid statuses for executive summary checks
VALID_CHECK_STATUSES = ("pass", "fail", "warn", "info", "error")


# ════════════════════════════════════════════════════════════════════════════════
# CANONICAL CHECK NAMES (QB-68 / R3)
# ════════════════════════════════════════════════════════════════════════════════
#
# Single source of truth mapping each Executive Summary check anchor to the
# canonical display name used both in the ES scoreboard row and in the section
# body <h3>. Before this constant was introduced, ES names and body <h3> texts
# were maintained as inline literals in separate render functions, which
# produced three known divergences (QB-68). Those three have been resolved
# here by picking the canonical form; see design.md → Fix 3 → step 4.
#
# For anchors where the body <h3> currently carries a decorative suffix
# (e.g. "Missed Calls Analysis", "Channel Usage (14-day)") or a wholly
# different section title (e.g. "Amazon Connect Global Resiliency" vs the ES
# label "Global Resiliency (ACGR)"), the CHECK_NAMES value is the ES-side
# canonical. The section renderer either composes the decorated form as
# `f"{CHECK_NAMES[anchor]} <suffix>"` (decorative case) or leaves the literal
# <h3> alone (substantive-difference case — noted inline where it occurs).
#
# The full anchor → (ES name, body <h3>) enumeration used to build this dict
# is:
#
#   sec-identity          Identity Management                            Identity Management
#   sec-s3-encryption     S3 Data Encryption                             S3 Data Encryption
#   sec-stream-encryption Streaming Encryption*                          Data Streaming Encryption*   → canonical: "Data Streaming Encryption"
#   res-acgr              Global Resiliency (ACGR)                       Amazon Connect Global Resiliency   (section-title divergence; h3 left literal)
#   res-carrier           Carrier Diversity                              Carrier Diversity with Amazon Connect Phone Numbers   (section-title divergence; h3 left literal)
#   ai-r1                 Knowledge Base Sync Health                     Knowledge Base Sync Health
#   ops-api-throttle      Amazon Connect API Throttling (Account Level)  Amazon Connect API Throttling (Account Level)
#   ch-missed             Missed Calls                                   Missed Calls Analysis   (decorative "Analysis" suffix)
#   ops-capacity          Amazon Connect Instance Resource Limits        Amazon Connect Instance Resource Limits
#   cap-concurrency       Amazon Connect Concurrency Limits              Amazon Connect Concurrency Limits
#   cap-api-limits        Amazon Connect API Limits (Account Level)*     Account Level Amazon Connect API Limits*   → canonical: "Amazon Connect API Limits (Account Level)"
#   ai-q1                 Amazon Connect AI Agents Limits                Amazon Connect AI Agents Limits
#   mon-alarms            CloudWatch Alarm Validation - Amazon Connect   CloudWatch Alarm Validation - Amazon Connect
#   ops-logging           Contact Flow Logging*                          Contact Flows Missing Logging*   → canonical: "Contact Flow Logging"
#   ops-kvs-retention     KVS Retention Period                           Kinesis Video Stream Retention   (section-title divergence; h3 left literal)
#   obs-log-groups        CloudWatch Log Retention                       CloudWatch Log Groups   (section-title divergence; h3 left literal)
#   obs-kinesis-streams   Kinesis Data Streams                           Kinesis Data Streams — Recommended Alarm Coverage   (decorative suffix)
#   cost-phone            Phone Number Distribution                      Phone Number Distribution
#   cost-phone-health     Misconfigured Phone Numbers                    Misconfigured Phone Numbers
#   cost-channel          Channel Usage                                  Channel Usage ({days_back}-day)   (decorative day-count suffix)
#
# Starred rows (*) are the three divergences resolved by this constant per
# QB-68. All other rows kept their pre-existing ES name as the canonical to
# preserve byte-identical rendered output.
CHECK_NAMES: dict = {
    "sec-identity": "Identity Management",
    "sec-s3-encryption": "S3 Data Encryption",
    "sec-stream-encryption": "Data Streaming Encryption",
    "res-acgr": "Global Resiliency (ACGR)",
    "res-carrier": "Carrier Diversity",
    "ai-r1": "Knowledge Base Sync Health",
    "ops-api-throttle": "Amazon Connect API Throttling (Account Level)",
    "ch-missed": "Missed Calls",
    "ops-capacity": "Amazon Connect Instance Resource Limits",
    "cap-concurrency": "Amazon Connect Concurrency Limits",
    "cap-api-limits": "Amazon Connect API Limits (Account Level)",
    "ai-q1": "Amazon Connect AI Agents Limits",
    "mon-alarms": "CloudWatch Alarm Validation - Amazon Connect",
    "ops-logging": "Contact Flow Logging",
    "ops-kvs-retention": "KVS Retention Period",
    "obs-log-groups": "CloudWatch Log Retention",
    "obs-kinesis-streams": "Kinesis Data Streams",
    "cost-phone": "Phone Number Distribution",
    "cost-phone-health": "Misconfigured Phone Numbers",
    "cost-channel": "Channel Usage",
}


def _assemble_cross_analyzer_data(findings_map):
    """Returns updated findings_map with cross-analyzer data merged.

    Cross-analyzer dependencies:
    - Instance Info needs: storage_configs (from security findings)
    - OpEx needs: api_throttling (from cloudtrail findings)
    - Cost needs: phone_numbers (already in cost; from shared data)
    - Observability needs: contact_flow_logging, kvs_retention (from opex findings)

    Additional cross-analyzer data flows handled here:
    - OpEx also needs: missed_calls_metrics (from observability findings),
      channel_mix (from cost findings)

    Data flow direction:
        security analyzer  ──► Instance Info renderer (storage_configs)
        cloudtrail analyzer ──► OpEx renderer (api_throttling)
        shared data (S3)   ──► Cost renderer (phone_numbers via telephony_usage)
        opex analyzer      ──► Observability renderer (contact_flow_logging,
                                                       kvs_retention)

    This function copies relevant keys from source analyzer findings into the
    target section's findings dict. It does NOT mutate the original dicts — it
    creates new merged copies where needed. When upstream data is missing or
    malformed, empty structures are provided to ensure graceful degradation.

    Args:
        findings_map (dict): componentType -> S3 document with findings.

    Returns:
        dict: Updated findings_map with cross-analyzer data merged in.
    """
    merged = dict(findings_map)  # shallow copy of the map

    # Extract findings sub-dicts from each analyzer's document structure.
    # Each document is either {findings: {...}, ...} or just {...} (flat).
    # Defensive: if an entry is not a dict (malformed), treat as empty (Req 5.5).
    def _safe_get_findings(doc):
        """Extract findings from a document, returning empty dict on error."""
        if not isinstance(doc, dict):
            return {}
        findings = doc.get("findings", doc)
        if not isinstance(findings, dict):
            return {}
        return findings

    opex_doc = findings_map.get("operational_excellence", {})
    if not isinstance(opex_doc, dict):
        logger.warning(
            "operational_excellence entry is not a dict; using empty findings (Req 5.5)."
        )
        opex_doc = {}
    opex_findings = _safe_get_findings(opex_doc)

    obs_doc = findings_map.get("observability", {})
    if not isinstance(obs_doc, dict):
        logger.warning(
            "observability entry is not a dict; using empty findings (Req 5.5)."
        )
        obs_doc = {}
    obs_findings = _safe_get_findings(obs_doc)

    cost_doc = findings_map.get("cost", {})
    if not isinstance(cost_doc, dict):
        logger.warning("cost entry is not a dict; using empty findings (Req 5.5).")
        cost_doc = {}
    cost_findings = _safe_get_findings(cost_doc)

    security_doc = findings_map.get("security", {})
    if not isinstance(security_doc, dict):
        logger.warning("security entry is not a dict; using empty findings (Req 5.5).")
        security_doc = {}
    security_findings = _safe_get_findings(security_doc)

    cloudtrail_doc = findings_map.get("cloudtrail", {})
    if not isinstance(cloudtrail_doc, dict):
        logger.warning(
            "cloudtrail entry is not a dict; using empty findings (Req 5.5)."
        )
        cloudtrail_doc = {}
    cloudtrail_findings = _safe_get_findings(cloudtrail_doc)

    # ── Merge into Observability section ──
    if "observability" in findings_map:
        try:
            obs_merged = dict(obs_findings)
            cross_keys_from_opex = [
                "contact_flow_logging",
                "kvs_retention",
            ]
            for key in cross_keys_from_opex:
                if key not in obs_merged and key in opex_findings:
                    obs_merged[key] = opex_findings[key]

            # QB-64: Hoist missed_calls_metrics into a `missed_calls` key so
            # the (moved) rendering block in _render_observability_section can
            # read it via the same shape it previously read from opex_findings.
            if "missed_calls" not in obs_merged:
                mcm = obs_findings.get("missed_calls_metrics", {})
                if mcm:
                    obs_merged["missed_calls"] = {
                        "days_back": mcm.get("days_analyzed", 14),
                        "total_missed_calls": mcm.get("total_missed_calls", 0),
                        "daily_average": mcm.get("daily_average", 0),
                        "daily_median": mcm.get("daily_median", 0),
                        "max_missed": mcm.get("max_missed", 0),
                        "min_missed": mcm.get("min_missed", 0),
                        "peak_day_date": mcm.get("peak_day_date", ""),
                        "peak_day_name": mcm.get("peak_day_name", ""),
                        "peak_day_count": mcm.get("peak_day_count", 0),
                        "daily_data": mcm.get("daily_data", []),
                        "status": mcm.get("status", "info"),
                        "detail": mcm.get("detail", ""),
                    }

            if "findings" in obs_doc:
                merged["observability"] = {**obs_doc, "findings": obs_merged}
            else:
                merged["observability"] = obs_merged
        except Exception as e:
            logger.warning(
                f"Cross-analyzer merge into observability failed: {e}. "
                f"Providing unmerged findings (Req 5.4/5.5)."
            )

    # ── Merge into Opex section ──
    if "operational_excellence" in findings_map:
        try:
            opex_merged = dict(opex_findings)

            if "api_throttling" not in opex_merged:
                if cloudtrail_findings:
                    opex_merged["api_throttling"] = {
                        "total_throttled": cloudtrail_findings.get(
                            "total_throttled", 0
                        ),
                        "throttled_by_api": cloudtrail_findings.get(
                            "throttled_by_api", []
                        ),
                        "total_events_analyzed": cloudtrail_findings.get(
                            "total_events_analyzed", 0
                        ),
                        "timed_out": cloudtrail_findings.get("timed_out", False),
                        "account_id": cloudtrail_findings.get("account_id", ""),
                        "days_back": cloudtrail_findings.get("days_back", 14),
                    }

            if "missed_calls" not in opex_merged:
                mcm = obs_findings.get("missed_calls_metrics", {})
                if mcm:
                    opex_merged["missed_calls"] = {
                        "days_back": mcm.get("days_analyzed", 14),
                        "total_missed_calls": mcm.get("total_missed_calls", 0),
                        "daily_average": mcm.get("daily_average", 0),
                        "daily_median": mcm.get("daily_median", 0),
                        "max_missed": mcm.get("max_missed", 0),
                        "min_missed": mcm.get("min_missed", 0),
                        "peak_day_date": mcm.get("peak_day_date", ""),
                        "peak_day_name": mcm.get("peak_day_name", ""),
                        "peak_day_count": mcm.get("peak_day_count", 0),
                        "daily_data": mcm.get("daily_data", []),
                        "status": mcm.get("status", "info"),
                        "detail": mcm.get("detail", ""),
                    }
                opex_merged["missed_calls_metrics"] = mcm

            if "channel_usage" not in opex_merged:
                cm = cost_findings.get("channel_mix", {})
                if cm and cm.get("channels"):
                    channels_dict = {}
                    for ch in cm.get("channels", []):
                        ch_name = ch.get("channel", "UNKNOWN")
                        channels_dict[ch_name] = {
                            "CONTACTS_CREATED": ch.get("contacts_created", 0),
                            "CONTACTS_HANDLED": ch.get("contacts_handled", 0),
                            "AVG_HANDLE_TIME": ch.get("avg_handle_time_seconds", 0),
                            "SUM_HANDLE_TIME": ch.get("sum_handle_time_seconds", 0),
                        }
                    opex_merged["channel_usage"] = {
                        "days_back": opex_merged.get("days_back", 14),
                        "channels": channels_dict,
                        "status": cm.get("status", "info"),
                        "detail": cm.get("detail", ""),
                    }
                opex_merged["channel_mix"] = cm

            if "findings" in opex_doc:
                merged["operational_excellence"] = {**opex_doc, "findings": opex_merged}
            else:
                merged["operational_excellence"] = opex_merged
        except Exception as e:
            logger.warning(
                f"Cross-analyzer merge into operational_excellence failed: {e}. "
                f"Providing unmerged findings (Req 5.4/5.5)."
            )

    # ── Hoist nested fields in Cost section ──
    if "cost" in findings_map:
        try:
            cost_merged = dict(cost_findings)
            telephony = cost_merged.get("telephony_usage", {})
            if "phone_numbers" not in cost_merged and "phone_numbers" in telephony:
                cost_merged["phone_numbers"] = telephony["phone_numbers"]
            if (
                "unused_phone_numbers" not in cost_merged
                and "unused_phone_numbers" in telephony
            ):
                cost_merged["unused_phone_numbers"] = telephony["unused_phone_numbers"]
            if "findings" in cost_doc:
                merged["cost"] = {**cost_doc, "findings": cost_merged}
            else:
                merged["cost"] = cost_merged
        except Exception as e:
            logger.warning(
                f"Cross-analyzer merge into cost failed: {e}. "
                f"Providing unmerged findings (Req 5.4/5.5)."
            )

    # ── Merge AI Analyzer findings into pillar sections ──
    ai_doc = findings_map.get("ai", {})
    if isinstance(ai_doc, dict) and ai_doc:
        ai_findings_data = _safe_get_findings(ai_doc)
        ai_partial = ai_doc.get("partial", False) or ai_findings_data.get(
            "metadata", {}
        ).get("timed_out", False)

        pillar_mapping = {
            "security": "security",
            "operational_excellence": "operational_excellence",
            "resilience": "resilience",
            "observability": "observability",
        }

        for ai_pillar_key, target_component in pillar_mapping.items():
            ai_pillar_findings = ai_findings_data.get(ai_pillar_key, [])
            if not isinstance(ai_pillar_findings, list):
                ai_pillar_findings = []

            if target_component in merged:
                try:
                    target_doc = merged[target_component]
                    if not isinstance(target_doc, dict):
                        continue
                    target_findings = target_doc.get("findings", target_doc)
                    if not isinstance(target_findings, dict):
                        continue
                    target_findings_merged = dict(target_findings)
                    target_findings_merged["_ai_structured_findings"] = (
                        ai_pillar_findings
                    )
                    target_findings_merged["_ai_partial"] = ai_partial
                    if (
                        "findings" in target_doc
                        and target_doc.get("findings") is not target_doc
                    ):
                        merged[target_component] = {
                            **target_doc,
                            "findings": target_findings_merged,
                        }
                    else:
                        merged[target_component] = target_findings_merged
                except Exception as e:
                    logger.warning(
                        f"AI findings merge into {target_component} failed: {e}. "
                        f"AI findings will be omitted from this section."
                    )

    return merged


def _render_html_report(
    review_id,
    instance_id,
    instance_alias,
    account_id,
    aws_region,
    successes,
    failures,
    skipped,
    findings_map,
    instance_data=None,
):
    """Render the full HTML report.

    Sections are rendered in fixed order. Skipped analyzers are omitted entirely.
    Failed sections get error placeholders. Partial results get amber indicators.

    Args:
        review_id (str): Review ID.
        instance_id (str): Instance ID.
        instance_alias (str): Instance alias/name.
        account_id (str): AWS account ID.
        aws_region (str): AWS region.
        successes (list): Successful analyzer results.
        failures (list): Failed analyzer results.
        skipped (list): Skipped analyzer results.
        findings_map (dict): componentType -> S3 document with findings.
        instance_data (dict|None): Full instance data from describe_instance API.
            When provided, renders the detailed instance info section matching
            the monolithic Lambda. Falls back to basic rendering otherwise.

    Returns:
        str: Complete HTML report content.
    """
    now_utc = datetime.now(timezone.utc)
    timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Build lookup maps
    success_map = {r["componentType"]: r for r in successes}
    failure_map = {r["componentType"]: r for r in failures}
    skipped_set = {r["componentType"] for r in skipped}

    # ── Cross-analyzer data assembly ──
    # Some section renderers need data collected by other analyzers.
    # Merge relevant findings into unified dicts before calling renderers.
    findings_map = _assemble_cross_analyzer_data(findings_map)

    # ── AI Analyzer error/skipped handling ──
    # Requirement 10.4: If AI analyzer failed, inject error state into pillar findings
    # Requirement 10.5: If AI analyzer was skipped, omit without error indicators
    ai_is_error = "ai" in failure_map
    ai_error_message = ""
    if ai_is_error:
        ai_failure = failure_map["ai"]
        ai_error_message = (
            ai_failure.get("error")
            or ai_failure.get("Error")
            or ai_failure.get("errorMessage")
            or ai_failure.get("cause")
            or ai_failure.get("Cause")
            or "AI analysis failed"
        )
        # Inject error flag into each pillar's findings for renderers to display
        for pillar_key in ("security", "operational_excellence", "resilience"):
            if pillar_key in findings_map:
                target_doc = findings_map[pillar_key]
                if isinstance(target_doc, dict):
                    target_findings = target_doc.get("findings", target_doc)
                    if isinstance(target_findings, dict):
                        target_findings["_ai_error"] = True
                        target_findings["_ai_error_message"] = ai_error_message

    # Collect individual checks from all analyzer findings for the executive summary.
    # First try checks from the S3 document (pre-computed by analyzers).
    # These will be supplemented/replaced by checks returned from section renderers.
    all_checks = []

    # Build HTML
    html_parts = []
    html_parts.append(_render_html_head(instance_alias, timestamp_str))

    # Placeholder for executive summary — will be inserted after sections render
    exec_summary_index = len(html_parts)
    html_parts.append("")  # placeholder

    # Render instance information section — use full renderer if instance_data
    # is available (for pixel-perfect parity with monolithic Lambda), otherwise
    # fall back to the basic version.
    if instance_data and instance_data.get("Id"):
        # Extract storage_configs from security analyzer findings (Req 1.7).
        # The cross-analyzer assembly makes this available in findings_map.
        # Defensive: handle missing or non-dict security data (Req 5.4/5.7).
        security_doc = findings_map.get("security", {})
        if not isinstance(security_doc, dict):
            security_doc = {}
        security_findings = security_doc.get("findings", security_doc)
        if not isinstance(security_findings, dict):
            security_findings = {}
        storage_configs = security_findings.get("storage_configs", [])
        if not isinstance(storage_configs, list):
            storage_configs = []
        html_parts.append(
            _render_instance_info_section(
                instance_data, storage_configs=storage_configs
            )
        )
    else:
        html_parts.append(
            _render_instance_info(
                instance_id,
                instance_alias,
                account_id,
                aws_region,
                review_id,
                timestamp_str,
            )
        )

    # Render sections in fixed order, collecting checks from each renderer
    for component_type in SECTION_ORDER:
        # Skip entirely for disabled/skipped analyzers
        if component_type in skipped_set:
            continue

        display_name = SECTION_DISPLAY_NAMES.get(component_type, component_type)

        if component_type in success_map:
            result = success_map[component_type]
            document = findings_map.get(component_type, {})
            # Defensive: ensure document is a dict (Req 5.4/5.7)
            if not isinstance(document, dict):
                document = {}
            findings = document.get("findings", result.get("findings", {}))
            # Defensive: ensure findings is a dict, never None (Req 5.4/5.7)
            if not isinstance(findings, dict):
                findings = {}
            # Detect partial status from multiple sources:
            # 1. Result-level partial flag (from Step Functions response)
            # 2. Document-level partial flag (from S3 JSON envelope)
            # 3. Findings-level timed_out flag (set by analyzers like capacity)
            is_partial = (
                result.get("partial", False)
                or document.get("partial", False)
                or findings.get("timed_out", False)
            )
            collected_count = result.get("collectedCount") or document.get(
                "collectedCount"
            )
            total_estimated = result.get("totalEstimated") or document.get(
                "totalEstimated"
            )

            section_html, section_checks = _render_section(
                display_name,
                component_type,
                findings,
                is_partial=is_partial,
                collected_count=collected_count,
                total_estimated=total_estimated,
            )
            html_parts.append(section_html)

            # Use renderer-derived checks if available, otherwise fall back
            # to checks from the S3 document
            if section_checks:
                all_checks.extend(section_checks)
            else:
                document_checks = document.get("checks", [])
                all_checks.extend(document_checks)
        elif component_type in failure_map:
            failure = failure_map[component_type]
            html_parts.append(
                _render_error_placeholder(display_name, component_type, failure)
            )
        else:
            # Component not in any result set but not skipped — render as missing
            html_parts.append(
                _render_error_placeholder(
                    display_name,
                    component_type,
                    {
                        "error": "No result received",
                        "cause": "Analyzer did not return a result",
                    },
                )
            )

    # Now render the executive summary with all collected checks and insert it
    # Build area_durations from analyzer result timings
    area_durations = {}
    for component_type in SECTION_ORDER:
        if component_type in success_map:
            result = success_map[component_type]
            duration_ms = result.get("durationMs", 0)
            if duration_ms:
                display_name_dur = SECTION_DISPLAY_NAMES.get(
                    component_type, component_type
                )
                area_durations[display_name_dur] = duration_ms / 1000.0
    html_parts[exec_summary_index] = _render_executive_summary(
        all_checks, area_durations
    )

    html_parts.append(_render_html_footer())

    # Assemble the raw HTML
    html_output = "\n".join(html_parts)

    # ── Post-processing: make each section collapsible ──
    # Iterates through the HTML and wraps each <div class="section"...><h3>...</h3>
    # block with section-header/section-body for the collapsible JS behavior.
    # Uses a proper div-depth counter to handle nested divs correctly (the regex
    # approach fails when sections contain inner <div> elements).
    def _make_sections_collapsible(html):
        """Walk the HTML and wrap each section's h3 with collapsible controls.

        When DEVOPS_AGENT_SPACE_ID is configured, each section header also gets
        an 'Ask DevOps Agent' button that opens the CLI command generator modal
        pre-populated with the section's title and plain-text content.
        """
        result = []
        i = 0
        section_pattern = re.compile(
            r'<div\s+class="section"([^>]*)>\s*<h3([^>]*)>(.*?)</h3>', re.DOTALL
        )
        inject_agent_btn = bool(DEVOPS_AGENT_SPACE_ID)

        while i < len(html):
            match = section_pattern.search(html, i)
            if not match:
                result.append(html[i:])
                break

            # Append everything before this match
            result.append(html[i : match.start()])

            attrs = match.group(1) or ""
            h3_attrs = match.group(2) or ""
            h3_content = match.group(3)

            # Find the matching closing </div> by counting nested divs
            # Start just after the opening <div class="section"...>
            content_start = match.end()
            depth = 1  # We're inside the section div
            pos = match.start() + len(f'<div class="section"{attrs}>')

            # Actually start scanning from after the full match (after </h3>)
            scan_pos = content_start
            while scan_pos < len(html) and depth > 0:
                next_open = html.find("<div", scan_pos)
                next_close = html.find("</div>", scan_pos)

                if next_close == -1:
                    # No more closing divs, break
                    break

                if next_open != -1 and next_open < next_close:
                    # Check it's actually a div tag (not e.g. <divider>)
                    after_div = (
                        html[next_open + 4 : next_open + 5]
                        if next_open + 4 < len(html)
                        else ""
                    )
                    if after_div in (">", " ", "\t", "\n", "\r"):
                        depth += 1
                    scan_pos = next_open + 4
                else:
                    depth -= 1
                    if depth == 0:
                        # Found the matching closing </div>
                        inner_content = html[content_start:next_close]

                        # Build the DevOps Agent button if configured
                        agent_btn = ""
                        if inject_agent_btn:
                            # Extract section id from attrs
                            id_match = re.search(r'id="([^"]+)"', attrs)
                            check_id = id_match.group(1) if id_match else ""
                            # Strip HTML from title for the button attribute
                            plain_title = re.sub(r"<[^>]+>", "", h3_content).strip()[
                                :160
                            ]
                            # Build plain-text context from section body
                            plain_ctx = _plain_text(inner_content)
                            # Escape for HTML attributes
                            from html import escape as _esc

                            if check_id:
                                agent_btn = (
                                    f'<button class="ask-agent-btn" type="button" '
                                    f'onclick="event.stopPropagation(); openAgentModal(this)" '
                                    f'data-check-id="{_esc(check_id, quote=True)}" '
                                    f'data-check-title="{_esc(plain_title, quote=True)}" '
                                    f'data-check-context="{_esc(plain_ctx, quote=True)}" '
                                    f'title="Open an AWS DevOps Agent investigation for this check">'
                                    f"&#128172; Ask DevOps Agent</button>"
                                )

                        # Build the collapsible section
                        result.append(
                            f'<div class="section"{attrs}>'
                            f'<div class="section-header" onclick="toggleSection(this)">'
                            f'<span class="toggle-arrow">&#9660;</span>'
                            f'<h3 style="margin:0;">{h3_content}</h3>'
                            f"{agent_btn}</div>"
                            f'<div class="section-body">{inner_content}</div></div>'
                        )
                        i = next_close + len("</div>")
                        break
                    scan_pos = next_close + len("</div>")
            else:
                # Depth never reached 0 — malformed HTML, output as-is
                result.append(html[match.start() : content_start])
                i = content_start

        return "".join(result)

    html_output = _make_sections_collapsible(html_output)

    # ── DevOps Agent modal injection ──
    # When the Agent Space ID is configured, inject the modal HTML just before
    # the closing </body> tag so it's available for per-section buttons.
    if DEVOPS_AGENT_SPACE_ID:
        instance_arn = (
            f"arn:aws:connect:{aws_region}:{account_id}:instance/{instance_id}"
        )
        modal_html = build_devops_agent_modal(
            DEVOPS_AGENT_SPACE_ID, instance_arn, aws_region
        )
        html_output = html_output.replace("</body>", f"{modal_html}\n</body>", 1)

    return html_output


# ── DevOps Agent Helpers ──


def _plain_text(html_str, limit=9000):
    """Strip HTML tags and collapse whitespace to produce a plain-text string.

    Used to generate data-check-context attributes for the DevOps Agent modal.

    Args:
        html_str (str): Raw HTML content.
        limit (int): Maximum character length for the output (default 9000).

    Returns:
        str: Plain text with HTML tags stripped and whitespace collapsed,
             truncated to limit characters at a character boundary.
    """
    if not html_str:
        return ""
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", html_str)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate at limit
    if len(text) > limit:
        text = text[:limit]
    return text


def build_devops_agent_modal(agent_space_id, instance_arn, aws_region):
    """Generate the DevOps Agent modal HTML/CSS/JS.

    Produces a self-contained modal dialog that generates AWS CLI commands
    for the DevOps Agent create-backlog-task workflow. Matches the monolithic
    implementation (PR14) with proper --agent-space-id, --task-type INVESTIGATION,
    structured description with skill reference, and TASK_ID capture.

    Args:
        agent_space_id (str): The DevOps Agent Space ID.
        instance_arn (str): The Connect instance ARN.
        aws_region (str): The AWS region.

    Returns:
        str: Complete HTML string containing modal markup, embedded CSS, and JS.
    """
    prefill_space = escape(agent_space_id or "")
    src_instance = escape(instance_arn or "")
    src_region = escape(aws_region or "us-east-1")
    # NOTE: doubled braces {{ }} are literal braces in this f-string.
    return f"""
    <style>
        .ask-agent-btn {{
            margin-left: auto;
            background: #232F3E;
            color: #FF9900;
            border: 1px solid #FF9900;
            border-radius: 6px;
            padding: 5px 12px;
            font-size: 0.78rem;
            font-weight: 700;
            cursor: pointer;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .ask-agent-btn:hover {{ background: #FF9900; color: #232F3E; }}
        .agent-overlay {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.55);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }}
        .agent-overlay.open {{ display: flex; }}
        .agent-modal {{
            background: #fff;
            width: 92%;
            max-width: 640px;
            border-radius: 12px;
            box-shadow: 0 12px 40px rgba(0,0,0,0.3);
            overflow: hidden;
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        .agent-modal-head {{
            background: linear-gradient(135deg, #232F3E 0%, #37475A 100%);
            color: #fff;
            padding: 16px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .agent-modal-head h3 {{ color: #fff; margin: 0; font-size: 1.02rem; }}
        .agent-modal-head .sub {{ font-size: 0.72rem; opacity: 0.7; margin-top: 2px; }}
        .agent-close {{ background: none; border: none; color: #fff; font-size: 1.4rem; cursor: pointer; line-height: 1; }}
        .agent-modal-body {{ padding: 18px 20px; max-height: 72vh; overflow-y: auto; }}
        .agent-field {{ margin-bottom: 14px; }}
        .agent-field label {{ display: block; font-size: 0.78rem; font-weight: 600; color: #334155; margin-bottom: 4px; }}
        .agent-field input, .agent-field textarea {{
            width: 100%;
            padding: 8px 10px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            font-size: 0.85rem;
            font-family: inherit;
        }}
        .agent-field textarea {{ resize: vertical; min-height: 64px; }}
        .agent-hint {{ font-size: 0.7rem; color: #64748b; margin-top: 3px; }}
        .agent-actions {{ display: flex; gap: 10px; justify-content: flex-end; margin-top: 6px; }}
        .agent-btn-primary {{
            background: #FF9900; color: #232F3E; border: none; border-radius: 6px;
            padding: 9px 18px; font-weight: 700; font-size: 0.85rem; cursor: pointer;
        }}
        .agent-btn-secondary {{
            background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; border-radius: 6px;
            padding: 9px 18px; font-weight: 600; font-size: 0.85rem; cursor: pointer;
        }}
        .agent-cmd-wrap {{ margin-top: 14px; display: none; }}
        .agent-cmd-wrap.show {{ display: block; }}
        .agent-cmd-head {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }}
        .agent-cmd-head label {{ font-size: 0.78rem; font-weight: 700; color: #334155; }}
        .agent-copy-btn {{
            background: #232F3E; color: #fff; border: none; border-radius: 5px;
            padding: 5px 12px; font-size: 0.74rem; font-weight: 700; cursor: pointer;
        }}
        .agent-copy-btn:hover {{ background: #FF9900; color: #232F3E; }}
        .agent-cmd {{
            background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px 14px;
            font-family: 'Consolas','Monaco',monospace; font-size: 0.72rem; line-height: 1.5;
            white-space: pre; overflow-x: auto; margin: 0;
        }}
        .agent-note {{ font-size: 0.7rem; color: #475569; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; margin-top: 10px; }}
    </style>
    <div class="agent-overlay" id="agentOverlay">
      <div class="agent-modal" role="dialog" aria-modal="true">
        <div class="agent-modal-head">
          <div>
            <h3>&#128172; Ask AWS DevOps Agent</h3>
            <div class="sub" id="agentCheckLabel"></div>
          </div>
          <button class="agent-close" type="button" onclick="closeAgentModal()" aria-label="Close">&times;</button>
        </div>
        <div class="agent-modal-body">
          <div class="agent-field">
            <label for="agentSpaceId">DevOps Agent Agent Space ID</label>
            <input type="text" id="agentSpaceId" placeholder="e.g. as-xxxxxxxx" value="{prefill_space}">
            <div class="agent-hint">Remembered in this browser. Find it with: <code>aws devops-agent list-agent-spaces --region {src_region}</code></div>
          </div>
          <div class="agent-field">
            <label for="agentQuestion">Your question for the agent</label>
            <textarea id="agentQuestion" placeholder="Ask about this check..."></textarea>
            <div class="agent-hint">The check finding from this report is automatically attached as context.</div>
          </div>
          <div class="agent-field">
            <label for="agentPriority">Investigation priority</label>
            <select id="agentPriority" style="width:100%; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; font-size:0.85rem; font-family:inherit;">
              <option value="CRITICAL">CRITICAL</option>
              <option value="HIGH">HIGH</option>
              <option value="MEDIUM" selected>MEDIUM</option>
              <option value="LOW">LOW</option>
              <option value="MINIMAL">MINIMAL</option>
            </select>
          </div>
          <div class="agent-actions">
            <button class="agent-btn-secondary" type="button" onclick="closeAgentModal()">Cancel</button>
            <button class="agent-btn-primary" type="button" onclick="generateAgentCommand()">Generate CLI command</button>
          </div>

          <div class="agent-cmd-wrap" id="agentCmdWrap">
            <div class="agent-cmd-head">
              <label>Run in your authenticated AWS CLI or CloudShell</label>
              <button class="agent-copy-btn" type="button" onclick="copyAgentCommand()" id="agentCopyBtn">Copy</button>
            </div>
            <pre class="agent-cmd" id="agentCmd"></pre>
            <div class="agent-note">
              This runs <code>devops-agent create-backlog-task</code> to open an investigation
              (using the <code>connect-ops-review-investigation</code> skill), then <code>get-backlog-task</code> /
              <code>list-recommendations</code> to read the result. It uses your own AWS credentials -
              nothing is sent from this report and no secret is stored.
            </div>
          </div>
        </div>
      </div>
    </div>
    <script>
      var AGENT_SRC = {{ instance: "{src_instance}", region: "{src_region}" }};
      var _agentCheck = {{ id: '', title: '', context: '' }};

      function openAgentModal(btn) {{
          _agentCheck.id = btn.getAttribute('data-check-id') || '';
          _agentCheck.title = btn.getAttribute('data-check-title') || '';
          _agentCheck.context = btn.getAttribute('data-check-context') || '';
          document.getElementById('agentCheckLabel').textContent = _agentCheck.title || _agentCheck.id;
          document.getElementById('agentQuestion').value = 'Why does the "' + _agentCheck.title + '" check report this finding, and how do I remediate it?';
          var spaceField = document.getElementById('agentSpaceId');
          if (!spaceField.value) {{
              try {{ spaceField.value = localStorage.getItem('devopsAgentSpaceId') || ''; }} catch (e) {{}}
          }}
          document.getElementById('agentCmdWrap').classList.remove('show');
          document.getElementById('agentOverlay').classList.add('open');
      }}

      function closeAgentModal() {{
          document.getElementById('agentOverlay').classList.remove('open');
      }}

      // Single-quote shell escape: wrap in single quotes, escape embedded ones.
      function _shq(s) {{
          return "'" + String(s).replace(/'/g, "'\\\\''") + "'";
      }}

      function generateAgentCommand() {{
          var space = document.getElementById('agentSpaceId').value.trim();
          var question = document.getElementById('agentQuestion').value.trim();
          var priority = document.getElementById('agentPriority').value;
          var region = AGENT_SRC.region || 'us-east-1';
          if (!space) {{
              document.getElementById('agentSpaceId').focus();
              return;
          }}
          try {{ localStorage.setItem('devopsAgentSpaceId', space); }} catch (e) {{}}

          // DevOps Agent create-backlog-task limits (API actuals).
          var TITLE_LIMIT = 400;     // title max
          var DESC_LIMIT = 10000;    // description max
          var instanceId = (AGENT_SRC.instance || '').split('/').pop() || 'unknown';

          function _clip(s, n) {{ s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '\\u2026' : s; }}

          // Title = crisp summary (<= 400 chars).
          var title = _clip('Connect Ops Review - ' + _agentCheck.title + ' [' + instanceId + ']', TITLE_LIMIT);

          // Description = investigation details (<= 10000 chars).
          // Crisp ask + skill + identifiers first, then the verbose finding.
          var ask = question || ('Why does the "' + _agentCheck.title + '" check report this finding, and how do I remediate it?');
          var head =
              'Use the "connect-ops-review-investigation" skill to investigate this Amazon Connect Operational Review finding.' +
              '\\n\\nQuestion: ' + ask +
              '\\n\\nCheck: ' + _agentCheck.title + ' (' + _agentCheck.id + ')' +
              '\\nConnect instance ID: ' + instanceId +
              '\\nConnect instance ARN: ' + AGENT_SRC.instance +
              '\\nRegion: ' + AGENT_SRC.region +
              '\\n\\nReport finding:\\n';
          var description = _clip(head + _agentCheck.context, DESC_LIMIT);

          var cmd =
              '# Open a DevOps Agent investigation for this check (uses the connect-ops-review-investigation skill)\\n' +
              'TASK_ID=$(aws devops-agent create-backlog-task \\\\\\n' +
              '  --agent-space-id ' + _shq(space) + ' \\\\\\n' +
              '  --task-type INVESTIGATION \\\\\\n' +
              '  --title ' + _shq(title) + ' \\\\\\n' +
              '  --priority ' + priority + ' \\\\\\n' +
              '  --description ' + _shq(description) + ' \\\\\\n' +
              '  --region ' + region + ' \\\\\\n' +
              '  --query task.taskId --output text)\\n\\n' +
              'echo "Investigation started: $TASK_ID"\\n\\n' +
              '# Check status (repeat until COMPLETED - usually 5-8 min)\\n' +
              'aws devops-agent get-backlog-task \\\\\\n' +
              '  --agent-space-id ' + _shq(space) + ' \\\\\\n' +
              '  --task-id "$TASK_ID" \\\\\\n' +
              '  --region ' + region + ' \\\\\\n' +
              '  --query "task.{{status:status,statusReason:statusReason,executionId:executionId}}"\\n\\n' +
              '# When COMPLETED, read the agent recommendations\\n' +
              'aws devops-agent list-recommendations \\\\\\n' +
              '  --agent-space-id ' + _shq(space) + ' \\\\\\n' +
              '  --task-id "$TASK_ID" \\\\\\n' +
              '  --region ' + region;

          document.getElementById('agentCmd').textContent = cmd;
          document.getElementById('agentCmdWrap').classList.add('show');
          document.getElementById('agentCopyBtn').textContent = 'Copy';
      }}

      function copyAgentCommand() {{
          var text = document.getElementById('agentCmd').textContent;
          var btn = document.getElementById('agentCopyBtn');
          function done() {{ btn.textContent = 'Copied!'; setTimeout(function() {{ btn.textContent = 'Copy'; }}, 1500); }}
          if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(text).then(done, function() {{ _fallbackCopy(text, done); }});
          }} else {{
              _fallbackCopy(text, done);
          }}
      }}

      function _fallbackCopy(text, done) {{
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          try {{ document.execCommand('copy'); done(); }} catch (e) {{}}
          document.body.removeChild(ta);
      }}

      document.addEventListener('keydown', function(e) {{
          if (e.key === 'Escape') closeAgentModal();
      }});
    </script>
"""


def _render_html_head(instance_alias, timestamp_str):
    """Render the HTML document head, CSS styles, header banner, and opening body tags.

    Extracted verbatim from describe_connect_to_html() in the monolithic Lambda
    to ensure identical visual output.
    """
    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Amazon Contact Center - Operations Review</title>
            <style>
                :root {{
                    --aws-dark: #232F3E;
                    --aws-orange: #FF9900;
                    --aws-blue: #1A73E8;
                    --border: #e2e8f0;
                    --bg: #f7f8fc;
                    --card-bg: #ffffff;
                    --text: #334155;
                    --text-light: #64748b;
                }}
                * {{ box-sizing: border-box; margin: 0; padding: 0; }}
                body {{
                    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                    background: var(--bg);
                    color: var(--text);
                    line-height: 1.6;
                    padding: 0;
                }}
                /* Header banner */
                .report-header {{
                    background: linear-gradient(135deg, var(--aws-dark) 0%, #37475A 100%);
                    color: #fff;
                    padding: 28px 40px;
                    margin-bottom: 24px;
                }}
                .report-header h1 {{
                    font-size: 1.6rem;
                    font-weight: 700;
                    margin-bottom: 4px;
                }}
                .report-header .subtitle {{
                    font-size: 0.85rem;
                    opacity: 0.7;
                }}
                .report-header .generated {{
                    font-size: 0.8rem;
                    opacity: 0.55;
                    margin-top: 8px;
                }}
                /* Container */
                .container {{
                    max-width: 1320px;
                    margin: 0 auto;
                    padding: 0 24px 40px;
                }}
                /* Section cards */
                .section {{
                    background: var(--card-bg);
                    border-radius: 10px;
                    padding: 24px 28px;
                    margin-bottom: 20px;
                    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
                    border: 1px solid var(--border);
                }}
                /* Headings */
                h2 {{
                    font-size: 1.3rem;
                    font-weight: 700;
                    color: var(--aws-dark);
                    margin: 32px 0 14px;
                    padding-bottom: 8px;
                    border-bottom: 3px solid var(--aws-orange);
                    display: inline-block;
                }}
                h3 {{
                    font-size: 1.05rem;
                    font-weight: 600;
                    color: var(--aws-dark);
                    margin: 18px 0 10px;
                }}
                h4 {{
                    font-size: 0.92rem;
                    font-weight: 600;
                    color: var(--aws-orange);
                    margin: 14px 0 8px;
                }}
                /* Tables */
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    font-size: 0.88rem;
                    margin: 10px 0;
                    border-radius: 8px;
                    overflow: hidden;
                }}
                th {{
                    background: var(--aws-dark);
                    color: #fff;
                    padding: 10px 14px;
                    text-align: left;
                    font-weight: 600;
                    font-size: 0.82rem;
                    text-transform: uppercase;
                    letter-spacing: 0.3px;
                }}
                td {{
                    padding: 9px 14px;
                    border-bottom: 1px solid var(--border);
                    vertical-align: top;
                }}
                tr:nth-child(even) td {{
                    background: #f8fafc;
                }}
                tr:hover td {{
                    background: #eef2ff;
                }}
                /* Recommendations */
                ul {{
                    margin: 8px 0 8px 20px;
                    font-size: 0.88rem;
                    color: var(--text);
                }}
                li {{
                    margin-bottom: 6px;
                    line-height: 1.5;
                }}
                /* Links */
                a {{
                    color: var(--aws-blue);
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                /* Paragraphs */
                p {{
                    margin: 8px 0;
                    font-size: 0.9rem;
                }}
                em {{
                    color: var(--text-light);
                    font-size: 0.82rem;
                }}
                /* Color-coded percentage cells */
                td[style*="background-color"] {{
                    font-weight: 600;
                    text-align: center;
                    border-radius: 4px;
                }}
                /* Code / ARN styling */
                code, .arn {{
                    font-family: 'Consolas', 'Monaco', monospace;
                    font-size: 0.82rem;
                    background: #f1f5f9;
                    padding: 2px 6px;
                    border-radius: 4px;
                    color: #475569;
                }}
                /* Print styles */
                @media print {{
                    body {{ background: #fff; }}
                    .section {{ box-shadow: none; border: 1px solid #ddd; page-break-inside: avoid; }}
                    .report-header {{ background: var(--aws-dark); -webkit-print-color-adjust: exact; }}
                    .back-to-top {{ display: none; }}
                }}
                /* Back to top button */
                .back-to-top {{
                    position: fixed;
                    bottom: 30px;
                    right: 30px;
                    width: 44px;
                    height: 44px;
                    border-radius: 50%;
                    background: var(--aws-dark);
                    color: #fff;
                    border: none;
                    cursor: pointer;
                    font-size: 1.2rem;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
                    display: none;
                    align-items: center;
                    justify-content: center;
                    z-index: 999;
                    transition: opacity 0.3s;
                }}
                .back-to-top:hover {{
                    background: var(--aws-orange);
                }}
                /* Collapsible sections */
                .section-header {{
                    display: flex;
                    align-items: center;
                    cursor: pointer;
                    user-select: none;
                }}
                .section-header:hover {{
                    opacity: 0.8;
                }}
                .toggle-arrow {{
                    display: inline-block;
                    width: 20px;
                    font-size: 0.8rem;
                    color: var(--text-light);
                    transition: transform 0.2s;
                    flex-shrink: 0;
                }}
                .toggle-arrow.collapsed {{
                    transform: rotate(-90deg);
                }}
                .section-body {{
                    transition: max-height 0.3s ease;
                    overflow: hidden;
                }}
                .section-body.collapsed {{
                    display: none;
                }}
                .expand-controls {{
                    display: flex;
                    gap: 8px;
                    margin: 0 0 16px 0;
                }}
                .expand-btn {{
                    padding: 6px 14px;
                    border: 1px solid var(--border);
                    border-radius: 6px;
                    background: var(--card-bg);
                    color: var(--text);
                    font-size: 0.8rem;
                    cursor: pointer;
                    font-weight: 600;
                }}
                .expand-btn:hover {{
                    background: #f1f5f9;
                }}
                /* Partial result amber indicator */
                .partial-indicator {{
                    background: #fff3cd;
                    border: 1px solid #ffc107;
                    border-radius: 4px;
                    padding: 10px 15px;
                    margin: 10px 0;
                    color: #856404;
                }}
                /* Error placeholder */
                .error-placeholder {{
                    background: #f8d7da;
                    border: 1px solid #f5c6cb;
                    border-radius: 4px;
                    padding: 15px;
                    margin: 10px 0;
                    color: #721c24;
                }}
            </style>
        </head>
        <body>
            <button class="back-to-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="Back to top">&#8679;</button>
            <script>
                window.addEventListener('scroll', function() {{
                    var btn = document.querySelector('.back-to-top');
                    if (window.scrollY > 400) {{
                        btn.style.display = 'flex';
                    }} else {{
                        btn.style.display = 'none';
                    }}
                }});
            </script>
            <script>
            function toggleSection(el) {{
                var body = el.parentElement.querySelector('.section-body');
                var arrow = el.querySelector('.toggle-arrow');
                if (body.classList.contains('collapsed')) {{
                    body.classList.remove('collapsed');
                    arrow.classList.remove('collapsed');
                }} else {{
                    body.classList.add('collapsed');
                    arrow.classList.add('collapsed');
                }}
            }}
            function expandAll() {{
                document.querySelectorAll('.section-body.collapsed').forEach(function(el) {{
                    el.classList.remove('collapsed');
                }});
                document.querySelectorAll('.toggle-arrow.collapsed').forEach(function(el) {{
                    el.classList.remove('collapsed');
                }});
            }}
            function collapseAll() {{
                document.querySelectorAll('.section-body').forEach(function(el) {{
                    el.classList.add('collapsed');
                }});
                document.querySelectorAll('.toggle-arrow').forEach(function(el) {{
                    el.classList.add('collapsed');
                }});
            }}
            var _allExpanded = true;
            function toggleAll() {{
                var btn = document.getElementById('toggleAllBtn');
                if (_allExpanded) {{
                    collapseAll();
                    btn.innerHTML = '&#9660; Expand All';
                    _allExpanded = false;
                }} else {{
                    expandAll();
                    btn.innerHTML = '&#9650; Collapse All';
                    _allExpanded = true;
                }}
            }}
            </script>
            <div class="report-header">
                <h1>Amazon Contact Center - Operations Review</h1>
                <div class="subtitle">Automated operational health assessment</div>
                <div class="generated">Generated on: {escape(timestamp_str)} UTC</div>
            </div>
            <div class="container">
            <div class="expand-controls">
                <button class="expand-btn" id="toggleAllBtn" onclick="toggleAll()">&#9650; Collapse All</button>
            </div>
            <!--SUMMARY_PLACEHOLDER-->
"""


def _render_page_skeleton(timestamp_str, content=""):
    """Return the full HTML page wrapper with head, header, footer and a content placeholder.

    Combines _render_html_head() and _render_html_footer() into a single template.
    If content is provided, it is inserted in place of the content placeholder.
    If content is empty, the string '{{CONTENT}}' is left as a placeholder marker
    for later substitution.

    This function is the single entry point for producing the complete HTML document
    structure, ensuring CSS, JavaScript (collapsible sections, back-to-top), header
    banner, and footer are always consistent with the monolithic Lambda output.

    Args:
        timestamp_str (str): Formatted timestamp for the report header
            (e.g., "2025-01-15 14:30:00 UTC").
        content (str): HTML content to insert between header and footer.
            If empty, '{{CONTENT}}' placeholder is used.

    Returns:
        str: Complete HTML document string with content inserted or placeholder marker.
    """
    head = _render_html_head("", timestamp_str)
    footer = _render_html_footer()

    if content:
        return head + "\n" + content + "\n" + footer
    else:
        return head + "\n{{CONTENT}}\n" + footer


def _render_executive_summary(checks, area_durations=None):
    """Render the Executive Summary section with pass/warn/fail/info summary table.

    Builds the full executive summary matching the monolithic Lambda's output:
    - Summary cards showing pass/warn/fail/info/total counts
    - Area-grouped breakdown table with individual check statuses
    - Color-coded rows with status icons and detail text
    - Optional area duration display in the Duration column

    Extracted verbatim from the monolithic lambda_function.py executive summary
    rendering logic to ensure identical HTML output.

    Args:
        checks (list): List of check dicts, each with keys:
            - area (str): Area name (e.g., "Security", "Resilience")
            - check (str): Check name (e.g., "Identity Management")
            - status (str): One of "pass", "fail", "warn", "info"
            - detail (str): Human-readable detail string
            - anchor (str, optional): HTML anchor ID for linking
        area_durations (dict, optional): Mapping of area name to duration in seconds.
            Used to display execution time per area in the summary table.

    Returns:
        str: HTML for the executive summary section.
    """
    if area_durations is None:
        area_durations = {}

    # ── Anchor uniqueness validation (Requirements 4.4, 4.5) ──
    # Anchors are derived deterministically from check data (area + check name),
    # so they are stable across consecutive runs against the same instance.
    # Validate uniqueness and log ERROR if duplicates found.
    anchors = [c.get("anchor", "") for c in checks if c.get("anchor")]
    seen_anchors = {}
    duplicate_anchors = []
    for c in checks:
        anchor = c.get("anchor", "")
        if not anchor:
            continue
        if anchor in seen_anchors:
            duplicate_anchors.append(
                f"Duplicate anchor '{anchor}' in area '{c.get('area', 'unknown')}' "
                f"check '{c.get('check', 'unknown')}' — conflicts with area "
                f"'{seen_anchors[anchor].get('area', 'unknown')}' check "
                f"'{seen_anchors[anchor].get('check', 'unknown')}'. "
                f"Resolve by prefixing with area name."
            )
        else:
            seen_anchors[anchor] = c

    if duplicate_anchors:
        for msg in duplicate_anchors:
            logger.error(f"Anchor uniqueness violation: {msg}")

    pass_count = sum(1 for c in checks if c["status"] == "pass")
    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    info_count = sum(1 for c in checks if c["status"] == "info")
    error_count = sum(1 for c in checks if c["status"] == "error")
    total_checks = len(checks)

    # STATUS_CONFIG keys must match VALID_CHECK_STATUSES (module-level constant).
    # If they drift, unknown statuses will silently fall through the defensive guard.
    STATUS_CONFIG = {
        "pass": {
            "icon": "&#9989;",
            "color": "#22c55e",
            "bg": "#f0fdf4",
            "border": "#bbf7d0",
            "label": "PASS",
        },
        "fail": {
            "icon": "&#10060;",
            "color": "#ef4444",
            "bg": "#fef2f2",
            "border": "#fecaca",
            "label": "FAIL",
        },
        "warn": {
            "icon": "&#9888;&#65039;",
            "color": "#f59e0b",
            "bg": "#fffbeb",
            "border": "#fde68a",
            "label": "WARN",
        },
        "info": {
            "icon": "&#128269;",
            "color": "#3b82f6",
            "bg": "#eff6ff",
            "border": "#bfdbfe",
            "label": "REVIEW",
        },
        "error": {
            "icon": "&#9940;",
            "color": "#6b7280",
            "bg": "#f3f4f6",
            "border": "#d1d5db",
            "label": "ERROR",
        },
    }
    if set(STATUS_CONFIG.keys()) != set(VALID_CHECK_STATUSES):
        logger.error(
            "STATUS_CONFIG keys %s do not match VALID_CHECK_STATUSES %s — update both together",
            sorted(STATUS_CONFIG.keys()),
            sorted(VALID_CHECK_STATUSES),
        )

    summary_html = f"""<h2>Executive Summary</h2>
        <div class="section">
            <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:16px;">
                <div style="flex:1; min-width:120px; padding:14px 18px; border-radius:8px; border:1px solid #bbf7d0; background:#f0fdf4; text-align:center;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:4px;">Passed</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#22c55e;">{pass_count}</div>
                </div>
                <div style="flex:1; min-width:120px; padding:14px 18px; border-radius:8px; border:1px solid #fde68a; background:#fffbeb; text-align:center;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:4px;">Warnings</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#f59e0b;">{warn_count}</div>
                </div>
                <div style="flex:1; min-width:120px; padding:14px 18px; border-radius:8px; border:1px solid #fecaca; background:#fef2f2; text-align:center;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:4px;">Failed</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#ef4444;">{fail_count}</div>
                </div>
                <div style="flex:1; min-width:120px; padding:14px 18px; border-radius:8px; border:1px solid #bfdbfe; background:#eff6ff; text-align:center;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:4px;">Review</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#3b82f6;">{info_count}</div>
                </div>
                <div style="flex:1; min-width:120px; padding:14px 18px; border-radius:8px; border:1px solid #d1d5db; background:#f3f4f6; text-align:center;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:4px;">Errors</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#6b7280;">{error_count}</div>
                </div>
                <div style="flex:1; min-width:120px; padding:14px 18px; border-radius:8px; border:1px solid #e2e8f0; background:#f8fafc; text-align:center;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:4px;">Total Checks</div>
                    <div style="font-size:1.3rem; font-weight:700;">{total_checks}</div>
                </div>
            </div>
            <table style="width:100%;">
                <tr><th style="width:35%;">Check</th><th style="width:12%; text-align:center;">Status</th><th>Detail</th><th style="width:8%; text-align:center;">Duration</th></tr>"""

    area_order = [
        "Security",
        "Resilience",
        "Operational Excellence",
        "Capacity Analysis",
        "Observability",
        "Cost",
    ]
    # Define check order within each area to match report section order.
    # Names come from CHECK_NAMES so this stays in sync with body <h3> renders.
    # AI-* and misc names not in CHECK_NAMES (AI checks and per-pillar capacity
    # sub-tables named at render time) stay inline.
    check_order = {
        "Security": [
            CHECK_NAMES["sec-identity"],
            CHECK_NAMES["sec-s3-encryption"],
            CHECK_NAMES["sec-stream-encryption"],
            "AI: Guardrails",
            "AI: Domain Encryption",
        ],
        "Resilience": [
            CHECK_NAMES["res-acgr"],
            CHECK_NAMES["res-carrier"],
            CHECK_NAMES["ai-r1"],
        ],
        "Operational Excellence": [
            CHECK_NAMES["ops-api-throttle"],
            CHECK_NAMES["cost-phone-health"],
            CHECK_NAMES["ops-kvs-retention"],
            "AI: Agent Inventory",
            "AI: Prompt Configuration",
        ],
        "Capacity Analysis": [
            CHECK_NAMES["ops-capacity"],
            CHECK_NAMES["cap-concurrency"],
            CHECK_NAMES["cap-api-limits"],
            "Amazon Connect Cases Limits",
            "Application Integrations Limits",
            "Customer Profiles Limits",
            CHECK_NAMES["ai-q1"],
        ],
        "Observability": [
            CHECK_NAMES["ops-logging"],
            CHECK_NAMES["mon-alarms"],
            CHECK_NAMES["obs-log-groups"],
            CHECK_NAMES["ch-missed"],
            "AI: Agent Logging",
        ],
        "Cost": [
            CHECK_NAMES["cost-phone"],
            CHECK_NAMES["cost-phone-health"],
            CHECK_NAMES["cost-channel"],
        ],
    }

    def get_check_sort_key(c):
        area_idx = area_order.index(c["area"]) if c["area"] in area_order else 99
        area_checks = check_order.get(c["area"], [])
        if c["check"] in area_checks:
            check_idx = area_checks.index(c["check"])
        else:
            check_idx = 99
        return (area_idx, check_idx)

    sorted_checks = sorted(checks, key=get_check_sort_key)

    current_area = ""
    for c in sorted_checks:
        st = c["status"]
        if st not in STATUS_CONFIG:
            st = "info"
        anchor = c.get("anchor", "")
        if c["area"] != current_area:
            current_area = c["area"]
            Area_checks = [x for x in sorted_checks if x["area"] == current_area]
            Area_fail = sum(1 for x in Area_checks if x["status"] == "fail")
            Area_warn = sum(1 for x in Area_checks if x["status"] == "warn")
            Area_pass = sum(1 for x in Area_checks if x["status"] == "pass")
            Area_info = sum(1 for x in Area_checks if x["status"] == "info")
            Area_error = sum(1 for x in Area_checks if x["status"] == "error")
            Area_badge = ""
            if Area_pass > 0:
                Area_badge += f'<span style="background:#22c55e; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.75em; margin-left:4px;">{Area_pass} passed</span>'
            if Area_fail > 0:
                Area_badge += f'<span style="background:#ef4444; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.75em; margin-left:4px;">{Area_fail} fail</span>'
            if Area_warn > 0:
                Area_badge += f'<span style="background:#f59e0b; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.75em; margin-left:4px;">{Area_warn} warn</span>'
            if Area_info > 0:
                Area_badge += f'<span style="background:#3b82f6; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.75em; margin-left:4px;">{Area_info} review</span>'
            if Area_error > 0:
                Area_badge += f'<span style="background:#6b7280; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.75em; margin-left:4px;">{Area_error} error</span>'
            area_dur = area_durations.get(current_area, "")
            dur_display = f"{area_dur}s" if area_dur else ""
            summary_html += f"""<tr style="background-color:#f1f5f9;"><td colspan="3" style="font-weight:700; font-size:0.9rem; padding:10px 14px; color:#334155;">{escape(current_area)} ({len(Area_checks)} checks) {Area_badge}</td><td style="text-align:center; font-size:0.8rem; color:#64748b;">{dur_display}</td></tr>"""
        ai_badge = (
            '<span style="background:#232F3E; color:#FF9900; padding:1px 6px; border-radius:3px; font-size:0.65rem; font-weight:700; margin-right:6px; vertical-align:middle;">AI</span>'
            if anchor.startswith("ai-")
            else ""
        )
        check_link = (
            f'<a href="#{anchor}" style="color:{STATUS_CONFIG[st]["color"]}; text-decoration:none; border-bottom:1px dashed {STATUS_CONFIG[st]["border"]}; font-weight:600;">{ai_badge}{escape(c["check"])}</a>'
            if anchor
            else f'<span style="font-weight:600;">{escape(c["check"])}</span>'
        )
        summary_html += f"""<tr style="background-color:{STATUS_CONFIG[st]["bg"]}; border-left:4px solid {STATUS_CONFIG[st]["border"]};"><td style="font-size:0.85rem;">{check_link}</td><td style="font-size:0.85rem; text-align:center; white-space:nowrap;">{STATUS_CONFIG[st]["icon"]} <span style="color:{STATUS_CONFIG[st]["color"]}; font-weight:700;">{STATUS_CONFIG[st]["label"]}</span></td><td style="font-size:0.85rem;">{escape(c["detail"])}</td><td></td></tr>"""

    summary_html += """</table></div>"""
    return summary_html


def _render_instance_info(
    instance_id, instance_alias, account_id, aws_region, review_id, timestamp_str
):
    """Render the Amazon Connect Instance Information section.

    This is the basic version used when no detailed instance data is available.
    When instance_data from PrepareContext or a pre-step is available, use
    _render_instance_info_section(instance_data) instead for full parity with
    the monolithic Lambda output.
    """
    return f"""
    <h2>Amazon Connect Instance Information</h2>
    <div class="section">
        <table>
            <tr><th>Property</th><th>Value</th></tr>
            <tr><td>Instance Alias</td><td>{escape(instance_alias)}</td></tr>
            <tr><td>Instance ID</td><td>{escape(instance_id)}</td></tr>
            <tr><td>AWS Account</td><td>{escape(account_id)}</td></tr>
            <tr><td>Region</td><td>{escape(aws_region)}</td></tr>
            <tr><td>Review ID</td><td>{escape(review_id)}</td></tr>
            <tr><td>Report Generated</td><td>{escape(timestamp_str)}</td></tr>
        </table>
    </div>
"""


def _render_instance_info_section(instance_data, storage_configs=None):
    """Render the instance details tables matching the monolithic Lambda output.

    Produces the same HTML as describe_connect_to_html() in lambda_function.py:
    - "Describe Connect" table with Instance ID, ARN, Alias, Identity Management
      Type, Status, Service Role, Created Time
    - "Call Configuration" table with Inbound/Outbound Calls Enabled
    - "Replication Configuration" section with replica details or a "no replication"
      message
    - "Data Storage Configuration" table (from security analyzer findings via
      cross-analyzer assembly)

    NOTE: The monolithic Lambda has a bug where the Replication Configuration table
    uses a regular string (not an f-string), so the {escape(...)} expressions are
    rendered as literal text in the HTML. This function reproduces that behavior
    exactly for output parity.

    Args:
        instance_data (dict): Structured instance data containing keys from the
            Connect describe_instance API response. Expected keys:
            - Id (str): Instance ID
            - Arn (str): Instance ARN
            - InstanceAlias (str): Instance alias/name
            - IdentityManagementType (str): SAML, CONNECT_MANAGED, or EXISTING_DIRECTORY
            - InstanceStatus (str): ACTIVE, CREATION_IN_PROGRESS, etc.
            - ServiceRole (str): IAM service role ARN
            - CreatedTime (str): ISO timestamp of instance creation
            - InboundCallsEnabled (bool, optional): Whether inbound calls are enabled
            - OutboundCallsEnabled (bool, optional): Whether outbound calls are enabled
            - ReplicationConfiguration (dict, optional): Raw replication config from
              the describe_instance API response
        storage_configs (list[dict] | dict | None): Storage configuration data from
            the security analyzer findings (via cross-analyzer assembly). When
            provided as a list of dicts, renders the Data Storage Configuration
            sub-section. When None or empty, the sub-section is omitted (graceful
            degradation). May also be a dict with an 'error' key for permission errors.

    Returns:
        str: HTML string for the instance information section.
    """
    if not instance_data:
        return """
    <div class="section">
        <h2>Amazon Connect Instance Information</h2>
        <p>No instance data available.</p>
    </div>
"""

    # Main instance details table — matches describe_connect_to_html() exactly
    # This section is rendered via f-string in the monolithic Lambda, so values
    # are properly interpolated.
    html_content = f"""
            <div class="section">
                <h2>Amazon Connect Instance Information</h2>
                <h3>Describe Connect</h3>
                <table>
                    <tr><th>Instance ID</th><td>{escape(instance_data.get("Id", "N/A"))}</td></tr>
                    <tr><th>Instance ARN</th><td>{escape(instance_data.get("Arn", "N/A"))}</td></tr>
                    <tr><th>Instance Alias</th><td>{escape(instance_data.get("InstanceAlias", "N/A"))}</td></tr>
                    <tr><th>Identity Management Type</th><td>{escape(instance_data.get("IdentityManagementType", "N/A"))}</td></tr>
                    <tr><th>Instance Status</th><td>{escape(instance_data.get("InstanceStatus", "N/A"))}</td></tr>
                    <tr><th>Service Role</th><td>{escape(instance_data.get("ServiceRole", "N/A"))}</td></tr>
                    <tr><th>Created Time</th><td>{escape(str(instance_data.get("CreatedTime", "N/A")))}</td></tr>
                </table>
            </div>
        """

    # Call Configuration table — rendered when inbound/outbound keys are present
    # In the monolithic Lambda, this section uses f-strings for the individual rows
    # but a regular string for the table wrapper.
    if (
        "InboundCallsEnabled" in instance_data
        or "OutboundCallsEnabled" in instance_data
    ):
        html_content += """
            <div class="section">
                <h3>Call Configuration</h3>
                <table style="width: 50%">
            """
        if "InboundCallsEnabled" in instance_data:
            html_content += f"<tr><th>Inbound Calls Enabled</th><td>{escape(str(instance_data['InboundCallsEnabled']))}</td></tr>"
        if "OutboundCallsEnabled" in instance_data:
            html_content += f"<tr><th>Outbound Calls Enabled</th><td>{escape(str(instance_data['OutboundCallsEnabled']))}</td></tr>"
        html_content += "</table></div>"

        # Replication Configuration section
        # NOTE: In the monolithic Lambda, this table uses a regular string (NOT an
        # f-string), so the {escape(replication.get(...))} expressions are rendered
        # as literal text in the HTML output. We reproduce this bug for parity.
        html_content += """<div class="section">
                <h3>Replication Configuration</h3>"""

        if "ReplicationConfiguration" in instance_data:
            # Monolithic Lambda bug: uses """...""" (not f"""...""") so escape() calls
            # appear as literal text in the output HTML.
            html_content += """<table>
                    <tr><th>Replication Region</th><td>{escape(replication.get('ReplicationRegion', 'N/A'))}</td></tr>
                    <tr><th>Replication Status</th><td>{escape(replication.get('ReplicationStatus', 'N/A'))}</td></tr>
                    <tr><th>Replication Status Message</th><td>{escape(replication.get('ReplicationStatusMessage', 'N/A'))}</td></tr>
                </table>
                </div>"""
        else:
            html_content += """<p>No replication configuration available.</p></div>"""

    # ── Data Storage Configuration sub-section ──
    # Sourced from security analyzer findings via cross-analyzer assembly.
    # Omit entirely if storage_configs is empty/None (graceful degradation per Req 5.7).
    html_content += _render_data_storage_config_table(storage_configs)

    return html_content


# ── All 13 Connect storage resource types (requirement 1.5) ──
# Maps the API resource type names to human-readable display names.
STORAGE_RESOURCE_TYPES = [
    ("CALL_RECORDINGS", "Call Recordings"),
    ("CHAT_TRANSCRIPTS", "Chat Transcripts"),
    ("SCHEDULED_REPORTS", "Scheduled Reports"),
    ("AGENT_EVENTS", "Agent Recordings"),
    ("SCREEN_RECORDINGS", "Screen Recordings"),
    ("ATTACHMENTS", "Exported Reports"),
    ("CONTACT_TRACE_RECORDS", "Contact Trace Records"),
    ("REAL_TIME_CONTACT_ANALYSIS_SEGMENTS", "Contact Lens"),
    ("CONTACT_EVALUATIONS", "Evaluations"),
    ("REAL_TIME_CONTACT_ANALYSIS_CHAT_SEGMENTS", "Real-Time Contact Analysis"),
    ("REAL_TIME_CONTACT_ANALYSIS_VOICE_SEGMENTS", "Call Recording Analysis"),
    ("MEDIA_STREAMS", "Contact Flow Logs"),
    ("EMAIL_MESSAGES", "Email Messages"),
]


def _render_data_storage_config_table(storage_configs):
    """Render the Data Storage Configuration sub-table for the Instance Info section.

    Renders all 13 Connect storage resource types as rows. Unconfigured types
    display "Not configured" spanning 3 columns via colspan. Permission errors
    produce a warning message instead of crashing.

    Args:
        storage_configs: List of dicts, each with keys:
            - resource_type (str): e.g., "Call Recordings"
            - storage_type (str): "S3", "KINESIS_VIDEO_STREAM", etc. or None
            - destination (str): S3 ARN/path or stream destination
            - encryption (str): CMK/AWS Managed/None description
            - configured (bool): Whether the resource type has storage configured

    Returns:
        HTML string for the Data Storage Configuration sub-section.
    """
    # Handle permission error: storage_configs may be a dict with an 'error' key
    # or None/empty indicating data was unavailable
    if isinstance(storage_configs, dict) and "error" in storage_configs:
        error_msg = escape(str(storage_configs["error"]))
        return f"""
            <div class="section" id="data-storage-config">
                <h3>Data Storage Configuration</h3>
                <p style="color: #f59e0b; font-weight: 600;">&#9888; Warning: Unable to retrieve storage configuration data. {error_msg}</p>
            </div>"""

    if not storage_configs:
        return ""

    # Build a lookup from resource_type display name to config entries
    config_lookup = {}
    for cfg in storage_configs:
        rt = cfg.get("resource_type", "")
        if rt not in config_lookup:
            config_lookup[rt] = []
        config_lookup[rt].append(cfg)

    # Render rows for all 13 resource types
    storage_rows = ""
    for _api_name, display_name in STORAGE_RESOURCE_TYPES:
        entries = config_lookup.get(display_name, [])

        if not entries:
            # Check if there's a title-cased version from the API name
            alt_name = _api_name.replace("_", " ").title()
            entries = config_lookup.get(alt_name, [])

        if not entries:
            # Resource type not present in data — treat as unconfigured
            storage_rows += f"""<tr><td>{escape(display_name)}</td><td style="text-align:center;" colspan="3">Not configured</td></tr>"""
        else:
            for cfg in entries:
                storage_type = cfg.get("storage_type", "N/A")
                destination = cfg.get("destination", "-")
                encryption = cfg.get("encryption", "None")
                configured = cfg.get("configured", True)

                if not configured or storage_type == "Not configured":
                    storage_rows += f"""<tr><td>{escape(display_name)}</td><td style="text-align:center;" colspan="3">Not configured</td></tr>"""
                elif storage_type.startswith("Error:"):
                    storage_rows += f"""<tr><td>{escape(display_name)}</td><td style="text-align:center;" colspan="3">{escape(storage_type)}</td></tr>"""
                else:
                    storage_rows += f"""<tr><td>{escape(display_name)}</td><td style="text-align:center;">{escape(storage_type)}</td><td style="word-break:break-all;">{escape(destination)}</td><td>{escape(encryption)}</td></tr>"""

    return f"""
            <div class="section" id="data-storage-config">
                <h3>Data Storage Configuration</h3>
                <table style="width: 100%; table-layout: fixed;">
                    <colgroup>
                        <col style="width: 25%;"><col style="width: 15%;"><col style="width: 40%;"><col style="width: 20%;">
                    </colgroup>
                    <tr><th>Resource Type</th><th style="text-align:center;">Storage Type</th><th>Destination</th><th>Encryption</th></tr>
                    {storage_rows}
                </table>
            </div>"""


def _render_section(
    display_name,
    component_type,
    findings,
    is_partial=False,
    collected_count=None,
    total_estimated=None,
):
    """Render a successful analyzer section using the dedicated section renderer.

    Dispatches to the appropriate dedicated renderer based on component_type.
    If no dedicated renderer exists, falls back to the generic findings table.
    Each dedicated renderer returns (html_content, checks_list) so the executive
    summary can be assembled from actual check results.

    If partial, includes an amber indicator with collection counts.

    Args:
        display_name (str): Human-readable section name.
        component_type (str): Analyzer component type.
        findings (dict): Analyzer findings data.
        is_partial (bool): Whether results are partial.
        collected_count (int|None): Items collected before timeout.
        total_estimated (int|None): Estimated total items.

    Returns:
        tuple: (html_string, checks_list) where html_string is the section HTML
            and checks_list is a list of dicts with area, check, status, detail,
            anchor keys for the executive summary.
    """
    # Map component types to their dedicated renderers
    renderer_map = {
        "security": _render_security_section,
        "resilience": _render_resilience_section,
        "operational_excellence": _render_opex_section,
        "capacity": _render_capacity_section,
        "observability": _render_observability_section,
        "cost": _render_cost_section,
    }

    # Dispatch to dedicated renderer if available.
    # Dedicated renderers manage their own <h2> headings and <div class="section">
    # wrappers to match the monolithic Lambda's exact HTML structure.
    renderer = renderer_map.get(component_type)
    checks = []
    if renderer:
        # Build partial indicator HTML if applicable
        partial_html = ""
        if is_partial:
            partial_msg = "&#9888;&#65039; <strong>Partial results</strong>"
            if collected_count is not None and total_estimated is not None:
                partial_msg += (
                    f" &mdash; analysis stopped after collecting "
                    f"{collected_count} of estimated {total_estimated} items "
                    f"due to time budget"
                )
            elif collected_count is not None:
                partial_msg += (
                    f" &mdash; analysis stopped after collecting "
                    f"{collected_count} items due to time budget"
                )
            else:
                partial_msg += (
                    " &mdash; analysis was incomplete due to time budget constraints"
                )
            partial_html = f'<div class="partial-indicator">{partial_msg}</div>\n'

        try:
            section_html, checks = renderer(findings)
            html = partial_html + section_html
        except Exception as e:
            logger.warning(
                f"Dedicated renderer for {component_type} failed: {e}. "
                f"Falling back to generic renderer."
            )
            html = f"""
    <h2>{escape(display_name)}</h2>
    <div class="section" data-component="{escape(component_type)}">
"""
            if partial_html:
                html += f"        {partial_html}"
            html += _render_findings_table(findings)
            html += "    </div>\n"
            checks = []
        return html, checks
    else:
        # Fallback to generic renderer for unknown component types — uses wrapper
        html = f"""
    <h2>{escape(display_name)}</h2>
    <div class="section" data-component="{escape(component_type)}">
"""
        # Render partial indicator if applicable
        if is_partial:
            partial_msg = "&#9888;&#65039; <strong>Partial results</strong>"
            if collected_count is not None and total_estimated is not None:
                partial_msg += (
                    f" &mdash; analysis stopped after collecting "
                    f"{collected_count} of estimated {total_estimated} items "
                    f"due to time budget"
                )
            elif collected_count is not None:
                partial_msg += (
                    f" &mdash; analysis stopped after collecting "
                    f"{collected_count} items due to time budget"
                )
            else:
                partial_msg += (
                    " &mdash; analysis was incomplete due to time budget constraints"
                )
            html += f'        <div class="partial-indicator">{partial_msg}</div>\n'

        html += _render_findings_table(findings)
        html += "    </div>\n"
        return html, checks


def _render_findings_table(findings):
    """Render findings as HTML tables/lists.

    Handles nested dicts and lists by rendering them recursively.

    Args:
        findings (dict): Analyzer findings data.

    Returns:
        str: HTML representation of findings.
    """
    if not findings:
        return "        <p>No findings data available.</p>\n"

    html = ""
    for key, value in findings.items():
        # Skip internal metadata fields
        if key in ("timed_out", "checks_completed", "account_id", "region"):
            continue

        display_key = key.replace("_", " ").title()

        if isinstance(value, dict):
            html += f"        <h3>{escape(display_key)}</h3>\n"
            html += "        <table>\n"
            html += "            <tr><th>Check</th><th>Value</th></tr>\n"
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, (dict, list)):
                    rendered = _render_complex_value(sub_value)
                else:
                    rendered = escape(str(sub_value))
                sub_display = sub_key.replace("_", " ").title()
                html += f"            <tr><td>{escape(sub_display)}</td><td>{rendered}</td></tr>\n"
            html += "        </table>\n"
        elif isinstance(value, list):
            html += f"        <h3>{escape(display_key)}</h3>\n"
            if value and isinstance(value[0], dict):
                # Render list of dicts as a table
                headers = list(value[0].keys())
                html += "        <table>\n"
                html += "            <tr>"
                html += "".join(
                    f"<th>{escape(h.replace('_', ' ').title())}</th>" for h in headers
                )
                html += "</tr>\n"
                for item in value[:50]:  # Limit to 50 rows
                    html += "            <tr>"
                    html += "".join(
                        f"<td>{escape(str(item.get(h, '')))}</td>" for h in headers
                    )
                    html += "</tr>\n"
                html += "        </table>\n"
                if len(value) > 50:
                    html += f"        <p><em>... and {len(value) - 50} more items</em></p>\n"
            else:
                html += "        <ul>\n"
                for item in value[:50]:
                    html += f"            <li>{escape(str(item))}</li>\n"
                html += "        </ul>\n"

    # Render simple key-value pairs as a summary table
    simple_pairs = {
        k: v
        for k, v in findings.items()
        if not isinstance(v, (dict, list))
        and k not in ("timed_out", "checks_completed", "account_id", "region")
    }
    if simple_pairs:
        html += "        <table>\n"
        html += "            <tr><th>Metric</th><th>Value</th></tr>\n"
        for key, value in simple_pairs.items():
            display_key = key.replace("_", " ").title()
            html += f"            <tr><td>{escape(display_key)}</td><td>{escape(str(value))}</td></tr>\n"
        html += "        </table>\n"

    return html


def _render_complex_value(value):
    """Render a complex value (dict or list) as inline HTML.

    Args:
        value: A dict or list to render.

    Returns:
        str: HTML representation.
    """
    if isinstance(value, dict):
        parts = [f"{escape(k)}: {escape(str(v))}" for k, v in value.items()]
        return ", ".join(parts)
    elif isinstance(value, list):
        if len(value) <= 5:
            return ", ".join(escape(str(item)) for item in value)
        else:
            shown = ", ".join(escape(str(item)) for item in value[:5])
            return f"{shown} ... (+{len(value) - 5} more)"
    return escape(str(value))


# ── Security Section Rendering ──
# Extracted verbatim from the monolithic lambda_function.py to ensure identical HTML output.
# Renders: Identity Management, Data Storage/Streaming tables, S3 Encryption,
# Streaming Encryption, AI Guardrails, AI Domain Encryption.


def _render_security_section(findings):
    """Render the Security section HTML from structured findings data.

    Produces HTML identical to the monolithic Lambda's security rendering:
    identity management check, data storage/streaming configuration tables,
    S3 encryption table, streaming encryption table, AI guardrails, and
    AI domain encryption.

    Args:
        findings (dict): Security analyzer findings containing:
            - identity_management: {identity_type, status, detail}
            - storage_configs: [{resource_type, storage_type, destination, encryption}]
            - streaming_configs: [{resource_type, stream_type, destination, encryption}]
            - s3_encryption: {total_configs, cmk_count, aws_managed_count,
                             unencrypted_count, encryption_details[]}
            - streaming_encryption: {total_streams, kvs_encrypted, kvs_unencrypted,
                                    kds_count, firehose_count, streams[]}
            - ai_guardrails: {guardrails[], missing_filters[], agent_associations}
            - domain_encryption: {has_cmk, kms_key_id, status, detail}

    Returns:
        tuple: (html_string, checks_list) where checks_list contains dicts with
            area, check, status, detail, anchor for the executive summary.
    """
    html_output = ""
    checks = []

    # ── Identity Management ──
    html_output += _render_identity_management(findings, checks)

    # ── Data Storage Configuration table ──
    html_output += _render_data_storage_table(findings)

    # ── Data Streaming Configuration table ──
    html_output += _render_data_streaming_table(findings)

    # ── S3 Data Encryption ──
    html_output += _render_s3_encryption_table(findings, checks)

    # ── Data Streaming Encryption ──
    html_output += _render_streaming_encryption_table(findings, checks)

    # ── AI Analyzer Structured Findings (from ai_analyzer.py) ──
    ai_findings_html, ai_checks = _render_ai_findings_block(
        findings.get("_ai_structured_findings", []),
        pillar_name="Security",
        is_partial=findings.get("_ai_partial", False),
        is_error=findings.get("_ai_error", False),
        error_message=findings.get("_ai_error_message", ""),
    )
    html_output += ai_findings_html
    checks.extend(ai_checks)

    return html_output, checks


def _render_identity_management(findings, checks):
    """Render the Identity Management sub-section.

    Args:
        findings (dict): Security findings.
        checks (list): Mutable list to append check results to.

    Returns:
        str: HTML for identity management.
    """
    identity_info = findings.get("identity_management", {})
    identity_type = identity_info.get("identity_type", "N/A")

    html = ""
    if identity_type == "SAML":
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-identity"],
                "status": "pass",
                "detail": "SAML 2.0 federation configured",
                "anchor": "sec-identity",
            }
        )
        html += f"""
            <h2>Security</h2>
            <div class="section" id="sec-identity">
                <h3>{CHECK_NAMES["sec-identity"]}</h3>
                <p>Current Identity Management Type: <strong>SAML</strong> &#9989; SAML 2.0 federation configured</p>
            </div>"""
    else:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-identity"],
                "status": "fail",
                "detail": f"Using {identity_type} - SAML 2.0 recommended",
                "anchor": "sec-identity",
            }
        )
        html += f"""
            <h2>Security</h2>
            <div class="section" id="sec-identity">
                <h3>{CHECK_NAMES["sec-identity"]}</h3>
                <p>Current Identity Management Type: <strong>{escape(identity_type)}</strong></p>
                {get_recommendation("identity_management")}
            </div>"""

    return html


def _render_data_storage_table(findings):
    """Render the Data Storage Configuration table.

    Args:
        findings (dict): Security findings containing storage_configs list.

    Returns:
        str: HTML for the data storage configuration table.
    """
    storage_configs = findings.get("storage_configs", [])
    if not storage_configs:
        return ""

    storage_rows = ""
    for cfg in storage_configs:
        resource_type = escape(cfg.get("resource_type", "N/A"))
        storage_type = escape(cfg.get("storage_type", "N/A"))
        destination = escape(cfg.get("destination", "-"))
        encryption = escape(cfg.get("encryption", "None"))

        # Handle "Not configured" entries (colspan=3)
        if storage_type == "Not configured":
            storage_rows += f"""<tr><td>{resource_type}</td><td style="text-align:center;" colspan="3">Not configured</td></tr>"""
        elif storage_type.startswith("Error:"):
            storage_rows += f"""<tr><td>{resource_type}</td><td style="text-align:center;" colspan="3">{storage_type}</td></tr>"""
        else:
            # Determine text-align for storage_type cell
            storage_rows += f"""<tr><td>{resource_type}</td><td style="text-align:center;">{storage_type}</td><td style="word-break:break-all;">{destination}</td><td>{encryption}</td></tr>"""

    return f"""
            <div class="section" id="data-storage">
                <h3>Data Storage Configuration</h3>
                <table style="width: 100%; table-layout: fixed;">
                    <colgroup>
                        <col style="width: 25%;"><col style="width: 15%;"><col style="width: 40%;"><col style="width: 20%;">
                    </colgroup>
                    <tr><th>Resource Type</th><th style="text-align:center;">Storage Type</th><th>Destination</th><th>Encryption</th></tr>
                    {storage_rows}
                </table>
            </div>"""


def _render_data_streaming_table(findings):
    """Render the Data Streaming Configuration table.

    Args:
        findings (dict): Security findings containing streaming_configs list.

    Returns:
        str: HTML for the data streaming configuration table.
    """
    streaming_configs = findings.get("streaming_configs", [])
    if not streaming_configs:
        return ""

    streaming_rows = ""
    for cfg in streaming_configs:
        resource_type = escape(cfg.get("resource_type", "N/A"))
        stream_type = escape(cfg.get("stream_type", "N/A"))
        destination = escape(cfg.get("destination", "-"))
        encryption = escape(cfg.get("encryption", "-"))

        streaming_rows += f"""<tr><td>{resource_type}</td><td>{stream_type}</td><td style="word-break:break-all;">{destination}</td><td>{encryption}</td></tr>"""

    return f"""
            <div class="section" id="data-streaming">
                <h3>Data Streaming Configuration</h3>
                <table style="width: 100%; table-layout: fixed;">
                    <colgroup>
                        <col style="width: 25%;"><col style="width: 15%;"><col style="width: 40%;"><col style="width: 20%;">
                    </colgroup>
                    <tr><th>Resource Type</th><th>Stream Type</th><th>Destination</th><th>Encryption</th></tr>
                    {streaming_rows}
                </table>
            </div>"""


def _render_s3_encryption_table(findings, checks):
    """Render the S3 Data Encryption sub-section.

    Args:
        findings (dict): Security findings containing s3_encryption data.
        checks (list): Mutable list to append check results to.

    Returns:
        str: HTML for S3 encryption table.
    """
    s3_enc = findings.get("s3_encryption", {})
    s3_total_configs = s3_enc.get("total_configs", 0)
    s3_cmk_count = s3_enc.get("cmk_count", 0)
    s3_aws_managed_count = s3_enc.get("aws_managed_count", 0)
    s3_none_count = s3_enc.get("unencrypted_count", 0)
    encryption_details = s3_enc.get("encryption_details", [])

    if s3_total_configs <= 0:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-s3-encryption"],
                "status": "info",
                "detail": "No S3 storage configs found",
                "anchor": "sec-s3-encryption",
            }
        )
        return f'<div class="section" id="sec-s3-encryption"><h3>{CHECK_NAMES["sec-s3-encryption"]}</h3><p>No S3 storage configurations found.</p></div>'

    # Build full encryption table
    enc_rows = ""
    for d in encryption_details:
        status = d.get("status", "None")
        if status == "CMK":
            status_html = '<span style="color:#22c55e; font-weight:600;">CMK</span>'
        elif status == "AWS Managed":
            status_html = (
                '<span style="color:#f59e0b; font-weight:600;">AWS Managed</span>'
            )
        else:
            status_html = '<span style="color:#ef4444; font-weight:600;">No KMS</span>'
        key_val = d.get("key", "")
        key_display = f"...{key_val}" if key_val else "-"
        enc_rows += f"""<tr><td>{escape(d.get("resource_type", ""))}</td><td>{escape(d.get("bucket", ""))}</td><td>{status_html}</td><td>{escape(key_display)}</td></tr>"""

    html = f"""
            <div class="section" id="sec-s3-encryption">
                <h3>{CHECK_NAMES["sec-s3-encryption"]}</h3>
                <p>{s3_total_configs} S3 storage configuration(s) found. <span style="color:#22c55e;">{s3_cmk_count} CMK</span>, <span style="color:#f59e0b;">{s3_aws_managed_count} AWS Managed</span>, <span style="color:#ef4444;">{s3_none_count} No encryption</span>.</p>
                <table style="width: 90%;">
                    <tr><th>Resource Type</th><th>S3 Bucket</th><th>Encryption</th><th>Key</th></tr>
                    {enc_rows}
                </table>"""

    if s3_none_count > 0 or s3_aws_managed_count > 0:
        html += get_recommendation("s3_encryption")
    html += """</div>"""

    if s3_none_count > 0:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-s3-encryption"],
                "status": "fail",
                "detail": f"{s3_cmk_count} CMK, {s3_aws_managed_count} AWS Managed, {s3_none_count} missing encryption",
                "anchor": "sec-s3-encryption",
            }
        )
    else:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-s3-encryption"],
                "status": "pass",
                "detail": f"All {s3_total_configs} S3 configs encrypted ({s3_cmk_count} CMK, {s3_aws_managed_count} AWS Managed)",
                "anchor": "sec-s3-encryption",
            }
        )

    return html


def _render_streaming_encryption_table(findings, checks):
    """Render the Data Streaming Encryption sub-section.

    Args:
        findings (dict): Security findings containing streaming_encryption data.
        checks (list): Mutable list to append check results to.

    Returns:
        str: HTML for streaming encryption table.
    """
    stream_enc = findings.get("streaming_encryption", {})
    streams = stream_enc.get("streams", [])

    if not streams:
        return ""

    total_streams = stream_enc.get("total_streams", len(streams))
    kvs_encrypted = stream_enc.get("kvs_encrypted", 0)
    kvs_unencrypted = stream_enc.get("kvs_unencrypted", 0)
    kds_count = stream_enc.get("kds_count", 0)
    firehose_count = stream_enc.get("firehose_count", 0)

    # Count KVS streams from data if not provided
    kvs_streams = [s for s in streams if s.get("stream_type") == "Kinesis Video Stream"]
    kds_streams = [s for s in streams if s.get("stream_type") == "Kinesis Data Stream"]
    firehose_streams = [
        s for s in streams if s.get("stream_type") == "Kinesis Firehose"
    ]

    if not kvs_encrypted and not kvs_unencrypted:
        kvs_encrypted = sum(1 for s in kvs_streams if s.get("encrypted") is True)
        kvs_unencrypted = sum(1 for s in kvs_streams if s.get("encrypted") is False)
    if not kds_count:
        kds_count = len(kds_streams)
    if not firehose_count:
        firehose_count = len(firehose_streams)

    # Build summary parts
    summary_parts = []
    if kvs_streams:
        summary_parts.append(f"{kvs_encrypted}/{len(kvs_streams)} KVS encrypted")
    if kds_streams:
        summary_parts.append(f"{len(kds_streams)} KDS (verify at stream level)")
    if firehose_streams:
        summary_parts.append(
            f"{len(firehose_streams)} Firehose (verify at stream level)"
        )

    # Build table rows
    stream_enc_rows = ""
    has_issues = False
    for s in streams:
        encrypted = s.get("encrypted")
        if encrypted is True:
            status = '<span style="color:#22c55e;">Encrypted (KMS)</span>'
        elif encrypted is False:
            status = '<span style="color:#ef4444;">No encryption configured</span>'
            has_issues = True
        else:
            status = '<span style="color:#d97706;">Verify at stream level</span>'
        rh = s.get("retention_hours")
        if rh is not None and rh > 0:
            retention = f"{rh}h"
        elif rh == 0:
            retention = "0 (no persistence)"
        else:
            retention = "-"
        stream_enc_rows += f"""<tr><td>{escape(s.get("resource_type", ""))}</td><td>{escape(s.get("stream_type", ""))}</td><td>{escape(s.get("destination", ""))}</td><td>{status}</td><td style="text-align:center;">{retention}</td></tr>"""

    # Determine check status
    if kvs_unencrypted > 0:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-stream-encryption"],
                "status": "fail",
                "detail": f"{kvs_unencrypted} KVS stream(s) missing encryption; {', '.join(summary_parts)}",
                "anchor": "sec-stream-encryption",
            }
        )
    elif kds_streams or firehose_streams:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-stream-encryption"],
                "status": "warn",
                "detail": f"{total_streams} stream(s) configured; {', '.join(summary_parts)}",
                "anchor": "sec-stream-encryption",
            }
        )
    else:
        checks.append(
            {
                "area": "Security",
                "check": CHECK_NAMES["sec-stream-encryption"],
                "status": "pass",
                "detail": f"All {total_streams} stream(s) encrypted",
                "anchor": "sec-stream-encryption",
            }
        )

    html = f"""
            <div class="section" id="sec-stream-encryption">
                <h3>{CHECK_NAMES["sec-stream-encryption"]}</h3>
                <p>{total_streams} data streaming configuration(s) found. {", ".join(summary_parts)}.</p>
                <table style="width: 90%;">
                    <tr><th>Resource Type</th><th>Stream Type</th><th>Destination</th><th>Encryption Status</th><th style="text-align:center;">Retention</th></tr>
                    {stream_enc_rows}
                </table>"""
    if has_issues or kds_streams or firehose_streams:
        html += get_recommendation("streaming_encryption")
    html += """</div>"""

    return html


def _render_resilience_section(findings):
    """Render the Resilience section HTML from structured findings data.

    Produces HTML identical to the monolithic Lambda's resilience rendering:
    Multi-AZ architecture info, ACGR replica status, carrier diversity table,
    and Knowledge Base Sync Health (AI resilience check).

    Args:
        findings (dict): Resilience analyzer findings containing:
            - multi_region: {has_replica, replication_region, replication_status,
                           replication_status_message, status, detail}
            - carrier_diversity: {total_numbers, carrier_groups, carrier_group_details,
                                 phone_numbers_by_group (optional), phone_type_counts,
                                 country_distribution, status, detail}
            - dr_readiness: {checks[], total_checks, status, detail}

    Returns:
        tuple: (html_string, checks_list) where checks_list contains dicts with
            area, check, status, detail, anchor for the executive summary.
    """
    html_output = ""
    checks = []

    # ── Section heading ──
    html_output += """<h2>Resilience</h2>
            <div class="section">
                <h3>Multi-AZ Architecture</h3>
                <p>Within each AWS Region Amazon Connect instance is created with a minimum of 3 AZs. When you create an Amazon Connect instance, that instance is propagated across those AZs in an active-active-active configuration. If there is a failure in one AZ, that node is taken out of rotation without impacting production. This architecture allows you to perform maintenance, release new features, and expand infrastructure without requiring any downtime.
                Refer <a href="https://docs.aws.amazon.com/connect/latest/adminguide/disaster-recovery-resiliency.html" target="_blank"> documentation </a> for more information</p>
            </div>"""

    # ── ACGR (Global Resiliency) Recommendation ──
    multi_region = findings.get("multi_region", {})
    has_replica = multi_region.get("has_replica", False)

    if not has_replica:
        checks.append(
            {
                "area": "Resilience",
                "check": CHECK_NAMES["res-acgr"],
                "status": "info",
                "detail": "No replica configured",
                "anchor": "res-acgr",
            }
        )
        html_output += f"""<div class="section" id="res-acgr">
                    <h3>Amazon Connect Global Resiliency</h3>
                    {get_recommendation("global_resiliency")}
                    </div>"""
    else:
        replication_region = multi_region.get("replication_region", "N/A")
        replication_status = multi_region.get("replication_status", "N/A")
        checks.append(
            {
                "area": "Resilience",
                "check": CHECK_NAMES["res-acgr"],
                "status": "pass",
                "detail": "Replica configured",
                "anchor": "res-acgr",
            }
        )
        html_output += f"""<div class="section" id="res-acgr">
                    <h3>Amazon Connect Global Resiliency</h3>
                    <p>&#9989; Global Resiliency replica configured. Region: <strong>{escape(str(replication_region))}</strong>, Status: <strong>{escape(str(replication_status))}</strong></p>
                    </div>"""

    # ── Carrier Diversity ──
    carrier_data = findings.get("carrier_diversity", {})
    carrier_group_details = carrier_data.get("carrier_group_details", {})
    phone_numbers_by_group = carrier_data.get("phone_numbers_by_group", {})
    total_numbers = carrier_data.get("total_numbers", 0)
    num_carriers = carrier_data.get("carrier_groups", 0)

    html_output += """<div class="section" id="res-carrier">
            <h3>Carrier Diversity with Amazon Connect Phone Numbers</h3>
            <table style="width: 80%">
            <tr><th>Country Code | Phone Carrier</th><th style="text-align:center;">Count</th><th>Phone Number List</th></tr>"""

    for group_key, count in carrier_group_details.items():
        # If phone number lists are available, render them as the monolithic Lambda does
        phone_list = phone_numbers_by_group.get(group_key, [])
        if phone_list:
            phone_list_display = str(phone_list)
        else:
            phone_list_display = f"[{count} number(s)]"
        html_output += f"""
                <tr><th>{escape(group_key)}</th><td>{escape(str(count))}</td><td>{phone_list_display}</td></tr>
                """

    html_output += """</table>"""
    if total_numbers > 1:
        html_output += get_recommendation("carrier_diversity")
    html_output += """</div>"""

    # Carrier diversity check for executive summary
    if num_carriers > 1:
        checks.append(
            {
                "area": "Resilience",
                "check": CHECK_NAMES["res-carrier"],
                "status": "pass",
                "detail": f"{total_numbers} numbers across {num_carriers} carrier groups",
                "anchor": "res-carrier",
            }
        )
    elif total_numbers > 0:
        checks.append(
            {
                "area": "Resilience",
                "check": CHECK_NAMES["res-carrier"],
                "status": "warn",
                "detail": f"All {total_numbers} numbers on a single carrier",
                "anchor": "res-carrier",
            }
        )
    else:
        checks.append(
            {
                "area": "Resilience",
                "check": CHECK_NAMES["res-carrier"],
                "status": "info",
                "detail": "No phone numbers configured",
                "anchor": "res-carrier",
            }
        )

    # ── AI Resilience Check (R1) - Knowledge Base Sync Health ──
    dr_readiness = findings.get("dr_readiness", {})
    dr_checks = dr_readiness.get("checks", [])

    # Find the KB sync health check in DR readiness checks
    kb_check = None
    for check in dr_checks:
        if check.get("check") == "Knowledge Base Sync Health":
            kb_check = check
            break

    if kb_check:
        html_output += f'<div class="section" id="ai-r1"><h3><span style="background:#232F3E; color:#FF9900; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:700; margin-right:8px; vertical-align:middle;">AI</span>{CHECK_NAMES["ai-r1"]}</h3>'
        html_output += (
            "<p><em>Checks knowledge base associations and content freshness.</em></p>"
        )
        # Cross-cutting ES/body contract note (QB-71 / R4): Knowledge Base
        # Sync Health is rendered inline in the Resilience section as an
        # informational signal but is intentionally not tallied in the
        # Executive Summary scoreboard.
        html_output += (
            "<p><em>Not counted in the Executive Summary &mdash; sync health "
            "is a rendered signal only, not a scored check.</em></p>"
        )

        # Render KB sync health details from findings
        kb_findings = findings.get("kb_sync_health", {})
        kb_associations = kb_findings.get("kb_associations", [])
        review_kbs = kb_findings.get("review_kbs", [])

        if kb_associations:
            html_output += "<b>Knowledge Base Sync Health</b><br>"
            html_output += f"<br>Found {len(kb_associations)} knowledge base association(s).<br><br>"
            html_output += """<table style="width: 90%"><tr><th>KB Name</th><th>KB ID</th><th>Status</th><th>Last Content Modified</th><th>Time Since Last Update</th><th>Freshness</th></tr>"""

            for kb in kb_associations:
                kb_name = escape(kb.get("name", "N/A"))
                kb_id = escape(kb.get("kb_id", "N/A"))
                kb_status = escape(kb.get("status", "N/A"))
                last_mod_str = escape(kb.get("last_modified_str", "Never"))
                time_str = escape(kb.get("time_since_update", "Unknown"))
                freshness = kb.get("freshness", "Unknown")
                color = kb.get("freshness_color", "#FFA500")
                html_output += f'<tr><td>{kb_name}</td><td>{kb_id}</td><td>{kb_status}</td><td>{last_mod_str}</td><td>{time_str}</td><td style="background-color: {escape(color)};">{escape(freshness)}</td></tr>'

            html_output += "</table>"
            html_output += """<p><em>Note: The required sync frequency depends on how often your source content changes.
            Verify that the last content modification time aligns with your expected update schedule.
            If source content has changed but the knowledge base has not been updated, check your sync configuration.</em></p>"""

            if review_kbs:
                html_output += f"""<p><strong>Knowledge base(s) to review:</strong> {", ".join(escape(kb) for kb in review_kbs)}</p>"""
                html_output += get_recommendation("kb_sync_review")
        elif kb_check.get("detail", "").startswith("No knowledge bases"):
            html_output += "<b>Knowledge Base Sync Health</b><br>"
            html_output += "<br>No knowledge bases associated with this assistant."
            html_output += get_recommendation("kb_none")
        else:
            # Fallback: render the check detail as summary
            html_output += "<b>Knowledge Base Sync Health</b><br>"
            html_output += f"<br>{escape(kb_check.get('detail', 'Knowledge base freshness checked'))}"

        html_output += "</div>"
        checks.append(
            {
                "area": "Resilience",
                "check": CHECK_NAMES["ai-r1"],
                "status": "info",
                "detail": "Knowledge base freshness checked",
                "anchor": "ai-r1",
            }
        )

    # ── AI Analyzer Structured Findings (from ai_analyzer.py) ──
    ai_findings_html, ai_checks = _render_ai_findings_block(
        findings.get("_ai_structured_findings", []),
        pillar_name="Resilience",
        is_partial=findings.get("_ai_partial", False),
        is_error=findings.get("_ai_error", False),
        error_message=findings.get("_ai_error_message", ""),
    )
    html_output += ai_findings_html
    checks.extend(ai_checks)

    return html_output, checks


def _render_opex_section(findings):
    """Render the Operational Excellence section HTML from structured findings data.

    Produces HTML identical to the monolithic Lambda's operational excellence rendering:
    Missed calls analysis and table, channel usage table, and misconfigured phone numbers.

    Args:
        findings (dict): Opex analyzer findings containing:
            - missed_calls (optional): {days_back, total_missed_calls, daily_average,
                daily_median, max_missed, min_missed, peak_day_date, peak_day_name,
                peak_day_count, daily_data: [(date_str, count), ...], status, detail}
            - channel_usage (optional): {days_back, channels: {VOICE: {CONTACTS_CREATED,
                CONTACTS_HANDLED, AVG_HANDLE_TIME, SUM_HANDLE_TIME}, ...}, status, detail}
            - misconfigured_phone_numbers (optional): {total_phone_numbers,
                misconfigured: [{phone_number, phone_number_country_code,
                phone_number_type, issue}, ...], orphaned_count, unpublished_count,
                failed_count, status, detail}
            - contact_flow_logging: {total_analyzed, flows_without_logging, ...}
            - kvs_retention: {total_streams, zero_retention_count, streams, ...}

    Returns:
        tuple: (html_string, checks_list) where checks_list contains dicts with
            area, check, status, detail, anchor for the executive summary.
    """
    html_output = ""
    checks = []

    # ── Section heading ──
    html_output += """<h2>Operational Excellence</h2>"""

    # ── API Throttling (from CloudTrail analyzer) ──
    api_throttling = findings.get("api_throttling", {})
    if api_throttling:
        total_throttled = api_throttling.get("total_throttled", 0)
        throttled_by_api = api_throttling.get("throttled_by_api", [])
        total_events = api_throttling.get("total_events_analyzed", 0)
        timed_out = api_throttling.get("timed_out", False)
        ct_account_id = api_throttling.get("account_id", "")
        ct_days_back = api_throttling.get("days_back", 14)

        html_output += f"""<div class="section" id="ops-api-throttle">
                <h3>{CHECK_NAMES["ops-api-throttle"]}</h3>"""

        partial_note = ""
        if timed_out:
            max_seconds = api_throttling.get("max_seconds", 120)
            partial_note = f' <span style="color:#f59e0b;">&#9888;&#65039; <em>Partial results - analysis stopped after {max_seconds}s time limit.</em></span>'

        html_output += f"""<p>Analyzing Amazon Connect API events over the last {ct_days_back} days.{partial_note}</p>"""
        html_output += f"""<p>Collected <strong>{total_events}</strong> Amazon Connect events during this time-period.</p>"""
        html_output += f"""<p>Found <strong>{total_throttled}</strong> throttled API calls in account {escape(str(ct_account_id))}, region {escape(str(api_throttling.get("region", "")))}.</p>"""

        if total_throttled > 0:
            html_output += """<table style="width: 60%">
                <tr><th>API Event Name</th><th>Throttle Count</th></tr>"""
            for entry in throttled_by_api:
                html_output += f"<tr><td>{escape(str(entry.get('event_name', '')))}</td><td style='text-align:center'>{entry.get('count', 0)}</td></tr>"
            html_output += "</table>"
            html_output += get_recommendation("api_throttling")
            checks.append(
                {
                    "area": "Operational Excellence",
                    "check": CHECK_NAMES["ops-api-throttle"],
                    "status": "warn",
                    "detail": f"{total_throttled} throttled API calls detected over {ct_days_back} days",
                    "anchor": "ops-api-throttle",
                }
            )
        else:
            html_output += """<p style="color:#22c55e;">&#9989; No API throttling detected during this period.</p>"""
            checks.append(
                {
                    "area": "Operational Excellence",
                    "check": CHECK_NAMES["ops-api-throttle"],
                    "status": "pass",
                    "detail": f"No throttling detected over {ct_days_back} days ({total_events} events analyzed)",
                    "anchor": "ops-api-throttle",
                }
            )

        html_output += """</div>"""

    # ── KVS Retention Period ──
    kvs_data = findings.get("kvs_retention", {})
    if kvs_data:
        kvs_streams = kvs_data.get("streams", [])
        zero_retention_count = kvs_data.get("zero_retention_count", 0)

        if kvs_streams:
            kvs_ret_rows = ""
            for s in kvs_streams:
                rh = s.get("retention_hours", 0)
                if rh == 0:
                    ret_display = '<span style="color:#ef4444; font-weight:600;">0 hours (no persistence)</span>'
                else:
                    ret_display = f'<span style="color:#22c55e;">{rh} hours</span>'
                kvs_ret_rows += f"""<tr><td>{escape(s.get("resource_type", ""))}</td><td>{escape(s.get("prefix", s.get("destination", "")))}</td><td>{ret_display}</td></tr>"""

            html_output += f"""
            <div class="section" id="ops-kvs-retention">
                <h3>Kinesis Video Stream Retention</h3>
                <p>{len(kvs_streams)} Kinesis Video Stream configuration(s) found. {len(kvs_streams) - zero_retention_count} with retention configured, {zero_retention_count} with 0h retention.</p>
                <table style="width: 80%;">
                    <tr><th>Resource Type</th><th>Stream</th><th>Retention Period</th></tr>
                    {kvs_ret_rows}
                </table>"""
            if zero_retention_count > 0:
                checks.append(
                    {
                        "area": "Operational Excellence",
                        "check": CHECK_NAMES["ops-kvs-retention"],
                        "status": "warn",
                        "detail": f"{zero_retention_count}/{len(kvs_streams)} KVS stream(s) with 0h retention (no persistence)",
                        "anchor": "ops-kvs-retention",
                    }
                )
                html_output += get_recommendation("kvs_retention")
            else:
                checks.append(
                    {
                        "area": "Operational Excellence",
                        "check": CHECK_NAMES["ops-kvs-retention"],
                        "status": "pass",
                        "detail": f"All {len(kvs_streams)} KVS stream(s) have retention > 0h",
                        "anchor": "ops-kvs-retention",
                    }
                )
            html_output += get_recommendation("kvs_retention_info")
            html_output += """</div>"""

    # ── Missed Calls Analysis (moved to Observability, QB-64) ──
    # The `missed_calls` block previously rendered here now lives in
    # `_render_observability_section` between Contact Flow Logging and
    # Log Groups so the body <h3> lands in the same pillar its Executive
    # Summary row is counted under. Data is still produced by the
    # observability analyzer (missed_calls_metrics) and is now also merged
    # into the observability findings shape by the cross-analyzer merge.

    # ── Channel Usage ──
    channel_usage_data = findings.get("channel_usage", {})
    if channel_usage_data:
        days_back = channel_usage_data.get("days_back", 14)
        channels = channel_usage_data.get("channels", {})

        if not channels:
            html_output += f"""<div class="section" id="cost-channel">
            <h3>{CHECK_NAMES["cost-channel"]} ({days_back}-day)</h3>
            <p>No contact data available for the last {days_back} days.</p>
            </div>"""
        else:
            html_output += f"""<div class="section" id="cost-channel">
        <h3>{CHECK_NAMES["cost-channel"]} ({days_back}-day)</h3>
        <table style="width: 70%">
            <tr><th>Channel</th><th>Contacts Created</th><th>Contacts Handled</th><th>Avg Handle Time</th><th>Total Handle Time</th></tr>"""

            channel_order = ["VOICE", "CHAT", "EMAIL", "TASK", "GUIDE"]
            all_channels = list(channels.keys())
            ordered = [ch for ch in channel_order if ch in all_channels]
            ordered += [ch for ch in all_channels if ch not in ordered]

            total_created = 0
            total_handled = 0
            channel_created = {}
            channel_sum_ht = {}

            for ch in ordered:
                metrics = channels[ch]
                created = int(metrics.get("CONTACTS_CREATED", 0))
                handled = int(metrics.get("CONTACTS_HANDLED", 0))
                avg_ht = metrics.get("AVG_HANDLE_TIME", 0)
                sum_ht = metrics.get("SUM_HANDLE_TIME", 0)
                total_created += created
                total_handled += handled
                channel_created[ch] = created
                channel_sum_ht[ch] = sum_ht

                avg_ht_str = (
                    f"{int(avg_ht // 60)}m {int(avg_ht % 60)}s" if avg_ht else "-"
                )
                sum_ht_hrs = sum_ht / 3600 if sum_ht else 0
                sum_ht_str = f"{sum_ht_hrs:.1f} hrs" if sum_ht else "-"

                html_output += f"""<tr>
            <td style="font-weight:600">{escape(ch)}</td>
            <td style="text-align:center">{escape(str(created))}</td>
            <td style="text-align:center">{escape(str(handled))}</td>
            <td style="text-align:center">{avg_ht_str}</td>
            <td style="text-align:center">{sum_ht_str}</td>
            </tr>"""

            html_output += f"""<tr style="font-weight:700;border-top:2px solid #333">
            <td>TOTAL</td>
            <td style="text-align:center">{total_created}</td>
            <td style="text-align:center">{total_handled}</td>
            <td></td><td></td>
            </tr>
        </table>"""

            # ── Cost Recommendations based on channel mix ──
            recommendations = []

            voice_created = channel_created.get("VOICE", 0)
            chat_created = channel_created.get("CHAT", 0)
            task_created = channel_created.get("TASK", 0)
            email_created = channel_created.get("EMAIL", 0)

            voice_pct = (
                (voice_created / total_created * 100) if total_created > 0 else 0
            )
            chat_pct = (chat_created / total_created * 100) if total_created > 0 else 0

            voice_sum_ht = channel_sum_ht.get("VOICE", 0)
            voice_avg_ht = (voice_sum_ht / voice_created) if voice_created > 0 else 0

            if voice_pct > 80 and total_created > 100:
                recommendations.append(
                    get_recommendation("channel_voice_heavy", voice_pct=voice_pct)
                )
            if chat_created == 0 and voice_created > 100:
                recommendations.append(get_recommendation("channel_no_chat"))
            if task_created == 0 and total_created > 100:
                recommendations.append(get_recommendation("channel_no_tasks"))
            if voice_avg_ht > 600 and voice_created > 50:
                recommendations.append(
                    get_recommendation("channel_high_voice_ht", avg_ht=voice_avg_ht)
                )
            if chat_pct > 0 and voice_pct > 0:
                recommendations.append(
                    get_recommendation(
                        "channel_multi_channel", voice_pct=voice_pct, chat_pct=chat_pct
                    )
                )
            if email_created > 0:
                recommendations.append(
                    get_recommendation(
                        "channel_email_active", email_count=email_created
                    )
                )
            recommendations.append(get_recommendation("channel_usage_general"))

            html_output += """<h4>Cost Recommendations</h4><ul>"""
            for rec in recommendations:
                html_output += f"<li>{rec}</li>"
            html_output += """</ul></div>"""

        # Channel usage cost check for executive summary
        checks.append(
            {
                "area": "Cost",
                "check": CHECK_NAMES["cost-channel"],
                "status": "info",
                "detail": f"Review {days_back}-day channel mix for cost optimization opportunities",
                "anchor": "cost-channel",
            }
        )

    # ── Misconfigured Phone Numbers ──
    # NOTE: Misconfigured phone numbers rendering is handled in the Cost section
    # (anchor cost-phone-health) via _render_unused_phone_numbers. The opex analyzer
    # produces the data (misconfigured_phone_numbers) but it was causing duplicate
    # executive summary entries when also rendered here. Removed to match monolith
    # which only shows this check under Cost.

    # ── AI Analyzer Structured Findings (from ai_analyzer.py) ──
    ai_findings_html, ai_checks = _render_ai_findings_block(
        findings.get("_ai_structured_findings", []),
        pillar_name="Operational Excellence",
        is_partial=findings.get("_ai_partial", False),
        is_error=findings.get("_ai_error", False),
        error_message=findings.get("_ai_error_message", ""),
    )
    html_output += ai_findings_html
    checks.extend(ai_checks)

    return html_output, checks


def _get_color_by_percentage(percentage):
    """Return color code based on percentage value.

    Extracted verbatim from the monolithic lambda_function.py.

    Args:
        percentage (float): Utilization percentage.

    Returns:
        str: HTML color code string.
    """
    if percentage >= 98:
        return "#FF0000"  # Red - Critical
    elif percentage >= 80:
        return "#FFA500"  # Orange - Warning
    else:
        return "#00FF00"  # Green - Normal


def _render_capacity_section(findings):
    """Render the Capacity Analysis section HTML from structured findings data.

    Produces HTML identical to the monolithic Lambda's capacity rendering:
    color legend, instance resource limits table, concurrency limits table,
    account-level API rate quotas, Cases/AppInt/Profiles limits, and AI agents limits.

    Args:
        findings (dict): Capacity analyzer findings containing:
            - instance_quotas: {resources[], total_checked, measurable_count,
                               unmeasurable_count, pass_count, warn_count,
                               fail_count, status, detail}
            - concurrency_limits: {metrics[], total_metrics, measurable_count,
                                   status, detail}
            - growth_trends: {trends[], days_analyzed, status, detail}
            - account_level_api: (optional) {api_rate_count, modified_count,
                                             modified_rows[], default_rows[]}
            - cases_limits: (optional) {domain_count, quotas[], status, detail}
            - appint_limits: (optional) {data_int_count, event_int_count, app_count,
                                         quotas[], status, detail}
            - profiles_limits: (optional) {domain_count, quotas[], status, detail}
            - ai_agents_limits: (optional) {html_content, status, detail}

    Returns:
        tuple: (html_string, checks_list) where checks_list contains dicts with
            area, check, status, detail, anchor for the executive summary.
    """
    html_output = ""
    checks = []

    # ── Section heading with color legend ──
    html_output += f"""
            <h2>Capacity Analysis</h2>
            <div style="display:flex; gap:12px; flex-wrap:wrap; margin:10px 0 18px 0;">
                <div style="padding:8px 18px; border-radius:6px; background-color:{_get_color_by_percentage(50)}; font-size:0.88rem; font-weight:600;">Green: Usage well within limit</div>
                <div style="padding:8px 18px; border-radius:6px; background-color:{_get_color_by_percentage(90)}; font-size:0.88rem; font-weight:600;">Orange: Nearing limit, monitor or increase</div>
                <div style="padding:8px 18px; border-radius:6px; background-color:{_get_color_by_percentage(100)}; font-size:0.88rem; font-weight:600; color:#fff;">Red: Quota reached or exceeded</div>
            </div>
            <p style="font-size:0.88rem; margin:0 0 14px 0;">For quotas approaching their limits, analyze current usage trends and submit increase requests through <a href="https://docs.aws.amazon.com/servicequotas/latest/userguide/request-quota-increase.html" target="_blank">AWS Service Quotas</a>, providing clear business justification for each adjustment.</p>"""

    # ── Instance Resource Limits table ──
    instance_quotas = findings.get("instance_quotas", {})
    resources = instance_quotas.get("resources", [])

    html_output += f"""<div class="section" id="ops-capacity">
                <h3>{CHECK_NAMES["ops-capacity"]}</h3>
                <table style="width: 100%; table-layout: fixed;">
                    <colgroup>
                        <col style="width: 40%;"><col style="width: 20%;"><col style="width: 20%;"><col style="width: 20%;">
                    </colgroup>
                    <tr><th>Resource</th><th style="text-align:center;">Current Use</th><th style="text-align:center;">Quota Limit</th><th style="text-align:center;">Usage %</th></tr>"""

    for resource in resources:
        label = resource.get("label", "")
        cur = resource.get("current", -1)
        limit = resource.get("limit", -1)
        pct = resource.get("percentage", -1)

        cur_display = "-" if cur < 0 else escape(str(cur))
        limit_str = str(limit) if limit >= 0 else "-"

        if pct < 0:
            pct_style = ""
            pct_display = "-"
        else:
            pct_style = f" background-color: {_get_color_by_percentage(pct)};"
            pct_display = f"{escape(str(pct))}%"

        html_output += f"""<tr><th>{escape(label)}</th><td style="text-align:center;">{cur_display}</td><td style="text-align:center;">{escape(limit_str)}</td><td style="text-align:center;{pct_style}">{pct_display}</td></tr>"""

    html_output += """</table></div>"""

    # Instance Resource Limits check for executive summary
    measurable = [r for r in resources if r.get("percentage", -1) >= 0]
    unmeasurable_count = sum(1 for r in resources if r.get("percentage", -1) < 0)
    cap_pass = sum(1 for r in measurable if r.get("percentage", 0) < 80)
    cap_warn = sum(1 for r in measurable if 80 <= r.get("percentage", 0) < 98)
    cap_fail = sum(1 for r in measurable if r.get("percentage", 0) >= 98)
    cap_total = len(measurable)
    warn_names = [r["label"] for r in measurable if 80 <= r.get("percentage", 0) < 98]
    fail_names = [r["label"] for r in measurable if r.get("percentage", 0) >= 98]
    review_note = f", {unmeasurable_count} to review" if unmeasurable_count > 0 else ""

    if cap_fail > 0:
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["ops-capacity"],
                "status": "fail",
                "detail": f"{cap_pass}/{cap_total} passed, {cap_fail} critical ({', '.join(fail_names)}){review_note}",
                "anchor": "ops-capacity",
            }
        )
    elif cap_warn > 0:
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["ops-capacity"],
                "status": "warn",
                "detail": f"{cap_pass}/{cap_total} passed, {cap_warn} nearing limit ({', '.join(warn_names)}){review_note}",
                "anchor": "ops-capacity",
            }
        )
    else:
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["ops-capacity"],
                "status": "pass",
                "detail": f"{cap_pass}/{cap_total} resources within safe limits{review_note}",
                "anchor": "ops-capacity",
            }
        )

    # ── Concurrency Limits table ──
    concurrency_limits = findings.get("concurrency_limits", {})
    concurrency_metrics = concurrency_limits.get("metrics", [])
    days_back = findings.get("growth_trends", {}).get("days_analyzed", 30)

    html_output += f"""
            <div class="section" id="cap-concurrency">
            <h3>{CHECK_NAMES["cap-concurrency"]}</h3>
                <table style="width: 100%; table-layout: fixed;">
                    <colgroup>
                        <col style="width: 40%;"><col style="width: 20%;"><col style="width: 20%;"><col style="width: 20%;">
                    </colgroup>
                    <tr><th>Resource</th><th style="text-align:center;">Peak Use ({days_back}d)</th><th style="text-align:center;">Quota Limit</th><th style="text-align:center;">Usage %</th></tr>"""

    for metric in concurrency_metrics:
        label = metric.get("label", "")
        peak_cur = metric.get("peak_current", -1)
        limit = metric.get("limit", 0)
        pct = metric.get("peak_percentage", -1)

        cur_display = "-" if peak_cur < 0 else str(peak_cur)
        limit_str = str(int(limit)) if limit else "0"

        if pct < 0:
            pct_style = ""
            pct_display = "-"
        else:
            pct_style = f" background-color: {_get_color_by_percentage(pct)};"
            pct_display = f"{pct}%"

        html_output += f"""<tr><th>{escape(label)}</th><td style="text-align:center;">{cur_display}</td><td style="text-align:center;">{escape(limit_str)}</td><td style="text-align:center;{pct_style}">{pct_display}</td></tr>"""

    html_output += """</table></div>
            """

    # Concurrency Limits check for executive summary
    conc_items = [
        (
            m.get("label", "").split()[1]
            if len(m.get("label", "").split()) > 1
            else m.get("label", ""),
            m.get("peak_percentage", -1),
        )
        for m in concurrency_metrics
        if m.get("peak_percentage", -1) >= 0
    ]
    conc_warn_names = [n for n, p in conc_items if p >= 80]
    conc_fail_names = [n for n, p in conc_items if p >= 98]

    if conc_fail_names:
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["cap-concurrency"],
                "status": "fail",
                "detail": f"{len(conc_items)} metrics evaluated; {len(conc_fail_names)} critical ({', '.join(conc_fail_names)})",
                "anchor": "cap-concurrency",
            }
        )
    elif conc_warn_names:
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["cap-concurrency"],
                "status": "warn",
                "detail": f"{len(conc_items)} metrics evaluated; {len(conc_warn_names)} nearing limit ({', '.join(conc_warn_names)})",
                "anchor": "cap-concurrency",
            }
        )
    else:
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["cap-concurrency"],
                "status": "pass",
                "detail": f"{len(conc_items)} concurrency metrics within safe limits",
                "anchor": "cap-concurrency",
            }
        )

    # ── Account Level API Limits ──
    account_api = findings.get("account_level_api", {})
    if account_api:
        api_rate_count = account_api.get("api_rate_count", 0)
        modified_count = account_api.get("modified_count", 0)
        modified_rows = account_api.get("modified_rows", [])
        default_rows = account_api.get("default_rows", [])

        html_output += f"""
            <div class="section" id="cap-api-limits">
                <h3>{CHECK_NAMES["cap-api-limits"]}</h3>
                <p>{api_rate_count} API rate quotas checked, {modified_count} have custom values. Review <a href="https://docs.aws.amazon.com/connect/latest/APIReference/best-practices-connect-apis.html" target="_blank">Best Practices for Amazon Connect APIs</a>.</p>"""

        if modified_rows:
            modified_html = ""
            for row in modified_rows:
                qname = escape(str(row.get("name", "")))
                default_val = row.get("default_value", "-")
                current_val = row.get("current_value", "-")
                util_display = row.get("utilization", "-")
                modified_html += f"""<tr>
                        <th>{qname}</th>
                        <td style="text-align:center;">{default_val}</td>
                        <td style="text-align:center; font-weight:600; color:#ea580c;">{current_val}</td>
                        <td style="text-align:center;">{util_display}</td>
                    </tr>"""

            html_output += f"""
                <h4>Modified API Rate Quotas ({modified_count})</h4>
                <table style="width: 100%; table-layout: fixed;">
                    <colgroup>
                        <col style="width: 40%;"><col style="width: 20%;"><col style="width: 20%;"><col style="width: 20%;">
                    </colgroup>
                    <tr><th>Resource</th><th style="text-align:center;">Default Value</th><th style="text-align:center;">Current Value</th><th style="text-align:center;">Utilization ({days_back}d peak)</th></tr>
                    {modified_html}
                </table>"""
        elif api_rate_count > 0:
            html_output += """<p style="color:#22c55e;">All API rate quotas are at default values.</p>"""

        if default_rows:
            default_html = ""
            for row in default_rows:
                qname = escape(str(row.get("name", "")))
                default_val = row.get("default_value", "-")
                default_html += f"""<tr>
                        <th>{qname}</th>
                        <td style="text-align:center;">{default_val}</td>
                    </tr>"""

            html_output += f"""
                <br><details>
                    <summary style="cursor:pointer; font-weight:600; font-size:0.9rem; color:#334155;">View all default API rate quotas ({api_rate_count - modified_count})</summary>
                    <div style="margin-top:8px;">
                    <table style="width: 60%; table-layout: fixed;">
                        <colgroup>
                            <col style="width: 70%;"><col style="width: 30%;">
                        </colgroup>
                        <tr><th>Resource</th><th style="text-align:center;">Default Value</th></tr>
                        {default_html}
                    </table>
                    </div>
                </details>"""

        html_output += """</div>
            """
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["cap-api-limits"],
                "status": "info",
                "detail": f"{api_rate_count} API rate quotas evaluated, {modified_count} with custom increases",
                "anchor": "cap-api-limits",
            }
        )

    # ── Cases Limits ──
    cases_limits = findings.get("cases_limits", {})
    if cases_limits:
        html_output += _render_capacity_sub_table(
            cases_limits,
            "Amazon Connect Cases Limits",
            "cap-cases",
            summary_prefix=f"{cases_limits.get('domain_count', 0)} Cases domain(s) found.",
        )
        cases_check = _build_capacity_check(
            cases_limits, "Amazon Connect Cases Limits", "cap-cases"
        )
        if cases_check:
            checks.append(cases_check)

    # ── Application Integrations Limits ──
    appint_limits = findings.get("appint_limits", {})
    if appint_limits:
        data_int = appint_limits.get("data_int_count", 0)
        event_int = appint_limits.get("event_int_count", 0)
        app_count = appint_limits.get("app_count", 0)
        summary = f"{data_int} data integration(s), {event_int} event integration(s), {app_count} application(s) found."
        html_output += _render_capacity_sub_table(
            appint_limits,
            "Amazon Connect Application Integrations Limits",
            "cap-appint",
            summary_prefix=summary,
        )
        appint_check = _build_capacity_check(
            appint_limits, "Application Integrations Limits", "cap-appint"
        )
        if appint_check:
            checks.append(appint_check)

    # ── Customer Profiles Limits ──
    profiles_limits = findings.get("profiles_limits", {})
    if profiles_limits:
        html_output += _render_capacity_sub_table(
            profiles_limits,
            "Amazon Connect Customer Profiles Limits",
            "cap-profiles",
            summary_prefix=f"{profiles_limits.get('domain_count', 0)} Customer Profiles domain(s) found.",
        )
        profiles_check = _build_capacity_check(
            profiles_limits, "Customer Profiles Limits", "cap-profiles"
        )
        if profiles_check:
            checks.append(profiles_check)

    # ── AI Agents Limits (Q1) ──
    ai_agents_limits = findings.get("ai_agents_limits", {})
    if ai_agents_limits:
        ai_html = ai_agents_limits.get("html_content", "")
        html_output += f'<div class="section" id="ai-q1"><h3><span style="background:#232F3E; color:#FF9900; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:700; margin-right:8px; vertical-align:middle;">AI</span>{CHECK_NAMES["ai-q1"]}</h3>'
        html_output += "<p><em>Checks usage against non-adjustable Amazon Connect AI Agents limits.</em></p>"
        if ai_html:
            html_output += ai_html
        else:
            html_output += "<p>No AI agents limit data available for this instance.</p>"
        html_output += "</div>"
        checks.append(
            {
                "area": "Capacity Analysis",
                "check": CHECK_NAMES["ai-q1"],
                "status": "info",
                "detail": "Service limits and article counts checked",
                "anchor": "ai-q1",
            }
        )

    return html_output, checks


def _render_capacity_sub_table(limits_data, title, anchor_id, summary_prefix=""):
    """Render a capacity sub-section table (Cases, AppInt, Profiles).

    Args:
        limits_data (dict): Sub-section data with 'quotas' list.
        title (str): Section title.
        anchor_id (str): HTML anchor ID.
        summary_prefix (str): Summary text before the table.

    Returns:
        str: HTML for the sub-section.
    """
    quotas = limits_data.get("quotas", [])

    rows = ""
    for q in quotas:
        label = q.get("label", "")
        current = q.get("current")
        limit_val = q.get("limit")
        limit_str = str(int(limit_val)) if limit_val else "-"
        cur_display = "-" if current is None else str(current)

        if current is not None and limit_val and limit_val > 0:
            pct = round(current / limit_val * 100, 1)
            pct_style = f" background-color: {_get_color_by_percentage(pct)};"
            pct_display = f"{pct}%"
        else:
            pct_style = ""
            pct_display = "-"

        rows += f"""<tr><th>{escape(label)}</th><td style="text-align:center;">{cur_display}</td><td style="text-align:center;">{limit_str}</td><td style="text-align:center;{pct_style}">{pct_display}</td></tr>"""

    html = f"""<div class="section" id="{anchor_id}"><h3>{escape(title)}</h3>
            <p>{escape(summary_prefix)}</p>
            <table style="width: 100%; table-layout: fixed;"><colgroup><col style="width: 40%;"><col style="width: 20%;"><col style="width: 20%;"><col style="width: 20%;"></colgroup>
            <tr><th>Resource</th><th style="text-align:center;">Current Use</th><th style="text-align:center;">Quota Limit</th><th style="text-align:center;">Usage %</th></tr>{rows}</table></div>"""

    return html


def _build_capacity_check(limits_data, check_name, anchor_id):
    """Build an executive summary check dict for a capacity sub-section.

    Args:
        limits_data (dict): Sub-section data with 'quotas' list.
        check_name (str): Check name for the summary.
        anchor_id (str): HTML anchor ID.

    Returns:
        dict: Check dict with area, check, status, detail, anchor.
    """
    quotas = limits_data.get("quotas", [])
    quota_pcts = []
    for q in quotas:
        current = q.get("current")
        limit_val = q.get("limit")
        if current is not None and limit_val and limit_val > 0:
            pct = round(current / limit_val * 100, 1)
            quota_pcts.append((q.get("label", ""), pct))

    cap_pass = sum(1 for _, p in quota_pcts if p < 80)
    cap_warn = sum(1 for _, p in quota_pcts if 80 <= p < 98)
    cap_fail = sum(1 for _, p in quota_pcts if p >= 98)
    cap_total = len(quota_pcts)
    unmeasurable = len(quotas) - cap_total
    review_note = f", {unmeasurable} to review" if unmeasurable > 0 else ""

    if cap_fail > 0:
        fail_names = [n for n, p in quota_pcts if p >= 98]
        return {
            "area": "Capacity Analysis",
            "check": check_name,
            "status": "fail",
            "detail": f"{cap_pass}/{cap_total} passed, {cap_fail} critical ({', '.join(fail_names)}){review_note}",
            "anchor": anchor_id,
        }
    elif cap_warn > 0:
        warn_names = [n for n, p in quota_pcts if 80 <= p < 98]
        return {
            "area": "Capacity Analysis",
            "check": check_name,
            "status": "warn",
            "detail": f"{cap_pass}/{cap_total} passed, {cap_warn} nearing limit ({', '.join(warn_names)}){review_note}",
            "anchor": anchor_id,
        }
    elif cap_total > 0:
        return {
            "area": "Capacity Analysis",
            "check": check_name,
            "status": "pass",
            "detail": f"{cap_total}/{cap_total} resources within safe limits{review_note}",
            "anchor": anchor_id,
        }
    else:
        return {
            "area": "Capacity Analysis",
            "check": check_name,
            "status": "info",
            "detail": f"Quotas reviewed{review_note}",
            "anchor": anchor_id,
        }


def _render_observability_section(findings):
    """Render the Observability section HTML from structured findings data.

    Produces HTML identical to the monolithic Lambda's observability rendering:
    CloudWatch alarm validation table (found/missing/extra), contact flow logging
    status, KVS retention analysis, AI agent inventory table, and AI prompt
    configuration table.

    Args:
        findings (dict): Combined findings containing:
            - cloudwatch_alarms: {found[], missing[], extra[], total_recommended,
                found_count, missing_count, coverage_pct, triggered_count, status, detail}
            - contact_flow_logging: {total_analyzed, flows_without_logging[],
                flows_without_logging_count, skipped_flows_count, was_capped, status, detail}
            - kvs_retention: {total_streams, zero_retention_count, streams[], status, detail}
            - missed_calls_metrics: (optional) {total_missed_calls, daily_average,
                peak_day_count, peak_day_date, daily_data[], days_analyzed, status, detail}
            - log_groups: (optional) {total_log_groups, no_retention_count,
                log_groups[], status, detail}

    Returns:
        tuple: (html_string, checks_list) where checks_list contains dicts with
            area, check, status, detail, anchor for the executive summary.
    """
    html_output = ""
    checks = []

    # Color maps for alarm rendering (verbatim from monolithic Lambda)
    SEVERITY_COLORS = {
        "CRITICAL": "#ef4444",
        "HIGH": "#f97316",
        "MEDIUM": "#f59e0b",
        "LOW": "#3b82f6",
    }
    STATE_COLORS = {
        "OK": "#22c55e",
        "ALARM": "#ef4444",
        "INSUFFICIENT_DATA": "#9ca3af",
        "UNKNOWN": "#9ca3af",
    }

    # ── Section heading ──
    html_output += """
            <h2>Observability</h2>"""

    # ── CloudWatch Alarm Validation - Amazon Connect ──
    alarm_data = findings.get("cloudwatch_alarms", {})
    if alarm_data:
        found = alarm_data.get("found", [])
        missing = alarm_data.get("missing", [])
        extra = alarm_data.get("extra", [])
        total = alarm_data.get("total_recommended", len(found) + len(missing))
        found_count = alarm_data.get("found_count", len(found))
        missing_count = alarm_data.get("missing_count", len(missing))
        coverage = alarm_data.get(
            "coverage_pct", round((found_count / total) * 100, 1) if total else 0
        )
        triggered_count = alarm_data.get(
            "triggered_count", sum(1 for f in found if f.get("alarm_state") == "ALARM")
        )

        if coverage >= 80:
            cov_color = "#22c55e"
        elif coverage >= 50:
            cov_color = "#f59e0b"
        else:
            cov_color = "#ef4444"

        html_output += f"""<div class="section" id="mon-alarms">
        <h3>{CHECK_NAMES["mon-alarms"]}</h3>
        <p>Validates recommended CloudWatch alarms for this Amazon Connect instance.
        Refer to <a href="https://docs.aws.amazon.com/connect/latest/adminguide/monitoring-cloudwatch.html" target="_blank">CloudWatch Metrics documentation</a> for metric details.</p>
        <table style="width: 60%">
            <tr><th>Coverage</th><th>Recommended</th><th>Found</th><th>Missing</th><th>Extra Alarms</th></tr>
            <tr>
                <td style="font-weight:bold; color:{cov_color}; font-size:1.2em;">{coverage}%</td>
                <td>{total}</td>
                <td style="color:#22c55e; font-weight:bold;">{found_count}</td>
                <td style="color:#ef4444; font-weight:bold;">{missing_count}</td>
                <td>{len(extra)}</td>
            </tr>
        </table>"""

        # Missing alarms
        if missing:
            severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
            sorted_missing = sorted(
                missing, key=lambda x: severity_order.get(x.get("severity", ""), 99)
            )
            html_output += f"""<br><h4>&#10060; Missing Alarms ({missing_count})</h4>
            <table style="width: 90%">
            <tr><th>Metric</th><th>Severity</th><th>Description</th><th>Recommended Config</th></tr>"""
            for m in sorted_missing:
                sev_c = SEVERITY_COLORS.get(m.get("severity", ""), "#64748b")
                comp_display = (
                    m.get("rec_comparison", "")
                    .replace("GreaterThanOrEqualToThreshold", "&ge;")
                    .replace("GreaterThanThreshold", "&gt;")
                )
                html_output += f"""<tr style="background-color: #fef2f2;">
                <td><code>{escape(m.get("metric_name", ""))}</code></td>
                <td><span style="background:{sev_c}; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.8em;">{escape(m.get("severity", ""))}</span></td>
                <td>{escape(m.get("description", ""))}</td>
                <td>{escape(m.get("rec_statistic", ""))} {comp_display} {m.get("rec_threshold", "")} / {m.get("rec_period", "")}s</td>
            </tr>"""
            html_output += "</table>"
        else:
            html_output += '<br><p style="color:#22c55e; font-weight:bold;">&#9989; All recommended alarms are configured.</p>'

        # Found alarms
        if found:
            triggered_note = (
                f' - <span style="color:#ef4444; font-weight:bold;">&#128680; {triggered_count} TRIGGERED</span>'
                if triggered_count > 0
                else ""
            )
            html_output += f"""<br><h4>&#9989; Configured Alarms ({found_count}){triggered_note}</h4>
            <table style="width: 90%">
            <tr><th>Metric</th><th>Alarm Name</th><th>State</th><th>Severity</th><th>Threshold</th><th>Actions?</th></tr>"""
            for f_item in found:
                sev_c = SEVERITY_COLORS.get(f_item.get("severity", ""), "#64748b")
                st_c = STATE_COLORS.get(f_item.get("alarm_state", "UNKNOWN"), "#9ca3af")
                actions_txt = (
                    "&#9989; Yes" if f_item.get("has_actions") else "&#9888;&#65039; No"
                )
                is_triggered = f_item.get("alarm_state") == "ALARM"
                row_bg = "#fef2f2" if is_triggered else "#f0fdf4"
                row_border = "border-left: 4px solid #ef4444;" if is_triggered else ""
                triggered_icon = " &#128680;" if is_triggered else ""
                html_output += f"""<tr style="background-color: {row_bg}; {row_border}">
                <td><code>{escape(f_item.get("metric_name", ""))}</code>{triggered_icon}</td>
                <td>{escape(f_item.get("alarm_name", ""))}</td>
                <td><span style="background:{st_c}; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.8em; {"font-weight:bold;" if is_triggered else ""}">{escape(f_item.get("alarm_state", "?"))}</span></td>
                <td><span style="background:{sev_c}; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.8em;">{escape(f_item.get("severity", ""))}</span></td>
                <td>{escape(str(f_item.get("actual_statistic", "?")))} {f_item.get("actual_threshold", "?")}</td>
                <td>{actions_txt}</td>
            </tr>"""
            html_output += "</table>"

        # Extra alarms
        if extra:
            html_output += f"""<br><h4>&#128270; Additional Connect Alarms (not in recommendations) ({len(extra)})</h4>
            <table style="width: 70%">
            <tr><th>Alarm Name</th><th>Metric</th><th>State</th></tr>"""
            for e in extra:
                st_c = STATE_COLORS.get(e.get("state", "UNKNOWN"), "#9ca3af")
                html_output += f"""<tr>
                <td>{escape(e.get("alarm_name", ""))}</td>
                <td><code>{escape(e.get("metric_name", ""))}</code></td>
                <td><span style="background:{st_c}; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.8em;">{escape(e.get("state", "?"))}</span></td>
            </tr>"""
            html_output += "</table>"

        # Recommendations
        html_output += get_recommendation("alarm_validation")
        html_output += """</div>"""

        # Alarm validation check for executive summary
        if missing_count > 0 and triggered_count > 0:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["mon-alarms"],
                    "status": "fail",
                    "detail": f"{missing_count} missing, {triggered_count} triggered",
                    "anchor": "mon-alarms",
                }
            )
        elif missing_count > 0:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["mon-alarms"],
                    "status": "warn",
                    "detail": f"{missing_count} of {missing_count + found_count} recommended alarms missing",
                    "anchor": "mon-alarms",
                }
            )
        elif triggered_count > 0:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["mon-alarms"],
                    "status": "fail",
                    "detail": f"All configured, {triggered_count} in ALARM state",
                    "anchor": "mon-alarms",
                }
            )
        else:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["mon-alarms"],
                    "status": "pass",
                    "detail": f"All {found_count} recommended alarms configured",
                    "anchor": "mon-alarms",
                }
            )

    # ── Contact Flow Logging ──
    flow_logging_data = findings.get("contact_flow_logging", {})
    if flow_logging_data:
        total_analyzed = flow_logging_data.get("total_analyzed", 0)
        flows_without_logging = flow_logging_data.get("flows_without_logging", [])
        skipped_flows_count = flow_logging_data.get("skipped_flows_count", 0)
        was_capped = flow_logging_data.get("was_capped", False)

        html_output += f"""<div class="section" id="ops-logging">
            <h3>{CHECK_NAMES["ops-logging"]}</h3>"""
        html_output += f"""<p>Analyzed {escape(str(total_analyzed))} contact flows (skipped {skipped_flows_count} default/sample flows).</p>"""

        if was_capped:
            html_output += """<p style="color:#d97706;"><em>Note: This instance has more than 1,000 contact flows. Only the first 1,000 non-default flows were analyzed to avoid API throttling. Remaining flows were not checked.</em></p>"""

        if len(flows_without_logging) == 0:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["ops-logging"],
                    "status": "pass",
                    "detail": "All analyzed flows have logging enabled",
                    "anchor": "ops-logging",
                }
            )
            html_output += """<p style="color:#22c55e;">No contact flows without logging enabled found.</p>"""
        else:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["ops-logging"],
                    "status": "warn",
                    "detail": f"{len(flows_without_logging)} flows missing logging",
                    "anchor": "ops-logging",
                }
            )
            html_output += f"""
                   <p>{len(flows_without_logging)} contact flows without an explicit <em>Set logging behavior</em> block.<br>
                   <em>Note: If instance-level flow logging is enabled, these flows may still generate logs via the instance default.
                   Adding explicit logging blocks is a best practice to prevent silent log loss if instance settings change.</em></p>
                    <table style="width: 80%">
                            <tr><th>Flow Name</th><th>Flow ID</th><th>Flow Type</th><th>Status</th><th>State</th></tr>"""

            for flow in flows_without_logging:
                flow_name = flow.get("flow_name", flow.get("FlowName", ""))
                flow_id = flow.get("flow_id", flow.get("FlowId", ""))
                flow_type = flow.get("flow_type", flow.get("FlowType", ""))
                flow_status = flow.get("status", flow.get("Status", ""))
                flow_state = flow.get("state", flow.get("State", ""))
                html_output += f"""<tr><td>{escape(flow_name)}</td><td>{escape(flow_id)}</td><td>{escape(flow_type)}</td><td>{escape(flow_status)}</td><td>{escape(flow_state)}</td></tr>"""
            html_output += f"""</table>
            {get_recommendation("contact_flow_logging")}"""

        html_output += """</div>"""

    # ── KVS Retention Period (moved to Operational Excellence, QB-73) ──
    # The `kvs_retention` block previously rendered here now lives in
    # `_render_opex_section` after the API Throttling block so the body
    # <h3> lands in the same pillar its Executive Summary row is counted
    # under. Data is still produced by the opex analyzer (`kvs_retention`)
    # and remains copied into the observability findings shape by the
    # cross-analyzer merge in case future consumers need it.

    # NOTE: AI Agent Logging (`agent_logging`) is now delivered via the
    # structured AI findings pipeline (`_ai_structured_findings`) and rendered
    # by the `_render_ai_findings_block` call at the end of this section
    # (see QB-52 routing change). The old direct renderer that read
    # `findings["ai_agent_logging"]` was dead code and has been removed.

    # ── Missed Calls Analysis ──
    missed_calls_data = findings.get("missed_calls", {})
    if missed_calls_data:
        days_back = missed_calls_data.get("days_back", 14)
        html_output += f"""<div class="section" id="ch-missed">
            <h3>{CHECK_NAMES["ch-missed"]} Analysis</h3>
            """
        html_output += f"<p>Analyzing missed calls over the last {days_back} days.</p>"

        daily_data = missed_calls_data.get("daily_data", [])
        total_missed_calls = missed_calls_data.get("total_missed_calls", 0)

        if daily_data:
            daily_average = missed_calls_data.get("daily_average", 0)
            daily_median = missed_calls_data.get("daily_median", 0)
            max_missed = missed_calls_data.get("max_missed", 0)
            min_missed = missed_calls_data.get("min_missed", 0)
            peak_day_date = missed_calls_data.get("peak_day_date", "")
            peak_day_name = missed_calls_data.get("peak_day_name", "")
            peak_day_count = missed_calls_data.get("peak_day_count", 0)

            html_output += f"""
            <table style="width: 60%;">
                <tr><th>Metric</th><th style="text-align:center;">Value</th></tr>
                <tr><td>Total Missed Calls ({days_back}d)</td><td style="text-align:center;">{total_missed_calls:,}</td></tr>
                <tr><td>Daily Average</td><td style="text-align:center;">{daily_average:.1f}</td></tr>
                <tr><td>Daily Median</td><td style="text-align:center;">{daily_median:.1f}</td></tr>
                <tr><td>Highest Single Day</td><td style="text-align:center;">{max_missed:,}</td></tr>
                <tr><td>Lowest Single Day</td><td style="text-align:center;">{min_missed:,}</td></tr>
                <tr><td>Peak Day</td><td style="text-align:center;">{peak_day_date} ({peak_day_name}) - {peak_day_count:,}</td></tr>
            </table>"""

            # Show recommendation if average exceeds threshold
            if daily_average > 5:
                html_output += get_recommendation("missed_calls")
        else:
            html_output += (
                "<p>No missed calls data available for the specified time period.</p>"
            )

        # Missed calls check for executive summary
        if total_missed_calls > 50:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["ch-missed"],
                    "status": "warn",
                    "detail": f"{total_missed_calls:,} missed calls in last {days_back} days",
                    "anchor": "ch-missed",
                }
            )
        elif total_missed_calls > 0:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["ch-missed"],
                    "status": "pass",
                    "detail": f"{total_missed_calls:,} missed calls in last {days_back} days",
                    "anchor": "ch-missed",
                }
            )
        else:
            checks.append(
                {
                    "area": "Observability",
                    "check": CHECK_NAMES["ch-missed"],
                    "status": "pass",
                    "detail": "No missed calls detected",
                    "anchor": "ch-missed",
                }
            )

        html_output += """</div>"""

    # ── CloudWatch Log Groups ──
    #
    # Consumes the log-groups payload emitted by
    # ``observability_analyzer.check_connect_log_groups``: total_log_groups,
    # no_retention_count, log_groups (list of {log_group_name, retention_days,
    # stored_bytes, has_retention_policy}), status, detail.
    #
    # The analyzer returns one of four shapes (error, empty, warn, pass) —
    # all four include ``status`` and ``detail``. Following the QB-40
    # Kinesis-render pattern, flow those analyzer-provided values through
    # verbatim so the check row surfaces in the Executive Summary for every
    # branch, not just the non-empty ones. The visual table is still guarded
    # on ``log_groups`` being non-empty because rendering a zero-row table
    # adds no signal.
    log_groups_data = findings.get("log_groups", {})
    if log_groups_data:
        log_groups = log_groups_data.get("log_groups", [])
        no_retention_count = log_groups_data.get("no_retention_count", 0)
        total_log_groups = log_groups_data.get("total_log_groups", 0)
        status = log_groups_data.get("status", "info")
        detail = log_groups_data.get(
            "detail", f"{total_log_groups} log group(s) analyzed"
        )

        if log_groups:
            html_output += f"""<div class="section" id="obs-log-groups">
                <h3>CloudWatch Log Groups</h3>
                <p>{total_log_groups} Connect-related log group(s) found. {no_retention_count} without retention policy.</p>
                <table style="width: 80%;">
                    <tr><th>Log Group</th><th>Retention (days)</th><th>Stored Bytes</th></tr>"""
            for lg in log_groups:
                retention = lg.get("retention_days")
                ret_display = (
                    str(retention)
                    if retention is not None
                    else '<span style="color:#f59e0b; font-weight:600;">Never expires</span>'
                )
                stored = lg.get("stored_bytes", 0)
                stored_display = (
                    f"{stored / (1024 * 1024):.1f} MB" if stored > 0 else "0"
                )
                html_output += f"""<tr>
                    <td><code>{escape(lg.get("log_group_name", ""))}</code></td>
                    <td style="text-align:center;">{ret_display}</td>
                    <td style="text-align:center;">{stored_display}</td>
                </tr>"""
            html_output += """</table></div>"""

        checks.append(
            {
                "area": "Observability",
                "check": CHECK_NAMES["obs-log-groups"],
                "status": status,
                "detail": detail,
                "anchor": "obs-log-groups",
            }
        )

    # ── Kinesis Data Streams — Recommended Alarm Coverage ──
    #
    # Consumes the alarm-coverage payload emitted by
    # ``observability_analyzer.validate_kinesis_stream_alarms``: streams_found
    # (list of stream names), found + missing (per-(stream, metric) entries),
    # total_recommended, found_count, missing_count, coverage_pct, status,
    # detail. Follows the log_groups QB-42 pattern: the check row is emitted
    # from analyzer-provided status/detail for every branch (error, empty,
    # warn, pass); the visual <div id="obs-kinesis-streams"> table is guarded
    # on ``streams_found`` being non-empty because rendering a zero-row table
    # adds no signal. Prior to QB-47 the check row was nested inside the
    # ``if streams_found:`` guard and silently dropped for the empty branch.
    kds_data = findings.get("kinesis_data_streams", {})
    if kds_data:
        streams_found = kds_data.get("streams_found", [])
        found = kds_data.get("found", [])
        missing = kds_data.get("missing", [])
        total_recommended = kds_data.get("total_recommended", len(found) + len(missing))
        found_count = kds_data.get("found_count", len(found))
        missing_count = kds_data.get("missing_count", len(missing))
        coverage_pct = kds_data.get(
            "coverage_pct",
            round((found_count / total_recommended) * 100, 1)
            if total_recommended
            else 0,
        )
        status = kds_data.get("status", "info")
        detail = kds_data.get("detail", f"{len(streams_found)} stream(s) analyzed")

        if streams_found:
            if coverage_pct >= 80:
                cov_color = "#22c55e"
            elif coverage_pct >= 50:
                cov_color = "#f59e0b"
            else:
                cov_color = "#ef4444"

            html_output += f"""<div class="section" id="obs-kinesis-streams">
                <h3>{CHECK_NAMES["obs-kinesis-streams"]} — Recommended Alarm Coverage</h3>
                <p>{len(streams_found)} Connect-associated Kinesis Data Stream(s) found.
                Recommended CloudWatch alarm coverage:
                <span style="color:{cov_color}; font-weight:600;">{coverage_pct}%</span>
                ({found_count} of {total_recommended} recommended alarms configured, {missing_count} missing).</p>
                <table style="width: 90%;">
                    <tr><th>Stream Name</th><th>Metric Name</th><th>Severity</th><th>Recommended Threshold</th><th>Current Alarm</th><th>State</th></tr>"""

            # Merge found + missing and sort by (stream_name, metric_name) so
            # each stream's rows are grouped together.
            merged = [{"__missing": False, **entry} for entry in found] + [
                {"__missing": True, **entry} for entry in missing
            ]
            merged.sort(
                key=lambda e: (
                    e.get("stream_name", ""),
                    e.get("metric_name", ""),
                )
            )

            for entry in merged:
                sev_c = SEVERITY_COLORS.get(entry.get("severity", ""), "#64748b")
                comp_display = (
                    entry.get("rec_comparison", "")
                    .replace("GreaterThanOrEqualToThreshold", "&ge;")
                    .replace("GreaterThanThreshold", "&gt;")
                    .replace("LessThanOrEqualToThreshold", "&le;")
                    .replace("LessThanThreshold", "&lt;")
                )
                threshold_display = (
                    f"{escape(str(entry.get('rec_statistic', '')))} "
                    f"{comp_display} {entry.get('rec_threshold', '')} / "
                    f"{entry.get('rec_period', '')}s"
                )
                if entry["__missing"]:
                    alarm_cell = (
                        '<span style="background:#ef4444; color:#fff; '
                        "padding:2px 8px; border-radius:999px; "
                        'font-size:0.8em; font-weight:600;">MISSING</span>'
                    )
                    state_cell = ""
                    row_bg = "#fef2f2"
                else:
                    alarm_cell = f"<code>{escape(entry.get('alarm_name', ''))}</code>"
                    st_c = STATE_COLORS.get(
                        entry.get("alarm_state", "UNKNOWN"), "#9ca3af"
                    )
                    state_cell = (
                        f'<span style="background:{st_c}; color:#fff; '
                        f"padding:2px 8px; border-radius:999px; "
                        f'font-size:0.8em;">'
                        f"{escape(entry.get('alarm_state', '?'))}</span>"
                    )
                    row_bg = "#f0fdf4"
                html_output += f"""<tr style="background-color: {row_bg};">
                    <td><code>{escape(entry.get("stream_name", ""))}</code></td>
                    <td><code>{escape(entry.get("metric_name", ""))}</code></td>
                    <td><span style="background:{sev_c}; color:#fff; padding:2px 8px; border-radius:999px; font-size:0.8em;">{escape(entry.get("severity", ""))}</span></td>
                    <td>{threshold_display}</td>
                    <td>{alarm_cell}</td>
                    <td>{state_cell}</td>
                </tr>"""

            html_output += """</table></div>"""
        else:
            # QB-75 (rc.9): emit a stub anchor so the Executive Summary row's
            # href="#obs-kinesis-streams" resolves on instances with 0 Kinesis
            # Data Streams. Mirrors the QB-58 rc.7 fix in
            # _render_unused_phone_numbers: the ES check row is registered
            # unconditionally (post-QB-47) whenever the analyzer key is present,
            # so the body anchor MUST also be emitted unconditionally in the
            # same branch. Without this stub, the ES row links to a body id
            # that is never rendered and clicking it scrolls to page top.
            html_output += (
                '<div class="section" id="obs-kinesis-streams">'
                f"<h3>{CHECK_NAMES['obs-kinesis-streams']}</h3>"
                "<p>No Kinesis Data Streams associated with this Connect instance.</p>"
                "</div>"
            )

        checks.append(
            {
                "area": "Observability",
                "check": CHECK_NAMES["obs-kinesis-streams"],
                "status": status,
                "detail": detail,
                "anchor": "obs-kinesis-streams",
            }
        )

    # ── AI Analyzer Structured Findings (from ai_analyzer.py) ──
    ai_findings_html, ai_checks = _render_ai_findings_block(
        findings.get("_ai_structured_findings", []),
        pillar_name="Observability",
        is_partial=findings.get("_ai_partial", False),
        is_error=findings.get("_ai_error", False),
        error_message=findings.get("_ai_error_message", ""),
    )
    html_output += ai_findings_html
    checks.extend(ai_checks)

    return html_output, checks


# ── Cost Section Rendering ──
# Extracted verbatim from the monolithic lambda_function.py to ensure identical HTML output.
# Renders: Phone Number Distribution (type/country table, recommendations),
# Unused Phone Numbers table, Channel Usage with Cost Recommendations.


def _render_cost_section(findings):
    """Render the Cost Considerations section HTML from structured findings data.

    Produces HTML identical to the monolithic Lambda's cost rendering:
    phone number analysis table with type/country distribution and recommendations,
    unused phone numbers cost table, and channel usage cost analysis with
    optimization recommendations.

    Args:
        findings (dict): Cost analyzer findings containing:
            - channel_mix: {channels: [{channel, contacts_created, contacts_handled,
                avg_handle_time_seconds, sum_handle_time_seconds}, ...],
                total_created, total_handled, voice_percentage, chat_percentage,
                recommendations: [{type, severity, detail}], status, detail}
            - telephony_usage: {total_numbers, type_distribution: {TOLL_FREE, DID, ...},
                country_distribution: {US: N, ...}, countries_count,
                toll_free_percentage, did_percentage,
                recommendations: [{type, severity, detail}], status, detail}
            - usage_patterns: {daily_volumes: [{date, peak_concurrent, avg_concurrent}],
                days_analyzed, max_peak_concurrent, avg_peak_concurrent,
                overall_avg_concurrent, recommendations, status, detail}
            - phone_numbers (optional): [{phone_number, phone_number_country_code,
                phone_number_type, target_arn, phone_number_status}, ...]
            - phone_type_counts (optional): {TOLL_FREE: N, DID: N, ...}
            - unused_phone_numbers (optional): [{phone_number, reason}, ...]

    Returns:
        tuple: (html_string, checks_list) where checks_list contains dicts with
            area, check, status, detail, anchor for the executive summary.
    """
    html_output = ""
    checks = []

    # ── Phone Number Distribution ──
    html_output += _render_phone_number_analysis(findings, checks)

    # ── Unused Phone Numbers ──
    html_output += _render_unused_phone_numbers(findings, checks)

    # ── Channel Usage Cost Analysis ──
    html_output += _render_channel_usage_cost(findings, checks)

    return html_output, checks


def _render_phone_number_analysis(findings, checks):
    """Render the Phone Number Distribution sub-section.

    Matches the monolithic Lambda's cost section phone number inventory table:
    grouped by country/type with recommendations for toll-free/DID mix.

    Args:
        findings (dict): Cost findings containing telephony_usage and/or
            phone_numbers/phone_type_counts data.
        checks (list): Mutable list to append check results to.

    Returns:
        str: HTML for the phone number analysis sub-section.
    """
    # Try phone_numbers data first (from security analyzer passthrough), else telephony_usage
    phone_numbers = findings.get("phone_numbers", [])
    phone_type_counts = findings.get("phone_type_counts", {})
    telephony_data = findings.get("telephony_usage", {})

    # Determine total_numbers from available data
    if phone_numbers:
        total_numbers = len(phone_numbers)
    elif phone_type_counts:
        metadata = phone_type_counts.get("_metadata", {})
        total_numbers = metadata.get("total_numbers", 0)
        if total_numbers == 0:
            # Sum up type counts
            total_numbers = sum(
                v
                for k, v in phone_type_counts.items()
                if k != "_metadata" and isinstance(v, int)
            )
    elif telephony_data:
        total_numbers = telephony_data.get("total_numbers", 0)
    else:
        total_numbers = 0

    html = f"""
            <h2>Cost Considerations</h2>
            <div class="section" id="cost-phone">
            <h3>{CHECK_NAMES["cost-phone"]}</h3>
            <p>Analyzed {escape(str(total_numbers))} phone numbers.</p>
            """

    if total_numbers == 0:
        html += "<p>No phone numbers analyzed.</p>"
        checks.append(
            {
                "area": "Cost",
                "check": CHECK_NAMES["cost-phone"],
                "status": "info",
                "detail": "No phone numbers to analyze",
                "anchor": "cost-phone",
            }
        )
    else:
        # Phone number inventory table grouped by country/type
        if phone_numbers:
            html += """<table style="width: 80%; margin-bottom:12px;">
                <tr><th>Country</th><th>Type</th><th style="text-align:center;">Count</th><th style="width:40%;">Phone Numbers</th></tr>"""
            phone_inventory_groups = defaultdict(list)
            for pn in phone_numbers:
                group_key = (
                    pn.get("phone_number_country_code", "UNKNOWN"),
                    pn.get("phone_number_type", "UNKNOWN"),
                )
                phone_inventory_groups[group_key].append(pn.get("phone_number", ""))
            for (country, ptype), numbers in sorted(phone_inventory_groups.items()):
                numbers_display = ", ".join(numbers)
                html += f"""<tr><td>{escape(country)}</td><td>{escape(ptype)}</td><td style="text-align:center;">{len(numbers)}</td><td style="font-size:0.85rem; word-wrap:break-word; white-space:normal;">{escape(numbers_display)}</td></tr>"""
            html += """</table>"""

        # Get type counts from available data
        if not phone_type_counts and telephony_data:
            phone_type_counts = telephony_data.get("type_distribution", {})

        toll_free_count = phone_type_counts.get("TOLL_FREE", 0)
        did_count = phone_type_counts.get("DID", 0)

        # Toll-free percentage analysis
        if toll_free_count > 0:
            toll_free_percentage = (toll_free_count / total_numbers) * 100
            if toll_free_percentage > 70:
                html += f"<p>High toll-free usage: {toll_free_percentage:.1f}% of numbers are toll-free (potential cost optimization opportunity)</p>"
            elif toll_free_percentage < 20:
                html += f"<p>Low toll-free usage: Only {toll_free_percentage:.1f}% are toll-free (consider customer accessibility)</p>"

        # DID usage
        if did_count > 0:
            did_percentage = (did_count / total_numbers) * 100
            html += f"<p>DID numbers provide local presence: {did_percentage:.1f}% of total numbers</p>"

        # International presence
        if telephony_data:
            countries_count = telephony_data.get("countries_count", 0)
        else:
            metadata = phone_type_counts.get("_metadata", {})
            countries_count = metadata.get("countries_count", 0)
        if countries_count > 1:
            html += (
                f"<p>International presence: Numbers in {countries_count} countries</p>"
            )

        # Special types
        uifn_count = phone_type_counts.get("UIFN", 0)
        short_code_count = phone_type_counts.get("SHORT_CODE", 0)
        if uifn_count > 0:
            html += f"<p>Global accessibility: {uifn_count} UIFN numbers for international toll-free access</p>"
        if short_code_count > 0:
            html += f"<p>SMS capability: {short_code_count} short codes for messaging services</p>"

        # Recommendations
        html += """
                <h4>Recommendations</h4>"""

        if phone_type_counts.get("TOLL_FREE", 0) == 0:
            html += f"""
                {get_recommendation("phone_number_no_tollfree")}
                """
        if phone_type_counts.get("TOLL_FREE", 0) < phone_type_counts.get("DID", 0):
            html += get_recommendation("phone_number_more_did")
        else:
            html += get_recommendation("phone_number_tollfree_dominant")

        # QB-65 / R2: flow the analyzer's status verbatim so retained JSON and
        # HTML report never contradict each other. Fall back to computing status
        # from raw counts only when the analyzer payload is unavailable (e.g.
        # phone_numbers came from the security-analyzer passthrough with no
        # telephony_usage sibling).
        analyzer_status = telephony_data.get("status") if telephony_data else None
        analyzer_detail = telephony_data.get("detail") if telephony_data else None
        if analyzer_status in VALID_CHECK_STATUSES:
            checks.append(
                {
                    "area": "Cost",
                    "check": CHECK_NAMES["cost-phone"],
                    "status": analyzer_status,
                    "detail": analyzer_detail or "",
                    "anchor": "cost-phone",
                }
            )
        elif toll_free_count == 0 and did_count > 0:
            checks.append(
                {
                    "area": "Cost",
                    "check": CHECK_NAMES["cost-phone"],
                    "status": "warn",
                    "detail": f"{did_count} DIDs, 0 toll-free - consider toll-free for redundancy",
                    "anchor": "cost-phone",
                }
            )
        elif toll_free_count > 0 and did_count > 0 and toll_free_count < did_count:
            checks.append(
                {
                    "area": "Cost",
                    "check": CHECK_NAMES["cost-phone"],
                    "status": "info",
                    "detail": f"{toll_free_count} toll-free, {did_count} DIDs - review cost vs resilience trade-off",
                    "anchor": "cost-phone",
                }
            )
        else:
            checks.append(
                {
                    "area": "Cost",
                    "check": CHECK_NAMES["cost-phone"],
                    "status": "pass",
                    "detail": f"{total_numbers} numbers analyzed, mix looks appropriate",
                    "anchor": "cost-phone",
                }
            )

    html += """</div>"""
    return html


def _render_unused_phone_numbers(findings, checks):
    """Render the Misconfigured Phone Numbers sub-section (Cost).

    Checks for phone numbers with FAILED or IN_PROGRESS claim/port status.
    These numbers incur charges without delivering value.

    Args:
        findings (dict): Cost findings containing phone_numbers data.
        checks (list): Mutable list to append check results to.

    Returns:
        str: HTML for the misconfigured phone numbers sub-section.
    """
    phone_numbers = findings.get("phone_numbers", [])
    total_numbers = len(phone_numbers) if phone_numbers else 0

    if total_numbers == 0:
        checks.append(
            {
                "area": "Cost",
                "check": CHECK_NAMES["cost-phone-health"],
                "status": "info",
                "detail": "No phone numbers to analyze",
                "anchor": "cost-phone-health",
            }
        )
        return (
            '<div class="section" id="cost-phone-health">'
            f"<h3>{CHECK_NAMES['cost-phone-health']}</h3>"
            "<p>No phone numbers to analyze on this instance.</p>"
            "</div>"
        )

    # Only check for FAILED or IN_PROGRESS status
    misconfigured = []
    for pn in phone_numbers:
        status = pn.get("phone_number_status", "CLAIMED")
        if status in ("FAILED", "IN_PROGRESS"):
            misconfigured.append(pn)

    html = f"""<div class="section" id="cost-phone-health">
                <h3>{CHECK_NAMES["cost-phone-health"]}</h3>
                <p>Analyzed <strong>{total_numbers}</strong> phone number(s) for claim/port status issues.</p>"""

    if misconfigured:
        html += f"""<p style="color:#ef4444;">Found <strong>{len(misconfigured)}</strong> phone number(s) with FAILED or IN_PROGRESS status.</p>
                <table style="width: 90%">
                <tr><th>Phone Number</th><th>Type</th><th>Country</th><th>Status</th></tr>"""
        for mpn in misconfigured:
            html += f"""<tr style="background:#fef2f2;"><td>{escape(mpn.get("phone_number", ""))}</td><td>{escape(mpn.get("phone_number_type", ""))}</td><td>{escape(mpn.get("phone_number_country_code", ""))}</td><td style="color:#ef4444; font-weight:600;">{escape(mpn.get("phone_number_status", "UNKNOWN"))}</td></tr>"""
        html += """</table>"""
        html += get_recommendation(
            "phone_number_health", count=len(misconfigured), total=total_numbers
        )
        html += """</div>"""
        checks.append(
            {
                "area": "Cost",
                "check": CHECK_NAMES["cost-phone-health"],
                "status": "warn",
                "detail": f"{len(misconfigured)} number(s) with FAILED/IN_PROGRESS status",
                "anchor": "cost-phone-health",
            }
        )
    else:
        html += """<p style="color:#22c55e;">&#9989; All phone numbers have CLAIMED status - no misconfiguration detected.</p>
                </div>"""
        checks.append(
            {
                "area": "Cost",
                "check": CHECK_NAMES["cost-phone-health"],
                "status": "pass",
                "detail": f"All {total_numbers} numbers have healthy CLAIMED status",
                "anchor": "cost-phone-health",
            }
        )

    return html


def _render_channel_usage_cost(findings, checks):
    """Render the Channel Usage cost analysis sub-section.

    Matches the monolithic Lambda's channel_usage_to_html() output: channel table
    with contacts created/handled, handle times, and cost optimization recommendations
    based on channel mix.

    Args:
        findings (dict): Cost findings containing channel_mix data from the cost analyzer.
        checks (list): Mutable list to append check results to.

    Returns:
        str: HTML for the channel usage cost analysis sub-section.
    """
    channel_mix_data = findings.get("channel_mix", {})
    channels = channel_mix_data.get("channels", [])
    days_back = findings.get("days_back", 14)

    if not channels:
        html = f"""<div class="section" id="cost-channel">
            <h3>{CHECK_NAMES["cost-channel"]} ({days_back}-day)</h3>
            <p>No contact data available for the last {days_back} days.</p>
            </div>"""
        checks.append(
            {
                "area": "Cost",
                "check": CHECK_NAMES["cost-channel"],
                "status": "info",
                "detail": "No contact data available for channel analysis",
                "anchor": "cost-channel",
            }
        )
        return html

    html = f"""<div class="section" id="cost-channel">
        <h3>{CHECK_NAMES["cost-channel"]} ({days_back}-day)</h3>
        <table style="width: 70%">
            <tr><th>Channel</th><th>Contacts Created</th><th>Contacts Handled</th><th>Avg Handle Time</th><th>Total Handle Time</th></tr>"""

    total_created = 0
    total_handled = 0
    channel_created = {}
    channel_sum_ht = {}

    for ch_data in channels:
        ch = ch_data.get("channel", "UNKNOWN")
        created = int(ch_data.get("contacts_created", 0))
        handled = int(ch_data.get("contacts_handled", 0))
        avg_ht = ch_data.get("avg_handle_time_seconds", 0)
        sum_ht = ch_data.get("sum_handle_time_seconds", 0)
        total_created += created
        total_handled += handled
        channel_created[ch] = created
        channel_sum_ht[ch] = sum_ht

        avg_ht_str = f"{int(avg_ht // 60)}m {int(avg_ht % 60)}s" if avg_ht else "-"
        sum_ht_hrs = sum_ht / 3600 if sum_ht else 0
        sum_ht_str = f"{sum_ht_hrs:.1f} hrs" if sum_ht else "-"

        html += f"""<tr>
            <td style="font-weight:600">{escape(ch)}</td>
            <td style="text-align:center">{escape(str(created))}</td>
            <td style="text-align:center">{escape(str(handled))}</td>
            <td style="text-align:center">{avg_ht_str}</td>
            <td style="text-align:center">{sum_ht_str}</td>
            </tr>"""

    html += f"""<tr style="font-weight:700;border-top:2px solid #333">
            <td>TOTAL</td>
            <td style="text-align:center">{total_created}</td>
            <td style="text-align:center">{total_handled}</td>
            <td></td><td></td>
            </tr>
        </table>"""

    # ── Cost Recommendations based on channel mix ──
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
            get_recommendation("channel_voice_heavy", voice_pct=voice_pct)
        )
    if chat_created == 0 and voice_created > 100:
        recommendations.append(get_recommendation("channel_no_chat"))
    if task_created == 0 and total_created > 100:
        recommendations.append(get_recommendation("channel_no_tasks"))
    if voice_avg_ht > 600 and voice_created > 50:
        recommendations.append(
            get_recommendation("channel_high_voice_ht", avg_ht=voice_avg_ht)
        )
    if chat_pct > 0 and voice_pct > 0:
        recommendations.append(
            get_recommendation(
                "channel_multi_channel", voice_pct=voice_pct, chat_pct=chat_pct
            )
        )
    if email_created > 0:
        recommendations.append(
            get_recommendation("channel_email_active", email_count=email_created)
        )
    recommendations.append(get_recommendation("channel_usage_general"))

    html += """<h4>Cost Recommendations</h4><ul>"""
    for rec in recommendations:
        html += f"<li>{rec}</li>"
    html += """</ul></div>"""

    checks.append(
        {
            "area": "Cost",
            "check": CHECK_NAMES["cost-channel"],
            "status": "info",
            "detail": f"Review {days_back}-day channel mix for cost optimization opportunities",
            "anchor": "cost-channel",
        }
    )

    return html


def _render_ai_structured_finding(finding):
    """Render a single AI Analyzer Structured_Finding as an HTML block.

    Each finding is rendered with:
    - check_name as a sub-heading
    - status as a colored badge (pass=green, fail=red, warn=amber, info=blue, error=grey)
    - detail as descriptive text
    - recommendation (if present) as a recommendation block

    Args:
        finding (dict): Structured_Finding with keys:
            - check_name (str): Check identifier
            - status (str): One of pass, fail, warn, info, error
            - detail (str): Descriptive text
            - recommendation (str|None): Optional recommendation text
            - data (dict|None): Optional supporting data (not rendered)

    Returns:
        str: HTML block for this finding.

    Validates: Requirements 10.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
    """
    raw_check_name = str(finding.get("check_name", "Unknown Check"))
    check_name = escape(raw_check_name)
    status = finding.get("status", "info")
    detail = escape(str(finding.get("detail", "")))
    recommendation = finding.get("recommendation")

    # Status badge colors: pass=green, fail=red, warn=amber, info=blue, error=grey
    status_colors = {
        "pass": ("#d4edda", "#155724", "&#9989;"),
        "fail": ("#f8d7da", "#721c24", "&#10060;"),
        "warn": ("#fff3cd", "#856404", "&#9888;&#65039;"),
        "info": ("#d1ecf1", "#0c5460", "&#8505;&#65039;"),
        "error": ("#e2e3e5", "#383d41", "&#9940;"),
    }
    bg_color, text_color, icon = status_colors.get(status, status_colors["info"])

    # Format check_name for display: replace underscores with spaces, title case
    display_name = check_name.replace("_", " ").title()

    html = f'<div id="ai-{check_name}" style="margin: 12px 0; padding: 12px 16px; border-left: 4px solid {text_color}; background: {bg_color}; border-radius: 4px;">'
    html += f'<h4 style="margin: 0 0 6px 0; color: {text_color};">'
    html += f'<span style="background:{text_color}; color:#fff; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:700; margin-right:8px; vertical-align:middle;">AI</span>'
    html += f"{icon} {display_name}</h4>"
    html += f'<p style="margin: 4px 0; color: {text_color};">{detail}</p>'

    if recommendation:
        html += '<div style="margin-top: 8px; padding: 8px 12px; background: rgba(255,255,255,0.7); border-radius: 4px;">'
        html += f"<strong>Recommendation:</strong> {escape(str(recommendation))}"
        html += "</div>"

    # ── QB-51: Per-row table rendering for tabular check_names ──
    # For agent_inventory, prompt_configuration, and guardrails, render a
    # per-row table below the summary detail. Matches the pre-parallel direct
    # renderer styling. Findings with data=None or an empty per-row list fall
    # back to summary-only (no empty table rendered).
    #
    # Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
    data = finding.get("data") or {}

    if raw_check_name == "agent_inventory":
        raw_agents = data.get("_raw_agents") or []
        if raw_agents:
            html += '<table style="width: 80%;">'
            html += "<tr><th>Agent Name</th><th>Type</th><th>Visibility</th></tr>"
            for agent in raw_agents:
                visibility = str(agent.get("visibility", "UNKNOWN"))
                vis_color = "#22c55e" if visibility == "PUBLISHED" else "#f59e0b"
                html += (
                    "<tr>"
                    f"<td>{escape(str(agent.get('name', '')))}</td>"
                    f"<td>{escape(str(agent.get('type', '')))}</td>"
                    f'<td><span style="color:{vis_color}; font-weight:600;">'
                    f"{escape(visibility)}</span></td>"
                    "</tr>"
                )
            html += "</table>"

    elif raw_check_name == "prompt_configuration":
        prompts = data.get("prompts") or []
        if prompts:
            models_used = data.get("models_used") or []
            if isinstance(models_used, list):
                models_display = ", ".join(str(m) for m in models_used)
            else:
                models_display = str(models_used)
            if models_display:
                html += (
                    f'<p style="margin: 4px 0; color: {text_color};">'
                    f"Models in use: {escape(models_display)}</p>"
                )
            html += '<table style="width: 80%;">'
            html += (
                "<tr><th>Prompt Name</th><th>Type</th><th>Model</th>"
                "<th>Visibility</th></tr>"
            )
            for prompt in prompts:
                visibility = str(prompt.get("visibility", "UNKNOWN"))
                vis_color = "#22c55e" if visibility == "PUBLISHED" else "#f59e0b"
                html += (
                    "<tr>"
                    f"<td>{escape(str(prompt.get('name', '')))}</td>"
                    f"<td>{escape(str(prompt.get('type', '')))}</td>"
                    f"<td><code>{escape(str(prompt.get('model_id', '')))}</code></td>"
                    f'<td><span style="color:{vis_color}; font-weight:600;">'
                    f"{escape(visibility)}</span></td>"
                    "</tr>"
                )
            html += "</table>"

    elif raw_check_name == "guardrails":
        guardrails_list = data.get("guardrails") or []
        if guardrails_list:
            html += (
                '<table style="width: 90%;">'
                "<tr><th>Guardrail Name</th><th>Status</th><th>Visibility</th>"
                "<th>Content Filter</th><th>Denied Topics</th><th>PII Filter</th>"
                "<th>Word Filter</th><th>Contextual Grounding</th></tr>"
            )
            yes = '<span style="color: green;">&#10003;</span>'
            no = '<span style="color: red;">&#10007;</span>'
            for gd in guardrails_list:
                html += (
                    "<tr>"
                    f"<td>{escape(str(gd.get('name', 'N/A')))}</td>"
                    f"<td>{escape(str(gd.get('status', 'N/A')))}</td>"
                    f"<td>{escape(str(gd.get('visibility', 'N/A')))}</td>"
                    f'<td style="text-align:center;">{yes if gd.get("content_filter") else no}</td>'
                    f'<td style="text-align:center;">{yes if gd.get("topic_policy") else no}</td>'
                    f'<td style="text-align:center;">{yes if gd.get("pii_filter") else no}</td>'
                    f'<td style="text-align:center;">{yes if gd.get("word_filter") else no}</td>'
                    f'<td style="text-align:center;">{yes if gd.get("grounding") else no}</td>'
                    "</tr>"
                )
            html += "</table>"

            # Missing filter warning banner
            missing_filters = data.get("missing_filters") or []
            if missing_filters:
                html += (
                    '<p style="color: #FFA500; font-weight: bold; margin-top: 8px;">'
                    "Missing guardrail filters detected:</p><ul>"
                )
                for mc in missing_filters:
                    html += f"<li>{escape(str(mc))}</li>"
                html += "</ul>"

            # Agent-guardrail association table
            agent_associations = data.get("agent_associations") or {}
            agents_pub = agent_associations.get("agents_with_guardrail_published") or []
            agents_draft = (
                agent_associations.get("agents_with_guardrail_draft_only") or []
            )
            if agents_pub or agents_draft:
                html += (
                    '<table style="width: 70%; margin-top: 8px;">'
                    "<tr><th>AI Agent</th><th>Guardrail ID</th>"
                    "<th>Status</th></tr>"
                )
                for a in agents_pub:
                    html += (
                        "<tr>"
                        f"<td>{escape(str(a.get('name', '')))}</td>"
                        f"<td>{escape(str(a.get('guardrail_id', '')))}</td>"
                        '<td style="background-color: #00FF00;">PUBLISHED</td>'
                        "</tr>"
                    )
                for a in agents_draft:
                    html += (
                        "<tr>"
                        f"<td>{escape(str(a.get('name', '')))}</td>"
                        f"<td>{escape(str(a.get('guardrail_id', '')))}</td>"
                        '<td style="background-color: #FFA500;">DRAFT ONLY (not live)</td>'
                        "</tr>"
                    )
                html += "</table>"

    html += "</div>"
    return html


def _render_ai_findings_block(
    findings_list, pillar_name, is_partial=False, is_error=False, error_message=""
):
    """Render a block of AI Analyzer structured findings for a pillar section.

    Handles three states:
    - Normal: renders each finding as an HTML block
    - Partial: renders findings + amber indicator noting incomplete analysis
    - Error: renders grey error placeholder

    Args:
        findings_list (list): List of Structured_Finding dicts.
        pillar_name (str): Display name for the pillar (e.g., "Security").
        is_partial (bool): Whether AI analysis was incomplete.
        is_error (bool): Whether the AI analyzer errored.
        error_message (str): Error message if is_error is True.

    Returns:
        tuple: (html_string, checks_list) where checks_list contains check dicts
            for the executive summary.

    Validates: Requirements 10.1, 10.2, 10.3, 10.4
    """
    if is_error:
        # Requirement 10.4: grey error placeholder
        html = '<div style="margin: 16px 0; padding: 12px 16px; background: #e2e3e5; border: 1px solid #d6d8db; border-radius: 4px; color: #383d41;">'
        html += "<strong>&#9940; AI Analysis Unavailable</strong>"
        html += f"<p><em>AI analysis failed: {escape(str(error_message or 'Unknown error'))}</em></p>"
        html += "</div>"
        return html, []

    if not findings_list:
        return "", []

    html = ""
    checks = []

    # Cross-cutting ES/body contract note (QB-71 / R4): AI findings are counted
    # in the Executive Summary but rendered inline within their host pillar,
    # not as dedicated <h3> sub-sections. Documented here so a reader who
    # notices the asymmetry between the 27-check ES and the pillar bodies has
    # the context in place.
    html += (
        "<p><em>AI findings for this pillar are counted in the Executive "
        "Summary but rendered inline here rather than as dedicated "
        "sub-sections.</em></p>\n"
    )

    # Render partial indicator if applicable (Requirement 10.3)
    if is_partial:
        html += '<div class="partial-indicator">'
        html += "&#9888;&#65039; <strong>Partial AI Analysis</strong> &mdash; "
        html += "AI analysis was incomplete due to time constraints. Some checks may not be represented below."
        html += "</div>\n"

    # Render each structured finding (Requirements 10.1, 10.2)
    for finding in findings_list:
        html += _render_ai_structured_finding(finding)

        # Build check entry for executive summary
        check_name = finding.get("check_name", "unknown")
        status = finding.get("status", "info")
        if status not in VALID_CHECK_STATUSES:
            logger.warning(
                "Unexpected AI finding status '%s' for check '%s', defaulting to 'info'",
                status,
                check_name,
            )
            status = "info"
        detail = finding.get("detail", "")
        # For error-status findings, append a pointer to CloudWatch Logs
        if status == "error" and "CloudWatch Logs" not in detail:
            detail = (
                detail.rstrip(". ") + ". " if detail else ""
            ) + "Check CloudWatch Logs for the analyzer Lambda for details."
        # Truncate detail for executive summary
        summary_detail = detail[:120] + "..." if len(detail) > 120 else detail
        display_check_name = check_name.replace("_", " ").title()

        checks.append(
            {
                "area": pillar_name,
                "check": f"AI: {display_check_name}",
                "status": status,
                "detail": summary_detail,
                "anchor": f"ai-{check_name}",
            }
        )

    return html, checks


def _render_error_placeholder(display_name, component_type, failure):
    """Render an error placeholder for a failed section.

    Handles multiple error field formats:
    - Analyzer-returned errors: {'error': '...'}
    - Step Functions catch errors: {'Error': '...', 'Cause': '...'}
    - Design-doc input format: {'errorMessage': '...'}

    Args:
        display_name (str): Human-readable section name.
        component_type (str): Analyzer component type.
        failure (dict): Failure result with error/cause fields in any format.

    Returns:
        str: HTML for the error placeholder.
    """
    # Support multiple field name conventions for error type
    error_type = escape(
        str(
            failure.get("error")
            or failure.get("Error")
            or failure.get("errorType")
            or "Unknown"
        )
    )
    # Support multiple field name conventions for cause/message
    cause = escape(
        str(
            failure.get("cause")
            or failure.get("Cause")
            or failure.get("errorMessage")
            or failure.get("error")
            or failure.get("Error")
            or "No details available"
        )
    )

    return f"""
    <h2>{escape(display_name)}</h2>
    <div class="section" data-component="{escape(component_type)}">
        <div class="error-placeholder">
            <strong>&#9888; Section Unavailable: {escape(display_name)}</strong>
            <p><strong>Analyzer:</strong> {escape(component_type)}</p>
            <p><strong>Error Type:</strong> {error_type}</p>
            <p><strong>Message:</strong> {cause}</p>
            <p><em>This section could not be generated due to an analyzer failure.</em></p>
        </div>
    </div>
"""


def _render_html_footer():
    """Render the HTML document closing tags.

    Matches the monolithic Lambda's footer structure: closes the container div,
    body, and html tags.
    """
    return """
            </div>
            </body>
            </html>
            """


# ════════════════════════════════════════════════════════════════════════════════
# HANDLER AND ORCHESTRATION
# ════════════════════════════════════════════════════════════════════════════════


def lambda_handler(event, context):
    """Report Generator Lambda handler.

    Assembles HTML report from analyzer results stored in Hive-style S3 partitions.

    Args:
        event (dict): Input containing reviewId, instanceId, instanceArn,
                      accountId, awsRegion, s3ReportingBucket, and analyzerResults array.
        context: Lambda context object.

    Returns:
        dict: Report URL, reviewId, status, and analyzer counts.
    """
    logger.info("Report Generator invoked")
    execution_start = time.time()

    # Validate required fields
    required_fields = [
        "reviewId",
        "instanceId",
        "instanceArn",
        "accountId",
        "awsRegion",
        "s3ReportingBucket",
        "analyzerResults",
    ]
    missing = [f for f in required_fields if f not in event]
    if missing:
        error_msg = f"Missing required fields: {', '.join(missing)}"
        logger.error(error_msg)
        return {
            "reportUrl": "",
            "reviewId": event.get("reviewId", ""),
            "status": "failed",
            "analyzersSucceeded": 0,
            "analyzersFailed": 0,
            "analyzersSkipped": 0,
            "error": error_msg,
        }

    review_id = event["reviewId"]
    instance_id = event["instanceId"]
    instance_arn = event["instanceArn"]
    account_id = event["accountId"]
    aws_region = event["awsRegion"]
    s3_bucket = event["s3ReportingBucket"]
    analyzer_results_raw = event["analyzerResults"]

    # Parse analyzer results — separate successes, failures, and skipped
    successes, failures, skipped = _parse_analyzer_results(analyzer_results_raw)

    # Read detailed findings from S3 for each successful result
    s3_client = boto3.client("s3")
    findings_map = _read_findings_from_s3(s3_client, s3_bucket, successes, review_id)

    # Get instance data (alias + full metadata) for the report
    instance_data = _get_instance_data(instance_arn, aws_region, instance_id)
    instance_alias = instance_data.get("InstanceAlias", instance_id)

    # Log whether we're using the resolved alias or falling back (Req 2.4)
    if instance_data.get("_alias_fallback", False):
        logger.warning(
            f"Using instance ID '{instance_id}' as filename identifier "
            "(alias resolution failed or returned empty)."
        )
    else:
        logger.info(f"Resolved instance alias: '{instance_alias}' for S3 filename.")

    # Determine overall status
    if not successes and failures:
        overall_status = "failed"
    elif failures or any(r.get("partial") for r in successes):
        overall_status = "partial"
    else:
        overall_status = "complete"

    # Generate HTML report
    html_content = _render_html_report(
        review_id=review_id,
        instance_id=instance_id,
        instance_alias=instance_alias,
        account_id=account_id,
        aws_region=aws_region,
        successes=successes,
        failures=failures,
        skipped=skipped,
        findings_map=findings_map,
        instance_data=instance_data,
    )

    # Upload HTML report to S3
    now_utc = datetime.now(timezone.utc)
    date_time = now_utc.strftime("%m%d%Y_%H%M%S")
    report_key = f"connect-review_{instance_alias}_{aws_region}_{date_time}.html"

    s3_client.put_object(
        Bucket=s3_bucket,
        Key=report_key,
        Body=html_content,
        ContentType="text/html; charset=utf-8",
    )
    logger.info(f"HTML report uploaded to s3://{s3_bucket}/{report_key}")

    report_url = f"s3://{s3_bucket}/{report_key}"

    # Upload review-metadata.json
    total_wall_clock = time.time() - execution_start
    metadata = _build_review_metadata(
        review_id=review_id,
        instance_id=instance_id,
        aws_region=aws_region,
        account_id=account_id,
        successes=successes,
        failures=failures,
        skipped=skipped,
        report_key=report_key,
        overall_status=overall_status,
        total_wall_clock=total_wall_clock,
    )

    metadata_key = f"review-metadata/{review_id}/review-metadata.json"
    s3_client.put_object(
        Bucket=s3_bucket,
        Key=metadata_key,
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )
    logger.info(f"Review metadata uploaded to s3://{s3_bucket}/{metadata_key}")

    return {
        "reportUrl": report_url,
        "reviewId": review_id,
        "status": overall_status,
        "analyzersSucceeded": len(successes),
        "analyzersFailed": len(failures),
        "analyzersSkipped": len(skipped),
    }


def _parse_analyzer_results(analyzer_results_raw):
    """Parse the analyzerResults array into successes, failures, and skipped.

    Each element in the array is expected to be either:
    - {"analyzerResult": {...}} (from Step Functions branch output)
    - A direct result dict (fallback)

    Args:
        analyzer_results_raw (list): Raw analyzer results from Parallel state.

    Returns:
        tuple: (successes, failures, skipped) — each a list of result dicts.
    """
    successes = []
    failures = []
    skipped = []

    for item in analyzer_results_raw:
        # Handle nested structure from Step Functions
        if isinstance(item, dict) and "analyzerResult" in item:
            result = item["analyzerResult"]
        else:
            result = item

        status = result.get("status", "")

        if status == "success":
            successes.append(result)
        elif status == "partial":
            # Partial results are treated as successes with partial=True flag
            # The data is still usable, just incomplete
            if not result.get("partial"):
                result["partial"] = True
            successes.append(result)
        elif status == "failed":
            failures.append(result)
        elif status == "skipped":
            skipped.append(result)
        elif status == "error":
            # Analyzer-returned errors treated as failures for report purposes
            failures.append(result)
        else:
            # Unknown status — treat as failure
            failures.append(result)

    return successes, failures, skipped


def _read_findings_from_s3(s3_client, bucket, successes, review_id):
    """Read detailed findings from S3 Hive-style paths for each successful result.

    Args:
        s3_client: Boto3 S3 client.
        bucket (str): S3 bucket name.
        successes (list): List of successful analyzer results.
        review_id (str): The review ID.

    Returns:
        dict: Mapping of componentType -> findings document from S3.
    """
    findings_map = {}
    now = datetime.now(timezone.utc)

    for result in successes:
        component_type = result.get("componentType", "")
        if not component_type:
            continue

        # Use s3ResultKey from the result if available, otherwise construct the key
        s3_key = result.get("s3ResultKey", "")
        if not s3_key:
            s3_key = (
                f"data/{component_type}"
                f"/year={now.year:04d}"
                f"/month={now.month:02d}"
                f"/day={now.day:02d}"
                f"/{review_id}.json"
            )

        try:
            response = s3_client.get_object(Bucket=bucket, Key=s3_key)
            body = response["Body"].read().decode("utf-8")
            document = json.loads(body)
            findings_map[component_type] = document
            logger.info(
                f"Read findings for {component_type} from s3://{bucket}/{s3_key}"
            )
        except json.JSONDecodeError as e:
            # Malformed JSON in S3 — log WARNING and provide empty dict (Req 5.5).
            # Do NOT fall back to inline findings since the data is corrupt.
            logger.warning(
                f"Malformed JSON for {component_type} from "
                f"s3://{bucket}/{s3_key}: {e}. Providing empty findings."
            )
            findings_map[component_type] = {
                "componentType": component_type,
                "findings": {},
            }
        except Exception as e:
            logger.warning(
                f"Could not read findings for {component_type} from "
                f"s3://{bucket}/{s3_key}: {e}. Falling back to inline findings."
            )
            # Fall back to inline findings from the result payload (Req 5.4).
            # If inline findings are also missing, provide empty structure.
            if "findings" in result:
                findings_map[component_type] = {
                    "componentType": component_type,
                    "partial": result.get("partial", False),
                    "collectedCount": result.get("collectedCount"),
                    "totalEstimated": result.get("totalEstimated"),
                    "findings": result["findings"],
                }
            else:
                findings_map[component_type] = {
                    "componentType": component_type,
                    "findings": {},
                }

    return findings_map


def _get_instance_data(instance_arn, aws_region, instance_id):
    """Get the full instance data from the Connect describe_instance API.

    Makes a single Connect API call to retrieve all instance metadata needed
    for both the report header (alias) and the instance information section.

    Falls back to a minimal dict with just the instance_id as alias if the
    API call fails. The fallback is treated as an exceptional case (Req 2.4)
    and always logs a WARNING (Req 2.3).

    Args:
        instance_arn (str): Full instance ARN.
        aws_region (str): AWS region.
        instance_id (str): Instance ID (used as fallback alias).

    Returns:
        dict: Instance data dict from the describe_instance API response.
            Contains keys: Id, Arn, InstanceAlias, IdentityManagementType,
            InstanceStatus, ServiceRole, CreatedTime, InboundCallsEnabled,
            OutboundCallsEnabled, and ReplicationConfiguration.
            On failure, returns a minimal dict with Id, InstanceAlias
            set to instance_id, and _alias_fallback=True.
    """
    try:
        connect_client = boto3.client("connect", region_name=aws_region)
        response = connect_client.describe_instance(InstanceId=instance_id)
        instance = response.get("Instance", {})
        # If API succeeded but alias is empty/None, log warning and fall back
        if not instance.get("InstanceAlias"):
            logger.warning(
                f"describe_instance() returned empty InstanceAlias for {instance_id}. "
                "Falling back to instance ID for filename."
            )
            instance["InstanceAlias"] = instance_id
            instance["_alias_fallback"] = True
        else:
            instance["_alias_fallback"] = False
        return instance
    except Exception as e:
        logger.warning(
            f"describe_instance() API call failed for {instance_id}: {e}. "
            "Falling back to instance ID for filename."
        )
        return {
            "Id": instance_id,
            "InstanceAlias": instance_id,
            "_alias_fallback": True,
        }


def _build_review_metadata(
    review_id,
    instance_id,
    aws_region,
    account_id,
    successes,
    failures,
    skipped,
    report_key,
    overall_status,
    total_wall_clock,
):
    """Build the review-metadata.json document.

    Args:
        review_id (str): Review ID.
        instance_id (str): Instance ID.
        aws_region (str): AWS region.
        account_id (str): AWS account ID.
        successes (list): Successful analyzer results.
        failures (list): Failed analyzer results.
        skipped (list): Skipped analyzer results.
        report_key (str): S3 key of the HTML report.
        overall_status (str): Overall review status.
        total_wall_clock (float): Total wall-clock time in seconds.

    Returns:
        dict: Review metadata document.
    """
    now_utc = datetime.now(timezone.utc)

    analyzers_metadata = []

    for result in successes:
        analyzers_metadata.append(
            {
                "componentType": result.get("componentType", ""),
                "status": "success",
                "partial": result.get("partial", False),
                "collectedCount": result.get("collectedCount"),
                "totalEstimated": result.get("totalEstimated"),
                "durationMs": result.get("durationMs", 0),
                "error": None,
            }
        )

    for result in failures:
        analyzers_metadata.append(
            {
                "componentType": result.get("componentType", ""),
                "status": "failed",
                "partial": False,
                "collectedCount": None,
                "totalEstimated": None,
                "durationMs": result.get("durationMs", 0),
                "error": result.get(
                    "error",
                    result.get(
                        "cause",
                        result.get(
                            "Error",
                            result.get("Cause", result.get("errorMessage", "Unknown")),
                        ),
                    ),
                ),
            }
        )

    for result in skipped:
        analyzers_metadata.append(
            {
                "componentType": result.get("componentType", ""),
                "status": "skipped",
                "partial": False,
                "collectedCount": None,
                "totalEstimated": None,
                "durationMs": 0,
                "error": None,
            }
        )

    return {
        "reviewId": review_id,
        "instanceId": instance_id,
        "awsRegion": aws_region,
        "accountId": account_id,
        "timestamp": now_utc.isoformat(),
        "totalWallClockSeconds": round(total_wall_clock, 2),
        "analyzers": analyzers_metadata,
        "reportS3Key": report_key,
        "outcome": overall_status,
    }
