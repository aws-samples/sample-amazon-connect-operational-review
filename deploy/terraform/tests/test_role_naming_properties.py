"""
Role Naming Property Tests — Preservation

This module asserts two preservation invariants for the shared Lambda role:

1. TestCfnSharedLambdaRolePreservation: the CFN template retains a
   `SharedLambdaRole` logical resource of type AWS::IAM::Role, and CFN
   analyzer Lambdas reference it via `!GetAtt SharedLambdaRole.Arn`.
2. TestTerraformLambdaRoleReferencesPreservation: every Terraform Lambda
   function references the role via `aws_iam_role.shared_lambda.arn` (by
   resource ID, not a hardcoded name string), so role renames do not break
   Lambda role wiring.

Note: the bug-condition test that asserted `aws_iam_role.shared_lambda` uses
a `name_prefix` containing "SharedLambdaRole" has been retired — the QB-49
fix (v2.0.1-rc.6) switched from `name_prefix` to a fixed
`name = "ConnectOpsReview-SharedLambdaRole-<region>"`. That invariant is now
covered by `src/lambda/tests/test_pbt_rc5_ganesh_feedback.py::TestQB49TfNames`
and `test_pbt_rc5_ganesh_feedback_preservation.py::TestQB49TfNamesPreserved`.

Validates: Requirements 3.11, 3.12, 3.13
"""

import re

import pytest

from _helpers import extract_lambda_function_blocks


# ---------------------------------------------------------------------------
# Test: CloudFormation SharedLambdaRole Logical Resource Preserved
# Validates: Requirements 3.11
# ---------------------------------------------------------------------------


class TestCfnSharedLambdaRolePreservation:
    """
    Asserts that the CloudFormation template has a logical resource named
    `SharedLambdaRole` of type `AWS::IAM::Role`. This ensures the CFN naming
    convention that produces `ConnectOpsReview-SharedLambdaRole-<hash>` is
    preserved.

    **Validates: Requirements 3.11**
    """

    def test_cfn_has_shared_lambda_role_logical_resource(self, cft_content: str):
        """CloudFormation template must define a `SharedLambdaRole` resource.

        The logical resource name `SharedLambdaRole` directly contributes to the
        physical name pattern `ConnectOpsReview-SharedLambdaRole-<hash>` via
        CloudFormation's naming convention (StackName-LogicalResourceId-Hash).
        Removing or renaming this resource would break the naming convention.
        """
        has_shared_lambda_role = bool(
            re.search(
                r'^\s*SharedLambdaRole:\s*$',
                cft_content,
                re.MULTILINE,
            )
        )
        assert has_shared_lambda_role, (
            "PRESERVATION VIOLATION: CloudFormation template is missing the "
            "`SharedLambdaRole` logical resource — this breaks the naming "
            "convention `ConnectOpsReview-SharedLambdaRole-<hash>`"
        )

    def test_cfn_shared_lambda_role_is_iam_role_type(self, cft_content: str):
        """SharedLambdaRole must be of type AWS::IAM::Role.

        This verifies the resource hasn't been accidentally changed to a
        different resource type which would alter naming behavior.
        """
        match = re.search(
            r'SharedLambdaRole:\s*\n\s*Type:\s*(\S+)',
            cft_content,
        )
        assert match is not None, (
            "PRESERVATION VIOLATION: Cannot find Type declaration for "
            "SharedLambdaRole in CloudFormation template"
        )
        resource_type = match.group(1)
        assert resource_type == "AWS::IAM::Role", (
            f"PRESERVATION VIOLATION: SharedLambdaRole has unexpected type "
            f"'{resource_type}' — expected 'AWS::IAM::Role'"
        )

    def test_cfn_analyzer_lambdas_reference_shared_lambda_role(self, cft_content: str):
        """CFN analyzer Lambda functions must reference SharedLambdaRole.Arn.

        The analyzer Lambda functions (those with SharedUtilsLayer) all use the
        shared role. This confirms the naming convention is consistently applied
        to all operational review analyzer functions.
        """
        shared_role_refs = re.findall(
            r'Role:\s*!GetAtt\s+SharedLambdaRole\.Arn',
            cft_content,
        )
        assert len(shared_role_refs) >= 3, (
            f"PRESERVATION VIOLATION: Expected at least 3 CFN Lambda functions "
            f"referencing SharedLambdaRole.Arn, found {len(shared_role_refs)} — "
            "analyzer functions may have been disconnected from the shared role"
        )


# ---------------------------------------------------------------------------
# Test: Terraform Lambda Functions Reference Role by Resource ID
# Validates: Requirements 3.12, 3.13
# ---------------------------------------------------------------------------


class TestTerraformLambdaRoleReferencesPreservation:
    """
    Asserts that ALL Terraform Lambda function resources reference the IAM
    role via `aws_iam_role.shared_lambda.arn` (Terraform resource ID), NOT
    a hardcoded name string.

    This is critical because:
    - When `name_prefix` changes, the role is replaced (destroy + create)
    - Terraform resource ID references automatically track the new ARN
    - Hardcoded name strings would break on role replacement

    **Validates: Requirements 3.12, 3.13**
    """

    def test_all_lambda_functions_have_role_attribute(self, parallel_tf_content: str):
        """Every aws_lambda_function resource must have a role attribute defined.

        A Lambda function without a role attribute would fail to deploy.
        """
        lambda_blocks = extract_lambda_function_blocks(parallel_tf_content)
        assert lambda_blocks, (
            "No aws_lambda_function resources found in parallel.tf"
        )
        assert len(lambda_blocks) >= 1, (
            "Expected at least one Lambda function resource in parallel.tf"
        )

    def test_all_lambda_roles_use_resource_id_reference(self, parallel_tf_content: str):
        """Every Lambda function role must use `aws_iam_role.shared_lambda.arn`.

        Lambda functions must reference the IAM role by Terraform resource ID
        (not a hardcoded ARN string or literal role name). This ensures role
        references automatically track any name changes without manual updates.
        """
        lambda_blocks = extract_lambda_function_blocks(parallel_tf_content)
        assert lambda_blocks, (
            "No aws_lambda_function resources found in parallel.tf"
        )

        expected_role_ref = "aws_iam_role.shared_lambda.arn"
        non_compliant = []

        for block in lambda_blocks:
            role_value = block["role"]
            if expected_role_ref not in role_value:
                non_compliant.append(
                    f"  - aws_lambda_function.{block['name']}: "
                    f"role = {role_value}"
                )

        assert not non_compliant, (
            "PRESERVATION VIOLATION: The following Lambda functions do NOT reference "
            "the role via `aws_iam_role.shared_lambda.arn` — they may break on role "
            "replacement:\n" + "\n".join(non_compliant)
        )

    def test_shared_lambda_role_resource_exists(self, parallel_tf_content: str):
        """Terraform must define aws_iam_role.shared_lambda resource.

        This resource is what all Lambda functions reference. If it's removed
        or renamed, all Lambda role references will break.
        """
        has_shared_lambda_role = bool(
            re.search(
                r'resource\s+"aws_iam_role"\s+"shared_lambda"\s*\{',
                parallel_tf_content,
            )
        )
        assert has_shared_lambda_role, (
            "PRESERVATION VIOLATION: aws_iam_role.shared_lambda resource not "
            "found in parallel.tf — all Lambda function role references will break"
        )
