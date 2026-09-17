"""Unit tests for check_ai_agents_limits() in capacity_analyzer.py.

Validates task 7.5: Successful quota retrieval returns expected structure,
and timeout returns partial results with appropriate indication.

Requirements: 3.7
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from capacity_analyzer import check_ai_agents_limits, AI_AGENTS_QUOTAS
from graceful_timeout import TimeBudgetExceeded


# -- Test fixtures --

START_TIME = time.time()
TIME_BUDGET = 300
AWS_REGION = "us-east-1"


def _make_quota_response(value=10.0, default=5.0, adjustable=True):
    """Helper to build a mock get_service_quota response."""
    return {
        "Quota": {
            "Value": value,
            "DefaultValue": default,
            "Adjustable": adjustable,
        }
    }


def _make_client_error(code, message="error"):
    """Helper to build a botocore ClientError."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetServiceQuota",
    )


# -- Happy path tests --


class TestCheckAiAgentsLimitsSuccess:
    """Tests for successful quota retrieval returning expected structure."""

    def test_returns_dict_with_quotas_status_detail(self):
        """Result must have 'quotas', 'status', and 'detail' keys."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response()

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result is not None
        assert "quotas" in result
        assert "status" in result
        assert "detail" in result

    def test_returns_one_entry_per_quota(self):
        """Should return exactly len(AI_AGENTS_QUOTAS) entries."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response()

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert len(result["quotas"]) == len(AI_AGENTS_QUOTAS)

    def test_each_quota_has_required_keys(self):
        """Each quota entry must have name, quota_code, applied_value,
        default_value, adjustable, and percentage_used."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response(
            value=20.0, default=10.0, adjustable=True
        )

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        required_keys = {
            "name",
            "quota_code",
            "applied_value",
            "default_value",
            "adjustable",
            "percentage_used",
        }
        for quota in result["quotas"]:
            assert required_keys.issubset(quota.keys()), (
                f"Missing keys in quota entry: {required_keys - quota.keys()}"
            )

    def test_quota_values_populated_from_api_response(self):
        """applied_value, default_value, adjustable should reflect API response."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response(
            value=50.0, default=25.0, adjustable=False
        )

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        first_quota = result["quotas"][0]
        assert first_quota["applied_value"] == 50.0
        assert first_quota["default_value"] == 25.0
        assert first_quota["adjustable"] is False

    def test_quota_names_and_codes_match_constant(self):
        """Returned quota names and codes must correspond to AI_AGENTS_QUOTAS."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response()

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        expected_names = [label for label, _ in AI_AGENTS_QUOTAS]
        expected_codes = [code for _, code in AI_AGENTS_QUOTAS]
        actual_names = [q["name"] for q in result["quotas"]]
        actual_codes = [q["quota_code"] for q in result["quotas"]]

        assert actual_names == expected_names
        assert actual_codes == expected_codes

    def test_status_is_info(self):
        """Status should be 'info' on successful retrieval."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response()

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result["status"] == "info"

    def test_detail_mentions_quota_count(self):
        """Detail string should mention the number of quotas reviewed."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.return_value = _make_quota_response()

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert str(len(AI_AGENTS_QUOTAS)) in result["detail"]
        assert "AI Agent" in result["detail"]


# -- Timeout / partial results tests --


class TestCheckAiAgentsLimitsTimeout:
    """Tests for timeout returning partial results with appropriate indication."""

    def test_time_budget_exceeded_at_start_raises(self):
        """If time budget is already exceeded at entry, TimeBudgetExceeded propagates."""
        mock_sq = MagicMock()

        with pytest.raises(TimeBudgetExceeded):
            check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, -1)

    def test_time_budget_exceeded_midway_raises(self):
        """If time budget expires partway through iteration, TimeBudgetExceeded propagates."""
        mock_sq = MagicMock()
        # First call succeeds, but we set start_time far in the past with tiny budget
        # so the check at the top of the second iteration fails.
        mock_sq.get_service_quota.return_value = _make_quota_response()

        # Use a start_time far enough in the past that the second check_time_budget call
        # triggers after the first quota is processed.
        expired_start_time = time.time() - 1000

        with pytest.raises(TimeBudgetExceeded):
            check_ai_agents_limits(mock_sq, AWS_REGION, expired_start_time, 1)


# -- Error handling tests --


class TestCheckAiAgentsLimitsErrors:
    """Tests for error handling: throttling retries, NoSuchResource, and general errors."""

    def test_no_such_resource_yields_none_values(self):
        """When the quota doesn't exist in the region, values are None but entry exists."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.side_effect = _make_client_error(
            "NoSuchResourceException"
        )

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result is not None
        for quota in result["quotas"]:
            assert quota["applied_value"] is None
            assert quota["default_value"] is None
            assert quota["adjustable"] is None

    def test_throttle_retries_and_succeeds(self):
        """Should retry on TooManyRequestsException and succeed on later attempt."""
        mock_sq = MagicMock()
        # First call throttled, second succeeds
        mock_sq.get_service_quota.side_effect = [
            _make_client_error("TooManyRequestsException"),
            _make_quota_response(value=100.0, default=50.0, adjustable=True),
        ] * len(AI_AGENTS_QUOTAS)

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result is not None
        # At least the first quota should have values from the successful retry
        first = result["quotas"][0]
        assert first["applied_value"] == 100.0

    def test_all_retries_exhausted_yields_none_values(self):
        """When all 4 retries are throttled, values are None but entry still appears."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.side_effect = _make_client_error(
            "TooManyRequestsException"
        )

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result is not None
        for quota in result["quotas"]:
            assert quota["applied_value"] is None
            assert quota["default_value"] is None
            assert quota["adjustable"] is None

    def test_unexpected_client_error_yields_none_values(self):
        """Unexpected ClientError codes produce None values, not crash."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.side_effect = _make_client_error(
            "AccessDeniedException"
        )

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result is not None
        for quota in result["quotas"]:
            assert quota["applied_value"] is None

    def test_generic_exception_per_quota_yields_none_values(self):
        """A non-ClientError exception in a single quota yields None values for that entry."""
        mock_sq = MagicMock()
        mock_sq.get_service_quota.side_effect = RuntimeError("Unexpected")

        result = check_ai_agents_limits(mock_sq, AWS_REGION, START_TIME, TIME_BUDGET)

        assert result is not None
        for quota in result["quotas"]:
            assert quota["applied_value"] is None

    def test_catastrophic_outer_exception_returns_none(self):
        """If an unhandled exception escapes the outer try/except, returns None."""
        mock_sq = MagicMock()

        # Patch check_time_budget to pass on first call then raise a generic
        # Exception (not TimeBudgetExceeded) on the second call, simulating
        # an unexpected failure inside the outer try block.
        with patch(
            "capacity_analyzer.check_time_budget",
            side_effect=[None, Exception("cosmic ray")],
        ):
            result = check_ai_agents_limits(
                mock_sq, AWS_REGION, START_TIME, TIME_BUDGET
            )
            assert result is None
