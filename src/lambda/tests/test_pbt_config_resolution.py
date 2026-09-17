# Feature: quality-backlog-v2.0.1, Config Resolution Property Tests
"""
Consolidated property-based tests for prepare_context.py config resolution.

Combines:
- Property 1 (Bug Condition): DEFAULT_CONFIG["retainJsonData"] must be False
- Property 2 (Preservation): Config resolution priority chain (input > SSM > DEFAULT_CONFIG)

The config resolution priority chain is:
    input JSON > SSM parameter > DEFAULT_CONFIG

Property 1 tests confirm the bug fix is in place (retainJsonData defaults to False).
Property 2 tests confirm the priority chain works correctly and no regressions
were introduced by the fix.

**Validates: Requirements 2.1, 2.4, 3.1, 3.2, 3.5, 3.6**
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, note, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------


def _make_mock_boto3_client(ssm_config):
    """Create a mock boto3.client factory that returns the given SSM config.

    Args:
        ssm_config: Dict to return from SSM get_parameter, or None to simulate failure.
    """

    def mock_boto3_client(service_name, **kwargs):
        if service_name == "ssm":
            mock_ssm = MagicMock()
            if ssm_config is None:
                mock_ssm.get_parameter.side_effect = Exception("SSM unavailable")
            else:
                mock_ssm.get_parameter.return_value = {
                    "Parameter": {"Value": json.dumps(ssm_config)}
                }
            return mock_ssm
        # Return a no-op mock for connect/s3 to avoid real AWS calls
        mock_client = MagicMock()
        mock_client.describe_instance.return_value = {"Instance": {}}
        mock_client.get_paginator.return_value.paginate.return_value = []
        mock_client.list_instance_storage_configs.return_value = {"StorageConfigs": []}
        mock_client.list_integration_associations.return_value = {
            "IntegrationAssociationSummaryList": []
        }
        mock_client.put_object.return_value = {}
        return mock_client

    return mock_boto3_client


def _invoke_handler(raw_input: dict, ssm_config):
    """Invoke lambda_handler with given rawInput and SSM config.

    Args:
        raw_input: The rawInput dict to pass in the event.
        ssm_config: Dict for SSM response, or None for SSM failure.

    Returns:
        The ExecutionContext dict returned by lambda_handler.
    """
    event = {
        "rawInput": raw_input,
        "executionStartTime": "2026-01-01T00:00:00Z",
        "executionName": "test-config-resolution",
    }

    with patch(
        "prepare_context.boto3.client", side_effect=_make_mock_boto3_client(ssm_config)
    ):
        from prepare_context import lambda_handler

        context = MagicMock()
        return lambda_handler(event, context)


# ---------------------------------------------------------------------------
# Shared Strategies
# ---------------------------------------------------------------------------

# Boolean values for retainJsonData / generateHtmlReport
_bool_values = st.booleans()

# daysBack values (positive integers, reasonable range)
_days_back_values = st.integers(min_value=1, max_value=90)


# ===========================================================================
# Property 1: Bug Condition — DEFAULT_CONFIG["retainJsonData"] is False
#
# These tests confirm the bug fix is in place. On unfixed code they would FAIL,
# confirming the bug. After the fix they PASS.
#
# Validates: Requirements 2.1, 2.4
# ===========================================================================


class TestDefaultConfigConstant:
    """Assert that DEFAULT_CONFIG['retainJsonData'] is False (expected behavior)."""

    def test_default_config_retain_json_data_is_false(self):
        """DEFAULT_CONFIG['retainJsonData'] must be False per design spec.

        **Validates: Requirements 2.1, 2.4**
        """
        from prepare_context import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["retainJsonData"] is False, (
            f"BUG CONFIRMED: DEFAULT_CONFIG['retainJsonData'] is "
            f"{DEFAULT_CONFIG['retainJsonData']} but should be False"
        )


class TestBugConditionSSMFallback:
    """When SSM fails and rawInput lacks retainJsonData, the resolved value
    must be False (uses DEFAULT_CONFIG which should have False after fix).

    **Validates: Requirements 2.1, 2.4**
    """

    @given(
        extra_fields=st.fixed_dictionaries(
            {},
            optional={
                "daysBack": st.integers(min_value=1, max_value=90),
                # generateHtmlReport pinned to True: when False, the D2 coercion
                # in prepare_context.py correctly forces retainJsonData=True,
                # which is a separate rule from the DEFAULT_CONFIG fallback this
                # test is asserting. See rc6-critical-report-defects/R6.
                "generateHtmlReport": st.just(True),
            },
        ),
    )
    @settings(max_examples=20, deadline=10000)
    def test_ssm_failure_resolves_retain_json_data_to_false(self, extra_fields):
        """When SSM raises an exception and rawInput lacks retainJsonData,
        the resolved value must be False.

        **Validates: Requirements 2.1, 2.4**
        """
        raw_input = {k: v for k, v in extra_fields.items() if k != "retainJsonData"}
        assert "retainJsonData" not in raw_input

        note(f"rawInput: {raw_input}")

        result = _invoke_handler(raw_input, ssm_config=None)

        note(f"resolved retainJsonData: {result['retainJsonData']}")

        assert result["retainJsonData"] is False, (
            f"BUG CONFIRMED: retainJsonData resolved to {result['retainJsonData']} "
            f"(should be False when SSM fails and rawInput lacks the key). "
            f"DEFAULT_CONFIG fallback is providing True instead of False."
        )

    @settings(max_examples=10, deadline=10000)
    @given(data=st.data())
    def test_ssm_missing_key_resolves_retain_json_data_to_false(self, data):
        """When SSM returns config without retainJsonData and rawInput also
        lacks it, the resolved value must be False.

        **Validates: Requirements 2.1, 2.4**
        """
        ssm_config = data.draw(
            st.fixed_dictionaries(
                {},
                optional={
                    # generateHtmlReport pinned to True: see the D2-coercion
                    # note on test_ssm_failure_resolves_retain_json_data_to_false.
                    "generateHtmlReport": st.just(True),
                    "daysBack": st.integers(min_value=1, max_value=90),
                },
            )
        )
        ssm_config.pop("retainJsonData", None)

        note(f"ssm_config (without retainJsonData): {ssm_config}")

        result = _invoke_handler(raw_input={}, ssm_config=ssm_config)

        note(f"resolved retainJsonData: {result['retainJsonData']}")

        assert result["retainJsonData"] is False, (
            f"BUG CONFIRMED: retainJsonData resolved to {result['retainJsonData']} "
            f"when SSM config lacks the key and rawInput is empty. "
            f"DEFAULT_CONFIG fallback provides True instead of False."
        )


# ===========================================================================
# Property 2: Preservation — Config Resolution Priority Chain
#
# These tests verify the three-tier resolution (input > SSM > DEFAULT_CONFIG)
# works correctly. They serve as regression guards ensuring the bug fix does
# not break existing priority chain logic.
#
# Validates: Requirements 3.1, 3.2, 3.5, 3.6
# ===========================================================================


class TestExplicitInputWins:
    """For all events where rawInput contains an explicit retainJsonData value,
    the resolved output equals that explicit value regardless of SSM or
    DEFAULT_CONFIG state.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(
        explicit_value=_bool_values,
        ssm_retain_value=st.one_of(st.none(), _bool_values),
        ssm_fails=st.booleans(),
    )
    @settings(max_examples=50, deadline=10000)
    def test_explicit_retain_json_data_overrides_all(
        self, explicit_value, ssm_retain_value, ssm_fails
    ):
        """When rawInput explicitly provides retainJsonData, the output must
        equal that value regardless of what SSM contains or whether SSM fails.

        **Validates: Requirements 3.1, 3.2**
        """
        raw_input = {"retainJsonData": explicit_value}

        if ssm_fails:
            ssm_config = None
        elif ssm_retain_value is not None:
            ssm_config = {"retainJsonData": ssm_retain_value}
        else:
            ssm_config = {}

        note(
            f"explicit_value={explicit_value}, ssm_config={ssm_config}, ssm_fails={ssm_fails}"
        )

        result = _invoke_handler(raw_input, ssm_config)

        note(f"resolved retainJsonData: {result['retainJsonData']}")

        assert result["retainJsonData"] is explicit_value, (
            f"Priority chain violation: rawInput provided retainJsonData={explicit_value} "
            f"but resolved to {result['retainJsonData']}. "
            f"Input JSON must always take precedence over SSM and DEFAULT_CONFIG."
        )

    @given(explicit_value=_bool_values)
    @settings(max_examples=20, deadline=10000)
    def test_explicit_false_override_works(self, explicit_value):
        """When rawInput explicitly sets retainJsonData=false, output is False.

        **Validates: Requirements 3.1, 3.2**
        """
        raw_input = {"retainJsonData": False}
        ssm_config = {"retainJsonData": True, "generateHtmlReport": True}

        result = _invoke_handler(raw_input, ssm_config)

        assert result["retainJsonData"] is False, (
            f"Explicit retainJsonData=false in rawInput must resolve to False, "
            f"but got {result['retainJsonData']}"
        )


class TestSSMPrecedenceOverDefault:
    """For all events where rawInput lacks retainJsonData but SSM contains it,
    the resolved output equals the SSM value (SSM wins over DEFAULT_CONFIG).

    **Validates: Requirements 3.1, 3.5**
    """

    @given(ssm_value=_bool_values)
    @settings(max_examples=30, deadline=10000)
    def test_ssm_retain_json_overrides_default_config(self, ssm_value):
        """When rawInput does NOT contain retainJsonData but SSM does,
        the resolved value must equal the SSM value.

        **Validates: Requirements 3.1, 3.5**
        """
        raw_input = {}
        ssm_config = {"retainJsonData": ssm_value}

        note(f"ssm_value={ssm_value}")

        result = _invoke_handler(raw_input, ssm_config)

        note(f"resolved retainJsonData: {result['retainJsonData']}")

        assert result["retainJsonData"] is ssm_value, (
            f"Priority chain violation: SSM contains retainJsonData={ssm_value} "
            f"and rawInput lacks the key, so resolved value should be {ssm_value}, "
            f"but got {result['retainJsonData']}. SSM must take precedence over DEFAULT_CONFIG."
        )

    @given(ssm_html_value=_bool_values)
    @settings(max_examples=30, deadline=10000)
    def test_ssm_generate_html_report_overrides_default_config(self, ssm_html_value):
        """When rawInput does NOT contain generateHtmlReport but SSM does,
        the resolved value must equal the SSM value.

        **Validates: Requirements 3.5, 3.6**
        """
        raw_input = {}
        ssm_config = {"generateHtmlReport": ssm_html_value}

        note(f"ssm_html_value={ssm_html_value}")

        result = _invoke_handler(raw_input, ssm_config)

        note(f"resolved generateHtmlReport: {result['generateHtmlReport']}")

        assert result["generateHtmlReport"] is ssm_html_value, (
            f"Priority chain violation: SSM contains generateHtmlReport={ssm_html_value} "
            f"and rawInput lacks the key, so resolved value should be {ssm_html_value}, "
            f"but got {result['generateHtmlReport']}. SSM must take precedence over DEFAULT_CONFIG."
        )


class TestOtherConfigFieldsPriorityChain:
    """For generateHtmlReport and other config fields, the same priority chain
    applies: input JSON > SSM parameter > DEFAULT_CONFIG.

    **Validates: Requirements 3.5, 3.6**
    """

    @given(
        explicit_html=_bool_values,
        ssm_html=st.one_of(st.none(), _bool_values),
    )
    @settings(max_examples=30, deadline=10000)
    def test_explicit_generate_html_report_overrides_all(self, explicit_html, ssm_html):
        """When rawInput explicitly provides generateHtmlReport, the output
        must equal that value regardless of SSM.

        **Validates: Requirements 3.5, 3.6**
        """
        raw_input = {"generateHtmlReport": explicit_html}
        ssm_config = {"generateHtmlReport": ssm_html} if ssm_html is not None else {}

        note(f"explicit_html={explicit_html}, ssm_html={ssm_html}")

        result = _invoke_handler(raw_input, ssm_config)

        assert result["generateHtmlReport"] is explicit_html, (
            f"Priority chain violation for generateHtmlReport: input provided "
            f"{explicit_html} but resolved to {result['generateHtmlReport']}"
        )

    @given(
        input_days=st.one_of(st.none(), _days_back_values),
        ssm_days=st.one_of(st.none(), _days_back_values),
    )
    @settings(max_examples=30, deadline=10000)
    def test_days_back_priority_chain(self, input_days, ssm_days):
        """daysBack follows the same priority chain: input > SSM > DEFAULT_CONFIG.

        **Validates: Requirements 3.5, 3.6**
        """
        raw_input = {}
        if input_days is not None:
            raw_input["daysBack"] = input_days

        ssm_config = {}
        if ssm_days is not None:
            ssm_config["daysBack"] = ssm_days

        note(f"input_days={input_days}, ssm_days={ssm_days}")

        result = _invoke_handler(raw_input, ssm_config)

        from prepare_context import DEFAULT_CONFIG

        if input_days is not None and input_days != 0:
            expected = input_days
        elif ssm_days is not None and ssm_days != 0:
            expected = ssm_days
        else:
            expected = DEFAULT_CONFIG["daysBack"]

        note(f"resolved daysBack={result['daysBack']}, expected={expected}")

        assert result["daysBack"] == expected, (
            f"Priority chain violation for daysBack: expected {expected} "
            f"(input={input_days}, ssm={ssm_days}, default={DEFAULT_CONFIG['daysBack']}) "
            f"but got {result['daysBack']}"
        )

    @given(
        ssm_analyzers=st.one_of(
            st.none(),
            st.fixed_dictionaries(
                {},
                optional={
                    "security": _bool_values,
                    "resilience": _bool_values,
                    "cloudtrail": _bool_values,
                    "operational_excellence": _bool_values,
                    "capacity": _bool_values,
                    "observability": _bool_values,
                    "cost": _bool_values,
                    "ai": _bool_values,
                },
            ),
        )
    )
    @settings(max_examples=20, deadline=10000)
    def test_analyzers_config_from_ssm_or_default(self, ssm_analyzers):
        """enabledAnalyzers resolves from SSM config or DEFAULT_CONFIG.

        **Validates: Requirements 3.5, 3.6**
        """
        raw_input = {}
        ssm_config = {}
        if ssm_analyzers is not None:
            ssm_config["analyzers"] = ssm_analyzers

        result = _invoke_handler(raw_input, ssm_config)

        from prepare_context import DEFAULT_CONFIG

        if ssm_analyzers is not None:
            expected = ssm_analyzers
        else:
            expected = DEFAULT_CONFIG["analyzers"]

        note(
            f"resolved enabledAnalyzers={result['enabledAnalyzers']}, expected={expected}"
        )

        assert result["enabledAnalyzers"] == expected, (
            f"Analyzers config did not resolve correctly. "
            f"SSM had analyzers={ssm_analyzers}, expected resolved={expected}, "
            f"got {result['enabledAnalyzers']}"
        )
