"""RC4 QB-42 regression tests: CloudWatch Log Groups observability rendering.

Mirrors the QB-40 Kinesis pattern for the same function
(``report_generator._render_observability_section``). The pre-fix code
appended the log-groups check row **inside** an inner ``if log_groups:``
guard, so when the analyzer reported zero log groups or hit an error the
check row was silently dropped — even though ``check_connect_log_groups``
had produced a meaningful ``status`` and ``detail`` for those cases.

The QB-42 fix flows the analyzer-provided ``status`` and ``detail`` through
verbatim and moves the ``checks.append(...)`` call outside the inner
guard, so the check row surfaces in the Executive Summary for every one
of the analyzer's four return shapes:

* **error branch**   — ``status="error"``, list empty, ``detail=<error string>``
* **empty branch**   — ``status="info"``,  list empty, ``detail="No Connect-related CloudWatch Log Groups found"``
* **warn branch**    — ``status="warn"``,  list non-empty, at least one log group has no retention
* **pass branch**    — ``status="pass"``,  list non-empty, every log group has a retention policy

Three regression invariants are covered:

* **Bug condition (Property 7)** — for all four analyzer branches, the
  renderer appends exactly one ``CloudWatch Log Retention`` check row with
  the analyzer-provided status and detail.
* **¬C preservation (Property 8)** — when ``log_groups`` is absent from
  findings the renderer emits no anchor and no check row.
* **Analyzer contract (Property 9)** — the mocked-boto3 return shape of
  ``check_connect_log_groups`` matches the observed baseline for both
  the empty branch and the non-empty branch.

Validates: QB-42.
"""

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from observability_analyzer import check_connect_log_groups  # noqa: E402
from report_generator import _render_observability_section  # noqa: E402


# ---------------------------------------------------------------------------
# Property 7 — Bug condition: renderer emits a check row for every analyzer
# branch. Parametrized over the four documented return shapes so a regression
# that drops the check row for any single branch surfaces as a targeted
# per-branch failure.
# ---------------------------------------------------------------------------


_ERROR_PAYLOAD = {
    "total_log_groups": 0,
    "no_retention_count": 0,
    "log_groups": [],
    "status": "error",
    "detail": "AccessDeniedException: not authorized to perform logs:DescribeLogGroups",
}

_EMPTY_PAYLOAD = {
    "total_log_groups": 0,
    "no_retention_count": 0,
    "log_groups": [],
    "status": "info",
    "detail": "No Connect-related CloudWatch Log Groups found",
}

_WARN_PAYLOAD = {
    "total_log_groups": 3,
    "no_retention_count": 2,
    "log_groups": [
        {
            "log_group_name": "/aws/connect/instance-0001/ContactFlowLogs",
            "retention_days": None,
            "stored_bytes": 1024 * 1024,
            "has_retention_policy": False,
        },
        {
            "log_group_name": "/aws/connect/instance-0001/ContactLens",
            "retention_days": None,
            "stored_bytes": 4096,
            "has_retention_policy": False,
        },
        {
            "log_group_name": "/aws/connect/instance-0001/AudioTranscripts",
            "retention_days": 30,
            "stored_bytes": 512,
            "has_retention_policy": True,
        },
    ],
    "status": "warn",
    "detail": "2/3 log group(s) have no retention policy (logs retained indefinitely)",
}

_PASS_PAYLOAD = {
    "total_log_groups": 2,
    "no_retention_count": 0,
    "log_groups": [
        {
            "log_group_name": "/aws/connect/instance-0002/ContactFlowLogs",
            "retention_days": 90,
            "stored_bytes": 2048,
            "has_retention_policy": True,
        },
        {
            "log_group_name": "/aws/connect/instance-0002/ContactLens",
            "retention_days": 30,
            "stored_bytes": 1024,
            "has_retention_policy": True,
        },
    ],
    "status": "pass",
    "detail": "All 2 log group(s) have retention policies configured",
}


@pytest.mark.parametrize(
    "payload,expect_table_rendered",
    [
        pytest.param(_ERROR_PAYLOAD, False, id="error-branch"),
        pytest.param(_EMPTY_PAYLOAD, False, id="empty-branch"),
        pytest.param(_WARN_PAYLOAD, True, id="warn-branch"),
        pytest.param(_PASS_PAYLOAD, True, id="pass-branch"),
    ],
)
def test_bug_condition_renderer_emits_check_row_for_every_analyzer_branch(
    payload, expect_table_rendered
):
    """For every ``check_connect_log_groups`` return shape, the renderer SHALL
    append exactly one ``CloudWatch Log Retention`` check row with the
    analyzer-provided status and detail. The visual table SHALL render only
    when the ``log_groups`` list is non-empty.

    Validates: QB-42.
    """
    findings = {"log_groups": payload}

    html, checks = _render_observability_section(findings)

    log_group_checks = [
        c
        for c in checks
        if c.get("area") == "Observability"
        and c.get("check") == "CloudWatch Log Retention"
    ]
    assert len(log_group_checks) == 1, (
        f"QB-42 regression: expected exactly one 'CloudWatch Log Retention' "
        f"check row for the {payload['status']!r} analyzer branch, got "
        f"{len(log_group_checks)}. The check_row append likely regressed "
        "back inside the inner `if log_groups:` guard."
    )
    entry = log_group_checks[0]
    assert entry["anchor"] == "obs-log-groups"
    assert entry["status"] == payload["status"], (
        f"QB-42 regression: check row status={entry['status']!r} does not "
        f"match analyzer-provided {payload['status']!r}. The renderer must "
        "flow the analyzer status through verbatim, not recompute it."
    )
    assert entry["detail"] == payload["detail"], (
        "QB-42 regression: check row detail does not match analyzer-provided "
        "detail verbatim."
    )

    if expect_table_rendered:
        assert 'id="obs-log-groups"' in html, (
            "QB-42 regression: log-groups table missing from HTML for a "
            f"branch with non-empty log_groups list ({payload['status']!r})."
        )
    else:
        assert 'id="obs-log-groups"' not in html, (
            "QB-42 regression: log-groups table rendered for a branch with "
            f"empty log_groups list ({payload['status']!r}). The table "
            "should only render when there is at least one log group to "
            "display."
        )


# ---------------------------------------------------------------------------
# Property 8 — ¬C preservation: no log-groups output when the key is absent.
# ---------------------------------------------------------------------------


def test_no_check_row_and_no_anchor_when_log_groups_absent_from_findings():
    """When ``log_groups`` is not in the findings dict, the renderer SHALL
    emit no ``CloudWatch Log Retention`` check row and no ``obs-log-groups``
    anchor.

    Validates: QB-42 preservation.
    """
    findings = {}

    html, checks = _render_observability_section(findings)

    assert 'id="obs-log-groups"' not in html, (
        "Preservation violated: obs-log-groups anchor emitted with no "
        "log_groups payload in findings."
    )
    log_group_checks = [
        c for c in checks if c.get("check") == "CloudWatch Log Retention"
    ]
    assert log_group_checks == [], (
        f"Preservation violated: {len(log_group_checks)} 'CloudWatch Log "
        "Retention' check row(s) appended with no log_groups payload."
    )


# ---------------------------------------------------------------------------
# Property 9 — Analyzer contract: check_connect_log_groups return shape is
# stable. QB-42 is a display-layer-only fix, so the analyzer's five-key
# return shape (total_log_groups, no_retention_count, log_groups, status,
# detail) MUST remain identical on both branches.
# ---------------------------------------------------------------------------


_EXPECTED_KEY_SET = {
    "total_log_groups",
    "no_retention_count",
    "log_groups",
    "status",
    "detail",
}


def _logs_client_no_log_groups():
    """CloudWatch Logs client mock reporting zero Connect-related log groups."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{"logGroups": []}]
    return client


def _logs_client_one_log_group_no_retention():
    """CloudWatch Logs client mock reporting one log group with no retention.

    Returns from every ``paginate(...)`` call (the analyzer calls paginate
    twice — once per prefix — and dedupes on log_group_name, so the same
    entry showing up twice is expected and correctly handled).
    """
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {
            "logGroups": [
                {
                    "logGroupName": "/aws/connect/instance-0001/ContactFlowLogs",
                    "storedBytes": 2048,
                    # retentionInDays intentionally omitted -> retention_days None
                }
            ]
        }
    ]
    return client


def test_analyzer_empty_branch_returns_expected_five_key_shape():
    """No Connect log groups discovered -> analyzer returns the empty-branch
    five-key shape with status='info'.

    Validates: QB-42 preservation.
    """
    result = check_connect_log_groups(
        _logs_client_no_log_groups(),
        "instance-0001",
        "us-east-1",
        time.time(),
        600,
    )

    assert set(result.keys()) == _EXPECTED_KEY_SET, (
        f"Analyzer contract regression: empty branch returned keys "
        f"{sorted(result.keys())!r}; expected {sorted(_EXPECTED_KEY_SET)!r}."
    )
    assert result["log_groups"] == []
    assert result["total_log_groups"] == 0
    assert result["no_retention_count"] == 0
    assert result["status"] == "info"
    assert result["detail"] == "No Connect-related CloudWatch Log Groups found"


def test_analyzer_nonempty_branch_returns_expected_five_key_shape():
    """At least one Connect log group discovered -> analyzer returns the
    non-empty branch five-key shape with the log_groups list populated.

    Validates: QB-42 preservation.
    """
    result = check_connect_log_groups(
        _logs_client_one_log_group_no_retention(),
        "instance-0001",
        "us-east-1",
        time.time(),
        600,
    )

    assert set(result.keys()) == _EXPECTED_KEY_SET, (
        f"Analyzer contract regression: non-empty branch returned keys "
        f"{sorted(result.keys())!r}; expected {sorted(_EXPECTED_KEY_SET)!r}."
    )
    assert result["total_log_groups"] == 1
    assert result["no_retention_count"] == 1  # retentionInDays absent -> None
    assert result["status"] == "warn"
    assert (
        result["log_groups"][0]["log_group_name"]
        == "/aws/connect/instance-0001/ContactFlowLogs"
    )
    assert result["log_groups"][0]["retention_days"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
