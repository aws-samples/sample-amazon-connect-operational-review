"""End-to-end report generation tests (TEST_BACKLOG TB-7).

Invokes ``report_generator.lambda_handler`` against a fake S3 client that
serves a full set of analyzer documents, captures the rendered HTML from the
upload call, and asserts structural invariants on it.

Why this exists: every other test in the suite covers either a single section
renderer or a single analyzer handler. Nothing exercised the seam between
them, so a mismatch between what an analyzer emits and what the renderer
expects produced a silently degraded report. QB-40 (Kinesis rows missing),
QB-50 (Executive Summary anchors resolving nowhere) and QB-52 (pillar
misrouting) are all instances of that class.

The anchor-integrity test below is the general form of the QB-50 bug: it
fails for ANY dangling in-page link, not just the AI ones.
"""

import json
import re
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

import report_generator
from fixtures import analyzer_payloads as payloads


class FakeS3Client:
    """Serves analyzer documents on get_object, records put_object calls."""

    def __init__(self, documents_by_key):
        self._documents = documents_by_key
        self.puts = []

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg casing
        if Key not in self._documents:
            raise KeyError(f"FakeS3Client has no document for key: {Key}")
        body = json.dumps(self._documents[Key]).encode("utf-8")
        return {"Body": BytesIO(body)}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {}

    def uploaded_html(self):
        """The body of the single text/html upload, or None."""
        for call in self.puts:
            ct = call.get("ContentType") or ""
            if ct.startswith("text/html"):
                body = call.get("Body")
                return body.decode("utf-8") if isinstance(body, bytes) else body
        return None


@pytest.fixture
def fake_s3():
    return FakeS3Client(payloads.documents_by_key())


@pytest.fixture
def rendered_report(fake_s3):
    """Run the handler end-to-end and return (result, html)."""
    with (
        patch("report_generator.boto3.client", return_value=fake_s3),
        patch(
            "report_generator._get_instance_data",
            return_value={"InstanceAlias": "e2e-instance"},
        ),
    ):
        result = report_generator.lambda_handler(payloads.full_event(), MagicMock())
    return result, fake_s3.uploaded_html()


@pytest.fixture
def rendered_report_empty_data():
    """Run the handler end-to-end against analyzer payloads that are PRESENT
    but carry EMPTY data lists.

    TB-19 (rc.9): data-conditional inner branches are the shape that produces
    the QB-58 / QB-75 class of anchor-orphan defects — the Executive Summary
    registers the check row unconditionally on every analyzer branch, but
    the body-section anchor is guarded on the data list being non-empty.
    Runs TestAnchorIntegrity against this second fixture so any future
    data-conditional section with the same shape surfaces the orphan in
    unit tests instead of a live-test.
    """
    fake = FakeS3Client(payloads.documents_by_key(empty_data=True))
    with (
        patch("report_generator.boto3.client", return_value=fake),
        patch(
            "report_generator._get_instance_data",
            return_value={"InstanceAlias": "e2e-instance"},
        ),
    ):
        result = report_generator.lambda_handler(payloads.full_event(), MagicMock())
    return result, fake.uploaded_html()


class TestEndToEndReportGeneration:
    """The handler completes and produces an HTML artifact."""

    def test_handler_reports_success(self, rendered_report):
        result, _ = rendered_report
        assert result["status"] != "failed", f"handler failed: {result.get('error')}"

    def test_html_report_is_uploaded(self, rendered_report):
        _, html = rendered_report
        assert html, "no text/html object was uploaded"
        assert "<html" in html.lower()

    def test_every_successful_analyzer_is_counted(self, rendered_report):
        result, _ = rendered_report
        assert result["analyzersSucceeded"] == len(payloads.COMPONENT_TYPES)
        assert result["analyzersFailed"] == 0


@pytest.fixture
def tabular_report():
    """Run the handler with AI pillar-keyed findings carrying per-row payloads."""
    fake = FakeS3Client(payloads.documents_by_key(tabular=True))
    with (
        patch("report_generator.boto3.client", return_value=fake),
        patch(
            "report_generator._get_instance_data",
            return_value={"InstanceAlias": "e2e-instance"},
        ),
    ):
        result = report_generator.lambda_handler(payloads.full_event(), MagicMock())
    return result, fake.uploaded_html()


class TestAiFindingsReachTheReport:
    """The AI analyzer's pillar-keyed findings survive cross-analyzer assembly.

    Locks in fixture fidelity. Without these, the tabular assertions below
    could pass vacuously against HTML that never contained AI content at all.
    """

    def test_ai_structured_findings_render(self, tabular_report):
        _, html = tabular_report
        assert html
        assert "No guardrails configured" in html, (
            "AI structured findings did not reach the report: the pillar-keyed "
            "findings shape or the cross-analyzer merge has changed."
        )

    def test_tabular_payloads_add_content_versus_plain(
        self, tabular_report, rendered_report
    ):
        _, tabular_html = tabular_report
        _, plain_html = rendered_report
        assert len(tabular_html) > len(plain_html), (
            "Adding AI findings produced no additional report content."
        )


class TestTabularRendering:
    """A non-empty per-row payload must render a <table> in its section.

    TB-7 named this invariant. It is currently the QB-51 defect: findings
    carrying non-empty `_raw_agents` / `prompts` / `guardrails` in their `data`
    render no table and no per-row content.
    """

    def test_ai_row_payloads_render_rows(self, tabular_report):
        _, html = tabular_report
        assert html
        assert "agent-alpha" in html, "_raw_agents rows are not rendered"
        assert "prompt-one" in html, "prompts rows are not rendered"
        assert "guardrail-one" in html, "guardrails rows are not rendered"

    def test_ai_findings_emit_anchors(self, tabular_report):
        _, html = tabular_report
        assert html
        assert re.search(r'id="ai-[^"]+"', html), "no AI anchor ids emitted"


class TestAnchorIntegrity:
    """Every in-page link resolves to an element that exists.

    This is the general form of QB-50: the Executive Summary linked to AI
    findings whose anchors were never emitted. Any dangling fragment link
    fails here, so future renderer/schema drift surfaces immediately.
    """

    @staticmethod
    def _anchors(html):
        hrefs = set(re.findall(r'href="#([^"]+)"', html))
        ids = set(re.findall(r'id="([^"]+)"', html))
        return hrefs, ids

    def test_all_fragment_hrefs_have_matching_ids(self, rendered_report):
        _, html = rendered_report
        assert html, "no HTML to inspect"
        hrefs, ids = self._anchors(html)
        dangling = sorted(h for h in hrefs if h and h not in ids)
        assert not dangling, (
            f"{len(dangling)} in-page link(s) point at ids that are never "
            f"emitted: {dangling[:10]}"
        )

    def test_all_fragment_hrefs_have_matching_ids_empty_data_branches(
        self, rendered_report_empty_data
    ):
        """TB-19 (rc.9): anchor integrity on the empty-data branches.

        The populated-fixture test above cannot fail on the QB-58 / QB-75
        defect shape (Executive Summary row registered but body-section
        anchor guarded on data being non-empty) because every data-conditional
        section has data to render. This variant runs the same assertion
        against analyzer payloads whose data lists are empty — the branch
        least likely to be exercised in live testing against a populated
        Connect instance, and the one where QB-75 hid until rc.8.

        Extend `EMPTY_DATA_FINDINGS_BY_PILLAR` in
        `tests/fixtures/analyzer_payloads.py` when new data-conditional
        sections appear so this test keeps its coverage.
        """
        _, html = rendered_report_empty_data
        assert html, "no HTML to inspect"
        hrefs, ids = self._anchors(html)
        dangling = sorted(h for h in hrefs if h and h not in ids)
        assert not dangling, (
            f"QB-75-class regression: {len(dangling)} in-page link(s) point "
            f"at ids that are never emitted on the empty-data branches: "
            f"{dangling[:10]}. This is the shape that produces dangling "
            f"Executive Summary links on Connect instances with zero data."
        )
