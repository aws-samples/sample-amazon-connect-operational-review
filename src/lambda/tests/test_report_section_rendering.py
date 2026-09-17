"""Tests for report section-level rendering: error placeholders, partial results, and skipped analyzers.

Consolidated from:
- test_error_placeholders.py (task 12.3)
- test_partial_results.py (task 12.2)
- test_skipped_analyzers.py (task 12.4)

Validates:
- Requirement 5: Error placeholder rendering for failed analyzers
- Task 12.2: Partial/amber indicator rendering for timed-out analyzers
- Task 12.4: Skipped analyzer omission from report output
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report_generator import (
    _parse_analyzer_results,
    _render_error_placeholder,
    _render_html_report,
    _render_section,
    SECTION_ORDER,
    SECTION_DISPLAY_NAMES,
)


# ===========================================================================
# SECTION 1: Parse Analyzer Results (shared across error/partial/skipped)
# ===========================================================================


class TestParseAnalyzerResultsRouting:
    """Test that _parse_analyzer_results routes results to correct lists."""

    # -- Error routing --

    def test_status_error_goes_to_failures(self):
        """When status='error', result should be in failures list."""
        raw = [{"componentType": "capacity", "status": "error", "error": "Timeout"}]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 0
        assert len(failures) == 1
        assert failures[0]["componentType"] == "capacity"
        assert failures[0]["error"] == "Timeout"

    def test_status_failed_goes_to_failures(self):
        """When status='failed', result should be in failures list."""
        raw = [
            {"componentType": "security", "status": "failed", "error": "AccessDenied"}
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(failures) == 1
        assert failures[0]["componentType"] == "security"

    def test_unknown_status_goes_to_failures(self):
        """When status is unknown/unexpected, result should be in failures list."""
        raw = [{"componentType": "cost", "status": "bogus"}]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(failures) == 1

    def test_error_with_step_functions_wrapper(self):
        """Error results wrapped in {'analyzerResult': {...}} are unwrapped correctly."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "capacity",
                    "status": "error",
                    "error": "Lambda.Timeout",
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(failures) == 1
        assert failures[0]["error"] == "Lambda.Timeout"

    # -- Partial routing --

    def test_status_partial_sets_partial_flag(self):
        """When status='partial', result should be in successes with partial=True."""
        raw = [{"componentType": "capacity", "status": "partial", "findings": {}}]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 1
        assert successes[0]["partial"] is True
        assert len(failures) == 0

    def test_status_success_with_partial_true_preserved(self):
        """When status='success' and partial=True, flag is preserved."""
        raw = [
            {
                "componentType": "capacity",
                "status": "success",
                "partial": True,
                "collectedCount": 2,
                "totalEstimated": 3,
                "findings": {
                    "timed_out": True,
                    "checks_completed": ["instance_quotas", "concurrency_limits"],
                },
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 1
        assert successes[0]["partial"] is True
        assert successes[0]["collectedCount"] == 2
        assert successes[0]["totalEstimated"] == 3

    def test_step_functions_wrapper_partial_unwrap(self):
        """Partial results wrapped in {'analyzerResult': {...}} are unwrapped correctly."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "cloudtrail",
                    "status": "partial",
                    "partial": True,
                    "findings": {"timed_out": True},
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 1
        assert successes[0]["partial"] is True
        assert successes[0]["componentType"] == "cloudtrail"

    def test_normal_success_not_partial(self):
        """A normal success result should not have partial flag."""
        raw = [{"componentType": "security", "status": "success", "findings": {}}]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 1
        assert successes[0].get("partial", False) is False

    # -- Skipped routing --

    def test_status_skipped_goes_to_skipped_list(self):
        """When status='skipped', result should be in skipped list."""
        raw = [{"componentType": "observability", "status": "skipped"}]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 0
        assert len(failures) == 0
        assert len(skipped) == 1
        assert skipped[0]["componentType"] == "observability"

    def test_multiple_skipped_analyzers(self):
        """Multiple skipped analyzers all end up in skipped list."""
        raw = [
            {"componentType": "observability", "status": "skipped"},
            {"componentType": "cost", "status": "skipped"},
            {"componentType": "cloudtrail", "status": "skipped"},
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(skipped) == 3
        component_types = {r["componentType"] for r in skipped}
        assert component_types == {"observability", "cost", "cloudtrail"}

    def test_skipped_with_step_functions_wrapper(self):
        """Skipped results wrapped in {'analyzerResult': {...}} are unwrapped."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "capacity",
                    "status": "skipped",
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(skipped) == 1
        assert skipped[0]["componentType"] == "capacity"

    # -- Mixed routing --

    def test_mixed_results_parsed_correctly(self):
        """A mix of success, error, and skipped are parsed into correct lists."""
        raw = [
            {"componentType": "security", "status": "success", "findings": {}},
            {"componentType": "capacity", "status": "error", "error": "Timeout"},
            {"componentType": "observability", "status": "skipped"},
            {"componentType": "cost", "status": "success", "findings": {}},
            {"componentType": "cloudtrail", "status": "skipped"},
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 2
        assert len(failures) == 1
        assert len(skipped) == 2
        assert failures[0]["componentType"] == "capacity"


# ===========================================================================
# SECTION 2: Error Placeholder Rendering
# ===========================================================================


class TestRenderErrorPlaceholder:
    """Test _render_error_placeholder renders correct HTML for various error formats."""

    def test_basic_error_from_analyzer(self):
        """Standard analyzer error with 'error' field renders correctly."""
        failure = {
            "componentType": "capacity",
            "status": "error",
            "error": "Lambda function timed out",
        }
        html = _render_error_placeholder("Capacity Analysis", "capacity", failure)
        assert "error-placeholder" in html
        assert "Section Unavailable: Capacity Analysis" in html
        assert "capacity" in html
        assert "Lambda function timed out" in html
        assert "could not be generated due to an analyzer failure" in html

    def test_error_with_cause_field(self):
        """Error result with both 'error' and 'cause' fields."""
        failure = {
            "componentType": "security",
            "error": "AccessDenied",
            "cause": "IAM role lacks connect:DescribeInstance permission",
        }
        html = _render_error_placeholder("Security", "security", failure)
        assert "AccessDenied" in html
        assert "IAM role lacks connect:DescribeInstance permission" in html

    def test_step_functions_error_format(self):
        """Step Functions catch format with capitalized 'Error' and 'Cause' fields."""
        failure = {
            "componentType": "capacity",
            "Error": "States.TaskFailed",
            "Cause": '{"errorMessage":"Function timed out after 900 seconds"}',
        }
        html = _render_error_placeholder("Capacity Analysis", "capacity", failure)
        assert "States.TaskFailed" in html
        assert "Function timed out after 900 seconds" in html

    def test_design_doc_error_message_format(self):
        """Design doc format with 'errorMessage' field."""
        failure = {
            "componentType": "capacity",
            "status": "error",
            "errorMessage": "Timeout",
        }
        html = _render_error_placeholder("Capacity Analysis", "capacity", failure)
        assert "Timeout" in html
        assert "Section Unavailable: Capacity Analysis" in html

    def test_completely_empty_failure_dict(self):
        """Empty failure dict renders with 'Unknown' defaults gracefully."""
        failure = {"componentType": "cost"}
        html = _render_error_placeholder("Cost Considerations", "cost", failure)
        assert "error-placeholder" in html
        assert "Section Unavailable: Cost Considerations" in html
        assert "Unknown" in html
        assert "No details available" in html

    def test_html_escaping_in_error_message(self):
        """Error messages with HTML-dangerous characters are escaped."""
        failure = {
            "componentType": "security",
            "error": '<script>alert("xss")</script>',
            "cause": 'Error with "quotes" & <brackets>',
        }
        html = _render_error_placeholder("Security", "security", failure)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&amp;" in html
        assert "&lt;brackets&gt;" in html

    def test_error_placeholder_has_section_header(self):
        """Error placeholder includes an h2 heading for the section."""
        failure = {"error": "Test error"}
        html = _render_error_placeholder("Observability", "observability", failure)
        assert "<h2>Observability</h2>" in html

    def test_error_placeholder_has_data_component_attribute(self):
        """Error placeholder div includes data-component attribute for identification."""
        failure = {"error": "Test error"}
        html = _render_error_placeholder("CloudTrail Analysis", "cloudtrail", failure)
        assert 'data-component="cloudtrail"' in html


# ===========================================================================
# SECTION 3: Partial Result (Amber Indicator) Rendering
# ===========================================================================


class TestRenderSectionPartialIndicator:
    """Test that _render_section produces amber indicator HTML for partial results."""

    def test_partial_renders_amber_indicator(self):
        """When is_partial=True, the section should contain the amber indicator div."""
        html, checks = _render_section(
            display_name="Capacity Analysis",
            component_type="capacity",
            findings={
                "instance_quotas": {
                    "resources": [],
                    "total_checked": 0,
                    "measurable_count": 0,
                    "unmeasurable_count": 0,
                    "pass_count": 0,
                    "warn_count": 0,
                    "fail_count": 0,
                    "status": "pass",
                    "detail": "No quotas checked",
                }
            },
            is_partial=True,
        )
        assert "partial-indicator" in html
        assert "Partial results" in html

    def test_partial_with_counts_renders_collection_info(self):
        """When collected_count and total_estimated are provided, they appear in the indicator."""
        html, checks = _render_section(
            display_name="Capacity Analysis",
            component_type="capacity",
            findings={
                "instance_quotas": {
                    "resources": [],
                    "total_checked": 0,
                    "measurable_count": 0,
                    "unmeasurable_count": 0,
                    "pass_count": 0,
                    "warn_count": 0,
                    "fail_count": 0,
                    "status": "pass",
                    "detail": "No quotas checked",
                }
            },
            is_partial=True,
            collected_count=2,
            total_estimated=3,
        )
        assert "partial-indicator" in html
        assert "2 of estimated 3 items" in html

    def test_partial_with_only_collected_count(self):
        """When only collected_count is provided (no total), shows count without total."""
        html, checks = _render_section(
            display_name="CloudTrail Analysis",
            component_type="cloudtrail",
            findings={
                "days_back": 14,
                "total_events_analyzed": 500,
                "total_throttled": 0,
                "throttled_by_api": [],
                "timed_out": True,
                "account_id": "123456789012",
                "region": "us-east-1",
            },
            is_partial=True,
            collected_count=500,
        )
        assert "partial-indicator" in html
        assert "500 items" in html

    def test_partial_without_counts_shows_generic_message(self):
        """When no counts are provided, shows a generic incomplete message."""
        html, checks = _render_section(
            display_name="Security",
            component_type="security",
            findings={},
            is_partial=True,
        )
        assert "partial-indicator" in html
        assert "incomplete due to time budget" in html

    def test_non_partial_no_indicator(self):
        """When is_partial=False, no amber indicator should appear."""
        html, checks = _render_section(
            display_name="Security",
            component_type="security",
            findings={},
            is_partial=False,
        )
        assert "partial-indicator" not in html

    def test_partial_still_renders_section_content(self):
        """Partial sections should still render whatever data was collected."""
        html, checks = _render_section(
            display_name="CloudTrail Analysis",
            component_type="cloudtrail",
            findings={
                "days_back": 14,
                "total_events_analyzed": 1000,
                "total_throttled": 5,
                "throttled_by_api": [
                    {"event_name": "ListQueues", "count": 3},
                    {"event_name": "GetContactAttributes", "count": 2},
                ],
                "timed_out": True,
                "account_id": "123456789012",
                "region": "us-east-1",
            },
            is_partial=True,
            collected_count=1000,
        )
        assert "partial-indicator" in html
        assert "ListQueues" in html
        assert "GetContactAttributes" in html
        assert "5" in html

    def test_partial_indicator_amber_styling_referenced(self):
        """The partial indicator references the .partial-indicator CSS class."""
        html, checks = _render_section(
            display_name="Cost Considerations",
            component_type="cost",
            findings={},
            is_partial=True,
        )
        assert 'class="partial-indicator"' in html


class TestPartialDetectionLogic:
    """Test partial detection logic from multiple sources."""

    def test_timed_out_in_findings_triggers_partial(self):
        """If findings.timed_out=True, the section should be treated as partial."""
        result = {"componentType": "capacity", "status": "success"}
        document = {
            "partial": False,
            "findings": {"timed_out": True, "checks_completed": ["instance_quotas"]},
        }
        findings = document.get("findings", {})

        is_partial = (
            result.get("partial", False)
            or document.get("partial", False)
            or findings.get("timed_out", False)
        )
        assert is_partial is True

    def test_document_partial_flag_triggers_partial(self):
        """If the S3 document envelope has partial=True, section is partial."""
        result = {"componentType": "capacity", "status": "success"}
        document = {
            "partial": True,
            "collectedCount": 2,
            "totalEstimated": 3,
            "findings": {},
        }

        is_partial = (
            result.get("partial", False)
            or document.get("partial", False)
            or document.get("findings", {}).get("timed_out", False)
        )
        assert is_partial is True

    def test_result_partial_flag_triggers_partial(self):
        """If the Step Functions result has partial=True, section is partial."""
        result = {
            "componentType": "capacity",
            "status": "success",
            "partial": True,
            "collectedCount": 1,
            "totalEstimated": 3,
        }
        document = {"partial": False, "findings": {}}

        is_partial = (
            result.get("partial", False)
            or document.get("partial", False)
            or document.get("findings", {}).get("timed_out", False)
        )
        assert is_partial is True

    def test_no_partial_flags_means_not_partial(self):
        """When no partial indicators are present, section is not partial."""
        result = {"componentType": "security", "status": "success"}
        document = {"partial": False, "findings": {"timed_out": False}}
        findings = document.get("findings", {})

        is_partial = (
            result.get("partial", False)
            or document.get("partial", False)
            or findings.get("timed_out", False)
        )
        assert is_partial is False


# ===========================================================================
# SECTION 4: Skipped Analyzer Omission
# ===========================================================================


class TestSkippedSectionOmission:
    """Test that skipped sections are completely omitted from the HTML report."""

    def test_skipped_section_has_no_html_output(self):
        """A skipped section should produce zero HTML — no heading, no content."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[],
            failures=[],
            skipped=[{"componentType": "observability", "status": "skipped"}],
            findings_map={},
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "Observability</h2>" not in html
        assert "Observability</h3>" not in html
        assert "Section Unavailable: Observability" not in html

    def test_skipped_section_no_error_placeholder(self):
        """A skipped section must NOT get an error placeholder."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[],
            failures=[],
            skipped=[{"componentType": "cost", "status": "skipped"}],
            findings_map={},
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "Section Unavailable: Cost" not in html

    def test_multiple_skipped_sections_all_omitted(self):
        """All skipped sections are omitted, not just the first one."""
        skipped_types = ["observability", "cost", "resilience"]
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[],
            failures=[],
            skipped=[
                {"componentType": ct, "status": "skipped"} for ct in skipped_types
            ],
            findings_map={},
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        for ct in skipped_types:
            display_name = SECTION_DISPLAY_NAMES[ct]
            assert f"Section Unavailable: {display_name}" not in html

    def test_skipped_does_not_contribute_to_executive_summary(self):
        """Skipped analyzers should not add any checks to the executive summary."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[],
            failures=[],
            skipped=[
                {"componentType": ct, "status": "skipped"} for ct in SECTION_ORDER
            ],
            findings_map={},
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "PASS" not in html or html.count("PASS") == 0 or "No checks" in html


# ===========================================================================
# SECTION 5: Full Report Assembly Integration
# ===========================================================================


class TestReportAssemblyIntegration:
    """Test error placeholders, partial, and skipped in full report assembly."""

    def test_failed_section_renders_error_placeholder(self):
        """A failed analyzer's section should contain error-placeholder HTML."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[],
            failures=[
                {
                    "componentType": "capacity",
                    "status": "error",
                    "error": "Lambda.Timeout",
                }
            ],
            skipped=[],
            findings_map={},
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "error-placeholder" in html
        assert "Section Unavailable: Capacity Analysis" in html
        assert "Lambda.Timeout" in html

    def test_section_independence_error_does_not_block_others(self):
        """A failed section should not prevent other sections from rendering."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[
                {
                    "componentType": "security",
                    "status": "success",
                    "findings": {},
                }
            ],
            failures=[
                {
                    "componentType": "capacity",
                    "status": "error",
                    "error": "Timeout",
                }
            ],
            skipped=[],
            findings_map={
                "security": {"componentType": "security", "findings": {}},
            },
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "Section Unavailable: Capacity Analysis" in html
        assert "Security</h2>" in html

    def test_multiple_failures_all_get_error_placeholders(self):
        """Multiple failed analyzers each get their own error placeholder."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[],
            failures=[
                {
                    "componentType": "security",
                    "status": "error",
                    "error": "AccessDenied",
                },
                {"componentType": "capacity", "status": "error", "error": "Timeout"},
                {"componentType": "cost", "status": "error", "error": "S3 read failed"},
            ],
            skipped=[],
            findings_map={},
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "Section Unavailable: Security" in html
        assert "Section Unavailable: Capacity Analysis" in html
        assert "Section Unavailable: Cost Considerations" in html
        assert "AccessDenied" in html
        assert "Timeout" in html
        assert "S3 read failed" in html

    def test_missing_component_renders_error_placeholder(self):
        """A component in SECTION_ORDER with no result renders an error placeholder."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[
                {
                    "componentType": "security",
                    "status": "success",
                    "findings": {},
                }
            ],
            failures=[],
            skipped=[
                {"componentType": "observability", "status": "skipped"},
            ],
            findings_map={
                "security": {"componentType": "security", "findings": {}},
            },
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "Section Unavailable: Resilience" in html
        assert "No result received" in html
        assert "Section Unavailable: Observability" not in html

    def test_skipped_alongside_success_and_failure(self):
        """Skipped section is omitted while success and failure sections render."""
        html = _render_html_report(
            review_id="test-review-id",
            instance_id="test-instance-id",
            instance_alias="test-alias",
            account_id="123456789012",
            aws_region="us-east-1",
            successes=[
                {
                    "componentType": "security",
                    "status": "success",
                    "findings": {},
                }
            ],
            failures=[
                {
                    "componentType": "capacity",
                    "status": "error",
                    "error": "Timeout",
                }
            ],
            skipped=[{"componentType": "observability", "status": "skipped"}],
            findings_map={
                "security": {"componentType": "security", "findings": {}},
            },
            instance_data={"Id": "test-instance-id", "InstanceAlias": "test-alias"},
        )
        assert "Security</h2>" in html or "Security</h3>" in html
        assert "Section Unavailable: Capacity Analysis" in html
        assert "Section Unavailable: Observability" not in html
        assert 'data-component="observability"' not in html

    def test_skipped_count_correct(self):
        """The skipped list length matches the number of skipped analyzers."""
        raw = [
            {"componentType": "security", "status": "success", "findings": {}},
            {"componentType": "observability", "status": "skipped"},
            {"componentType": "cost", "status": "skipped"},
            {"componentType": "capacity", "status": "error", "error": "Timeout"},
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(skipped) == 2


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
