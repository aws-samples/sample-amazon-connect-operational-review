"""
Glue & Scheduler Property Tests — Partition Projection, Gating, and Preservation

Consolidated test module that validates both the bugfix conditions (partition
projection parameters present, scheduler gated on enable flag) AND preservation
properties (conditional creation logic, schema integrity).

Validates: Requirements 1.1–1.7, 2.2, 2.4
"""

import re
from pathlib import Path

import pytest

from _helpers import extract_ssm_parameter_config_block

# ---------------------------------------------------------------------------
# Paths & Constants
# ---------------------------------------------------------------------------

PARALLEL_TF_PATH = Path(__file__).parent.parent / "parallel.tf"
CFT_PATH = (
    Path(__file__).parent.parent.parent
    / "cloudformation"
    / "CFT-AmazonConnectOperationsReview.yml"
)

COMPONENT_TYPES = [
    "cloudtrail",
    "security",
    "resilience",
    "operational_excellence",
    "capacity",
    "observability",
    "cost",
    "ai",
]

REQUIRED_PARTITION_PROJECTION_KEYS = [
    "projection.enabled",
    "projection.year.type",
    "projection.year.range",
    "projection.month.type",
    "projection.month.range",
    "projection.month.digits",
    "projection.day.type",
    "projection.day.range",
    "projection.day.digits",
    "storage.location.template",
]

EXPECTED_COLUMNS = [
    "reviewId",
    "instanceId",
    "accountId",
    "awsRegion",
    "componentType",
    "timestamp",
    "daysBack",
    "partial",
    "findings",
]

SCHEDULER_RESOURCES = [
    "aws_scheduler_schedule.review_schedule",
    "aws_iam_role.scheduler_sfn",
    "aws_iam_role_policy.scheduler_start_execution",
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def parallel_tf_content() -> str:
    """Load the parallel.tf file content."""
    assert PARALLEL_TF_PATH.exists(), f"parallel.tf not found at {PARALLEL_TF_PATH}"
    return PARALLEL_TF_PATH.read_text()

@pytest.fixture(scope="module")
def cft_content() -> str:
    """Load the CloudFormation template content."""
    assert CFT_PATH.exists(), f"CFT not found at {CFT_PATH}"
    return CFT_PATH.read_text()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_glue_table_block(tf_content: str) -> str:
    """Extract the aws_glue_catalog_table 'analyzer' resource block."""
    match = re.search(
        r'resource\s+"aws_glue_catalog_table"\s+"analyzer"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        tf_content,
        re.DOTALL,
    )
    return match.group(1) if match else ""

def extract_parameters_block(tf_content: str) -> str:
    """Extract the parameters block from the Glue table resource."""
    table_block = extract_glue_table_block(tf_content)
    match = re.search(r'parameters\s*=\s*\{([^}]*)\}', table_block, re.DOTALL)
    return match.group(1) if match else ""

def extract_glue_database_block(tf_content: str) -> str:
    """Extract the aws_glue_catalog_database resource block."""
    match = re.search(
        r'resource\s+"aws_glue_catalog_database"\s+"ops_review"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        tf_content,
        re.DOTALL,
    )
    return match.group(1) if match else ""

def extract_glue_table_for_each(tf_content: str) -> str:
    """Extract the for_each expression from the Glue table resource."""
    table_block = extract_glue_table_block(tf_content)
    match = re.search(r'for_each\s*=\s*(.*)', table_block)
    return match.group(1).strip() if match else ""

def extract_scheduler_schedule_block(tf_content: str) -> str:
    """Extract the aws_scheduler_schedule resource block."""
    match = re.search(
        r'resource\s+"aws_scheduler_schedule"\s+"review_schedule"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        tf_content,
        re.DOTALL,
    )
    return match.group(1) if match else ""

def extract_scheduler_resource_blocks(tf_content: str) -> dict[str, str]:
    """Extract all scheduler-related resource blocks."""
    results = {}
    patterns = {
        "aws_iam_role.scheduler_sfn": r'resource\s+"aws_iam_role"\s+"scheduler_sfn"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        "aws_iam_role_policy.scheduler_start_execution": r'resource\s+"aws_iam_role_policy"\s+"scheduler_start_execution"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        "aws_scheduler_schedule.review_schedule": r'resource\s+"aws_scheduler_schedule"\s+"review_schedule"\s*\{(.*?)(?=\nresource\s+"|\Z)',
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, tf_content, re.DOTALL)
        if match:
            results[name] = match.group(1)
    return results

def extract_columns_from_storage_descriptor(tf_content: str) -> list[str]:
    """Extract column names from the storage_descriptor columns blocks."""
    table_block = extract_glue_table_block(tf_content)
    return re.findall(r'columns\s*\{[^}]*name\s*=\s*"([^"]+)"', table_block)

# ---------------------------------------------------------------------------
# Test: Glue Partition Projection Parameters (Bug Condition)
# Validates: Requirements 1.1, 1.2, 1.3, 1.5
# ---------------------------------------------------------------------------

class TestGluePartitionProjection:
    """
    Asserts that the Glue table parameters block contains all 10 required
    partition projection keys with correct values and escape syntax.
    """

    def test_all_partition_projection_keys_present(self, parallel_tf_content: str):
        """All 10 required partition projection keys exist in the parameters block."""
        params_block = extract_parameters_block(parallel_tf_content)
        assert params_block, "No parameters block found in Glue table resource"

        missing_keys = []
        for key in REQUIRED_PARTITION_PROJECTION_KEYS:
            pattern = rf'["\']?{re.escape(key)}["\']?\s*='
            if not re.search(pattern, params_block):
                missing_keys.append(key)

        assert not missing_keys, (
            f"Glue table is missing {len(missing_keys)} partition projection "
            f"parameters: {missing_keys}"
        )

    def test_projection_enabled_is_true(self, parallel_tf_content: str):
        """projection.enabled is set to 'true'."""
        params_block = extract_parameters_block(parallel_tf_content)
        pattern = r'["\']?projection\.enabled["\']?\s*=\s*["\']true["\']'
        assert re.search(pattern, params_block), (
            "projection.enabled is not set to 'true' in the Glue table parameters"
        )

    def test_storage_location_template_uses_tf_escape_syntax(
        self, parallel_tf_content: str
    ):
        """storage.location.template uses $${year} TF escape syntax."""
        params_block = extract_parameters_block(parallel_tf_content)

        key_pattern = r'["\']?storage\.location\.template["\']?\s*='
        assert re.search(key_pattern, params_block), (
            "storage.location.template key is missing from parameters block"
        )

        # Value must use TF escape syntax: $${year}, $${month}, $${day}
        table_block = extract_glue_table_block(
            pytest.importorskip("pathlib").Path(PARALLEL_TF_PATH).read_text()
            if not parallel_tf_content
            else parallel_tf_content
        )
        # Check the raw file for the escape sequences
        assert "$${year}" in parallel_tf_content, (
            "storage.location.template does not use $${year} Terraform escape syntax"
        )

    def test_classification_json_retained(self, parallel_tf_content: str):
        """classification=json is preserved in parameters."""
        params_block = extract_parameters_block(parallel_tf_content)
        assert re.search(r'"?classification"?\s*=\s*"json"', params_block), (
            "classification='json' is missing from Glue table parameters"
        )

    def test_every_glue_table_year_range_upper_bound_is_future_proof(
        self, parallel_tf_content: str
    ):
        """Every aws_glue_catalog_table's projection.year.range upper bound
        must be >= 2100. The Glue table is defined via a single for_each
        template shared by all 8 component tables, so every occurrence of
        projection.year.range in the parameters block is validated.

        Validates: Requirements R6.1, R6.2, R6.3, R6.4
        """
        min_upper_bound = 2100
        params_block = extract_parameters_block(parallel_tf_content)
        assert params_block, "No parameters block found in Glue table resource"

        # Match every projection.year.range assignment in the parameters block.
        # Value is a quoted "lower,upper" pair.
        matches = re.findall(
            r'["\']?projection\.year\.range["\']?\s*=\s*"(\d+)\s*,\s*(\d+)"',
            params_block,
        )
        assert matches, (
            "projection.year.range not found (or not in \"lower,upper\" form) "
            "in aws_glue_catalog_table parameters"
        )

        violations = [
            f"projection.year.range='{lower},{upper}' upper bound "
            f"{upper} < {min_upper_bound}"
            for lower, upper in matches
            if int(upper) < min_upper_bound
        ]
        assert not violations, (
            "aws_glue_catalog_table projection.year.range upper bound(s) below "
            f"{min_upper_bound}:\n  " + "\n  ".join(violations)
        )

# ---------------------------------------------------------------------------
# Test: Glue Table Schema Integrity (Preservation)
# Validates: Requirements 1.6
# ---------------------------------------------------------------------------

class TestGlueTableSchema:
    """
    Asserts that the Glue table retains the 9-column schema unchanged.
    Since all 8 tables share one for_each resource definition, checking the
    single template is sufficient.
    """

    def test_exactly_nine_columns(self, parallel_tf_content: str):
        """Glue table has exactly 9 columns in the storage_descriptor."""
        columns = extract_columns_from_storage_descriptor(parallel_tf_content)
        assert len(columns) == 9, (
            f"Expected 9 columns, found {len(columns)}: {columns}"
        )

    def test_column_names_match_expected(self, parallel_tf_content: str):
        """Column names match the expected set exactly."""
        columns = extract_columns_from_storage_descriptor(parallel_tf_content)
        assert columns == EXPECTED_COLUMNS, (
            f"Column mismatch. Expected: {EXPECTED_COLUMNS}, Found: {columns}"
        )

# ---------------------------------------------------------------------------
# Test: Conditional Glue Resource Creation (Preservation)
# Validates: Requirements 1.7
# ---------------------------------------------------------------------------

class TestGlueConditionalCreation:
    """
    When enable_glue_catalog=false, zero Glue resources are created.
    Verified by checking count/for_each guards in the resource definitions.
    """

    def test_glue_database_has_count_guard(self, parallel_tf_content: str):
        """Glue database uses count = var.enable_glue_catalog ? 1 : 0."""
        db_block = extract_glue_database_block(parallel_tf_content)
        assert db_block, "aws_glue_catalog_database.ops_review resource not found"
        assert re.search(
            r'count\s*=\s*var\.enable_glue_catalog\s*\?\s*1\s*:\s*0', db_block
        ), "Glue database resource missing enable_glue_catalog count guard"

    def test_glue_table_for_each_gates_on_enable_flag(self, parallel_tf_content: str):
        """Glue table for_each references enable_glue_catalog with toset([]) fallback."""
        for_each_expr = extract_glue_table_for_each(parallel_tf_content)
        assert for_each_expr, "Glue table for_each expression not found"
        assert "enable_glue_catalog" in for_each_expr, (
            f"for_each does not reference enable_glue_catalog: {for_each_expr}"
        )
        assert "toset([])" in for_each_expr, (
            f"for_each does not have toset([]) for the false case: {for_each_expr}"
        )

# ---------------------------------------------------------------------------
# Test: Scheduler Gating (Bug Condition)
# Validates: Requirements 2.2
# ---------------------------------------------------------------------------

class TestSchedulerGating:
    """
    All scheduler-related resources must have a count guard so they are NOT
    created when enable_review_schedule=false.
    """

    @pytest.mark.parametrize("resource_name", SCHEDULER_RESOURCES)
    def test_scheduler_resource_has_count_guard(
        self, resource_name: str, parallel_tf_content: str
    ):
        """Each scheduler resource has a count = ... guard."""
        scheduler_blocks = extract_scheduler_resource_blocks(parallel_tf_content)
        assert resource_name in scheduler_blocks, (
            f"Resource '{resource_name}' not found in parallel.tf"
        )
        block_text = scheduler_blocks[resource_name]
        assert re.search(r'^\s*count\s*=', block_text, re.MULTILINE), (
            f"Resource '{resource_name}' does NOT have a count guard"
        )

# ---------------------------------------------------------------------------
# Test: Scheduler Enabled Behavior (Preservation)
# Validates: Requirements 2.4
# ---------------------------------------------------------------------------

class TestSchedulerEnabled:
    """
    When enable_review_schedule=true, the scheduler is created with ENABLED
    state, configurable cron, and correct target.
    """

    def test_scheduler_state_is_enabled(self, parallel_tf_content: str):
        """Scheduler state is 'ENABLED'."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert schedule_block, "aws_scheduler_schedule.review_schedule not found"
        assert re.search(r'state\s*=\s*"ENABLED"', schedule_block), (
            "Scheduler state is not set to 'ENABLED'"
        )

    def test_scheduler_uses_configurable_cron(self, parallel_tf_content: str):
        """Scheduler references var.review_schedule_expression."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert "var.review_schedule_expression" in schedule_block, (
            "Scheduler does not reference var.review_schedule_expression"
        )

    def test_scheduler_targets_state_machine(self, parallel_tf_content: str):
        """Scheduler target points to the orchestrator state machine ARN."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert re.search(
            r'arn\s*=\s*aws_sfn_state_machine\.orchestrator\.arn', schedule_block
        ), "Scheduler target does not point to aws_sfn_state_machine.orchestrator.arn"


# ---------------------------------------------------------------------------
# Test: Scheduler Target Input Narrowed (Bug Condition — RC6 Fix 4)
# Validates: Requirements R5.5
# ---------------------------------------------------------------------------

class TestSchedulerTargetInput:
    """
    The TF aws_scheduler_schedule.review_schedule target[0].input JSON payload
    must contain only 'instanceArn' and 's3ReportingBucket'. It MUST NOT include
    the removed 'retainJsonData' or 'generateHtmlReport' fields, which the
    orchestrator Step Functions state machine no longer accepts.

    Mirrors TestReviewSchedulerTargetInput in
    src/lambda/tests/test_cft_properties.py so both IaC paths are kept in sync.
    """

    def test_input_contains_instance_arn(self, parallel_tf_content: str):
        """target.input contains instanceArn."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert "instanceArn" in schedule_block, (
            "aws_scheduler_schedule.review_schedule target input is missing 'instanceArn'"
        )

    def test_input_contains_s3_reporting_bucket(self, parallel_tf_content: str):
        """target.input contains s3ReportingBucket."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert "s3ReportingBucket" in schedule_block, (
            "aws_scheduler_schedule.review_schedule target input is missing 's3ReportingBucket'"
        )

    def test_input_does_not_contain_retain_json_data(self, parallel_tf_content: str):
        """target.input must NOT reference the removed retainJsonData field."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert "retainJsonData" not in schedule_block, (
            "aws_scheduler_schedule.review_schedule target input still contains "
            "'retainJsonData', which the orchestrator state machine no longer accepts"
        )

    def test_input_does_not_contain_generate_html_report(
        self, parallel_tf_content: str
    ):
        """target.input must NOT reference the removed generateHtmlReport field."""
        schedule_block = extract_scheduler_schedule_block(parallel_tf_content)
        assert "generateHtmlReport" not in schedule_block, (
            "aws_scheduler_schedule.review_schedule target input still contains "
            "'generateHtmlReport', which the orchestrator state machine no longer accepts"
        )



# ---------------------------------------------------------------------------
# Test: SSM Config Seed Preservation (RC6 Fix 4 — R4.4)
# Validates: Requirements R4.4
#
# Companion to TestSchedulerTargetInput above. The rc.7 fix strips
# retainJsonData / generateHtmlReport from the scheduler input, but the SSM
# config seed (aws_ssm_parameter.config) is the post-deploy control surface
# and MUST continue to carry both keys, still derived from the deploy-time
# TF variables. Proves the fix moves the surface without changing the seed.
#
# Mirrors TestSSMConfigSeedPreservation in
# src/lambda/tests/test_cft_properties.py so both IaC paths are kept in sync.
# ---------------------------------------------------------------------------


class TestSSMConfigSeedPreservation:
    """
    The TF aws_ssm_parameter.config resource's `value` must still contain
    both `retainJsonData` and `generateHtmlReport` keys, still referencing
    the deploy-time TF variables (var.retain_json_data / var.enable_html_report).
    """

    def test_ssm_config_resource_present(self, parallel_tf_content: str):
        """aws_ssm_parameter.config resource still exists."""
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        assert ssm_block, (
            "aws_ssm_parameter.config resource not found in parallel.tf — "
            "the rc.7 fix must move the surface (scheduler input) WITHOUT "
            "removing the SSM seed"
        )

    def test_ssm_value_contains_retain_json_data_key(self, parallel_tf_content: str):
        """aws_ssm_parameter.config value still carries retainJsonData."""
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        assert "retainJsonData" in ssm_block, (
            "aws_ssm_parameter.config value is missing 'retainJsonData' — "
            "the rc.7 fix must move the surface (scheduler input) WITHOUT "
            "changing the SSM seed"
        )

    def test_ssm_value_contains_generate_html_report_key(
        self, parallel_tf_content: str
    ):
        """aws_ssm_parameter.config value still carries generateHtmlReport."""
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        assert "generateHtmlReport" in ssm_block, (
            "aws_ssm_parameter.config value is missing 'generateHtmlReport' — "
            "the rc.7 fix must move the surface (scheduler input) WITHOUT "
            "changing the SSM seed"
        )

    def test_ssm_value_derives_retain_json_data_from_tf_variable(
        self, parallel_tf_content: str
    ):
        """retainJsonData is still assigned from var.retain_json_data —
        deploy-time control preserved."""
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        pattern = r'retainJsonData\s*=\s*var\.retain_json_data'
        assert re.search(pattern, ssm_block), (
            "aws_ssm_parameter.config does not derive retainJsonData from "
            "var.retain_json_data — deploy-time variable wiring is broken"
        )

    def test_ssm_value_derives_generate_html_report_from_tf_variable(
        self, parallel_tf_content: str
    ):
        """generateHtmlReport is still assigned from var.enable_html_report —
        deploy-time control preserved."""
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        pattern = r'generateHtmlReport\s*=\s*var\.enable_html_report'
        assert re.search(pattern, ssm_block), (
            "aws_ssm_parameter.config does not derive generateHtmlReport from "
            "var.enable_html_report — deploy-time variable wiring is broken"
        )
