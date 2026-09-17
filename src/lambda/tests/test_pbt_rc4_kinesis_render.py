"""RC4 QB-40 regression tests: Kinesis observability rendering.

Consolidates the bug-condition and preservation properties for QB-40 into a
single module. The QB-40 fix rewrote ``_render_observability_section``'s
Kinesis block to read the analyzer's actual output keys
(``streams_found`` / ``found`` / ``missing`` / ``status`` / ``detail``)
instead of the rc.4 shape (``streams`` etc.). The fix is display-layer-only —
``observability_analyzer.validate_kinesis_stream_alarms`` was not touched.

Three regression invariants are covered:

* **Bug condition (Property 1)** — when the analyzer discovers streams
  (``streams_found`` non-empty), ``_render_observability_section`` MUST emit
  ``id="obs-kinesis-streams"`` and append exactly one ``Kinesis Data Streams``
  check row whose ``status`` and ``detail`` come verbatim from the analyzer.
* **¬C preservation (Property 2)** — when ``kinesis_data_streams`` is absent
  or ``streams_found`` is empty, the renderer MUST emit no anchor and append
  no check row, regardless of what other findings keys are present.
* **Analyzer contract (Property 3)** — the mocked-boto3 return shape of
  ``validate_kinesis_stream_alarms`` MUST match the observed baselines: a
  7-key shape on the empty branch (``found_count`` and ``missing_count`` are
  omitted by that early return) and a 9-key shape on the non-empty branch.

Validates: Requirements 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8.
"""

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from observability_analyzer import (  # noqa: E402
    RECOMMENDED_KINESIS_ALARMS,
    validate_kinesis_stream_alarms,
)
from report_generator import _render_observability_section  # noqa: E402


# ---------------------------------------------------------------------------
# Property 1 — Bug condition: renderer emits anchor and check row when
# streams_found is non-empty. Runs on a small set of concrete analyzer-shaped
# payloads that vary status/detail. The renderer's per-stream table rendering
# is exercised by having representative found/missing entries; no Hypothesis
# strategy is needed because the assertions only inspect the presence of the
# anchor, the check row, and the analyzer-provided status/detail flow-through.
# ---------------------------------------------------------------------------


def _analyzer_kds_payload(status: str, detail: str) -> dict:
    """Return a realistic ``kinesis_data_streams`` payload as the analyzer emits it."""
    stream_names = ["amazon-connect-ctr-0001", "amazon-connect-agent-0001"]
    missing = [
        {
            "stream_name": stream_names[0],
            "metric_name": "GetRecords.IteratorAgeMilliseconds",
            "description": "Iterator age exceeds threshold",
            "severity": "HIGH",
            "rec_statistic": "Maximum",
            "rec_threshold": 60000,
            "rec_period": 300,
            "rec_comparison": "GreaterThanThreshold",
        },
    ]
    found = [
        {
            "stream_name": stream_names[1],
            "metric_name": "WriteProvisionedThroughputExceeded",
            "description": "Write throughput exceeded",
            "severity": "CRITICAL",
            "rec_statistic": "Sum",
            "rec_threshold": 1,
            "rec_period": 60,
            "rec_comparison": "GreaterThanThreshold",
            "alarm_name": "connect-kinesis-write-0001",
            "alarm_state": "OK",
            "has_actions": True,
        },
    ]
    total_recommended = len(found) + len(missing)
    return {
        "streams_found": stream_names,
        "found": found,
        "missing": missing,
        "total_recommended": total_recommended,
        "found_count": len(found),
        "missing_count": len(missing),
        "coverage_pct": round(len(found) / total_recommended * 100, 1),
        "status": status,
        "detail": detail,
    }


@pytest.mark.parametrize(
    "status,detail",
    [
        ("warn", "1 of 2 recommended Kinesis alarms configured across 2 stream(s)"),
        ("pass", "All 2 recommended Kinesis alarms configured across 2 stream(s)"),
        ("error", "Boto client error while enumerating alarms"),
    ],
)
def test_bug_condition_renderer_emits_anchor_and_check_row_when_streams_found(
    status, detail
):
    """When ``streams_found`` is non-empty, the renderer SHALL emit the anchor
    and append exactly one Kinesis check row with analyzer-provided status/detail.

    Validates: Requirements 1.1, 1.2, 1.3.
    """
    findings = {"kinesis_data_streams": _analyzer_kds_payload(status, detail)}

    html, checks = _render_observability_section(findings)

    assert 'id="obs-kinesis-streams"' in html, (
        "QB-40 regression: Kinesis subsection anchor missing from HTML even "
        "though the analyzer reported non-empty streams_found. The renderer "
        "likely regressed to reading the wrong key."
    )
    kinesis_checks = [
        c
        for c in checks
        if c.get("area") == "Observability" and c.get("check") == "Kinesis Data Streams"
    ]
    assert len(kinesis_checks) == 1, (
        f"QB-40 regression: expected exactly one 'Kinesis Data Streams' check "
        f"row, got {len(kinesis_checks)}."
    )
    entry = kinesis_checks[0]
    assert entry["anchor"] == "obs-kinesis-streams"
    assert entry["status"] == status, (
        f"QB-40 regression: check row status={entry['status']!r} does not "
        f"match analyzer-provided {status!r}."
    )
    assert entry["detail"] == detail, (
        "QB-40 regression: check row detail does not match analyzer-provided "
        "detail verbatim."
    )


# ---------------------------------------------------------------------------
# Property 2 — anchor vs check-row separation (post-QB-47, post-QB-75).
#
# QB-40 (rc.5) rewrote the Kinesis renderer to consume the analyzer's real
# alarm-coverage payload; QB-47 (rc.5, post-tag) then unindented the
# ``checks.append(...)`` call so the check row surfaces on the empty branch
# too (the analyzer emits status="info" / detail="No Kinesis Data Streams…"
# and dropping that row on the executive summary was the exact silent-drop
# pattern QB-40 was created to eliminate).
#
# QB-75 (rc.9) then closed the anchor-integrity loop that QB-47 opened: the
# check row was appended unconditionally on the empty branch, but the visual
# ``<div id="obs-kinesis-streams">`` was still guarded on ``streams_found``
# being non-empty — so the ES row's ``href="#obs-kinesis-streams"`` linked
# to an id that was never emitted on instances with 0 KDS. The fix mirrors
# the QB-58 rc.7 pattern in ``_render_unused_phone_numbers``: emit a
# minimal stub ``<div class="section" id="obs-kinesis-streams">`` on the
# empty branch so the anchor exists in the DOM even though the alarm table
# has no data to render.
#
# So the post-QB-75 spec has these guards:
#   * Full alarm-coverage table (``<table>`` inside the section div) —
#     guarded on ``streams_found`` being non-empty. Rendering a zero-row
#     table adds no signal.
#   * Section anchor (``<div class="section" id="obs-kinesis-streams">``) —
#     emitted whenever the ``kinesis_data_streams`` key is present. On the
#     empty branch it wraps a minimal stub body; on the populated branch it
#     wraps the alarm-coverage table.
#   * Check row (Executive Summary entry) — appended whenever the
#     ``kinesis_data_streams`` key is present, regardless of whether
#     ``streams_found`` is empty. Only the "key absent entirely" case
#     produces no check row.
#
# These tests cover all three guards independently.
# ---------------------------------------------------------------------------


_EMPTY_KDS_PAYLOAD = {
    "streams_found": [],
    "found": [],
    "missing": [],
    "total_recommended": 0,
    "coverage_pct": 0,
    "status": "info",
    "detail": "No Kinesis Data Streams are associated with the Connect instance",
}

_LOG_GROUPS_PAYLOAD = {
    "log_groups": [
        {
            "log_group_name": "/aws/connect/main",
            "retention_days": 30,
            "stored_bytes": 12345,
        },
        {
            "log_group_name": "/aws/connect/agent",
            "retention_days": None,
            "stored_bytes": 99,
        },
    ],
    "no_retention_count": 1,
    "total_log_groups": 2,
    "status": "warn",
    "detail": "2 log group(s)",
}


def test_no_kinesis_anchor_when_kinesis_data_streams_key_absent():
    """When the ``kinesis_data_streams`` key is entirely absent from the
    findings dict, the renderer SHALL emit no ``obs-kinesis-streams`` anchor.

    This is the only ¬anchor case post-QB-75 — presence of the key with an
    empty ``streams_found`` list DOES emit a stub anchor (see
    ``test_stub_kinesis_anchor_emitted_when_streams_found_is_empty`` below).

    Validates: outer ``if kds_data:`` guard.
    """
    html, _checks = _render_observability_section({})

    assert 'id="obs-kinesis-streams"' not in html, (
        'Outer guard violated: <div id="obs-kinesis-streams"> emitted on '
        "a findings dict whose kinesis_data_streams key is absent."
    )


@pytest.mark.parametrize(
    "findings",
    [
        pytest.param(
            {"kinesis_data_streams": _EMPTY_KDS_PAYLOAD},
            id="streams-found-empty",
        ),
        pytest.param(
            {
                "kinesis_data_streams": _EMPTY_KDS_PAYLOAD,
                "log_groups": _LOG_GROUPS_PAYLOAD,
                "_ai_structured_findings": [{"kind": "hint", "message": "noop"}],
            },
            id="streams-found-empty-with-unrelated-findings",
        ),
    ],
)
def test_stub_kinesis_anchor_emitted_when_streams_found_is_empty(findings):
    """QB-75 (rc.9): when ``kinesis_data_streams`` is present but
    ``streams_found`` is empty, the renderer SHALL emit a minimal stub
    ``<div class="section" id="obs-kinesis-streams">`` so the Executive
    Summary row's ``href="#obs-kinesis-streams"`` resolves.

    Mirrors the QB-58 rc.7 fix in ``_render_unused_phone_numbers``:
    check row and body anchor are emitted or omitted together. The
    ``obs-kinesis-streams`` anchor pre-QB-75 was silently dropped on
    instances with 0 Kinesis Data Streams, producing a dangling
    Executive Summary link that scrolled to page top.

    Validates: QB-75 (empty-branch stub anchor emission).
    """
    html, _checks = _render_observability_section(findings)

    assert 'id="obs-kinesis-streams"' in html, (
        'QB-75 regression: stub <div id="obs-kinesis-streams"> not emitted '
        "on the empty-streams branch. The Executive Summary row for "
        "'Kinesis Data Streams' now points at a body id that doesn't exist "
        "— clicking it will scroll to page top instead of the section."
    )

    # The stub replaces the alarm-coverage <table>; the table MUST NOT render
    # on the empty branch (nothing meaningful to show, and QB-40 would regress
    # if a zero-row table appeared). Scope the negative assertion to the KDS
    # section — other sections (e.g. log_groups) render their own tables and
    # co-exist in the same HTML output.
    import re

    kds_slice = re.search(
        r'<div class="section" id="obs-kinesis-streams">.*?</div>',
        html,
        flags=re.DOTALL,
    )
    assert kds_slice is not None, (
        "obs-kinesis-streams section did not close cleanly on the empty branch."
    )
    assert "<table" not in kds_slice.group(0), (
        "Empty-branch stub over-renders: the alarm-coverage <table> was "
        "emitted inside the obs-kinesis-streams section for a payload with "
        "zero streams_found. The stub should carry only the heading and a "
        "no-data note."
    )
    assert "No Kinesis Data Streams" in kds_slice.group(0), (
        "Empty-branch stub is missing the no-data explanatory text — the "
        "section body is present but the visible content does not tell the "
        "reader why the table is absent."
    )


def test_no_kinesis_check_row_when_kinesis_data_streams_key_absent():
    """When the ``kinesis_data_streams`` key is entirely absent from the
    findings dict, the renderer SHALL append no Kinesis check row.

    This is the only ¬check-row case post-QB-47 — presence of the key with
    an empty ``streams_found`` list DOES emit a check row (see
    ``test_kinesis_check_row_emitted_on_empty_branch`` below).

    Validates: Requirements 1.7, 1.8 (outer ``if kds_data:`` guard).
    """
    _html, checks = _render_observability_section({})

    kinesis_checks = [c for c in checks if c.get("check") == "Kinesis Data Streams"]
    assert kinesis_checks == [], (
        f"Outer guard violated: {len(kinesis_checks)} 'Kinesis Data Streams' "
        "check row(s) appended even though kinesis_data_streams key is absent "
        "from the findings dict."
    )


def test_kinesis_check_row_emitted_on_empty_branch():
    """QB-47: when ``kinesis_data_streams`` is present but ``streams_found``
    is empty, the renderer SHALL append exactly one Kinesis check row with
    the analyzer-provided ``status`` and ``detail`` flowed through verbatim.

    Regression protection for QB-47 — the pre-fix ``checks.append(...)`` was
    nested inside the inner ``if streams_found:`` guard, silently dropping
    the executive-summary row on instances with zero Kinesis streams.

    Validates: QB-47 (empty-branch check-row emission).
    """
    findings = {"kinesis_data_streams": _EMPTY_KDS_PAYLOAD}

    _html, checks = _render_observability_section(findings)

    kinesis_checks = [
        c
        for c in checks
        if c.get("area") == "Observability" and c.get("check") == "Kinesis Data Streams"
    ]
    assert len(kinesis_checks) == 1, (
        f"QB-47 regression: expected exactly one 'Kinesis Data Streams' check "
        f"row on the empty branch, got {len(kinesis_checks)}. The "
        "checks.append(...) likely regressed back inside the inner "
        "`if streams_found:` guard."
    )
    entry = kinesis_checks[0]
    assert entry["status"] == _EMPTY_KDS_PAYLOAD["status"], (
        f"QB-47 regression: check row status={entry['status']!r} does not "
        f"match analyzer-provided {_EMPTY_KDS_PAYLOAD['status']!r}. The "
        "renderer must flow the analyzer status through verbatim, not "
        "recompute it."
    )
    assert entry["detail"] == _EMPTY_KDS_PAYLOAD["detail"], (
        "QB-47 regression: check row detail does not match analyzer-provided "
        "detail verbatim."
    )


# ---------------------------------------------------------------------------
# Property 3 — Analyzer contract: validate_kinesis_stream_alarms return shape
# is stable. Observed baselines: 7 keys on the empty branch, 9 keys on the
# non-empty branch. The QB-40 fix is display-layer-only so both shapes MUST
# remain identical.
# ---------------------------------------------------------------------------


_NONEMPTY_KEY_SET = {
    "streams_found",
    "found",
    "missing",
    "total_recommended",
    "found_count",
    "missing_count",
    "coverage_pct",
    "status",
    "detail",
}
_EMPTY_KEY_SET = {
    "streams_found",
    "found",
    "missing",
    "total_recommended",
    "coverage_pct",
    "status",
    "detail",
}


def _cw_mock_no_alarms():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{"MetricAlarms": []}]
    return client


def test_analyzer_empty_branch_returns_observed_seven_key_shape():
    """No Kinesis storage configs -> analyzer returns exactly the 7-key shape.

    Validates: Requirement 1.5 (empty-branch shape).
    """
    connect = MagicMock()
    connect.list_instance_storage_configs.return_value = {"StorageConfigs": []}

    result = validate_kinesis_stream_alarms(
        connect,
        _cw_mock_no_alarms(),
        "instance-empty",
        "arn:aws:connect:us-east-1:123456789012:instance/instance-empty",
        time.time(),
        600,
    )

    assert set(result.keys()) == _EMPTY_KEY_SET, (
        f"Analyzer contract regression: empty branch returned keys "
        f"{sorted(result.keys())!r}; expected {sorted(_EMPTY_KEY_SET)!r}."
    )
    assert result["streams_found"] == []
    assert result["status"] == "info"


def test_analyzer_nonempty_branch_returns_observed_nine_key_shape():
    """At least one Kinesis stream -> analyzer returns exactly the 9-key shape.

    Validates: Requirement 1.5 (non-empty-branch shape).
    """
    stream_arn = "arn:aws:kinesis:us-east-1:123456789012:stream/amazon-connect-ctr-0001"
    connect = MagicMock()

    def list_configs(**kwargs):
        if kwargs.get("ResourceType") == "CONTACT_TRACE_RECORDS":
            return {
                "StorageConfigs": [
                    {
                        "StorageType": "KINESIS_STREAM",
                        "KinesisStreamConfig": {"StreamArn": stream_arn},
                    }
                ]
            }
        return {"StorageConfigs": []}

    connect.list_instance_storage_configs.side_effect = list_configs

    result = validate_kinesis_stream_alarms(
        connect,
        _cw_mock_no_alarms(),
        "instance-nonempty",
        "arn:aws:connect:us-east-1:123456789012:instance/instance-nonempty",
        time.time(),
        600,
    )

    assert set(result.keys()) == _NONEMPTY_KEY_SET, (
        f"Analyzer contract regression: non-empty branch returned keys "
        f"{sorted(result.keys())!r}; expected {sorted(_NONEMPTY_KEY_SET)!r}."
    )
    assert result["streams_found"] == ["amazon-connect-ctr-0001"]
    assert result["found_count"] == 0
    assert result["missing_count"] == len(RECOMMENDED_KINESIS_ALARMS)
    assert result["status"] == "warn"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
