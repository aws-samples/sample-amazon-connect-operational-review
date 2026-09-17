# Feature: rc6-critical-report-defects (Task 5.5)
"""
Integration-style unit test for scheduler-triggered PrepareContext invocations.

Task 5.1 / 5.2 stripped `generateHtmlReport` and `retainJsonData` from the
EventBridge scheduler target input, leaving only `instanceArn` and
`s3ReportingBucket`. This test proves the runtime effect of that strip:

    A scheduler-shaped invocation resolves `retainJsonData` (and
    `generateHtmlReport`) from SSM, not from `raw_input`.

`prepare_context.lambda_handler` uses precedence
`raw_input.get(k, config.get(k, DEFAULT_CONFIG[k]))` — so when the key is
absent from raw_input, resolution falls through to the SSM-backed config.

Coercion note (Task 4): the runtime D1 / D2 guard rewrites `retainJsonData`
to True when `enableGlueCatalog=True` or `generateHtmlReport=False`. To
isolate "SSM vs default" from "coercion fired", we pick a coercion-free
combination:

    SSM: retainJsonData=True, generateHtmlReport=True, enableGlueCatalog=False

and monkey-patch DEFAULT_CONFIG so the SSM values are distinctive from both
the DEFAULT_CONFIG values and any coercion outcome. That way the assertion
`ctx["retainJsonData"] is True` proves the value came from SSM (not from
DEFAULT_CONFIG=False, not from a D1/D2 rewrite).

**Validates: Requirement R5.3**
"""

import copy
import json
from unittest.mock import MagicMock, patch


SCHEDULER_INSTANCE_ARN = (
    "arn:aws:connect:us-east-1:123456789012:instance/"
    "00000000-0000-0000-0000-000000000000"
)
SCHEDULER_S3_BUCKET = "test-scheduler-bucket"


def _make_mock_boto3_client(ssm_config):
    """Return a boto3.client factory that yields ssm_config from SSM.

    All other AWS clients (connect, s3) are stubbed so _prefetch_shared_data
    does not hit real AWS. Pattern mirrors test_pbt_rc7_d1_d2_coercion.py.
    """

    def mock_boto3_client(service_name, **kwargs):
        if service_name == "ssm":
            mock_ssm = MagicMock()
            mock_ssm.get_parameter.return_value = {
                "Parameter": {"Value": json.dumps(ssm_config)}
            }
            return mock_ssm
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


def _scheduler_event():
    """Return the exact event shape an EventBridge scheduler produces after
    Task 5.1 / 5.2 — only `instanceArn` + `s3ReportingBucket` in rawInput.
    """
    return {
        "rawInput": {
            "instanceArn": SCHEDULER_INSTANCE_ARN,
            "s3ReportingBucket": SCHEDULER_S3_BUCKET,
        },
        "executionStartTime": "2026-01-01T00:00:00Z",
        "executionName": "scheduler-ssm-resolution-test",
    }


def test_scheduler_invocation_resolves_retain_json_data_from_ssm():
    """A scheduler invocation (rawInput has only instanceArn + s3ReportingBucket)
    resolves both `retainJsonData` and `generateHtmlReport` from the SSM-backed
    config, not from raw_input and not from DEFAULT_CONFIG.

    Setup:
      - SSM config: retainJsonData=True, generateHtmlReport=True,
        enableGlueCatalog=False (coercion-free).
      - DEFAULT_CONFIG monkey-patched so retainJsonData=False AND
        generateHtmlReport=False. This makes the SSM values distinctive
        from the defaults — the only way the handler can return True for
        either field is by reading from SSM.

    Expected:
      - retainJsonData == True (came from SSM; DEFAULT_CONFIG=False could
        not have produced True, and no D1/D2 coercion is active).
      - generateHtmlReport == True (came from SSM; DEFAULT_CONFIG=False
        could not have produced True).

    **Validates: Requirement R5.3**
    """
    ssm_config = {
        "retainJsonData": True,
        "generateHtmlReport": True,
        "enableGlueCatalog": False,
    }

    import prepare_context

    # Flip defaults so True can ONLY come from SSM.
    patched_defaults = copy.deepcopy(prepare_context.DEFAULT_CONFIG)
    patched_defaults["retainJsonData"] = False
    patched_defaults["generateHtmlReport"] = False
    patched_defaults["enableGlueCatalog"] = False

    with patch.dict(prepare_context.DEFAULT_CONFIG, patched_defaults, clear=False):
        with patch(
            "prepare_context.boto3.client",
            side_effect=_make_mock_boto3_client(ssm_config),
        ):
            ctx = prepare_context.lambda_handler(_scheduler_event(), MagicMock())

    # Sanity: raw_input truly did not carry either field
    event = _scheduler_event()
    assert "retainJsonData" not in event["rawInput"]
    assert "generateHtmlReport" not in event["rawInput"]

    # The core assertion — value came from SSM, not from raw_input, not from
    # DEFAULT_CONFIG (which we patched to False), not from D1/D2 coercion
    # (enableGlueCatalog=False AND generateHtmlReport=True, so neither branch fires).
    assert ctx["retainJsonData"] is True, (
        f"Expected retainJsonData=True from SSM, got {ctx['retainJsonData']!r}. "
        f"Scheduler raw_input omitted the key, so the value must fall through "
        f"to SSM; DEFAULT_CONFIG was patched to False so True can only come "
        f"from the SSM-backed config."
    )
    assert ctx["generateHtmlReport"] is True, (
        f"Expected generateHtmlReport=True from SSM, got "
        f"{ctx['generateHtmlReport']!r}. Same logic: raw_input omitted the "
        f"key, DEFAULT_CONFIG patched to False, so True can only come from SSM."
    )
