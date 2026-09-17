"""Regression tests for RC6 report charset declaration.

Covers the two-part fix that ensures browsers render the report body as UTF-8:

1. The S3 upload of the report body declares ``ContentType`` as
   ``"text/html; charset=utf-8"`` (not the bare ``"text/html"``).
2. The rendered HTML head declares ``<meta charset="utf-8">`` before
   ``<title>`` so file:// or otherwise-header-less loads still render as UTF-8.

Requirements: R2.5, R4.1.
"""

from unittest.mock import MagicMock, patch

import pytest

import report_generator
from fixtures import analyzer_payloads as payloads
from test_e2e_report_generation import FakeS3Client


def _find_html_put(puts):
    """Return the put_object call for the HTML report body, or None."""
    for call in puts:
        ct = call.get("ContentType", "")
        if ct.startswith("text/html"):
            return call
    return None


@pytest.fixture
def uploaded():
    """Run the end-to-end handler and return the FakeS3Client for inspection."""
    fake = FakeS3Client(payloads.documents_by_key())
    with (
        patch("report_generator.boto3.client", return_value=fake),
        patch(
            "report_generator._get_instance_data",
            return_value={"InstanceAlias": "charset-test"},
        ),
    ):
        report_generator.lambda_handler(payloads.full_event(), MagicMock())
    return fake


class TestS3UploadContentType:
    """R2.5: the S3 put_object call for the report body declares UTF-8."""

    def test_s3_upload_content_type_declares_utf8(self, uploaded):
        html_put = _find_html_put(uploaded.puts)
        assert html_put is not None, "no HTML put_object call was made"
        assert html_put["ContentType"] == "text/html; charset=utf-8", (
            f"expected ContentType 'text/html; charset=utf-8', "
            f"got {html_put['ContentType']!r}"
        )


class TestRenderedHtmlCharsetMeta:
    """R2.5: the rendered HTML head declares <meta charset="utf-8"> before <title>."""

    def test_rendered_html_has_meta_charset_before_title(self):
        html = report_generator._render_html_head(
            instance_alias="charset-test",
            timestamp_str="2026-01-01 00:00:00 UTC",
        )
        head_idx = html.find("<head>")
        meta_idx = html.find('<meta charset="utf-8">')
        title_idx = html.find("<title>")

        assert head_idx != -1, "rendered HTML missing <head>"
        assert title_idx != -1, "rendered HTML missing <title>"
        assert meta_idx != -1, 'rendered HTML missing <meta charset="utf-8">'
        assert head_idx < meta_idx < title_idx, (
            f"expected <meta charset='utf-8'> to appear between <head> and <title>; "
            f"got head@{head_idx}, meta@{meta_idx}, title@{title_idx}"
        )

    def test_end_to_end_uploaded_html_has_meta_charset_before_title(self, uploaded):
        html_put = _find_html_put(uploaded.puts)
        assert html_put is not None, "no HTML put_object call was made"
        body = html_put["Body"]
        html = body.decode("utf-8") if isinstance(body, bytes) else body

        head_idx = html.find("<head>")
        meta_idx = html.find('<meta charset="utf-8">')
        title_idx = html.find("<title>")

        assert head_idx != -1, "uploaded HTML missing <head>"
        assert title_idx != -1, "uploaded HTML missing <title>"
        assert meta_idx != -1, 'uploaded HTML missing <meta charset="utf-8">'
        assert head_idx < meta_idx < title_idx, (
            f"expected <meta charset='utf-8'> between <head> and <title> in the "
            f"uploaded HTML; got head@{head_idx}, meta@{meta_idx}, title@{title_idx}"
        )
