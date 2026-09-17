"""Tests for AI Analyzer findings rendering in report_generator.py.

Validates task 6.1: Report Generator reads and merges AI Analyzer pillar-keyed
structured findings into the correct HTML report sections.

Requirements tested: 10.1, 10.2, 10.3, 10.4, 10.5
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report_generator import (
    _render_ai_structured_finding,
    _render_ai_findings_block,
    _parse_analyzer_results,
    _assemble_cross_analyzer_data,
    _render_section,
)


class TestRenderAiStructuredFinding:
    """Test individual structured finding rendering (Req 10.2)."""

    def test_pass_finding_green_badge(self):
        """Pass status should render with green styling."""
        finding = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "Customer-managed KMS key configured",
            "recommendation": None,
            "data": {"has_cmk": True},
        }
        html = _render_ai_structured_finding(finding)
        assert "#155724" in html  # green text color
        assert "#d4edda" in html  # green background
        assert "Domain Encryption" in html  # formatted check_name
        assert "Customer-managed KMS key configured" in html
        assert "AI" in html  # AI badge label

    def test_fail_finding_red_badge(self):
        """Fail status should render with red styling."""
        finding = {
            "check_name": "guardrails",
            "status": "fail",
            "detail": "No guardrails configured",
            "recommendation": "Add guardrails for content filtering",
        }
        html = _render_ai_structured_finding(finding)
        assert "#721c24" in html  # red text color
        assert "#f8d7da" in html  # red background
        assert "Guardrails" in html
        assert "No guardrails configured" in html
        assert "Add guardrails for content filtering" in html
        assert "Recommendation:" in html

    def test_warn_finding_amber_badge(self):
        """Warn status should render with amber styling."""
        finding = {
            "check_name": "agent_logging",
            "status": "warn",
            "detail": "Logging not enabled for all agents",
        }
        html = _render_ai_structured_finding(finding)
        assert "#856404" in html  # amber text color
        assert "#fff3cd" in html  # amber background
        assert "Agent Logging" in html

    def test_info_finding_blue_badge(self):
        """Info status should render with blue styling."""
        finding = {
            "check_name": "hard_limits",
            "status": "info",
            "detail": "Service quotas within normal range",
        }
        html = _render_ai_structured_finding(finding)
        assert "#0c5460" in html  # blue text color
        assert "#d1ecf1" in html  # blue background

    def test_error_finding_grey_badge(self):
        """Error status should render with grey styling."""
        finding = {
            "check_name": "prompt_configuration",
            "status": "error",
            "detail": "Could not retrieve prompt configuration",
        }
        html = _render_ai_structured_finding(finding)
        assert "#383d41" in html  # grey text color
        assert "#e2e3e5" in html  # grey background

    def test_recommendation_block_rendered_when_present(self):
        """When recommendation is present, it should be rendered."""
        finding = {
            "check_name": "test_check",
            "status": "warn",
            "detail": "Something needs attention",
            "recommendation": "Fix this by doing X",
        }
        html = _render_ai_structured_finding(finding)
        assert "Recommendation:" in html
        assert "Fix this by doing X" in html

    def test_recommendation_block_omitted_when_none(self):
        """When recommendation is None, no recommendation block should appear."""
        finding = {
            "check_name": "test_check",
            "status": "pass",
            "detail": "All good",
            "recommendation": None,
        }
        html = _render_ai_structured_finding(finding)
        assert "Recommendation:" not in html

    def test_html_escaping(self):
        """Detail and check_name should be HTML-escaped."""
        finding = {
            "check_name": "test<script>alert</script>",
            "status": "info",
            "detail": '<b>bold</b> & "quotes"',
            "recommendation": "<script>hack</script>",
        }
        html = _render_ai_structured_finding(finding)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&lt;b&gt;" in html
        assert "&amp;" in html


class TestRenderAiFindingsBlock:
    """Test pillar-level findings block rendering (Req 10.1, 10.3, 10.4)."""

    def test_normal_findings_rendered(self):
        """Normal findings list should render all findings."""
        findings_list = [
            {"check_name": "guardrails", "status": "pass", "detail": "OK"},
            {"check_name": "encryption", "status": "warn", "detail": "Review needed"},
        ]
        html, checks = _render_ai_findings_block(findings_list, "Security")
        assert "Guardrails" in html
        assert "Encryption" in html
        assert len(checks) == 2
        assert checks[0]["area"] == "Security"
        assert checks[0]["check"] == "AI: Guardrails"
        assert checks[0]["status"] == "pass"

    def test_empty_findings_returns_empty(self):
        """Empty findings list should return empty HTML and checks."""
        html, checks = _render_ai_findings_block([], "Security")
        assert html == ""
        assert checks == []

    def test_partial_indicator_rendered(self):
        """When is_partial=True, amber indicator should be rendered (Req 10.3)."""
        findings_list = [
            {"check_name": "agent_inventory", "status": "pass", "detail": "OK"},
        ]
        html, checks = _render_ai_findings_block(
            findings_list, "Operational Excellence", is_partial=True
        )
        assert "Partial AI Analysis" in html
        assert "incomplete due to time constraints" in html
        assert "partial-indicator" in html
        # Findings should still be rendered
        assert "Agent Inventory" in html

    def test_error_placeholder_rendered(self):
        """When is_error=True, grey error placeholder should be rendered (Req 10.4)."""
        html, checks = _render_ai_findings_block(
            [], "Security", is_error=True, error_message="Lambda timeout"
        )
        assert "AI Analysis Unavailable" in html
        assert "Lambda timeout" in html
        assert "#e2e3e5" in html  # grey background
        assert checks == []

    def test_error_takes_priority_over_findings(self):
        """Error state should show placeholder even if findings exist."""
        findings_list = [
            {"check_name": "test", "status": "pass", "detail": "OK"},
        ]
        html, checks = _render_ai_findings_block(
            findings_list, "Security", is_error=True, error_message="Oops"
        )
        assert "AI Analysis Unavailable" in html
        assert "Test" not in html  # findings should not be rendered
        assert checks == []


class TestAssembleCrossAnalyzerDataAI:
    """Test that AI analyzer findings are distributed to pillar sections (Req 10.1)."""

    def test_ai_findings_distributed_to_pillars(self):
        """AI findings should be placed in _ai_structured_findings for each pillar."""
        findings_map = {
            "security": {
                "findings": {"identity_management": {"identity_type": "SAML"}}
            },
            "operational_excellence": {"findings": {"contact_flow_logging": {}}},
            "resilience": {"findings": {"multi_region": {}}},
            "ai": {
                "findings": {
                    "security": [
                        {"check_name": "guardrails", "status": "pass", "detail": "OK"},
                    ],
                    "operational_excellence": [
                        {
                            "check_name": "agent_inventory",
                            "status": "warn",
                            "detail": "Review",
                        },
                    ],
                    "resilience": [
                        {
                            "check_name": "kb_capacity",
                            "status": "info",
                            "detail": "Normal",
                        },
                    ],
                    "metadata": {
                        "assistant_id": "test-123",
                        "checks_completed": [
                            "guardrails",
                            "agent_inventory",
                            "kb_capacity",
                        ],
                        "timed_out": False,
                        "account_id": "123456789012",
                        "region": "us-east-1",
                    },
                }
            },
        }
        merged = _assemble_cross_analyzer_data(findings_map)

        # Security pillar should have AI findings
        sec_findings = merged["security"]["findings"]
        assert "_ai_structured_findings" in sec_findings
        assert len(sec_findings["_ai_structured_findings"]) == 1
        assert sec_findings["_ai_structured_findings"][0]["check_name"] == "guardrails"
        assert sec_findings["_ai_partial"] is False

        # Opex pillar should have AI findings
        opex_findings = merged["operational_excellence"]["findings"]
        assert "_ai_structured_findings" in opex_findings
        assert len(opex_findings["_ai_structured_findings"]) == 1
        assert (
            opex_findings["_ai_structured_findings"][0]["check_name"]
            == "agent_inventory"
        )

        # Resilience pillar should have AI findings
        res_findings = merged["resilience"]["findings"]
        assert "_ai_structured_findings" in res_findings
        assert len(res_findings["_ai_structured_findings"]) == 1
        assert res_findings["_ai_structured_findings"][0]["check_name"] == "kb_capacity"

    def test_ai_partial_flag_propagated(self):
        """When AI result has partial=True, _ai_partial should be True in pillar findings."""
        findings_map = {
            "security": {"findings": {}},
            "ai": {
                "partial": True,
                "findings": {
                    "security": [
                        {"check_name": "guardrails", "status": "pass", "detail": "OK"},
                    ],
                    "operational_excellence": [],
                    "resilience": [],
                    "metadata": {
                        "timed_out": True,
                        "assistant_id": None,
                        "checks_completed": [],
                        "account_id": "",
                        "region": "",
                    },
                },
            },
        }
        merged = _assemble_cross_analyzer_data(findings_map)
        sec_findings = merged["security"]["findings"]
        assert sec_findings["_ai_partial"] is True

    def test_ai_not_present_no_ai_findings_injected(self):
        """When AI analyzer not in findings_map, no _ai_structured_findings key injected."""
        findings_map = {
            "security": {"findings": {"identity_management": {}}},
        }
        merged = _assemble_cross_analyzer_data(findings_map)
        sec_findings = merged["security"]["findings"]
        assert "_ai_structured_findings" not in sec_findings

    def test_ai_skipped_no_findings_injected(self):
        """When AI analyzer is skipped (not in findings_map at all), nothing injected (Req 10.5)."""
        findings_map = {
            "security": {"findings": {}},
            "resilience": {"findings": {}},
            "operational_excellence": {"findings": {}},
        }
        merged = _assemble_cross_analyzer_data(findings_map)
        # No AI keys should be present
        assert "_ai_structured_findings" not in merged["security"].get(
            "findings", merged["security"]
        )
        assert "_ai_structured_findings" not in merged["resilience"].get(
            "findings", merged["resilience"]
        )


class TestParseAnalyzerResultsAI:
    """Test that AI analyzer results are parsed correctly."""

    def test_ai_success_goes_to_successes(self):
        """AI analyzer with status='success' should be in successes."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "ai",
                    "status": "success",
                    "findings": {
                        "security": [],
                        "operational_excellence": [],
                        "resilience": [],
                        "metadata": {},
                    },
                    "durationMs": 5000,
                    "s3ResultKey": "data/ai/year=2025/month=07/day=15/review-123.json",
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 1
        assert successes[0]["componentType"] == "ai"

    def test_ai_error_goes_to_failures(self):
        """AI analyzer with status='error' should be in failures."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "ai",
                    "status": "error",
                    "error": "Lambda timeout",
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(failures) == 1
        assert failures[0]["componentType"] == "ai"

    def test_ai_skipped_goes_to_skipped(self):
        """AI analyzer with status='skipped' should be in skipped."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "ai",
                    "status": "skipped",
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(skipped) == 1
        assert skipped[0]["componentType"] == "ai"

    def test_ai_partial_goes_to_successes_with_flag(self):
        """AI analyzer with status='partial' should be in successes with partial=True."""
        raw = [
            {
                "analyzerResult": {
                    "componentType": "ai",
                    "status": "partial",
                    "partial": True,
                    "findings": {
                        "security": [],
                        "operational_excellence": [],
                        "resilience": [],
                        "metadata": {"timed_out": True},
                    },
                }
            }
        ]
        successes, failures, skipped = _parse_analyzer_results(raw)
        assert len(successes) == 1
        assert successes[0]["partial"] is True


class TestSectionRenderersWithAIFindings:
    """Test that section renderers include AI findings in their output."""

    def test_security_section_includes_ai_findings(self):
        """Security section should render AI structured findings at the end."""
        findings = {
            "identity_management": {"identity_type": "SAML"},
            "_ai_structured_findings": [
                {
                    "check_name": "guardrails",
                    "status": "warn",
                    "detail": "Missing filters",
                },
            ],
            "_ai_partial": False,
            "_ai_error": False,
            "_ai_error_message": "",
        }
        html, checks = _render_section("Security", "security", findings)
        assert "Guardrails" in html
        assert "Missing filters" in html
        # Should have AI check in checks list
        ai_checks = [c for c in checks if "AI:" in c.get("check", "")]
        assert len(ai_checks) == 1

    def test_security_section_shows_ai_error(self):
        """Security section should show error placeholder when AI failed."""
        findings = {
            "identity_management": {"identity_type": "SAML"},
            "_ai_structured_findings": [],
            "_ai_partial": False,
            "_ai_error": True,
            "_ai_error_message": "AI Lambda timed out",
        }
        html, checks = _render_section("Security", "security", findings)
        assert "AI Analysis Unavailable" in html
        assert "AI Lambda timed out" in html

    def test_opex_section_includes_ai_findings(self):
        """Opex section should render AI structured findings."""
        findings = {
            "_ai_structured_findings": [
                {
                    "check_name": "agent_inventory",
                    "status": "pass",
                    "detail": "3 agents found",
                },
            ],
            "_ai_partial": False,
            "_ai_error": False,
            "_ai_error_message": "",
        }
        html, checks = _render_section(
            "Operational Excellence", "operational_excellence", findings
        )
        assert "Agent Inventory" in html
        assert "3 agents found" in html

    def test_resilience_section_includes_ai_findings(self):
        """Resilience section should render AI structured findings."""
        findings = {
            "multi_region": {},
            "_ai_structured_findings": [
                {
                    "check_name": "knowledge_base_capacity",
                    "status": "info",
                    "detail": "KB within limits",
                },
            ],
            "_ai_partial": False,
            "_ai_error": False,
            "_ai_error_message": "",
        }
        html, checks = _render_section("Resilience", "resilience", findings)
        assert "Knowledge Base Capacity" in html
        assert "KB within limits" in html

    def test_section_without_ai_findings_still_works(self):
        """Sections without AI findings should render normally (Req 10.5 — skipped case)."""
        findings = {
            "identity_management": {"identity_type": "SAML"},
        }
        html, checks = _render_section("Security", "security", findings)
        assert "SAML" in html
        # No AI content should be present
        assert "AI Analysis Unavailable" not in html
