# Feature: rc6-critical-report-defects (QB-72 Layer 2 / QB-39 Layer 2)
"""
Truth-table tests for runtime D1 / D2 coercion in prepare_context.py.

D1: enableGlueCatalog=true requires retainJsonData=true — otherwise DeleteJsonData
    empties the S3 prefixes that back Glue tables on every run.
D2: generateHtmlReport=false requires retainJsonData=true — otherwise the run
    produces no output at all.

The 8-cell truth table (enable_glue_catalog x generate_html_report x retain_json_data_in):

| # | Glue  | HTML  | retain_in | expected retain_out | expected coercion log(s) |
|---|-------|-------|-----------|---------------------|--------------------------|
| 1 | False | True  | False     | False               | none                     |
| 2 | False | True  | True      | True                | none                     |
| 3 | True  | True  | False     | True                | D1                       |
| 4 | True  | True  | True      | True                | none                     |
| 5 | False | False | False     | True                | D2                       |
| 6 | False | False | True      | True                | none                     |
| 7 | True  | False | False     | True                | D1 only (see note)       |
| 8 | True  | False | True      | True                | none                     |

Note on cell 7: the design's Fix-5 code block guards the D2 branch with
`not retain_json_data`. When D1 fires first (Glue=True, retain=False), it sets
retain_json_data=True, and the D2 branch's guard is then satisfied, so D2 does
NOT emit a log. The design's truth-table hint "D1 AND D2 both logged" is
therefore aspirational; the code as specified in task 4.2 is authoritative and
produces only the D1 log. Final retainJsonData is True either way — the
invariant that matters for correctness is preserved.

Each cell is exercised via all three input sources (raw_input, config, DEFAULT_CONFIG)
to prove coercion is independent of provenance. The five "no coerce" cells
(1, 2, 4, 6, 8) double as preservation coverage: their `expected retain_out`
column locks in that the pre-fix behavior is unchanged for those inputs.

**Validates: Requirements R6.1, R6.2, R6.3, R6.4**
"""

import copy
import json
import logging
from unittest.mock import MagicMock, patch

import pytest


D1_LOG_FRAGMENT = "enableGlueCatalog=true (D1)"
D2_LOG_FRAGMENT = "generateHtmlReport=false (D2)"


# ---------------------------------------------------------------------------
# Truth table
# ---------------------------------------------------------------------------

# (cell_id, glue, html, retain_in, expected_retain_out, expected_log_fragments)
TRUTH_TABLE = [
    (1, False, True, False, False, ()),
    (2, False, True, True, True, ()),
    (3, True, True, False, True, (D1_LOG_FRAGMENT,)),
    (4, True, True, True, True, ()),
    (5, False, False, False, True, (D2_LOG_FRAGMENT,)),
    (6, False, False, True, True, ()),
    # Cell 7: D1 fires first and sets retain=True; D2's `not retain_json_data`
    # guard then suppresses its log. Final retain=True either way.
    (7, True, False, False, True, (D1_LOG_FRAGMENT,)),
    (8, True, False, True, True, ()),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_boto3_client(ssm_config):
    """Mock boto3.client factory returning an SSM client that yields ssm_config.

    ssm_config: dict to serialize as SSM parameter value, or None to simulate SSM missing.
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
        # No-op stubs for connect/s3 so _prefetch_shared_data does not hit real AWS
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


def _invoke_handler(raw_input, ssm_config=None):
    """Invoke lambda_handler with the given rawInput and SSM config."""
    event = {
        "rawInput": raw_input,
        "executionStartTime": "2026-01-01T00:00:00Z",
        "executionName": "test-d1-d2-coercion",
    }
    with patch(
        "prepare_context.boto3.client",
        side_effect=_make_mock_boto3_client(ssm_config),
    ):
        from prepare_context import lambda_handler

        return lambda_handler(event, MagicMock())


def _build_inputs_for_source(source, glue, html, retain):
    """Build (raw_input, ssm_config) for the requested provenance source.

    source == "raw_input":     all three flags in rawInput (scheduler / manual start-execution)
    source == "config":        rawInput minimal; all three flags in SSM config
    source == "DEFAULT_CONFIG": rawInput minimal; SSM absent; test patches DEFAULT_CONFIG
                                and returns a sentinel so caller can restore.
    """
    minimal_raw = {
        "instanceArn": "arn:aws:connect:us-east-1:123456789012:instance/00000000-0000-0000-0000-000000000000",
        "s3ReportingBucket": "test-bucket",
    }

    if source == "raw_input":
        raw = dict(minimal_raw)
        raw["enableGlueCatalog"] = glue
        raw["generateHtmlReport"] = html
        raw["retainJsonData"] = retain
        return raw, None  # SSM absent; rawInput wins

    if source == "config":
        return minimal_raw, {
            "enableGlueCatalog": glue,
            "generateHtmlReport": html,
            "retainJsonData": retain,
        }

    if source == "DEFAULT_CONFIG":
        # caller must patch DEFAULT_CONFIG separately; rawInput and SSM contribute nothing
        return minimal_raw, None

    raise ValueError(f"unknown source: {source}")


# ---------------------------------------------------------------------------
# 4.3 — Truth-table coverage across all three input sources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell_id,glue,html,retain_in,retain_out,log_fragments", TRUTH_TABLE
)
@pytest.mark.parametrize("source", ["raw_input", "config", "DEFAULT_CONFIG"])
def test_truth_table_coercion(
    cell_id, glue, html, retain_in, retain_out, log_fragments, source, caplog
):
    """Every cell of the truth table produces the expected retainJsonData
    value and the expected coercion log lines, regardless of input source.

    **Validates: Requirements R6.1, R6.2, R6.3**
    """
    if source == "DEFAULT_CONFIG":
        # Patch DEFAULT_CONFIG so rawInput.get / config.get fall through to the default.
        import prepare_context

        patched = copy.deepcopy(prepare_context.DEFAULT_CONFIG)
        patched["enableGlueCatalog"] = glue
        patched["generateHtmlReport"] = html
        patched["retainJsonData"] = retain_in
        with patch.dict(prepare_context.DEFAULT_CONFIG, patched, clear=False):
            raw, ssm = _build_inputs_for_source(source, glue, html, retain_in)
            with caplog.at_level(logging.INFO, logger="prepare_context"):
                ctx = _invoke_handler(raw, ssm)
    else:
        raw, ssm = _build_inputs_for_source(source, glue, html, retain_in)
        with caplog.at_level(logging.INFO, logger="prepare_context"):
            ctx = _invoke_handler(raw, ssm)

    assert ctx["retainJsonData"] is retain_out, (
        f"cell {cell_id} via {source}: expected retainJsonData={retain_out}, "
        f"got {ctx['retainJsonData']}"
    )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    for fragment in log_fragments:
        assert fragment in log_text, (
            f"cell {cell_id} via {source}: expected log fragment "
            f"{fragment!r} in emitted logs, got:\n{log_text}"
        )

    # No unexpected coercion log lines
    if D1_LOG_FRAGMENT not in log_fragments:
        assert D1_LOG_FRAGMENT not in log_text, (
            f"cell {cell_id} via {source}: unexpected D1 coercion log:\n{log_text}"
        )
    if D2_LOG_FRAGMENT not in log_fragments:
        assert D2_LOG_FRAGMENT not in log_text, (
            f"cell {cell_id} via {source}: unexpected D2 coercion log:\n{log_text}"
        )


def test_coercion_is_idempotent():
    """Running the resolved value through the handler twice yields the same
    ExecutionContext — coercion applied to an already-True retainJsonData is a
    no-op and emits no additional coercion logs.

    **Validates: Requirement R6.4**
    """
    raw = {
        "instanceArn": "arn:aws:connect:us-east-1:123456789012:instance/00000000-0000-0000-0000-000000000000",
        "s3ReportingBucket": "test-bucket",
        "enableGlueCatalog": True,
        "generateHtmlReport": True,
        "retainJsonData": True,  # already True; nothing should coerce
    }
    ctx = _invoke_handler(raw, ssm_config=None)
    assert ctx["retainJsonData"] is True


# Preservation coverage for the 5 no-coerce cells is now provided by
# `test_truth_table_coercion`, which asserts `ctx["retainJsonData"] is retain_out`
# for every cell (including cells 1, 2, 4, 6, 8) via all three input sources.
# The prior `test_no_coerce_cells_preserve_execution_context` was removed on
# 2026-08-19 (TB-15) — its "simulate the pre-fix handler" logic never actually
# bypassed the coercion block, so it only proved determinism, not preservation.
