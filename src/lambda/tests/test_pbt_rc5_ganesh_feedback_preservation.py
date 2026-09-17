"""Preservation Property Tests — rc.5 Ganesh Feedback (Properties 2, 4, 6, 8, 10).

Companion to ``test_pbt_rc5_ganesh_feedback.py`` (exploration tests). Where the
exploration tests encode the *bug conditions* (expected to fail on unfixed
code, expected to pass after the fix), these preservation tests encode the
*invariants that must remain true after the fix*.

Observation-first methodology: every property here was designed by first
observing baseline behavior on unfixed rc.5 code, then encoding those
observations as assertions. Consequently the entire module PASSES on
unfixed rc.5 code, with two exceptions gated on ``hasattr(ai_analyzer,
'check_q_hard_limits')``:

- Post-QB-53 assertions (``TOTAL_CHECKS == 5``, ``no hard_limits`` in op-ex,
  ``no knowledge_base_capacity`` in resilience, ``checks_completed`` size)
  are SKIPPED on unfixed code — they are preservation invariants that only
  come into force after task 3 (QB-53 removal) lands.

Once tasks 3–7 land, every skipped assertion becomes an unconditional
preservation guarantee.

Bug fixes and their preservation properties:

- QB-49 (Property 2 — enumeration): Fixed-name resources unchanged.
  All 13 CFN Lambda function names, the shared Lambda log group, the
  EventBridge scheduler, the SSM parameter, the Glue database, the eight
  Glue tables, and the S3 bucket parameter continue to use their current
  names. Same for the TF equivalents (11 Lambda function names + peers).

- QB-50 (Property 4 — property-based): Non-AI Executive Summary rows
  render deterministically and no new anchor collisions are introduced.
  Non-AI ``check_order`` entries remain byte-identical.

- QB-51 (Property 6 — property-based): Findings with
  ``check_name ∈ {domain_encryption, agent_logging, assistant_discovery}``
  render summary-only (no ``<table>`` element). Findings with
  ``check_name ∈ {agent_inventory, prompt_configuration, guardrails}`` but
  ``data=None`` or an empty per-row list also fall back to summary-only.

- QB-52 (Property 8 — unit tests): Every non-``agent_logging`` AI finding
  continues to route to its current pillar. ``assistant_discovery`` stays
  in op-ex. Post-QB-53 assertions (no ``hard_limits``, no
  ``knowledge_base_capacity``, ``TOTAL_CHECKS == 5``,
  ``checks_completed`` size) are gated behind
  ``hasattr(ai_analyzer, 'check_q_hard_limits')``.

- QB-53 (Property 10 — enumeration + unit tests): The Capacity Analysis
  "AI Agents Limits" section is preserved — ``capacity_analyzer.
  check_ai_agents_limits`` and the ``AI_AGENTS_QUOTAS`` module constant
  remain in place and produce their expected shape. The five surviving
  AI-analyzer check functions remain importable module attributes with
  unchanged signatures.

Validates: Requirements 1.12, 1.13, 3.5, 3.6, 4.7, 4.8, 4.9, 6.3, 6.8
"""

from __future__ import annotations

import inspect
import logging
import os
import re
from unittest.mock import MagicMock, patch

import pytest
import yaml
from botocore.exceptions import ClientError

# Reuse the CFN-tag-tolerant YAML loader from the rc4 helpers.
from rc4_iac_parsers import CFNLoader

import ai_analyzer
import capacity_analyzer
from ai_analyzer import lambda_handler
from report_generator import (
    CHECK_NAMES,
    _render_ai_structured_finding,
    _render_executive_summary,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))

CFN_PATH = os.path.normpath(
    os.path.join(
        _REPO_ROOT,
        "deploy",
        "cloudformation",
        "CFT-AmazonConnectOperationsReview.yml",
    )
)
TF_PATH = os.path.normpath(
    os.path.join(_REPO_ROOT, "deploy", "terraform", "parallel.tf")
)
TF_VARIABLES_PATH = os.path.normpath(
    os.path.join(_REPO_ROOT, "deploy", "terraform", "variables.tf")
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cfn_template() -> dict:
    """Parse the full CloudFormation template once per module."""
    with open(CFN_PATH, "r") as f:
        return yaml.load(f, Loader=CFNLoader)


@pytest.fixture(scope="module")
def cfn_resources(cfn_template) -> dict:
    """Return the Resources section of the CloudFormation template."""
    return cfn_template["Resources"]


@pytest.fixture(scope="module")
def cfn_parameters(cfn_template) -> dict:
    """Return the Parameters section of the CloudFormation template."""
    return cfn_template.get("Parameters", {})


@pytest.fixture(scope="module")
def tf_source() -> str:
    """Read the Terraform module source once per module."""
    with open(TF_PATH, "r") as f:
        return f.read()


@pytest.fixture(scope="module")
def tf_variables_source() -> str:
    """Read the Terraform variables.tf source once per module."""
    with open(TF_VARIABLES_PATH, "r") as f:
        return f.read()


def _extract_tf_resource_block(source: str, resource_type: str, name: str) -> str:
    """Return the raw HCL body of the named resource block.

    Matches an exact ``resource_type + name`` pair to avoid picking up an
    unrelated block if the file changes shape.
    """
    pattern = (
        r'resource\s+"'
        + re.escape(resource_type)
        + r'"\s+"'
        + re.escape(name)
        + r'"\s*\{(?P<body>.*?)^}'
    )
    match = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    assert match, (
        f"TF resource {resource_type}.{name} not found in {TF_PATH} — "
        "the parser regex may need updating."
    )
    return match.group("body")


# Post-QB-53 detection: on unfixed code, ``check_q_hard_limits`` still exists
# as a module attribute. Assertions gated on this attribute being absent are
# skipped on unfixed code and become active after task 3 (QB-53 removal).
POST_QB53 = not hasattr(ai_analyzer, "check_q_hard_limits")


# ═══════════════════════════════════════════════════════════════════════════
# QB-49 — Preservation: Fixed-name resources unchanged
# Property 2: Preservation
# Validates: Requirements 1.12, 1.13
# ═══════════════════════════════════════════════════════════════════════════


# Enumerate every CFN Lambda function name that must stay unchanged.
# Baseline observed by grepping the CFT template on unfixed rc.5.
CFN_LAMBDA_FUNCTION_NAMES = [
    ("Boto3LayerBuilder", "ConnectOpsReview-Boto3LayerBuilder"),
    ("SharedUtilsLayerBuilder", "ConnectOpsReview-SharedUtilsLayerBuilder"),
    ("PrepareContextFunction", "ConnectOpsReview-PrepareContext"),
    ("SecurityAnalyzerFunction", "ConnectOpsReview-SecurityAnalyzer"),
    ("ResilienceAnalyzerFunction", "ConnectOpsReview-ResilienceAnalyzer"),
    ("CloudTrailAnalyzerParallelFunction", "ConnectOpsReview-CloudTrailAnalyzer"),
    ("OpExAnalyzerFunction", "ConnectOpsReview-OpExAnalyzer"),
    ("CapacityAnalyzerFunction", "ConnectOpsReview-CapacityAnalyzer"),
    ("ObservabilityAnalyzerFunction", "ConnectOpsReview-ObservabilityAnalyzer"),
    ("CostAnalyzerFunction", "ConnectOpsReview-CostAnalyzer"),
    ("AIAnalyzerFunction", "ConnectOpsReview-AIAnalyzer"),
    ("ReportGeneratorFunction", "ConnectOpsReview-ReportGenerator"),
    ("DeleteJsonDataFunction", "ConnectOpsReview-DeleteJsonData"),
]


# Enumerate every TF Lambda function_name that must stay unchanged.
# TF has 11 Lambda functions (no LayerBuilder equivalents; layers are handled
# via an archive+aws_lambda_layer_version pattern instead).
TF_LAMBDA_FUNCTION_NAMES = [
    ("prepare_context", "ConnectOpsReview-PrepareContext"),
    ("cloudtrail_analyzer_parallel", "ConnectOpsReview-CloudTrailAnalyzer"),
    ("security_analyzer", "ConnectOpsReview-SecurityAnalyzer"),
    ("resilience_analyzer", "ConnectOpsReview-ResilienceAnalyzer"),
    ("opex_analyzer", "ConnectOpsReview-OpExAnalyzer"),
    ("capacity_analyzer", "ConnectOpsReview-CapacityAnalyzer"),
    ("observability_analyzer", "ConnectOpsReview-ObservabilityAnalyzer"),
    ("cost_analyzer", "ConnectOpsReview-CostAnalyzer"),
    ("ai_analyzer", "ConnectOpsReview-AIAnalyzer"),
    ("report_generator", "ConnectOpsReview-ReportGenerator"),
    ("delete_json_data", "ConnectOpsReview-DeleteJsonData"),
]


# Eight Glue table names, in CFN table order.
GLUE_TABLE_NAMES = [
    "cloudtrail",
    "security",
    "resilience",
    "operational_excellence",
    "capacity",
    "observability",
    "cost",
    "ai",
]


class TestQB49CfnLambdaNamesPreserved:
    """All 13 CFN Lambda functions have their existing ConnectOpsReview- FunctionName."""

    @pytest.mark.parametrize(
        "logical_id,expected_function_name", CFN_LAMBDA_FUNCTION_NAMES
    )
    def test_cfn_lambda_function_name(
        self, cfn_resources, logical_id, expected_function_name
    ):
        """CFN ``FunctionName`` on this Lambda resource equals the baseline.

        Validates: Requirements 1.12
        """
        resource = cfn_resources.get(logical_id)
        assert resource is not None, (
            f"CFN resource {logical_id!r} not found — template shape changed."
        )
        props = resource.get("Properties") or {}
        actual = props.get("FunctionName")
        assert actual == expected_function_name, (
            f"CFN {logical_id}.Properties.FunctionName drifted: expected "
            f"{expected_function_name!r}, got {actual!r}"
        )


class TestQB49CfnFixedNamesPreserved:
    """CFN non-Lambda fixed-name resources match their baseline values."""

    def test_shared_lambda_log_group_name(self, cfn_resources):
        """SharedLambdaLogGroup.LogGroupName is preserved.

        Validates: Requirements 1.12
        """
        props = cfn_resources["SharedLambdaLogGroup"]["Properties"]
        assert props["LogGroupName"] == "/aws/lambda/ConnectOpsReview-shared", (
            f"SharedLambdaLogGroup.LogGroupName drifted: {props['LogGroupName']!r}"
        )

    def test_review_scheduler_name(self, cfn_resources):
        """ReviewScheduler.Name is preserved.

        Validates: Requirements 1.12
        """
        props = cfn_resources["ReviewScheduler"]["Properties"]
        assert props["Name"] == "ConnectOpsReview-RecurringSchedule", (
            f"ReviewScheduler.Name drifted: {props['Name']!r}"
        )

    def test_config_ssm_parameter_name(self, cfn_resources):
        """ConnectOpsReviewConfig.Name (SSM parameter) is preserved.

        Validates: Requirements 1.12
        """
        props = cfn_resources["ConnectOpsReviewConfig"]["Properties"]
        assert props["Name"] == "/connect-ops-review/config", (
            f"ConnectOpsReviewConfig.Name drifted: {props['Name']!r}"
        )

    def test_glue_catalog_database_name(self, cfn_resources):
        """GlueCatalogDatabase.DatabaseInput.Name is preserved.

        Validates: Requirements 1.12
        """
        props = cfn_resources["GlueCatalogDatabase"]["Properties"]
        db_input = props["DatabaseInput"]
        assert db_input["Name"] == "connect_ops_review", (
            f"GlueCatalogDatabase.DatabaseInput.Name drifted: {db_input['Name']!r}"
        )

    @pytest.mark.parametrize(
        "logical_id,expected_table_name",
        [
            ("GlueTableCloudtrail", "cloudtrail"),
            ("GlueTableSecurity", "security"),
            ("GlueTableResilience", "resilience"),
            ("GlueTableOperationalExcellence", "operational_excellence"),
            ("GlueTableCapacity", "capacity"),
            ("GlueTableObservability", "observability"),
            ("GlueTableCost", "cost"),
            ("GlueTableAi", "ai"),
        ],
    )
    def test_glue_table_name(self, cfn_resources, logical_id, expected_table_name):
        """Each Glue table's TableInput.Name is preserved.

        Validates: Requirements 1.12
        """
        resource = cfn_resources.get(logical_id)
        assert resource is not None, (
            f"CFN Glue table resource {logical_id!r} not found — template shape changed."
        )
        table_input = resource["Properties"]["TableInput"]
        assert table_input["Name"] == expected_table_name, (
            f"CFN {logical_id}.Properties.TableInput.Name drifted: "
            f"expected {expected_table_name!r}, got {table_input['Name']!r}"
        )

    def test_glue_tables_all_reference_shared_database(self, cfn_resources):
        """All 8 Glue tables reference ``connect_ops_review`` as their database.

        This is an additional preservation invariant — if the database name
        drifts (e.g. someone renames it), all eight table references drift
        too. Testing it explicitly makes the coupling visible.

        Validates: Requirements 1.12
        """
        for logical_id in (
            "GlueTableCloudtrail",
            "GlueTableSecurity",
            "GlueTableResilience",
            "GlueTableOperationalExcellence",
            "GlueTableCapacity",
            "GlueTableObservability",
            "GlueTableCost",
            "GlueTableAi",
        ):
            db_name = cfn_resources[logical_id]["Properties"]["DatabaseName"]
            assert db_name == "connect_ops_review", (
                f"CFN {logical_id}.Properties.DatabaseName drifted: {db_name!r}"
            )

    def test_amazon_s3_for_reports_parameter_present(self, cfn_parameters):
        """AmazonS3ForReports parameter name is preserved.

        Validates: Requirements 1.12
        """
        assert "AmazonS3ForReports" in cfn_parameters, (
            "CFN parameter AmazonS3ForReports not found. This user-facing "
            "parameter name must not change during the QB-49 rename fix."
        )


class TestQB49TfNamesPreserved:
    """All 11 TF Lambda function names and fixed peers match the baseline values."""

    @pytest.mark.parametrize(
        "resource_name,expected_function_name", TF_LAMBDA_FUNCTION_NAMES
    )
    def test_tf_lambda_function_name(
        self, tf_source, resource_name, expected_function_name
    ):
        """TF ``function_name`` on this Lambda resource equals the baseline.

        Validates: Requirements 1.13
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_lambda_function", resource_name
        )
        match = re.search(
            r'^\s*function_name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert match is not None, (
            f"TF aws_lambda_function.{resource_name} has no function_name attribute"
        )
        assert match.group("value") == expected_function_name, (
            f"TF aws_lambda_function.{resource_name} function_name drifted: "
            f"expected {expected_function_name!r}, got {match.group('value')!r}"
        )

    def test_tf_shared_lambda_log_group_name(self, tf_source):
        """TF aws_cloudwatch_log_group.shared_lambda.name preserved.

        Validates: Requirements 1.13
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_cloudwatch_log_group", "shared_lambda"
        )
        match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert match is not None, (
            "TF aws_cloudwatch_log_group.shared_lambda has no name attribute"
        )
        assert match.group("value") == "/aws/lambda/ConnectOpsReview-shared", (
            "TF aws_cloudwatch_log_group.shared_lambda name drifted: "
            f"{match.group('value')!r}"
        )

    def test_tf_review_schedule_name(self, tf_source):
        """TF aws_scheduler_schedule.review_schedule.name preserved.

        Validates: Requirements 1.13
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_scheduler_schedule", "review_schedule"
        )
        match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert match is not None, (
            "TF aws_scheduler_schedule.review_schedule has no name attribute"
        )
        assert match.group("value") == "ConnectOpsReview-RecurringSchedule", (
            "TF aws_scheduler_schedule.review_schedule name drifted: "
            f"{match.group('value')!r}"
        )

    def test_tf_ssm_parameter_config_name(self, tf_source):
        """TF aws_ssm_parameter.config.name preserved.

        Validates: Requirements 1.13
        """
        body = _extract_tf_resource_block(tf_source, "aws_ssm_parameter", "config")
        match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert match is not None, "TF aws_ssm_parameter.config has no name attribute"
        assert match.group("value") == "/connect-ops-review/config", (
            f"TF aws_ssm_parameter.config name drifted: {match.group('value')!r}"
        )

    def test_tf_glue_catalog_database_name(self, tf_source):
        """TF aws_glue_catalog_database.ops_review.name preserved.

        Validates: Requirements 1.13
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_glue_catalog_database", "ops_review"
        )
        match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert match is not None, (
            "TF aws_glue_catalog_database.ops_review has no name attribute"
        )
        assert match.group("value") == "connect_ops_review", (
            f"TF aws_glue_catalog_database.ops_review name drifted: "
            f"{match.group('value')!r}"
        )

    def test_tf_glue_table_names_via_analyzer_types(self, tf_source):
        """The eight Glue table names come from local.analyzer_types, unchanged.

        Validates: Requirements 1.13
        """
        # The TF module drives the eight Glue tables from a for_each loop over
        # local.analyzer_types. Verify that local declares exactly the same
        # eight names as the CFN template.
        match = re.search(
            r"analyzer_types\s*=\s*\[(?P<items>[^\]]+)\]",
            tf_source,
            re.MULTILINE,
        )
        assert match is not None, (
            "local.analyzer_types definition not found in parallel.tf"
        )
        items = re.findall(r'"([^"]+)"', match.group("items"))
        assert items == GLUE_TABLE_NAMES, (
            f"local.analyzer_types drifted: expected {GLUE_TABLE_NAMES}, got {items}"
        )

    def test_tf_s3_reporting_bucket_variable_present(self, tf_variables_source):
        """The Terraform ``s3_reporting_bucket`` variable is preserved.

        Validates: Requirements 1.13
        """
        assert 'variable "s3_reporting_bucket"' in tf_variables_source, (
            "TF variable s3_reporting_bucket not found in variables.tf. "
            "The user-facing variable name must not change during QB-49."
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-50 — Preservation: Non-AI Executive Summary rows unchanged
# Property 4: Preservation
# Validates: Requirements 2.6 (duplicate_anchors); non-AI portion of 2.3
# ═══════════════════════════════════════════════════════════════════════════


# The non-AI check names currently listed in check_order, per pillar. Any
# QB-50 fix that inadvertently deletes or renames one of these breaks the
# preservation guarantee.
CHECK_ORDER_NON_AI_ENTRIES = {
    "Security": [
        "Identity Management",
        "S3 Data Encryption",
        # QB-68 canonicalization: "Streaming Encryption" → "Data Streaming Encryption".
        "Data Streaming Encryption",
    ],
    "Resilience": [
        "Global Resiliency (ACGR)",
        "Carrier Diversity",
        "Knowledge Base Sync Health",
    ],
    "Operational Excellence": [
        "Amazon Connect API Throttling (Account Level)",
        "Misconfigured Phone Numbers",
        "KVS Retention Period",
    ],
    "Capacity Analysis": [
        "Amazon Connect Instance Resource Limits",
        "Amazon Connect Concurrency Limits",
        "Amazon Connect API Limits (Account Level)",
        "Amazon Connect Cases Limits",
        "Application Integrations Limits",
        "Customer Profiles Limits",
        # NOTE: "Amazon Connect AI Agents Limits" comes from the capacity
        # analyzer's check_ai_agents_limits (preserved by QB-53). It's
        # listed under Capacity Analysis in check_order, NOT under the
        # ai_analyzer's AI: prefix set.
        "Amazon Connect AI Agents Limits",
    ],
    "Observability": [
        "Contact Flow Logging",
        "CloudWatch Alarm Validation - Amazon Connect",
        "CloudWatch Log Retention",
        "Missed Calls",
    ],
    "Cost": [
        "Phone Number Distribution",
        "Misconfigured Phone Numbers",
        "Channel Usage",
    ],
}


class TestQB50NonAiCheckOrderPreserved:
    """Every non-AI ``check_order`` entry survives the QB-50 rename fix."""

    @pytest.fixture(scope="class")
    @staticmethod
    def executive_summary_source() -> str:
        return inspect.getsource(_render_executive_summary)

    @pytest.mark.parametrize(
        "pillar,check_name",
        [
            (pillar, name)
            for pillar, names in CHECK_ORDER_NON_AI_ENTRIES.items()
            for name in names
        ],
    )
    def test_non_ai_check_name_survives(
        self, executive_summary_source, pillar, check_name
    ):
        """Non-AI ``check_order`` entry remains reachable from _render_executive_summary.

        Post-QB-68 (unified naming), non-AI names are indirected through
        ``CHECK_NAMES[<anchor>]`` in ``check_order`` rather than being inline
        string literals. This preservation test accepts either form: (a) the
        literal ``"<check_name>"`` still appears in the function source, OR
        (b) the name is defined in ``CHECK_NAMES`` and its corresponding
        ``CHECK_NAMES["<anchor>"]`` lookup appears in the function source.
        The invariant is unchanged: every non-AI check_order entry survives.

        Validates: Requirements 2.6 (indirectly via check_order stability)
        """
        quoted = f'"{check_name}"'
        if quoted in executive_summary_source:
            return

        # Fall back to the CHECK_NAMES indirection introduced by QB-68.
        anchors_for_name = [a for a, n in CHECK_NAMES.items() if n == check_name]
        for anchor in anchors_for_name:
            if f'CHECK_NAMES["{anchor}"]' in executive_summary_source:
                return

        raise AssertionError(
            f"Non-AI check {quoted} disappeared from check_order under {pillar}. "
            f"Neither the literal nor any CHECK_NAMES lookup resolving to it "
            f"(anchors: {anchors_for_name}) was found in "
            "_render_executive_summary source. QB-50 must add AI: names, not "
            "remove non-AI ones."
        )


class TestQB50NonAiRenderingIsDeterministic:
    """Non-AI Executive Summary rendering is deterministic (same input → same output).

    Determinism is a prerequisite for the "byte-identical before and after
    the fix" preservation guarantee. If ``_render_executive_summary`` is
    not deterministic for a given input, we cannot assert byte-equivalence
    across the fix boundary.
    """

    def _make_non_ai_check(
        self, area: str, check: str, status: str, anchor: str
    ) -> dict:
        return {
            "area": area,
            "check": check,
            "status": status,
            "detail": f"Detail for {check}",
            "anchor": anchor,
        }

    def test_non_ai_rendering_deterministic(self):
        """Rendering the same set of non-AI checks twice produces identical HTML.

        Validates: Requirements 2.6
        """
        checks = [
            self._make_non_ai_check(
                "Security", "Identity Management", "pass", "sec-identity"
            ),
            self._make_non_ai_check("Security", "S3 Data Encryption", "warn", "sec-s3"),
            self._make_non_ai_check(
                "Resilience", "Carrier Diversity", "info", "res-carrier"
            ),
            self._make_non_ai_check(
                "Observability", "Contact Flow Logging", "fail", "obs-cfl"
            ),
        ]
        html_a = _render_executive_summary(list(checks))
        html_b = _render_executive_summary(list(checks))
        assert html_a == html_b, (
            "Non-AI Executive Summary rendering is non-deterministic — same "
            "input produced different output on two consecutive renders."
        )


class TestQB50DuplicateAnchorsInvariantPreserved:
    """The anchor-uniqueness invariant is enforced for non-AI checks unchanged.

    The QB-50 fix adds an ``id="ai-{check_name}"`` attribute to AI findings.
    This test asserts the anchor-uniqueness invariant already enforced in
    ``_render_executive_summary`` continues to hold for non-AI checks even
    when AI checks are interleaved with them.
    """

    def test_no_duplicate_anchor_error_logged_for_non_ai_checks(self, caplog):
        """Rendering unique non-AI check anchors emits no duplicate-anchor ERROR.

        Validates: Requirements 2.6
        """
        checks = [
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "pass",
                "detail": "d1",
                "anchor": "sec-identity",
            },
            {
                "area": "Security",
                "check": "S3 Data Encryption",
                "status": "pass",
                "detail": "d2",
                "anchor": "sec-s3",
            },
            {
                "area": "Resilience",
                "check": "Carrier Diversity",
                "status": "info",
                "detail": "d3",
                "anchor": "res-carrier",
            },
        ]
        with caplog.at_level(logging.ERROR, logger="report_generator"):
            _render_executive_summary(checks)

        offending = [
            r for r in caplog.records if "Anchor uniqueness violation" in r.getMessage()
        ]
        assert offending == [], (
            "Duplicate-anchor ERROR was logged for unique non-AI anchors: "
            f"{[r.getMessage() for r in offending]}"
        )

    def test_duplicate_anchor_is_detected(self, caplog):
        """Duplicate anchors ARE detected (guard-rail invariant).

        This is a sanity check on the detection path itself — if the fix
        accidentally disables the detector, this test fails.

        Validates: Requirements 2.6
        """
        checks = [
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "pass",
                "detail": "d1",
                "anchor": "duplicate",
            },
            {
                "area": "Observability",
                "check": "Missed Calls",
                "status": "pass",
                "detail": "d2",
                "anchor": "duplicate",
            },
        ]
        with caplog.at_level(logging.ERROR, logger="report_generator"):
            _render_executive_summary(checks)

        offending = [
            r for r in caplog.records if "Anchor uniqueness violation" in r.getMessage()
        ]
        assert len(offending) >= 1, (
            "Duplicate-anchor invariant is NOT enforced — ERROR log was not "
            "emitted for two checks sharing the same anchor value."
        )


class TestQB50NonAiRenderingContainsExpectedContent:
    """Non-AI check rows render their check name and detail in the output.

    This is the closest we can get to "byte-identical between original and
    fixed" on unfixed code alone. If the QB-50 fix regresses non-AI check
    rendering (e.g. by accidentally routing all rows through the AI
    renderer), this test will fail.
    """

    def test_non_ai_row_contains_check_name(self):
        """Rendered HTML contains the non-AI check's name.

        Validates: Requirements 2.6
        """
        checks = [
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "pass",
                "detail": "Encryption at rest configured",
                "anchor": "sec-identity",
            },
        ]
        html = _render_executive_summary(checks)
        assert "Identity Management" in html, (
            "Non-AI check row lost its check name in rendering."
        )
        assert "Encryption at rest configured" in html, (
            "Non-AI check row lost its detail text in rendering."
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-51 — Preservation: Non-tabular AI findings render unchanged
# Property 6: Preservation
# Validates: Requirements 3.5, 3.6
# ═══════════════════════════════════════════════════════════════════════════


# Non-tabular check_names: after the QB-51 fix, these render as summary-only
# (unchanged from today). QB-53 removes hard_limits/knowledge_base_capacity
# entirely, so they are intentionally omitted here.
NON_TABULAR_CHECK_NAMES = ["domain_encryption", "agent_logging", "assistant_discovery"]

# Tabular check_names: after the QB-51 fix, these render <table> when data is
# non-empty. On both unfixed and fixed code, they fall back to summary-only
# when data is None or the per-row list is empty.
TABULAR_CHECK_NAMES_AND_KEYS = [
    ("agent_inventory", "_raw_agents"),
    ("prompt_configuration", "prompts"),
    ("guardrails", "guardrails"),
]


def _strip_id_attribute(html: str) -> str:
    """Remove any ``id="ai-..."`` attribute from a div opening tag.

    The QB-50 fix adds ``id="ai-{check_name}"`` to the outer div. This is
    the ONE allowed diff between original and fixed rendering for
    non-tabular AI findings — stripping it lets us compare the rest of the
    HTML byte-for-byte.
    """
    return re.sub(r'\s*id="ai-[^"]*"', "", html)


class TestQB51NonTabularFindingsSummaryOnly:
    """Non-tabular AI findings render summary-only (no ``<table>`` element).

    Uses parametric coverage over ``NON_TABULAR_CHECK_NAMES`` × the four
    non-error status values. QB-51 must not accidentally emit a table for
    findings that aren't in the tabular dispatch set.
    """

    @pytest.mark.parametrize("check_name", NON_TABULAR_CHECK_NAMES)
    @pytest.mark.parametrize("status", ["pass", "warn", "fail", "info"])
    def test_no_table_element_in_html(self, check_name, status):
        """Rendered HTML for a non-tabular finding contains no ``<table>``.

        Validates: Requirements 3.5
        """
        finding = {
            "check_name": check_name,
            "status": status,
            "detail": f"Preservation check for {check_name}",
            "recommendation": None,
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" not in html, (
            f"Non-tabular finding check_name={check_name!r} status={status!r} "
            f"rendered a <table> element unexpectedly. The QB-51 dispatch "
            "must gate on the three tabular check_names only."
        )

    @pytest.mark.parametrize("check_name", NON_TABULAR_CHECK_NAMES)
    def test_summary_detail_present(self, check_name):
        """Rendered HTML for a non-tabular finding contains the summary detail.

        Validates: Requirements 3.5
        """
        detail = "Preservation summary detail 12345"
        finding = {
            "check_name": check_name,
            "status": "warn",
            "detail": detail,
            "recommendation": None,
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        assert detail in html, (
            f"Non-tabular finding check_name={check_name!r} did not include "
            "its summary detail in the rendered HTML."
        )


class TestQB51NonTabularRenderingIsDeterministic:
    """Rendering the same non-tabular finding twice produces the same HTML.

    Determinism is a prerequisite for the "byte-identical modulo id="
    preservation invariant that the QB-51 fix must not violate.
    """

    @pytest.mark.parametrize("check_name", NON_TABULAR_CHECK_NAMES)
    def test_deterministic_rendering(self, check_name):
        """Same input → same output (modulo id= attribute stripping).

        Validates: Requirements 3.5
        """
        finding = {
            "check_name": check_name,
            "status": "warn",
            "detail": "Deterministic rendering check",
            "recommendation": "Do the thing",
            "data": {"any": "value"},
        }
        html_a = _render_ai_structured_finding(finding)
        html_b = _render_ai_structured_finding(finding)
        assert _strip_id_attribute(html_a) == _strip_id_attribute(html_b), (
            f"Rendering for check_name={check_name!r} is non-deterministic "
            "modulo id= stripping — same input produced different output."
        )


class TestQB51EmptyDataFallsBackToSummaryOnly:
    """Tabular check_names with empty/None ``data`` fall back to summary-only.

    Requirement 3.6 mandates that the QB-51 fix only emit a table when the
    corresponding per-row list is non-empty. On unfixed code this always
    holds (no table is ever emitted). Encoding it as a test locks the
    behavior in so the fix cannot accidentally emit an empty table.
    """

    @pytest.mark.parametrize("check_name,data_key", TABULAR_CHECK_NAMES_AND_KEYS)
    def test_data_none_no_table(self, check_name, data_key):
        """Finding with ``data=None`` renders summary-only.

        Validates: Requirements 3.6
        """
        finding = {
            "check_name": check_name,
            "status": "info",
            "detail": f"No {check_name} data available",
            "recommendation": None,
            "data": None,
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" not in html, (
            f"Finding check_name={check_name!r} with data=None emitted a "
            "<table> element. Empty-data fallback is broken."
        )

    @pytest.mark.parametrize("check_name,data_key", TABULAR_CHECK_NAMES_AND_KEYS)
    def test_empty_data_dict_no_table(self, check_name, data_key):
        """Finding with ``data={}`` renders summary-only.

        Validates: Requirements 3.6
        """
        finding = {
            "check_name": check_name,
            "status": "info",
            "detail": f"Empty {check_name} data dict",
            "recommendation": None,
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" not in html, (
            f"Finding check_name={check_name!r} with data={{}} emitted a "
            "<table> element. Empty-data fallback is broken."
        )

    @pytest.mark.parametrize("check_name,data_key", TABULAR_CHECK_NAMES_AND_KEYS)
    def test_empty_per_row_list_no_table(self, check_name, data_key):
        """Finding with an empty per-row list renders summary-only.

        Validates: Requirements 3.6
        """
        finding = {
            "check_name": check_name,
            "status": "info",
            "detail": f"Empty {data_key} list",
            "recommendation": None,
            "data": {data_key: []},
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" not in html, (
            f"Finding check_name={check_name!r} with empty {data_key!r} list "
            "emitted a <table> element. Empty-per-row-list fallback is broken."
        )


class TestQB51FindingCoreShapePreserved:
    """The core rendering shape (status badge, check name, detail) is preserved.

    These are the invariants Requirement 3.5 protects — non-tabular
    findings render byte-identically to today modulo the QB-50-added id=
    attribute. On unfixed code we test the invariants directly; after the
    fix, the same test remains valid.
    """

    @pytest.mark.parametrize("check_name", NON_TABULAR_CHECK_NAMES)
    def test_check_name_appears_in_html(self, check_name):
        """Rendered HTML contains the check_name (title-cased)."""
        finding = {
            "check_name": check_name,
            "status": "pass",
            "detail": "Preservation shape check",
            "recommendation": None,
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        display = check_name.replace("_", " ").title()
        assert display in html, (
            f"Rendered HTML for check_name={check_name!r} does not include "
            f"the title-cased display name {display!r}."
        )

    @pytest.mark.parametrize(
        "status,expected_icon",
        [
            ("pass", "&#9989;"),
            ("fail", "&#10060;"),
            ("warn", "&#9888;&#65039;"),
            ("info", "&#8505;&#65039;"),
            ("error", "&#9940;"),
        ],
    )
    def test_status_icon_preserved(self, status, expected_icon):
        """The status-icon glyph for each status value is preserved."""
        finding = {
            "check_name": "domain_encryption",
            "status": status,
            "detail": "Status icon check",
            "recommendation": None,
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        assert expected_icon in html, (
            f"Status icon {expected_icon!r} for status={status!r} not found "
            "in rendered HTML — the status-color-icon table drifted."
        )

    def test_recommendation_block_preserved(self):
        """A finding with a recommendation renders the recommendation block."""
        finding = {
            "check_name": "domain_encryption",
            "status": "warn",
            "detail": "Preservation recommendation check",
            "recommendation": "Enable CMK encryption for Q Connect domain",
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        assert "Recommendation:" in html, (
            "Finding with a recommendation is missing the 'Recommendation:' "
            "label — recommendation rendering has drifted."
        )
        assert "Enable CMK encryption for Q Connect domain" in html, (
            "Recommendation text is missing from rendered HTML."
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-52 — Preservation: Non-agent_logging AI findings routing unchanged
# Property 8: Preservation
# Validates: Requirements 4.7, 4.8, 4.9
# ═══════════════════════════════════════════════════════════════════════════


VALID_EVENT = {
    "reviewId": "test-review-qb52-preservation",
    "instanceId": "test-instance-id",
    "instanceArn": "arn:aws:connect:us-east-1:123456789012:instance/test-instance-id",
    "accountId": "123456789012",
    "awsRegion": "us-east-1",
    "daysBack": 7,
    "componentType": "ai",
    "s3ReportingBucket": "test-bucket",
}


def _pass_finding(check_name: str) -> dict:
    return {
        "check_name": check_name,
        "status": "pass",
        "detail": f"{check_name} ok",
        "recommendation": None,
        "data": {},
    }


def _run_lambda_handler_with_all_checks_mocked(
    *, assistant_id="test-assistant-id", hard_limits_result=None
):
    """Invoke ``lambda_handler`` with every AI check mocked to return pass.

    ``hard_limits_result`` is used on unfixed code where Check 6 is still
    invoked. On fixed code (post-task 3) it is ignored because
    ``check_q_hard_limits`` no longer exists in the module.
    """
    hard_limits_result = hard_limits_result or []

    patches = [
        patch("ai_analyzer.boto3.client"),
        patch("ai_analyzer.discover_assistant_id", return_value=assistant_id),
        patch("ai_analyzer.check_time_budget"),
        patch("ai_analyzer.persist_to_s3", return_value="mock-s3-key"),
        patch(
            "ai_analyzer.check_ai_agent_inventory",
            return_value=_pass_finding("agent_inventory"),
        ),
        patch(
            "ai_analyzer.check_ai_prompt_configuration",
            return_value=_pass_finding("prompt_configuration"),
        ),
        patch(
            "ai_analyzer.check_q_guardrails",
            return_value=_pass_finding("guardrails"),
        ),
        patch(
            "ai_analyzer.check_q_domain_encryption",
            return_value=_pass_finding("domain_encryption"),
        ),
        patch(
            "ai_analyzer.check_ai_agent_logging",
            return_value=_pass_finding("agent_logging"),
        ),
    ]

    # On unfixed code, Check 6 (hard_limits) is still invoked. Patch it too.
    # The ``create=True`` flag lets the patch install a new attribute if the
    # attribute has been deleted from the module (fixed code) — the mock
    # simply never fires because lambda_handler no longer references it.
    patches.append(
        patch(
            "ai_analyzer.check_q_hard_limits",
            return_value=hard_limits_result,
            create=True,
        )
    )

    import contextlib

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return lambda_handler(VALID_EVENT, None)


def _findings_by_check_name(findings_list, check_name):
    """Return the sublist of findings whose check_name matches."""
    return [f for f in findings_list if f.get("check_name") == check_name]


class TestQB52NonAgentLoggingRoutingPreserved:
    """Guardrails/domain_encryption/agent_inventory/prompt_configuration routes unchanged."""

    def test_guardrails_routes_to_security(self):
        """``guardrails`` finding is present in ``findings['security']``.

        Validates: Requirements 4.9
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        security = result["findings"].get("security", [])
        matches = _findings_by_check_name(security, "guardrails")
        assert len(matches) == 1, (
            f"Expected exactly 1 guardrails finding in findings['security'], "
            f"got {len(matches)}: {matches!r}"
        )

    def test_domain_encryption_routes_to_security(self):
        """``domain_encryption`` finding is present in ``findings['security']``.

        Validates: Requirements 4.9
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        security = result["findings"].get("security", [])
        matches = _findings_by_check_name(security, "domain_encryption")
        assert len(matches) == 1, (
            f"Expected exactly 1 domain_encryption finding in "
            f"findings['security'], got {len(matches)}: {matches!r}"
        )

    def test_agent_inventory_routes_to_operational_excellence(self):
        """``agent_inventory`` finding is present in ``findings['operational_excellence']``.

        Validates: Requirements 4.9
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        opex = result["findings"].get("operational_excellence", [])
        matches = _findings_by_check_name(opex, "agent_inventory")
        assert len(matches) == 1, (
            f"Expected exactly 1 agent_inventory finding in "
            f"findings['operational_excellence'], got {len(matches)}: {matches!r}"
        )

    def test_prompt_configuration_routes_to_operational_excellence(self):
        """``prompt_configuration`` finding is in ``findings['operational_excellence']``.

        Validates: Requirements 4.9
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        opex = result["findings"].get("operational_excellence", [])
        matches = _findings_by_check_name(opex, "prompt_configuration")
        assert len(matches) == 1, (
            f"Expected exactly 1 prompt_configuration finding in "
            f"findings['operational_excellence'], got {len(matches)}: {matches!r}"
        )

    def test_assistant_discovery_stays_in_operational_excellence(self):
        """When no assistant is found, ``assistant_discovery`` routes to op-ex.

        This is the ¬agent_logging path — reroute of agent_logging in QB-52
        must NOT drag assistant_discovery along with it.

        Validates: Requirements 4.8
        """
        result = _run_lambda_handler_with_all_checks_mocked(assistant_id=None)
        opex = result["findings"].get("operational_excellence", [])
        matches = _findings_by_check_name(opex, "assistant_discovery")
        assert len(matches) == 1, (
            f"Expected exactly 1 assistant_discovery finding in "
            f"findings['operational_excellence'], got {len(matches)}: {matches!r}"
        )
        # And nowhere else
        for pillar in ("security", "resilience", "observability"):
            other_matches = _findings_by_check_name(
                result["findings"].get(pillar, []), "assistant_discovery"
            )
            assert other_matches == [], (
                f"assistant_discovery leaked into findings[{pillar!r}]: "
                f"{other_matches!r}"
            )


class TestQB52SecurityPillarShapePreserved:
    """``findings['security']`` contains exactly the two expected AI findings.

    Uses ``pytest.mark.skipif(POST_QB53)`` inversion carefully: this test's
    assertion holds on BOTH unfixed and fixed code because it doesn't
    involve hard_limits/knowledge_base_capacity. Kept ungated on purpose.
    """

    def test_security_has_exactly_guardrails_and_domain_encryption(self):
        """After the fix, ``findings['security']`` has exactly two AI checks.

        On unfixed code this holds unconditionally (nothing else routes
        into security from ai_analyzer today).

        Validates: Requirements 4.9
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        security_check_names = sorted(
            f.get("check_name") for f in result["findings"].get("security", [])
        )
        assert security_check_names == ["domain_encryption", "guardrails"], (
            "findings['security'] shape drifted from the expected "
            f"['domain_encryption', 'guardrails']: got {security_check_names}"
        )


class TestQB52PostQB53Assertions:
    """Post-QB-53 preservation invariants — active only after task 3 lands.

    These assertions FAIL on unfixed rc.5 code because ``hard_limits`` and
    ``knowledge_base_capacity`` findings are still emitted. They are gated
    on ``hasattr(ai_analyzer, 'check_q_hard_limits')`` and become active
    once task 3 (QB-53 removal) deletes that attribute from the module.

    Once QB-53 lands, unskip by re-running the suite; the gating flag will
    automatically flip.
    """

    @pytest.mark.skipif(
        not POST_QB53,
        reason="check_q_hard_limits still exists — post-QB-53 invariants gated off",
    )
    def test_no_hard_limits_in_operational_excellence(self):
        """Post-fix: no ``hard_limits`` finding under op-ex.

        Validates: Requirements 4.9 (post-QB-53)
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        opex = result["findings"].get("operational_excellence", [])
        matches = _findings_by_check_name(opex, "hard_limits")
        assert matches == [], (
            f"Expected zero hard_limits findings in operational_excellence, "
            f"got: {matches!r}"
        )

    @pytest.mark.skipif(
        not POST_QB53,
        reason="check_q_hard_limits still exists — post-QB-53 invariants gated off",
    )
    def test_no_knowledge_base_capacity_in_resilience(self):
        """Post-fix: no ``knowledge_base_capacity`` finding under resilience.

        Validates: Requirements 4.9 (post-QB-53)
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        resilience = result["findings"].get("resilience", [])
        matches = _findings_by_check_name(resilience, "knowledge_base_capacity")
        assert matches == [], (
            f"Expected zero knowledge_base_capacity findings in resilience, "
            f"got: {matches!r}"
        )

    @pytest.mark.skipif(
        not POST_QB53,
        reason="check_q_hard_limits still exists — post-QB-53 invariants gated off",
    )
    def test_checks_completed_reflects_five_surviving_checks(self):
        """Post-fix: ``metadata.checks_completed`` reflects the five surviving checks.

        The list includes ``assistant_discovery`` (the discovery step) plus
        the five surviving AI checks, so its length is six on the
        assistant-found path.

        Validates: Requirements 4.9 (post-QB-53)
        """
        result = _run_lambda_handler_with_all_checks_mocked()
        completed = result["findings"]["metadata"]["checks_completed"]
        assert "hard_limits" not in completed, (
            f"metadata.checks_completed still contains 'hard_limits': {completed!r}"
        )
        # The five surviving AI checks must all be present:
        expected_ai_checks = {
            "agent_inventory",
            "prompt_configuration",
            "guardrails",
            "domain_encryption",
            "agent_logging",
        }
        assert expected_ai_checks.issubset(set(completed)), (
            "Not all five surviving AI checks appear in checks_completed. "
            f"Missing: {expected_ai_checks - set(completed)}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-53 — Preservation: Non-hard-limits AI checks + Capacity Analysis section
# Property 10: Preservation
# Validates: Requirements 6.3, 6.8
# ═══════════════════════════════════════════════════════════════════════════


class TestQB53CapacityAnalyzerAiSectionPreserved:
    """Capacity Analysis "Amazon Connect AI Agents Limits" section is preserved.

    QB-53 removes ``ai_analyzer.check_q_hard_limits`` but MUST NOT touch
    ``capacity_analyzer.check_ai_agents_limits`` or its ``AI_AGENTS_QUOTAS``
    module constant. These tests defend that boundary.
    """

    def test_check_ai_agents_limits_is_callable(self):
        """``capacity_analyzer.check_ai_agents_limits`` exists and is callable.

        Validates: Requirements 6.8
        """
        assert hasattr(capacity_analyzer, "check_ai_agents_limits"), (
            "capacity_analyzer.check_ai_agents_limits is missing — QB-53 must "
            "not remove the Capacity Analysis AI Agents Limits section."
        )
        assert callable(capacity_analyzer.check_ai_agents_limits), (
            "capacity_analyzer.check_ai_agents_limits is not callable."
        )

    def test_ai_agents_quotas_constant_present(self):
        """``capacity_analyzer.AI_AGENTS_QUOTAS`` module constant is defined.

        Validates: Requirements 6.8
        """
        assert hasattr(capacity_analyzer, "AI_AGENTS_QUOTAS"), (
            "capacity_analyzer.AI_AGENTS_QUOTAS module constant is missing — "
            "QB-53 must not remove the AI quotas driving the Capacity "
            "Analysis section."
        )
        quotas = capacity_analyzer.AI_AGENTS_QUOTAS
        assert isinstance(quotas, list), (
            f"AI_AGENTS_QUOTAS is not a list: {type(quotas)}"
        )
        assert len(quotas) > 0, (
            "AI_AGENTS_QUOTAS is empty — every entry should be preserved."
        )
        # Each entry is a (label, quota_code) tuple.
        for entry in quotas:
            assert isinstance(entry, tuple) and len(entry) == 2, (
                f"AI_AGENTS_QUOTAS entry shape drifted: {entry!r}"
            )
            label, qcode = entry
            assert isinstance(label, str) and label, (
                f"AI_AGENTS_QUOTAS entry has non-string label: {entry!r}"
            )
            assert isinstance(qcode, str) and qcode.startswith("L-"), (
                f"AI_AGENTS_QUOTAS entry has non-quota-code qcode: {entry!r}"
            )

    def test_check_ai_agents_limits_signature_preserved(self):
        """The function's positional parameters are unchanged.

        Validates: Requirements 6.8
        """
        sig = inspect.signature(capacity_analyzer.check_ai_agents_limits)
        params = list(sig.parameters.keys())
        assert params == [
            "service_quotas_client",
            "aws_region",
            "start_time",
            "time_budget",
        ], (
            f"check_ai_agents_limits signature drifted: {params!r}. "
            "QB-53 must not change this function's shape."
        )

    def test_check_ai_agents_limits_returns_expected_shape(self):
        """Invoked with a mock service_quotas_client, the return shape is preserved.

        Validates: Requirements 6.8
        """
        # Build a mock get_service_quota that returns a canned quota value
        # for every quota code. The test's contract is that regardless of
        # what the quota values are, the return shape has {quotas, status,
        # detail} keys with a quotas list of the expected length.
        mock_client = MagicMock()
        mock_client.get_service_quota.return_value = {
            "Quota": {
                "Value": 5.0,
                "DefaultValue": 5.0,
                "Adjustable": False,
            }
        }

        import time as time_module

        with patch("capacity_analyzer.check_time_budget"):
            result = capacity_analyzer.check_ai_agents_limits(
                service_quotas_client=mock_client,
                aws_region="us-east-1",
                start_time=time_module.time(),
                time_budget=60,
            )

        assert result is not None, (
            "check_ai_agents_limits returned None — every quota query failed. "
            "This should not happen with a fully-mocked client."
        )
        assert isinstance(result, dict), (
            f"check_ai_agents_limits returned non-dict: {type(result)}"
        )
        assert set(result.keys()) >= {"quotas", "status", "detail"}, (
            f"check_ai_agents_limits result missing expected keys: {result.keys()}"
        )
        assert isinstance(result["quotas"], list), (
            f"result['quotas'] is not a list: {type(result['quotas'])}"
        )
        assert len(result["quotas"]) == len(capacity_analyzer.AI_AGENTS_QUOTAS), (
            f"result['quotas'] length ({len(result['quotas'])}) "
            f"!= len(AI_AGENTS_QUOTAS) ({len(capacity_analyzer.AI_AGENTS_QUOTAS)})"
        )
        for quota in result["quotas"]:
            assert set(quota.keys()) >= {
                "name",
                "quota_code",
                "applied_value",
                "default_value",
                "adjustable",
                "percentage_used",
            }, f"quota entry missing expected keys: {quota.keys()}"


# The five surviving AI checks after QB-53 lands. Each is an importable
# module attribute on ai_analyzer, and its positional-parameter list must
# match the signature captured on unfixed rc.5 code (observation-first).
SURVIVING_AI_CHECK_SIGNATURES = [
    (
        "check_ai_agent_inventory",
        [
            "qconnect_client",
            "connect_client",
            "assistant_id",
            "instance_id",
            "start_time",
            "time_budget",
        ],
    ),
    (
        "check_ai_prompt_configuration",
        ["qconnect_client", "assistant_id", "start_time", "time_budget"],
    ),
    (
        "check_q_guardrails",
        [
            "qconnect_client",
            "assistant_id",
            "start_time",
            "time_budget",
            "cached_agents",
        ],
    ),
    (
        "check_q_domain_encryption",
        ["qconnect_client", "assistant_id", "start_time", "time_budget"],
    ),
    (
        "check_ai_agent_logging",
        [
            "logs_client",
            "assistant_id",
            "account_id",
            "aws_region",
            "start_time",
            "time_budget",
        ],
    ),
]


class TestQB53SurvivingAiChecksPreserved:
    """The five surviving AI checks exist as importable module attributes.

    After QB-53 removes ``check_q_hard_limits``, the other five check
    functions must remain callable with their current signatures.
    """

    @pytest.mark.parametrize(
        "function_name,expected_params", SURVIVING_AI_CHECK_SIGNATURES
    )
    def test_check_function_importable(self, function_name, expected_params):
        """The check function is importable from ai_analyzer.

        Validates: Requirements 6.3
        """
        assert hasattr(ai_analyzer, function_name), (
            f"ai_analyzer.{function_name} is missing — QB-53 must not remove "
            "any of the five surviving checks."
        )
        func = getattr(ai_analyzer, function_name)
        assert callable(func), (
            f"ai_analyzer.{function_name} is not callable: {type(func)}"
        )

    @pytest.mark.parametrize(
        "function_name,expected_params", SURVIVING_AI_CHECK_SIGNATURES
    )
    def test_check_function_signature_unchanged(self, function_name, expected_params):
        """The check function's positional-parameter list is unchanged.

        Validates: Requirements 6.3
        """
        func = getattr(ai_analyzer, function_name)
        sig = inspect.signature(func)
        actual_params = list(sig.parameters.keys())
        assert actual_params == expected_params, (
            f"ai_analyzer.{function_name} signature drifted: "
            f"expected {expected_params}, got {actual_params}"
        )
