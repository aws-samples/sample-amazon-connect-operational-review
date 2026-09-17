"""Tests for instance alias resolution and S3 filename generation.

Consolidated from:
- test_alias_resolution_fallback.py (task 2.4) — unit tests with mocked API
- test_property_instance_alias_filename.py (Property 3) — PBT for filename format

Validates:
- Requirements 2.1: Alias used in S3 key when describe_instance() succeeds
- Requirements 2.3: Instance ID used as fallback when describe_instance() fails
"""

import logging
import re
import sys
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from report_generator import _get_instance_data


INSTANCE_ID = "abc12345-def6-7890-ghij-klmnopqrstuv"
INSTANCE_ARN = f"arn:aws:connect:us-east-1:123456789012:instance/{INSTANCE_ID}"
AWS_REGION = "us-east-1"


# ===========================================================================
# SECTION 1: Alias Resolution — Success Path (Requirements 2.1)
# ===========================================================================


class TestAliasResolutionSuccess:
    """When describe_instance() succeeds, the alias should be returned."""

    @patch("report_generator.boto3.client")
    def test_valid_alias_used_in_result(self, mock_boto_client):
        """When API succeeds and returns a valid alias, the alias is propagated."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.return_value = {
            "Instance": {
                "Id": INSTANCE_ID,
                "Arn": INSTANCE_ARN,
                "InstanceAlias": "my-production-instance",
                "InstanceStatus": "ACTIVE",
            }
        }

        result = _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        assert result["InstanceAlias"] == "my-production-instance"
        assert result["_alias_fallback"] is False

    @patch("report_generator.boto3.client")
    def test_alias_used_in_s3_key_pattern(self, mock_boto_client):
        """The resolved alias should appear in the S3 key pattern."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.return_value = {
            "Instance": {
                "Id": INSTANCE_ID,
                "Arn": INSTANCE_ARN,
                "InstanceAlias": "customer-connect",
                "InstanceStatus": "ACTIVE",
            }
        }

        result = _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)
        instance_alias = result.get("InstanceAlias", INSTANCE_ID)

        report_key = (
            f"connect-review_{instance_alias}_{AWS_REGION}_01012026_120000.html"
        )

        assert "customer-connect" in report_key
        assert INSTANCE_ID not in report_key
        assert (
            report_key
            == "connect-review_customer-connect_us-east-1_01012026_120000.html"
        )


# ===========================================================================
# SECTION 2: Alias Resolution — Fallback Path (Requirements 2.3)
# ===========================================================================


class TestAliasResolutionFallback:
    """When describe_instance() fails, the instance ID should be used."""

    @patch("report_generator.boto3.client")
    def test_instance_id_used_when_api_fails(self, mock_boto_client):
        """When the API raises an exception, instance ID is used as the alias."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.side_effect = Exception("AccessDeniedException")

        result = _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        assert result["InstanceAlias"] == INSTANCE_ID
        assert result["_alias_fallback"] is True

    @patch("report_generator.boto3.client")
    def test_instance_id_in_s3_key_when_api_fails(self, mock_boto_client):
        """When the API fails, the S3 key should contain the instance ID."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.side_effect = Exception("ServiceError")

        result = _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)
        instance_alias = result.get("InstanceAlias", INSTANCE_ID)

        report_key = (
            f"connect-review_{instance_alias}_{AWS_REGION}_01012026_120000.html"
        )

        assert INSTANCE_ID in report_key
        assert (
            report_key == f"connect-review_{INSTANCE_ID}_us-east-1_01012026_120000.html"
        )

    @patch("report_generator.boto3.client")
    def test_instance_id_used_when_alias_is_empty(self, mock_boto_client):
        """When the API returns an empty alias, instance ID is used."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.return_value = {
            "Instance": {
                "Id": INSTANCE_ID,
                "Arn": INSTANCE_ARN,
                "InstanceAlias": "",
                "InstanceStatus": "ACTIVE",
            }
        }

        result = _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        assert result["InstanceAlias"] == INSTANCE_ID
        assert result["_alias_fallback"] is True

    @patch("report_generator.boto3.client")
    def test_instance_id_used_when_alias_is_none(self, mock_boto_client):
        """When the API returns None for alias, instance ID is used."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.return_value = {
            "Instance": {
                "Id": INSTANCE_ID,
                "Arn": INSTANCE_ARN,
                "InstanceAlias": None,
                "InstanceStatus": "ACTIVE",
            }
        }

        result = _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        assert result["InstanceAlias"] == INSTANCE_ID
        assert result["_alias_fallback"] is True


# ===========================================================================
# SECTION 3: Warning Logging on Fallback
# ===========================================================================


class TestAliasResolutionWarningLogging:
    """WARNING-level log messages when fallback occurs."""

    @patch("report_generator.boto3.client")
    def test_warning_logged_on_api_failure(self, mock_boto_client, caplog):
        """A WARNING is logged when describe_instance() raises an exception."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.side_effect = Exception("AccessDeniedException")

        with caplog.at_level(logging.WARNING):
            _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "failed" in msg.lower() or "falling back" in msg.lower()
            for msg in warning_messages
        ), f"Expected WARNING about fallback, got: {warning_messages}"

    @patch("report_generator.boto3.client")
    def test_warning_logged_on_empty_alias(self, mock_boto_client, caplog):
        """A WARNING is logged when describe_instance() returns empty alias."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.return_value = {
            "Instance": {
                "Id": INSTANCE_ID,
                "Arn": INSTANCE_ARN,
                "InstanceAlias": "",
                "InstanceStatus": "ACTIVE",
            }
        }

        with caplog.at_level(logging.WARNING):
            _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "falling back" in msg.lower() or "empty" in msg.lower()
            for msg in warning_messages
        ), f"Expected WARNING about empty alias fallback, got: {warning_messages}"

    @patch("report_generator.boto3.client")
    def test_no_warning_on_successful_alias(self, mock_boto_client, caplog):
        """No WARNING is logged when alias resolves successfully."""
        mock_connect = MagicMock()
        mock_boto_client.return_value = mock_connect
        mock_connect.describe_instance.return_value = {
            "Instance": {
                "Id": INSTANCE_ID,
                "Arn": INSTANCE_ARN,
                "InstanceAlias": "good-alias",
                "InstanceStatus": "ACTIVE",
            }
        }

        with caplog.at_level(logging.WARNING):
            _get_instance_data(INSTANCE_ARN, AWS_REGION, INSTANCE_ID)

        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert len(warning_messages) == 0, (
            f"Unexpected WARNING on successful alias resolution: {warning_messages}"
        )


# ===========================================================================
# SECTION 4: Property-Based — Filename Round-Trip (Requirements 2.1)
# ===========================================================================

# Connect aliases: lowercase alphanumeric with hyphens, 1-64 chars
alias_strategy = st.from_regex(r"[a-z][a-z0-9\-]{0,62}[a-z0-9]", fullmatch=True).filter(
    lambda s: "--" not in s
)

region_strategy = st.sampled_from(
    [
        "us-east-1",
        "us-west-2",
        "eu-west-1",
        "eu-central-1",
        "ap-southeast-1",
        "ap-northeast-1",
        "ap-south-1",
        "ca-central-1",
        "af-south-1",
        "me-south-1",
    ]
)

datetime_strategy = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
)

# Expected filename pattern
FILENAME_PATTERN = re.compile(
    r"^connect-review_"
    r"([a-z][a-z0-9\-]*[a-z0-9])"
    r"_"
    r"([a-z]{2}(?:-[a-z]+-\d+)?)"
    r"_"
    r"(\d{8}_\d{6})"
    r"\.html$"
)


def _build_report_key(instance_alias: str, aws_region: str, now_utc: datetime) -> str:
    """Replicate the filename construction logic from report_generator.py."""
    date_time = now_utc.strftime("%m%d%Y_%H%M%S")
    return f"connect-review_{instance_alias}_{aws_region}_{date_time}.html"


@settings(max_examples=200)
@given(alias=alias_strategy, region=region_strategy, dt=datetime_strategy)
def test_instance_alias_filename_roundtrip(alias, region, dt):
    """Property 3: Instance alias filename round-trip.

    For any valid alias string, the generated S3 report key SHALL match
    the pattern connect-review_{alias}_{region}_{MMDDYYYY_HHMMSS}.html
    where {alias} equals the API-returned InstanceAlias.

    **Validates: Requirements 2.1**
    """
    report_key = _build_report_key(alias, region, dt)

    assert FILENAME_PATTERN.match(report_key), (
        f"Filename '{report_key}' does not match expected pattern"
    )

    match = FILENAME_PATTERN.match(report_key)
    extracted_alias = match.group(1)
    assert extracted_alias == alias, (
        f"Alias round-trip failed: input='{alias}', extracted='{extracted_alias}'"
    )

    extracted_region = match.group(2)
    assert extracted_region == region, (
        f"Region round-trip failed: input='{region}', extracted='{extracted_region}'"
    )

    extracted_timestamp = match.group(3)
    expected_timestamp = dt.strftime("%m%d%Y_%H%M%S")
    assert extracted_timestamp == expected_timestamp, (
        f"Timestamp mismatch: expected='{expected_timestamp}', got='{extracted_timestamp}'"
    )


@settings(max_examples=100)
@given(alias=alias_strategy, region=region_strategy, dt=datetime_strategy)
def test_filename_contains_alias_verbatim(alias, region, dt):
    """Verify the alias appears verbatim in the filename (no transformation).

    **Validates: Requirements 2.1**
    """
    report_key = _build_report_key(alias, region, dt)

    prefix = "connect-review_"
    after_prefix = report_key[len(prefix) :]
    assert after_prefix.startswith(f"{alias}_{region}_"), (
        f"Alias '{alias}' not found verbatim in filename '{report_key}'"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
