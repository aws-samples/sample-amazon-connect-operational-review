"""
CFT Property Tests — Lambda Naming, Layer Content, and Observability Functions

Consolidated test module that validates both the bugfix conditions (layer builder
naming, ANALYZER_COMMON_PY completeness, observability check functions) AND
preservation properties (existing Lambda names, original utility functions,
graceful_timeout embedding).

Validates: Requirements 2.1–2.8, 3.1–3.5
"""

import ast
import os

import pytest
import yaml

# Reuse the CFN-tag-tolerant YAML loader from the rc4 test helpers so we do not
# duplicate the intrinsic-function-tag constructor list here.
from rc4_iac_parsers import CFNLoader

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CFT_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "deploy",
        "cloudformation",
        "CFT-AmazonConnectOperationsReview.yml",
    )
)

OBSERVABILITY_ANALYZER_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "observability_analyzer.py")
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cft_resources():
    """Parse CFT YAML and return the Resources section."""
    with open(CFT_PATH, "r") as f:
        template = yaml.load(f, Loader=CFNLoader)
    return template["Resources"]


@pytest.fixture(scope="module")
def shared_utils_layer_builder_code(cft_resources):
    """Extract the ZipFile code from SharedUtilsLayerBuilder."""
    resource = cft_resources["SharedUtilsLayerBuilder"]
    return resource["Properties"]["Code"]["ZipFile"]


@pytest.fixture(scope="module")
def observability_analyzer_functions():
    """Parse observability_analyzer.py with AST and return top-level function names."""
    with open(OBSERVABILITY_ANALYZER_PATH, "r") as f:
        source = f.read()
    tree = ast.parse(source)
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


# ---------------------------------------------------------------------------
# Test: Layer Builder Lambda Naming (Bug Condition - Bug 1)
# Validates: Requirements 2.1, 2.2
# ---------------------------------------------------------------------------


class TestLayerBuilderNaming:
    """
    Verifies that layer builder Lambdas have explicit FunctionName properties
    matching the ConnectOpsReview- naming convention.
    """

    def test_boto3_layer_builder_has_function_name(self, cft_resources):
        """Boto3LayerBuilder has FunctionName: ConnectOpsReview-Boto3LayerBuilder."""
        props = cft_resources["Boto3LayerBuilder"]["Properties"]
        assert props.get("FunctionName") == "ConnectOpsReview-Boto3LayerBuilder", (
            f"Expected 'ConnectOpsReview-Boto3LayerBuilder', "
            f"got '{props.get('FunctionName')}'"
        )

    def test_shared_utils_layer_builder_has_function_name(self, cft_resources):
        """SharedUtilsLayerBuilder has FunctionName: ConnectOpsReview-SharedUtilsLayerBuilder."""
        props = cft_resources["SharedUtilsLayerBuilder"]["Properties"]
        assert (
            props.get("FunctionName") == "ConnectOpsReview-SharedUtilsLayerBuilder"
        ), (
            f"Expected 'ConnectOpsReview-SharedUtilsLayerBuilder', "
            f"got '{props.get('FunctionName')}'"
        )


# ---------------------------------------------------------------------------
# Test: Existing Lambda FunctionNames Preserved (Preservation)
# Validates: Requirements 3.1
# ---------------------------------------------------------------------------


class TestExistingLambdaNames:
    """All 9 existing analyzer Lambdas keep their ConnectOpsReview- FunctionNames."""

    EXPECTED_LAMBDA_NAMES = {
        "PrepareContextFunction": "ConnectOpsReview-PrepareContext",
        "SecurityAnalyzerFunction": "ConnectOpsReview-SecurityAnalyzer",
        "ResilienceAnalyzerFunction": "ConnectOpsReview-ResilienceAnalyzer",
        "CloudTrailAnalyzerParallelFunction": "ConnectOpsReview-CloudTrailAnalyzer",
        "OpExAnalyzerFunction": "ConnectOpsReview-OpExAnalyzer",
        "CapacityAnalyzerFunction": "ConnectOpsReview-CapacityAnalyzer",
        "ObservabilityAnalyzerFunction": "ConnectOpsReview-ObservabilityAnalyzer",
        "CostAnalyzerFunction": "ConnectOpsReview-CostAnalyzer",
        "AIAnalyzerFunction": "ConnectOpsReview-AIAnalyzer",
    }

    @pytest.mark.parametrize(
        "logical_name,expected_function_name", EXPECTED_LAMBDA_NAMES.items()
    )
    def test_existing_lambda_function_name_unchanged(
        self, cft_resources, logical_name, expected_function_name
    ):
        """Each existing analyzer Lambda preserves its ConnectOpsReview- FunctionName."""
        props = cft_resources[logical_name]["Properties"]
        assert props.get("FunctionName") == expected_function_name, (
            f"{logical_name}: expected '{expected_function_name}', "
            f"got '{props.get('FunctionName')}'"
        )


# ---------------------------------------------------------------------------
# Test: ANALYZER_COMMON_PY Content (Bug Condition - Bug 3)
# Validates: Requirements 2.5, 2.6, 2.7
# ---------------------------------------------------------------------------


class TestAnalyzerCommonContent:
    """
    Verifies ANALYZER_COMMON_PY contains all required functions (both new
    additions from Bug 3 fix and original preserved functions).
    """

    # New functions added by Bug 3 fix
    NEW_FUNCTIONS = [
        "discover_assistant_id",
        "paginate_api_call",
        "paginate_qconnect",
        "_parse_assistant_id_from_arn",
    ]

    # Original functions that must be preserved
    ORIGINAL_FUNCTIONS = [
        "validate_input",
        "success_result",
        "error_result",
        "persist_to_s3",
        "read_shared_data",
    ]

    @pytest.mark.parametrize("func_name", NEW_FUNCTIONS + ORIGINAL_FUNCTIONS)
    def test_function_present_in_layer(
        self, shared_utils_layer_builder_code, func_name
    ):
        """Required function exists in the ANALYZER_COMMON_PY constant."""
        assert f"def {func_name}" in shared_utils_layer_builder_code, (
            f"ANALYZER_COMMON_PY is missing 'def {func_name}'"
        )


# ---------------------------------------------------------------------------
# Test: Build Version (Bug Condition - Bug 3)
# Validates: Requirements 2.8
# ---------------------------------------------------------------------------


class TestBuildVersion:
    """SharedUtilsLayerCustomResource BuildVersion forces layer rebuild."""

    def test_build_version_is_3(self, cft_resources):
        """BuildVersion must match the current deployed version to force layer rebuild after content changes."""
        props = cft_resources["SharedUtilsLayerCustomResourceV2"]["Properties"]
        assert props.get("BuildVersion") == "10", (
            f"Expected BuildVersion '10', got '{props.get('BuildVersion')}'"
        )


# ---------------------------------------------------------------------------
# Test: Graceful Timeout Embedding (Preservation)
# Validates: Requirements 3.4
# ---------------------------------------------------------------------------


class TestGracefulTimeout:
    """graceful_timeout.py is still embedded in SharedUtilsLayerBuilder."""

    def test_graceful_timeout_in_layer_builder(self, shared_utils_layer_builder_code):
        """SharedUtilsLayerBuilder ZipFile writes graceful_timeout.py to the layer."""
        assert "graceful_timeout.py" in shared_utils_layer_builder_code, (
            "SharedUtilsLayerBuilder no longer writes 'graceful_timeout.py' file"
        )


# ---------------------------------------------------------------------------
# Test: ReviewScheduler Target Input Narrowed (Bug Condition - RC6 Fix 4)
# Validates: Requirements R5.5
# ---------------------------------------------------------------------------


class TestReviewSchedulerTargetInput:
    """
    The CFN ReviewScheduler.Target.Input JSON payload must contain only
    'instanceArn' and 's3ReportingBucket'. It MUST NOT include the removed
    'retainJsonData' or 'generateHtmlReport' fields, which the orchestrator
    Step Functions state machine no longer accepts.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def scheduler_input(cft_resources):
        """Return the !Sub-rendered Input string for ReviewScheduler."""
        target = cft_resources["ReviewScheduler"]["Properties"]["Target"]
        input_value = target["Input"]
        # !Sub with a single-argument scalar is loaded as a string by CFNLoader
        assert isinstance(input_value, str), (
            f"Expected ReviewScheduler.Target.Input to be a single-arg !Sub scalar "
            f"(string), got {type(input_value).__name__}: {input_value!r}"
        )
        return input_value

    def test_input_contains_instance_arn(self, scheduler_input):
        """Target.Input references instanceArn."""
        assert "instanceArn" in scheduler_input, (
            "ReviewScheduler.Target.Input is missing 'instanceArn'"
        )

    def test_input_contains_s3_reporting_bucket(self, scheduler_input):
        """Target.Input references s3ReportingBucket."""
        assert "s3ReportingBucket" in scheduler_input, (
            "ReviewScheduler.Target.Input is missing 's3ReportingBucket'"
        )

    def test_input_does_not_contain_retain_json_data(self, scheduler_input):
        """Target.Input must NOT reference the removed retainJsonData field."""
        assert "retainJsonData" not in scheduler_input, (
            "ReviewScheduler.Target.Input still contains 'retainJsonData', which "
            "the orchestrator state machine no longer accepts"
        )

    def test_input_does_not_contain_generate_html_report(self, scheduler_input):
        """Target.Input must NOT reference the removed generateHtmlReport field."""
        assert "generateHtmlReport" not in scheduler_input, (
            "ReviewScheduler.Target.Input still contains 'generateHtmlReport', which "
            "the orchestrator state machine no longer accepts"
        )


# ---------------------------------------------------------------------------
# Test: Observability Analyzer Functions (Bug Condition - Bug 4 + Preservation)
# Validates: Requirements 2.5, 3.5
# ---------------------------------------------------------------------------


class TestObservabilityFunctions:
    """
    Verifies observability_analyzer.py has both new check functions (Bug 4 fix)
    and preserves all original check functions.
    """

    # New functions from Bug 4 fix
    NEW_CHECKS = [
        "validate_kinesis_stream_alarms",
    ]

    # Original functions that must be preserved
    ORIGINAL_CHECKS = [
        "validate_connect_alarms",
        "check_missed_calls_metrics",
        "check_connect_log_groups",
    ]

    @pytest.mark.parametrize("func_name", NEW_CHECKS + ORIGINAL_CHECKS)
    def test_observability_function_exists(
        self, observability_analyzer_functions, func_name
    ):
        """Required observability check function exists in observability_analyzer.py."""
        assert func_name in observability_analyzer_functions, (
            f"observability_analyzer.py is missing '{func_name}' function"
        )


# ---------------------------------------------------------------------------
# Test: SSM Config Seed Preservation (RC6 Fix 4 — R4.4)
# Validates: Requirements R4.4
#
# Companion to TestReviewSchedulerTargetInput above. The rc.7 fix strips
# retainJsonData / generateHtmlReport from the scheduler input, but the SSM
# config seed (ConnectOpsReviewConfig) is the post-deploy control surface and
# MUST continue to carry both keys, still derived from the deploy-time CFN
# parameters. Proves the fix moves the surface without changing the seed.
# ---------------------------------------------------------------------------


class TestSSMConfigSeedPreservation:
    """
    The CFN ConnectOpsReviewConfig SSM parameter's Value must still contain
    both `retainJsonData` and `generateHtmlReport` keys, still derived from
    the deploy-time CFN parameters (RetainJsonData / EnableHtmlReport) via
    the BooleanMap FindInMap lookup.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def ssm_value(cft_resources):
        """Return the two-arg !Sub Value for ConnectOpsReviewConfig.

        The Value is a !Sub with a sequence: [template_string, mapping_dict].
        CFNLoader constructs !Sub-with-sequence as a Python list.
        """
        props = cft_resources["ConnectOpsReviewConfig"]["Properties"]
        value = props["Value"]
        assert isinstance(value, list), (
            f"Expected ConnectOpsReviewConfig.Value to be a two-arg !Sub "
            f"(sequence -> list), got {type(value).__name__}: {value!r}"
        )
        assert len(value) == 2, (
            f"Expected two-arg !Sub with [template, mapping], got {len(value)} args"
        )
        return value

    @pytest.fixture(scope="class")
    @staticmethod
    def ssm_template(ssm_value):
        """The template string (first arg of !Sub)."""
        template = ssm_value[0]
        assert isinstance(template, str), (
            f"Expected !Sub template arg to be a string, got {type(template).__name__}"
        )
        return template

    @pytest.fixture(scope="class")
    @staticmethod
    def ssm_mapping(ssm_value):
        """The variable mapping dict (second arg of !Sub)."""
        mapping = ssm_value[1]
        assert isinstance(mapping, dict), (
            f"Expected !Sub mapping arg to be a dict, got {type(mapping).__name__}"
        )
        return mapping

    def test_ssm_value_contains_retain_json_data_key(self, ssm_template):
        """ConnectOpsReviewConfig.Value template still carries retainJsonData."""
        assert "retainJsonData" in ssm_template, (
            "ConnectOpsReviewConfig.Value is missing 'retainJsonData' — "
            "the rc.7 fix must move the surface (scheduler input) WITHOUT "
            "changing the SSM seed"
        )

    def test_ssm_value_contains_generate_html_report_key(self, ssm_template):
        """ConnectOpsReviewConfig.Value template still carries generateHtmlReport."""
        assert "generateHtmlReport" in ssm_template, (
            "ConnectOpsReviewConfig.Value is missing 'generateHtmlReport' — "
            "the rc.7 fix must move the surface (scheduler input) WITHOUT "
            "changing the SSM seed"
        )

    def test_ssm_value_derives_retain_json_data_from_cfn_parameter(self, ssm_mapping):
        """RetainJsonData_Bool maps to a FindInMap that references the CFN
        RetainJsonData parameter — deploy-time control preserved."""
        assert "RetainJsonData_Bool" in ssm_mapping, (
            "ConnectOpsReviewConfig !Sub mapping is missing 'RetainJsonData_Bool' "
            "— deploy-time parameter wiring for retainJsonData is broken"
        )
        # !FindInMap is loaded by CFNLoader as a sequence -> list
        find_in_map = ssm_mapping["RetainJsonData_Bool"]
        assert isinstance(find_in_map, list), (
            f"Expected RetainJsonData_Bool to be a !FindInMap sequence, "
            f"got {type(find_in_map).__name__}: {find_in_map!r}"
        )
        # !FindInMap [BooleanMap, !Ref RetainJsonData, Value]
        assert find_in_map[0] == "BooleanMap", (
            f"RetainJsonData_Bool !FindInMap first arg must be 'BooleanMap', "
            f"got {find_in_map[0]!r}"
        )
        assert find_in_map[1] == "RetainJsonData", (
            f"RetainJsonData_Bool !FindInMap must reference the RetainJsonData "
            f"CFN parameter (via !Ref), got {find_in_map[1]!r}"
        )
        assert find_in_map[2] == "Value", (
            f"RetainJsonData_Bool !FindInMap third arg must be 'Value', "
            f"got {find_in_map[2]!r}"
        )

    def test_ssm_value_derives_generate_html_report_from_cfn_parameter(
        self, ssm_mapping
    ):
        """GenerateHtmlReport_Bool maps to a FindInMap that references the CFN
        EnableHtmlReport parameter — deploy-time control preserved."""
        assert "GenerateHtmlReport_Bool" in ssm_mapping, (
            "ConnectOpsReviewConfig !Sub mapping is missing "
            "'GenerateHtmlReport_Bool' — deploy-time parameter wiring for "
            "generateHtmlReport is broken"
        )
        find_in_map = ssm_mapping["GenerateHtmlReport_Bool"]
        assert isinstance(find_in_map, list), (
            f"Expected GenerateHtmlReport_Bool to be a !FindInMap sequence, "
            f"got {type(find_in_map).__name__}: {find_in_map!r}"
        )
        assert find_in_map[0] == "BooleanMap", (
            f"GenerateHtmlReport_Bool !FindInMap first arg must be 'BooleanMap', "
            f"got {find_in_map[0]!r}"
        )
        assert find_in_map[1] == "EnableHtmlReport", (
            f"GenerateHtmlReport_Bool !FindInMap must reference the "
            f"EnableHtmlReport CFN parameter (via !Ref), got {find_in_map[1]!r}"
        )
        assert find_in_map[2] == "Value", (
            f"GenerateHtmlReport_Bool !FindInMap third arg must be 'Value', "
            f"got {find_in_map[2]!r}"
        )


# ---------------------------------------------------------------------------
# Test: Glue projection.year.range future-proofed (RC6 Fix 6)
# Validates: Requirements R6.1, R6.2, R6.3, R6.4
# ---------------------------------------------------------------------------


class TestGlueYearRangeFutureProofed:
    """
    Every AWS::Glue::Table resource's
    TableInput.Parameters["projection.year.range"] upper bound (the number
    after the comma) must be >= 2100. Guards against the partition-projection
    window closing at 2025-12-31 and silently hiding new data.
    """

    MIN_UPPER_BOUND = 2100

    @pytest.fixture(scope="class")
    @staticmethod
    def glue_tables(cft_resources):
        """All AWS::Glue::Table resources in the CFN template."""
        tables = {
            name: res
            for name, res in cft_resources.items()
            if res.get("Type") == "AWS::Glue::Table"
        }
        assert tables, "No AWS::Glue::Table resources found in CFN template"
        return tables

    def test_at_least_one_glue_table_present(self, glue_tables):
        """Sanity check: the CFN template defines Glue tables to validate."""
        assert len(glue_tables) >= 1, (
            "Expected at least one AWS::Glue::Table resource in the CFN template"
        )

    def test_every_glue_table_year_range_upper_bound_is_future_proof(self, glue_tables):
        """Every Glue table's projection.year.range upper bound is >= 2100."""
        violations = []
        for logical_name, resource in glue_tables.items():
            params = (
                resource.get("Properties", {})
                .get("TableInput", {})
                .get("Parameters", {})
            )
            year_range = params.get("projection.year.range")
            if year_range is None:
                violations.append(
                    f"{logical_name}: missing 'projection.year.range' parameter"
                )
                continue
            parts = [p.strip() for p in str(year_range).split(",")]
            if len(parts) != 2:
                violations.append(
                    f"{logical_name}: projection.year.range='{year_range}' is not "
                    f"a comma-separated pair"
                )
                continue
            try:
                upper = int(parts[1])
            except ValueError:
                violations.append(
                    f"{logical_name}: projection.year.range upper bound "
                    f"'{parts[1]}' is not an integer"
                )
                continue
            if upper < self.MIN_UPPER_BOUND:
                violations.append(
                    f"{logical_name}: projection.year.range upper bound "
                    f"{upper} < {self.MIN_UPPER_BOUND}"
                )
        assert not violations, (
            "Glue tables with a projection.year.range upper bound below "
            f"{self.MIN_UPPER_BOUND}:\n  " + "\n  ".join(violations)
        )


# ---------------------------------------------------------------------------
# R5 / CFT-QB-26 — Parameter label typo (Connnect → Connect)
# ---------------------------------------------------------------------------


def test_no_connnect_typo_in_cft_template():
    """CFN template must not contain the 'Connnect' (three n's) typo.

    Validates: Requirements R5.1, R5.2
    """
    with open(CFT_PATH, encoding="utf-8") as f:
        template_text = f.read()
    assert "Connnect" not in template_text, (
        "CFN template contains 'Connnect' (three n's) typo — must be 'Connect'"
    )
