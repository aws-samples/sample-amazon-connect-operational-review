"""Canonical analyzer payload fixtures for end-to-end report generation tests.

Addresses TEST_BACKLOG TB-7: no test previously invoked
``report_generator.lambda_handler`` end-to-end against a mocked S3 client
returning a full set of analyzer documents. Per-section renderers and
per-analyzer handlers were covered in isolation, so bugs living in the seam
between analyzer output schema and renderer expectations (QB-40 Kinesis
render, QB-50 anchor-id mismatch, QB-52 pillar routing) were structurally
invisible to the suite.

These builders produce the two shapes the report generator consumes:

1. ``analyzer_result()`` — an entry in the ``analyzerResults`` array that the
   Step Functions Parallel state hands to the report generator.
2. ``s3_document()`` — the metadata envelope that ``analyzer_common.persist_to_s3``
   writes to ``data/{componentType}/year=.../{reviewId}.json``, which
   ``_read_findings_from_s3`` reads back.

Scope note: the per-pillar ``findings`` bodies here are deliberately minimal.
They are sufficient to drive the full handler path and to assert structural
HTML invariants (anchor integrity, section presence). Enriching them with
realistic per-check payloads is the follow-on work needed to assert
content-level invariants such as "table present when tabular data present".
"""

from datetime import datetime, timezone

# componentType values the report generator recognises, in the order the
# report renders its sections.
COMPONENT_TYPES = [
    "security",
    "observability",
    "operational_excellence",
    "cost",
    "resilience",
    "capacity",
    "cloudtrail",
    "ai",
]

REVIEW_ID = "e2e-review-0001"
INSTANCE_ID = "11111111-2222-3333-4444-555555555555"
INSTANCE_ARN = f"arn:aws:connect:us-east-1:123456789012:instance/{INSTANCE_ID}"
ACCOUNT_ID = "123456789012"
AWS_REGION = "us-east-1"
S3_BUCKET = "e2e-reporting-bucket"


def s3_result_key(component_type, review_id=REVIEW_ID, when=None):
    """Build the Hive-style S3 key that persist_to_s3 would have written."""
    now = when or datetime.now(timezone.utc)
    return (
        f"data/{component_type}"
        f"/year={now.year:04d}"
        f"/month={now.month:02d}"
        f"/day={now.day:02d}"
        f"/{review_id}.json"
    )


def analyzer_result(
    component_type, status="success", review_id=REVIEW_ID, partial=False
):
    """One entry of the ``analyzerResults`` array.

    Wrapped in ``analyzerResult`` to match the Step Functions branch output
    shape that ``_parse_analyzer_results`` unwraps.
    """
    result = {
        "status": status,
        "componentType": component_type,
        "s3ResultKey": s3_result_key(component_type, review_id),
    }
    if partial:
        result["partial"] = True
    return {"analyzerResult": result}


def ai_structured_finding(check_name, status="pass", detail=None, data=None):
    """One entry of ``_ai_structured_findings``.

    Shape confirmed against tests/test_ai_findings_rendering.py, which drives
    ``_render_ai_structured_finding`` directly.
    """
    return {
        "check_name": check_name,
        "status": status,
        "detail": detail or f"{check_name} evaluated",
        "recommendation": None,
        "data": data or {},
    }


# The AI analyzer's findings body is PILLAR-KEYED: report_generator's
# _assemble_cross_analyzer_data reads findings["security"] /
# ["operational_excellence"] / ["resilience"] as lists of structured findings
# and injects each into the target pillar's _ai_structured_findings.
# (observability is deliberately absent from that mapping on unfixed code —
# that is the QB-52 routing bug.)
#
# Tabular payloads per QB-51 ride inside each finding's `data`: a non-empty
# _raw_agents / prompts / guardrails is supposed to render a <table>.
AI_PILLAR_FINDINGS = {
    "security": [
        ai_structured_finding(
            "guardrails",
            status="fail",
            detail="No guardrails configured",
            data={
                "guardrails": [
                    {"name": "guardrail-one", "guardrailId": "g-1", "status": "ACTIVE"}
                ]
            },
        ),
        ai_structured_finding(
            "domain_encryption", detail="Customer-managed KMS key configured"
        ),
    ],
    "operational_excellence": [
        ai_structured_finding(
            "agent_inventory",
            detail="2 agents found",
            data={
                "_raw_agents": [
                    {"name": "agent-alpha", "agentId": "a-1", "type": "MANUAL"},
                    {"name": "agent-beta", "agentId": "a-2", "type": "AUTOMATED"},
                ]
            },
        ),
        ai_structured_finding(
            "prompt_configuration",
            detail="3 prompts found",
            data={
                "prompts": [
                    {"name": "prompt-one", "promptId": "p-1", "type": "TEXT"},
                    {"name": "prompt-two", "promptId": "p-2", "type": "TEXT"},
                    {"name": "prompt-three", "promptId": "p-3", "type": "TEXT"},
                ]
            },
        ),
        ai_structured_finding(
            "agent_logging", status="warn", detail="Logging not enabled for all agents"
        ),
    ],
    "resilience": [
        ai_structured_finding("assistant_discovery", detail="Assistant discovered"),
    ],
}


# ── Empty-data payloads (TB-19) ─────────────────────────────────────────────
#
# The `empty_data=True` mode of `canonical_findings` / `documents_by_key`
# injects analyzer payload shapes that are PRESENT (so their outer
# `if key:` guards pass in the renderer) but carry EMPTY data lists (so
# every renderer's data-conditional inner branch takes the empty path).
#
# Data-conditional branches are the shape that produces the QB-58 / QB-75
# class of defect: the Executive Summary registers the check row
# unconditionally, but the body-section anchor `<div id="...">` is guarded
# on the data list being non-empty — so on instances with zero data the ES
# row links to an id that is never emitted. TestAnchorIntegrity in
# test_e2e_report_generation.py runs against this mode as a second
# parametrized fixture so any future data-conditional section with the
# same shape surfaces the orphan in unit tests instead of a live-test.
#
# Add new entries here as new data-conditional analyzer sections arrive.

_EMPTY_KDS_PAYLOAD_FIXTURE = {
    "streams_found": [],
    "found": [],
    "missing": [],
    "total_recommended": 0,
    "coverage_pct": 0,
    "status": "info",
    "detail": "No Kinesis Data Streams associated with this Connect instance",
}


EMPTY_DATA_FINDINGS_BY_PILLAR = {
    # Observability: analyzer emits `kinesis_data_streams` on every branch;
    # `streams_found=[]` exercises the QB-75 empty-branch stub anchor path.
    "observability": {
        "kinesis_data_streams": _EMPTY_KDS_PAYLOAD_FIXTURE,
    },
}


def canonical_findings(component_type, tabular=False, empty_data=False):
    """Findings body for a pillar.

    Every pillar gets one passing check so that the section renders a row
    rather than being skipped, which is what makes the anchor-integrity
    assertion meaningful.

    With ``tabular=True`` the AI pillar carries pillar-keyed structured
    findings whose ``data`` holds the non-empty per-row payloads that are
    supposed to render as tables (QB-51).

    With ``empty_data=True`` the pillar carries analyzer payloads that are
    present but carry empty data lists — the shape that surfaces the
    QB-58 / QB-75 class of anchor-orphan defects (TB-19).
    """
    findings = {
        "checks": [
            {
                "checkName": f"{component_type}_baseline_check",
                "status": "PASS",
                "summary": f"Baseline {component_type} check passed.",
                "details": [],
            }
        ]
    }
    if component_type == "ai":
        findings.update(AI_PILLAR_FINDINGS if tabular else {})
    if empty_data and component_type in EMPTY_DATA_FINDINGS_BY_PILLAR:
        findings.update(EMPTY_DATA_FINDINGS_BY_PILLAR[component_type])
    return findings


def s3_document(
    component_type,
    findings=None,
    review_id=REVIEW_ID,
    partial=False,
    tabular=False,
    empty_data=False,
):
    """The metadata envelope persist_to_s3 writes, as read back by the generator."""
    document = {
        "reviewId": review_id,
        "instanceId": INSTANCE_ID,
        "accountId": ACCOUNT_ID,
        "awsRegion": AWS_REGION,
        "componentType": component_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "daysBack": 30,
        "partial": partial,
        "findings": (
            canonical_findings(component_type, tabular=tabular, empty_data=empty_data)
            if findings is None
            else findings
        ),
    }
    return document


def full_event(component_types=None, review_id=REVIEW_ID):
    """A complete, valid report generator event with every analyzer succeeding."""
    types = COMPONENT_TYPES if component_types is None else component_types
    return {
        "reviewId": review_id,
        "instanceId": INSTANCE_ID,
        "instanceArn": INSTANCE_ARN,
        "accountId": ACCOUNT_ID,
        "awsRegion": AWS_REGION,
        "s3ReportingBucket": S3_BUCKET,
        "analyzerResults": [analyzer_result(ct, review_id=review_id) for ct in types],
    }


def documents_by_key(
    component_types=None, review_id=REVIEW_ID, tabular=False, empty_data=False
):
    """Map of S3 key -> document, for a fake S3 client to serve via get_object."""
    types = COMPONENT_TYPES if component_types is None else component_types
    return {
        s3_result_key(ct, review_id): s3_document(
            ct, review_id=review_id, tabular=tabular, empty_data=empty_data
        )
        for ct in types
    }
