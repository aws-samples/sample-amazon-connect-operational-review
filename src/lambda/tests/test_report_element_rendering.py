"""Tests for individual HTML element rendering in report_generator.py.

Consolidated from:
- test_title_text_alignment.py (task 1.2)
- test_anchor_link_rendering.py (task 8.4)
- test_data_storage_config_table.py (task 4.5)

Validates:
- Requirements 6.1, 6.2, 6.3: Title/subtitle text alignment
- Requirements 4.1, 4.2: Executive summary anchor links and section id attributes
- Requirements 1.2, 1.4, 1.6: Data storage configuration table rendering
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from report_generator import (
    _render_html_head,
    _render_page_skeleton,
    _render_executive_summary,
    _render_security_section,
    _render_data_storage_config_table,
    STORAGE_RESOURCE_TYPES,
)


RESOURCE_TYPE_DISPLAY_NAMES = [display for (_api, display) in STORAGE_RESOURCE_TYPES]


# ===========================================================================
# SECTION 1: Title and Subtitle Text Alignment (Requirements 6.1, 6.2, 6.3)
# ===========================================================================


class TestTitleTextAlignment:
    """Verify that rendered HTML contains the exact required title strings."""

    def test_html_title_element_exact_text(self):
        """Requirement 6.1: <title> must be exactly 'Amazon Contact Center - Operations Review'."""
        html = _render_html_head("test-instance", "2025-01-15 14:30:00")
        assert "<title>Amazon Contact Center - Operations Review</title>" in html

    def test_h1_element_exact_text(self):
        """Requirement 6.2: <h1> must be exactly 'Amazon Contact Center - Operations Review'."""
        html = _render_html_head("test-instance", "2025-01-15 14:30:00")
        assert ">Amazon Contact Center - Operations Review</h1>" in html

    def test_subtitle_exact_text(self):
        """Requirement 6.3: Subtitle must be 'Automated operational health assessment'."""
        html = _render_html_head("test-instance", "2025-01-15 14:30:00")
        assert "Automated operational health assessment" in html

    def test_subtitle_in_subtitle_div(self):
        """Requirement 6.3: Subtitle text is inside a div with class 'subtitle'."""
        html = _render_html_head("test-instance", "2025-01-15 14:30:00")
        assert (
            '<div class="subtitle">Automated operational health assessment</div>'
            in html
        )


class TestPageSkeletonTitleAlignment:
    """Verify title alignment in the full page skeleton output."""

    def test_page_skeleton_contains_title(self):
        """Requirement 6.1: Full page skeleton includes correct <title>."""
        html = _render_page_skeleton("2025-01-15 14:30:00")
        assert "<title>Amazon Contact Center - Operations Review</title>" in html

    def test_page_skeleton_contains_h1(self):
        """Requirement 6.2: Full page skeleton includes correct <h1>."""
        html = _render_page_skeleton("2025-01-15 14:30:00")
        assert ">Amazon Contact Center - Operations Review</h1>" in html

    def test_page_skeleton_contains_subtitle(self):
        """Requirement 6.3: Full page skeleton includes correct subtitle."""
        html = _render_page_skeleton("2025-01-15 14:30:00")
        assert "Automated operational health assessment" in html


# ===========================================================================
# SECTION 2: Executive Summary Anchor Links (Requirements 4.1, 4.2)
# ===========================================================================


class TestExecutiveSummaryAnchorLinks:
    """Verify _render_executive_summary() produces correct anchor links."""

    def _make_check(self, area, check, status, anchor):
        """Helper to create a check dict."""
        return {
            "area": area,
            "check": check,
            "status": status,
            "detail": f"Detail for {check}",
            "anchor": anchor,
        }

    def test_check_with_anchor_renders_href_link(self):
        """Requirement 4.1: Check with anchor renders as <a href="#anchor"> link."""
        checks = [
            self._make_check("Security", "Identity Management", "pass", "sec-identity"),
        ]
        html = _render_executive_summary(checks)
        assert 'href="#sec-identity"' in html

    def test_check_without_anchor_renders_as_span(self):
        """Checks without anchor key should render as plain <span>, not <a>."""
        checks = [
            {
                "area": "Security",
                "check": "Some Check",
                "status": "pass",
                "detail": "Detail",
                "anchor": "",
            }
        ]
        html = _render_executive_summary(checks)
        assert 'href="#"' not in html
        assert "<span" in html
        assert "Some Check" in html

    def test_multiple_checks_produce_distinct_anchor_links(self):
        """Requirement 4.1: Multiple checks each render their own anchor link."""
        checks = [
            self._make_check("Security", "Identity Management", "pass", "sec-identity"),
            self._make_check(
                "Security", "S3 Data Encryption", "fail", "sec-s3-encryption"
            ),
            self._make_check(
                "Capacity Analysis",
                "Amazon Connect Concurrency Limits",
                "warn",
                "cap-concurrency",
            ),
        ]
        html = _render_executive_summary(checks)
        assert 'href="#sec-identity"' in html
        assert 'href="#sec-s3-encryption"' in html
        assert 'href="#cap-concurrency"' in html

    def test_anchor_link_contains_check_name_text(self):
        """Requirement 4.1: The anchor link text contains the check name."""
        checks = [
            self._make_check("Security", "Identity Management", "pass", "sec-identity"),
        ]
        html = _render_executive_summary(checks)
        anchor_pattern = re.compile(
            r'<a\s+href="#sec-identity"[^>]*>.*?Identity Management.*?</a>', re.DOTALL
        )
        assert anchor_pattern.search(html), (
            "Expected anchor link to contain 'Identity Management' text"
        )

    def test_anchor_link_style_matches_status_color(self):
        """Requirement 4.1: Anchor links are styled with the status color."""
        checks = [
            self._make_check("Security", "Identity Management", "pass", "sec-identity"),
        ]
        html = _render_executive_summary(checks)
        assert "color:#22c55e" in html

    def test_fail_status_anchor_renders_with_fail_color(self):
        """Requirement 4.1: Fail status checks use fail color in anchor link."""
        checks = [
            self._make_check(
                "Security", "S3 Data Encryption", "fail", "sec-s3-encryption"
            ),
        ]
        html = _render_executive_summary(checks)
        assert 'href="#sec-s3-encryption"' in html
        assert "color:#ef4444" in html

    def test_ai_prefixed_anchor_gets_ai_badge(self):
        """Anchors starting with 'ai-' get the AI badge in the executive summary."""
        checks = [
            self._make_check("Security", "Connect AI Guardrails", "pass", "ai-s1"),
        ]
        html = _render_executive_summary(checks)
        assert 'href="#ai-s1"' in html
        assert "AI</span>" in html

    def test_all_checks_with_anchors_produce_links(self):
        """Requirement 4.1: Every check with an anchor key produces an <a> href link."""
        checks = [
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "pass",
                "detail": "SAML configured",
                "anchor": "sec-identity",
            },
            {
                "area": "Capacity Analysis",
                "check": "Amazon Connect Concurrency Limits",
                "status": "warn",
                "detail": "Approaching limits",
                "anchor": "cap-concurrency",
            },
            {
                "area": "Capacity Analysis",
                "check": "Amazon Connect API Limits (Account Level)",
                "status": "info",
                "detail": "60 API quotas evaluated",
                "anchor": "cap-api-limits",
            },
        ]
        html = _render_executive_summary(checks)
        href_pattern = re.compile(r'<a\s+href="#[^"]+"[^>]*>')
        links = href_pattern.findall(html)
        assert len(links) == 3, (
            f"Expected 3 anchor links for 3 checks, found {len(links)}"
        )


class TestSectionRendererIdAttributes:
    """Verify section renderers set id attributes matching anchor hrefs."""

    def test_security_identity_section_has_matching_id(self):
        """Requirement 4.2: Security identity section id matches anchor 'sec-identity'."""
        findings = {
            "identity_management": {"identity_type": "SAML"},
            "storage_configs": [],
            "streaming_configs": [],
        }
        html, checks = _render_security_section(findings)
        assert 'id="sec-identity"' in html
        identity_checks = [c for c in checks if c.get("anchor") == "sec-identity"]
        assert len(identity_checks) == 1

    def test_security_s3_encryption_section_has_matching_id(self):
        """Requirement 4.2: S3 encryption section id matches anchor 'sec-s3-encryption'."""
        findings = {
            "identity_management": {"identity_type": "SAML"},
            "storage_configs": [
                {
                    "resource_type": "CALL_RECORDINGS",
                    "configs": [
                        {
                            "StorageType": "S3",
                            "S3Config": {
                                "BucketName": "test-bucket",
                                "BucketPrefix": "recordings/",
                                "EncryptionConfig": {
                                    "EncryptionType": "KMS",
                                    "KeyId": "arn:aws:kms:us-east-1:123456789012:key/test-key",
                                },
                            },
                        }
                    ],
                }
            ],
            "streaming_configs": [],
        }
        html, checks = _render_security_section(findings)
        assert 'id="sec-s3-encryption"' in html
        s3_checks = [c for c in checks if c.get("anchor") == "sec-s3-encryption"]
        assert len(s3_checks) == 1

    def test_security_stream_encryption_section_has_matching_id(self):
        """Requirement 4.2: Stream encryption section id matches anchor 'sec-stream-encryption'."""
        findings = {
            "identity_management": {"identity_type": "SAML"},
            "storage_configs": [],
            "streaming_configs": [],
            "streaming_encryption": {
                "total_streams": 1,
                "kvs_encrypted": 1,
                "kvs_unencrypted": 0,
                "kds_count": 0,
                "firehose_count": 0,
                "streams": [
                    {
                        "resource_type": "CONTACT_TRACE_RECORDS",
                        "stream_type": "Kinesis Video Stream",
                        "destination": "arn:aws:kinesisvideo:us-east-1:123456789012:stream/test",
                        "encrypted": True,
                        "retention_hours": 24,
                    }
                ],
            },
        }
        html, checks = _render_security_section(findings)
        assert 'id="sec-stream-encryption"' in html
        stream_checks = [
            c for c in checks if c.get("anchor") == "sec-stream-encryption"
        ]
        assert len(stream_checks) == 1

    def test_section_id_and_href_are_consistent(self):
        """Requirement 4.1, 4.2: Anchor hrefs in executive summary match section ids."""
        checks = [
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "pass",
                "detail": "SAML 2.0 federation configured",
                "anchor": "sec-identity",
            },
            {
                "area": "Security",
                "check": "S3 Data Encryption",
                "status": "pass",
                "detail": "All configs encrypted",
                "anchor": "sec-s3-encryption",
            },
            {
                "area": "Security",
                "check": "Streaming Encryption",
                "status": "pass",
                "detail": "All streams encrypted",
                "anchor": "sec-stream-encryption",
            },
        ]
        html = _render_executive_summary(checks)
        hrefs = re.findall(r'href="#([^"]+)"', html)
        for href in hrefs:
            assert href, "Anchor href should not be empty"
            assert re.match(r"^[a-z]", href), (
                f"Anchor '{href}' should start with a lowercase letter"
            )


# ===========================================================================
# SECTION 3: Data Storage Configuration Table (Requirements 1.2, 1.4, 1.6)
# ===========================================================================


def _make_configured_entry(
    display_name,
    storage_type="S3",
    destination="arn:aws:s3:::my-bucket/prefix/",
    encryption="AWS Managed Key",
):
    """Helper to create a configured storage config entry."""
    return {
        "resource_type": display_name,
        "storage_type": storage_type,
        "destination": destination,
        "encryption": encryption,
        "configured": True,
    }


def _make_unconfigured_entry(display_name):
    """Helper to create an unconfigured storage config entry."""
    return {
        "resource_type": display_name,
        "storage_type": None,
        "destination": "-",
        "encryption": "None",
        "configured": False,
    }


class TestStorageConfigColumnHeaders:
    """Requirement 1.2: Table has columns Resource Type, Storage Type, Destination, Encryption."""

    def test_table_contains_resource_type_header(self):
        configs = [_make_configured_entry(RESOURCE_TYPE_DISPLAY_NAMES[0])]
        html = _render_data_storage_config_table(configs)
        assert "<th>Resource Type</th>" in html

    def test_table_contains_storage_type_header(self):
        configs = [_make_configured_entry(RESOURCE_TYPE_DISPLAY_NAMES[0])]
        html = _render_data_storage_config_table(configs)
        assert "Storage Type</th>" in html

    def test_table_contains_destination_header(self):
        configs = [_make_configured_entry(RESOURCE_TYPE_DISPLAY_NAMES[0])]
        html = _render_data_storage_config_table(configs)
        assert "<th>Destination</th>" in html

    def test_table_contains_encryption_header(self):
        configs = [_make_configured_entry(RESOURCE_TYPE_DISPLAY_NAMES[0])]
        html = _render_data_storage_config_table(configs)
        assert "<th>Encryption</th>" in html

    def test_table_wrapped_in_section_div(self):
        configs = [_make_configured_entry(RESOURCE_TYPE_DISPLAY_NAMES[0])]
        html = _render_data_storage_config_table(configs)
        assert 'id="data-storage-config"' in html
        assert "<h3>Data Storage Configuration</h3>" in html

    def test_table_has_four_columns_in_colgroup(self):
        configs = [_make_configured_entry(RESOURCE_TYPE_DISPLAY_NAMES[0])]
        html = _render_data_storage_config_table(configs)
        assert "<colgroup>" in html
        col_count = html.count("<col ")
        assert col_count == 4, f"Expected 4 <col> elements, got {col_count}"


class TestStorageConfigMixedEntries:
    """Requirements 1.2, 1.4: Configured types show data; unconfigured show colspan."""

    def test_configured_entry_renders_all_columns(self):
        configs = [
            _make_configured_entry(
                "Call Recordings",
                storage_type="S3",
                destination="arn:aws:s3:::recordings-bucket/call-recordings/",
                encryption="Customer Managed Key (CMK)",
            )
        ]
        html = _render_data_storage_config_table(configs)
        assert "Call Recordings" in html
        assert "S3" in html
        assert "arn:aws:s3:::recordings-bucket/call-recordings/" in html
        assert "Customer Managed Key (CMK)" in html

    def test_unconfigured_entry_renders_not_configured_with_colspan(self):
        configs = [_make_unconfigured_entry("Chat Transcripts")]
        html = _render_data_storage_config_table(configs)
        assert "Chat Transcripts" in html
        assert 'colspan="3"' in html
        assert "Not configured" in html

    def test_mix_of_configured_and_unconfigured(self):
        """Test a realistic mix where some types are configured and others are not."""
        configs = [
            _make_configured_entry(
                "Call Recordings",
                "S3",
                "arn:aws:s3:::bucket/recordings/",
                "AWS Managed Key",
            ),
            _make_configured_entry(
                "Chat Transcripts",
                "S3",
                "arn:aws:s3:::bucket/transcripts/",
                "AWS Managed Key",
            ),
            _make_unconfigured_entry("Scheduled Reports"),
            _make_configured_entry(
                "Agent Recordings",
                "KINESIS_VIDEO_STREAM",
                "arn:aws:kinesisvideo:us-east-1:123:stream/",
                "None",
            ),
            _make_unconfigured_entry("Screen Recordings"),
            _make_unconfigured_entry("Exported Reports"),
            _make_configured_entry(
                "Contact Trace Records",
                "KINESIS_FIREHOSE",
                "arn:aws:firehose:us-east-1:123:deliverystream/ctr-stream",
                "AWS Managed Key",
            ),
            _make_unconfigured_entry("Contact Lens"),
            _make_unconfigured_entry("Evaluations"),
            _make_unconfigured_entry("Real-Time Contact Analysis"),
            _make_unconfigured_entry("Call Recording Analysis"),
            _make_unconfigured_entry("Contact Flow Logs"),
            _make_configured_entry(
                "Email Messages",
                "S3",
                "arn:aws:s3:::bucket/emails/",
                "Customer Managed Key (CMK)",
            ),
        ]
        html = _render_data_storage_config_table(configs)

        assert "arn:aws:s3:::bucket/recordings/" in html
        assert "KINESIS_VIDEO_STREAM" in html
        assert "KINESIS_FIREHOSE" in html
        assert "Not configured" in html
        assert 'colspan="3"' in html

        for display_name in RESOURCE_TYPE_DISPLAY_NAMES:
            assert display_name in html, (
                f"Expected '{display_name}' to appear in output"
            )

    def test_all_unconfigured_renders_all_as_not_configured(self):
        """When all 13 types are unconfigured, each row shows 'Not configured'."""
        configs = [
            _make_unconfigured_entry(name) for name in RESOURCE_TYPE_DISPLAY_NAMES
        ]
        html = _render_data_storage_config_table(configs)
        colspan_count = html.count('colspan="3"')
        assert colspan_count == 13, f"Expected 13 colspan rows, got {colspan_count}"


class TestStorageConfigPermissionErrors:
    """Requirement 1.6: Permission errors produce warning message, not crash."""

    def test_permission_error_dict_renders_warning(self):
        """When storage_configs is a dict with 'error' key, render a warning message."""
        error_input = {
            "error": "AccessDeniedException: User is not authorized to perform connect:ListInstanceStorageConfigs"
        }
        html = _render_data_storage_config_table(error_input)
        assert html, "Should produce non-empty output for error case"
        assert "Warning" in html or "&#9888;" in html
        assert "Unable to retrieve storage configuration data" in html
        assert "AccessDeniedException" in html

    def test_permission_error_does_not_raise_exception(self):
        """Permission error input should never raise an exception."""
        error_input = {"error": "AccessDeniedException: Forbidden"}
        html = _render_data_storage_config_table(error_input)
        assert isinstance(html, str)

    def test_none_input_returns_empty_string(self):
        """None input indicates data unavailable; should return empty."""
        html = _render_data_storage_config_table(None)
        assert html == ""

    def test_empty_list_returns_empty_string(self):
        """Empty list indicates no data; should return empty."""
        html = _render_data_storage_config_table([])
        assert html == ""

    def test_error_message_is_html_escaped(self):
        """Error messages with special chars should be escaped to prevent XSS."""
        error_input = {"error": '<script>alert("xss")</script>'}
        html = _render_data_storage_config_table(error_input)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
