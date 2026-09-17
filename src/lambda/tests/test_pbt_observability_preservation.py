"""Preservation property tests: existing observability checks render unchanged.

**Validates: Requirements 3.7, 3.8, 3.9, 3.10**

These tests confirm that existing rendering logic in _render_observability_section()
works correctly on UNFIXED code. They serve as a regression safety net before adding
the kinesis_data_streams render block (task 9).

Methodology: observation-first — we observed the actual behavior of each finding key
on the unfixed code, then encoded those observations as property-based tests.

Observed behaviors:
- cloudwatch_alarms: renders <div class="section" id="mon-alarms"> with alarm table,
  appends a check entry with anchor "mon-alarms"
- contact_flow_logging: renders <div class="section" id="ops-logging"> with flow table,
  appends a check entry with anchor "ops-logging"
- missed_calls_metrics: rendering is delegated to OpEx section (NOT rendered here)
  per code comment — no HTML or check is produced by _render_observability_section()
- empty findings dict: produces only the <h2>Observability</h2> heading, no crash
- log_groups: renders <div class="section" id="obs-log-groups"> with table
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from report_generator import _render_observability_section


# ===========================================================================
# Strategies — generate realistic findings payloads for existing keys
# ===========================================================================

ALARM_SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ALARM_STATES = ["OK", "ALARM", "INSUFFICIENT_DATA"]


@st.composite
def alarm_entry_strategy(draw):
    """Generate a single alarm entry (found alarm)."""
    return {
        "metric_name": draw(st.from_regex(r"[A-Z][a-zA-Z]{3,20}", fullmatch=True)),
        "alarm_name": draw(
            st.from_regex(r"connect-[a-z]{3,10}-alarm-[0-9]{1,3}", fullmatch=True)
        ),
        "alarm_state": draw(st.sampled_from(ALARM_STATES)),
        "severity": draw(st.sampled_from(ALARM_SEVERITIES)),
        "has_actions": draw(st.booleans()),
        "actual_statistic": draw(st.sampled_from(["Sum", "Average", "Maximum"])),
        "actual_threshold": draw(st.integers(min_value=1, max_value=1000)),
    }


@st.composite
def missing_alarm_strategy(draw):
    """Generate a single missing alarm entry."""
    return {
        "metric_name": draw(st.from_regex(r"[A-Z][a-zA-Z]{3,20}", fullmatch=True)),
        "severity": draw(st.sampled_from(ALARM_SEVERITIES)),
        "description": draw(
            st.text(
                min_size=5,
                max_size=50,
                alphabet=st.characters(categories=("L", "N", "Z")),
            )
        ),
        "rec_comparison": draw(
            st.sampled_from(["GreaterThanOrEqualToThreshold", "GreaterThanThreshold"])
        ),
        "rec_statistic": draw(st.sampled_from(["Sum", "Average", "Maximum"])),
        "rec_threshold": draw(st.integers(min_value=1, max_value=500)),
        "rec_period": draw(st.sampled_from([60, 300, 900])),
    }


@st.composite
def cloudwatch_alarms_strategy(draw):
    """Generate a cloudwatch_alarms findings payload."""
    found = draw(st.lists(alarm_entry_strategy(), min_size=0, max_size=5))
    missing = draw(st.lists(missing_alarm_strategy(), min_size=0, max_size=5))
    # Ensure at least one of found or missing is non-empty for meaningful rendering
    assume(len(found) + len(missing) > 0)

    total = len(found) + len(missing)
    found_count = len(found)
    missing_count = len(missing)
    coverage_pct = round((found_count / total) * 100, 1) if total else 0
    triggered_count = sum(1 for f in found if f.get("alarm_state") == "ALARM")

    return {
        "found": found,
        "missing": missing,
        "extra": [],
        "total_recommended": total,
        "found_count": found_count,
        "missing_count": missing_count,
        "coverage_pct": coverage_pct,
        "triggered_count": triggered_count,
        "status": "pass" if missing_count == 0 else "warn",
        "detail": f"{found_count}/{total} configured",
    }


FLOW_TYPES = ["CONTACT_FLOW", "CUSTOMER_QUEUE", "CUSTOMER_HOLD", "AGENT_WHISPER"]
FLOW_STATUSES = ["PUBLISHED", "SAVED"]
FLOW_STATES = ["ACTIVE", "ARCHIVED"]


@st.composite
def flow_entry_strategy(draw):
    """Generate a single contact flow entry missing logging."""
    return {
        "flow_name": draw(st.from_regex(r"[A-Z][a-zA-Z ]{3,20}", fullmatch=True)),
        "flow_id": draw(
            st.from_regex(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", fullmatch=True)
        ),
        "flow_type": draw(st.sampled_from(FLOW_TYPES)),
        "status": draw(st.sampled_from(FLOW_STATUSES)),
        "state": draw(st.sampled_from(FLOW_STATES)),
    }


@st.composite
def contact_flow_logging_strategy(draw):
    """Generate a contact_flow_logging findings payload."""
    total_analyzed = draw(st.integers(min_value=1, max_value=200))
    flows_without = draw(st.lists(flow_entry_strategy(), min_size=0, max_size=5))
    skipped = draw(st.integers(min_value=0, max_value=20))
    was_capped = draw(st.booleans())

    return {
        "total_analyzed": total_analyzed,
        "flows_without_logging": flows_without,
        "flows_without_logging_count": len(flows_without),
        "skipped_flows_count": skipped,
        "was_capped": was_capped,
        "status": "pass" if len(flows_without) == 0 else "warn",
        "detail": f"{len(flows_without)} flows missing logging",
    }


@st.composite
def missed_calls_metrics_strategy(draw):
    """Generate a missed_calls_metrics findings payload."""
    total_missed = draw(st.integers(min_value=0, max_value=5000))
    daily_avg = draw(
        st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
    )
    peak_count = draw(st.integers(min_value=0, max_value=1000))
    days = draw(st.integers(min_value=1, max_value=30))

    return {
        "total_missed_calls": total_missed,
        "daily_average": round(daily_avg, 1),
        "peak_day_count": peak_count,
        "peak_day_date": "2025-01-15",
        "daily_data": [],
        "days_analyzed": days,
        "status": "info",
        "detail": f"{total_missed} missed calls over {days} days",
    }


@st.composite
def log_groups_strategy(draw):
    """Generate a log_groups findings payload."""
    log_groups = draw(
        st.lists(
            st.fixed_dictionaries(
                {
                    "log_group_name": st.from_regex(
                        r"/aws/connect/[a-z]{3,10}", fullmatch=True
                    ),
                    "retention_days": st.one_of(
                        st.none(), st.sampled_from([1, 7, 14, 30, 90, 365])
                    ),
                    "stored_bytes": st.integers(min_value=0, max_value=10_000_000_000),
                }
            ),
            min_size=1,
            max_size=5,
        )
    )
    no_retention_count = sum(1 for lg in log_groups if lg["retention_days"] is None)

    return {
        "log_groups": log_groups,
        "no_retention_count": no_retention_count,
        "total_log_groups": len(log_groups),
        "status": "pass" if no_retention_count == 0 else "warn",
        "detail": f"{len(log_groups)} log groups",
    }


# ===========================================================================
# Property 2: Preservation — Existing Observability Rendering Unchanged
# ===========================================================================


class TestCloudWatchAlarmsPreservation:
    """Verify cloudwatch_alarms rendering works correctly and never crashes.

    **Validates: Requirements 3.7**
    """

    @given(alarm_findings=cloudwatch_alarms_strategy())
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_cloudwatch_alarms_renders_section(self, alarm_findings):
        """Assert cloudwatch_alarms produces the mon-alarms section in HTML.

        **Validates: Requirements 3.7**
        """
        findings = {"cloudwatch_alarms": alarm_findings}
        html_output, checks = _render_observability_section(findings)

        # Crash-safety: verify return types
        assert isinstance(html_output, str)
        assert isinstance(checks, list)

        # Section must render with the expected anchor
        assert 'id="mon-alarms"' in html_output, (
            "cloudwatch_alarms findings did not produce 'id=\"mon-alarms\"' section"
        )

        # Must contain the section heading
        assert "CloudWatch Alarm Validation" in html_output

        # Must produce at least one check entry with the mon-alarms anchor
        alarm_checks = [c for c in checks if c.get("anchor") == "mon-alarms"]
        assert len(alarm_checks) == 1, (
            f"Expected exactly 1 check with anchor 'mon-alarms', got {len(alarm_checks)}"
        )

        # Check must have required fields
        check = alarm_checks[0]
        assert check["area"] == "Observability"
        assert check["status"] in ("pass", "warn", "fail")
        assert "detail" in check


class TestContactFlowLoggingPreservation:
    """Verify contact_flow_logging rendering works correctly and never crashes.

    **Validates: Requirements 3.9**
    """

    @given(flow_findings=contact_flow_logging_strategy())
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_contact_flow_logging_renders_section(self, flow_findings):
        """Assert contact_flow_logging produces the ops-logging section.

        **Validates: Requirements 3.9**
        """
        findings = {"contact_flow_logging": flow_findings}
        html_output, checks = _render_observability_section(findings)

        # Crash-safety: verify return types
        assert isinstance(html_output, str)
        assert isinstance(checks, list)

        # Section must render with the expected anchor
        assert 'id="ops-logging"' in html_output, (
            "contact_flow_logging findings did not produce 'id=\"ops-logging\"' section"
        )

        # Must contain the canonical section heading (QB-68: unified naming;
        # "Contact Flow Logging" is the ES form, never "Contact Flows Missing Logging").
        assert "Contact Flow Logging" in html_output

        # Must produce a check entry with the ops-logging anchor
        flow_checks = [c for c in checks if c.get("anchor") == "ops-logging"]
        assert len(flow_checks) == 1, (
            f"Expected exactly 1 check with anchor 'ops-logging', got {len(flow_checks)}"
        )

        check = flow_checks[0]
        assert check["area"] == "Observability"
        assert check["status"] in ("pass", "warn")


class TestMissedCallsMetricsPreservation:
    """Verify missed_calls_metrics behavior: NOT rendered in observability section.

    **Validates: Requirements 3.8**

    Observed behavior: The code comment at line ~4182 states missed calls rendering
    is handled in the OpEx section via cross-analyzer data merge. The
    _render_observability_section() does NOT produce HTML or checks for this key.
    This test confirms that observed behavior is preserved.
    """

    @given(missed_findings=missed_calls_metrics_strategy())
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_missed_calls_metrics_not_rendered_here(self, missed_findings):
        """Assert missed_calls_metrics does not crash and is NOT rendered here.

        **Validates: Requirements 3.8**

        The function accepts this key in findings but delegates rendering to OpEx.
        It must not crash when the key is present, and must not produce any
        check entries in the observability section.
        """
        findings = {"missed_calls_metrics": missed_findings}
        html_output, checks = _render_observability_section(findings)

        # Crash-safety: verify return types
        assert isinstance(html_output, str)
        assert isinstance(checks, list)

        # Should NOT produce any check with "missed" in it
        missed_checks = [c for c in checks if "missed" in c.get("check", "").lower()]
        assert len(missed_checks) == 0, (
            "missed_calls_metrics should NOT produce check entries in observability "
            "section — rendering is delegated to OpEx"
        )


class TestEmptyFindingsPreservation:
    """Verify empty findings dict produces no errors.

    **Validates: Requirements 3.10**
    """

    def test_empty_findings_no_crash(self):
        """Assert empty findings dict produces section heading without errors.

        **Validates: Requirements 3.10**
        """
        html_output, checks = _render_observability_section({})
        assert isinstance(html_output, str)
        assert isinstance(checks, list)
        # Should have the section heading
        assert "<h2>Observability</h2>" in html_output
        # Should have zero checks (no findings to render)
        assert len(checks) == 0

    def test_none_values_no_crash(self):
        """Assert findings with None values for known keys don't crash.

        **Validates: Requirements 3.10**
        """
        findings = {
            "cloudwatch_alarms": None,
            "contact_flow_logging": None,
            "missed_calls_metrics": None,
        }
        # None values should be falsy and skip rendering
        html_output, checks = _render_observability_section(findings)
        assert isinstance(html_output, str)
        assert isinstance(checks, list)


class TestCombinedFindingsPreservation:
    """Verify combinations of existing finding keys render correctly together.

    **Validates: Requirements 3.7, 3.8, 3.9, 3.10**
    """

    @given(
        alarm_findings=st.one_of(st.none(), cloudwatch_alarms_strategy()),
        flow_findings=st.one_of(st.none(), contact_flow_logging_strategy()),
        log_findings=st.one_of(st.none(), log_groups_strategy()),
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_combined_findings_render_correctly(
        self, alarm_findings, flow_findings, log_findings
    ):
        """Assert any combination of existing findings keys renders without crash
        and produces correct check anchors.

        **Validates: Requirements 3.7, 3.9, 3.10**
        """
        findings = {}
        if alarm_findings is not None:
            findings["cloudwatch_alarms"] = alarm_findings
        if flow_findings is not None:
            findings["contact_flow_logging"] = flow_findings
        if log_findings is not None:
            findings["log_groups"] = log_findings

        html_output, checks = _render_observability_section(findings)
        assert isinstance(html_output, str)
        assert isinstance(checks, list)

        # Verify consistency: if alarm_findings provided, alarm section rendered
        if alarm_findings is not None:
            assert 'id="mon-alarms"' in html_output
            anchors = [c.get("anchor") for c in checks]
            assert "mon-alarms" in anchors
        # If flow findings provided, flow section rendered
        if flow_findings is not None:
            assert 'id="ops-logging"' in html_output
            anchors = [c.get("anchor") for c in checks]
            assert "ops-logging" in anchors
        # If log groups provided, log section rendered
        if log_findings is not None:
            assert 'id="obs-log-groups"' in html_output


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
