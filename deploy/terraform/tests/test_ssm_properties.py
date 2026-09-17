"""
SSM Parameter Property Tests — Bug Condition and Preservation

This module combines two concerns for the SSM parameter configuration:

1. Bug Condition (TestSSMRetentionBugCondition): Asserts that SSM parameters
   do NOT have retention policies that block clean teardown/redeploy. On unfixed
   code, these tests FAIL — confirming the bug exists. Once fixed, they PASS.

2. Preservation (TestTerraformIgnoreChangesPreservation, TestPrepareContextFallbackPreservation):
   Asserts that existing preservation behaviors remain intact — Terraform
   ignore_changes = [value] ensures manual SSM edits persist, and
   prepare_context.py gracefully falls back to DEFAULT_CONFIG on failure.

Validates: Requirements 2.1, 2.2, 2.3, 3.1, 3.2, 3.3
"""

import ast
import re

import pytest

from _helpers import extract_lifecycle_block, extract_ssm_parameter_config_block


# ---------------------------------------------------------------------------
# Test: SSM Retain Policy Blocks Redeploy (Bug Condition)
# Validates: Requirements 2.1, 2.2, 2.3
#
# These tests assert the DESIRED state (no retention policies).
# On unfixed code they FAIL — confirming the bug exists.
# ---------------------------------------------------------------------------


class TestSSMRetentionBugCondition:
    """
    Asserts that SSM parameter resources do NOT have retention policies
    that would block clean teardown and redeploy.

    **Validates: Requirements 2.1, 2.2, 2.3**
    """

    def test_cft_no_deletion_policy_retain(self, cft_content: str):
        """ConnectOpsReviewConfig must NOT have DeletionPolicy: Retain.

        The retain policy causes the SSM parameter to persist after stack
        deletion, blocking subsequent redeploys with ParameterAlreadyExists.
        """
        match = re.search(
            r'ConnectOpsReviewConfig:\s*\n'
            r'\s*Type:\s*AWS::SSM::Parameter\s*\n'
            r'\s*DeletionPolicy:\s*(\w+)',
            cft_content,
        )
        has_deletion_policy_retain = match and match.group(1) == "Retain"
        assert not has_deletion_policy_retain, (
            "BUG CONFIRMED: ConnectOpsReviewConfig has DeletionPolicy: Retain — "
            "this blocks clean teardown and redeploy"
        )

    def test_cft_no_update_replace_policy_retain(self, cft_content: str):
        """ConnectOpsReviewConfig must NOT have UpdateReplacePolicy: Retain.

        Combined with DeletionPolicy: Retain, this creates orphaned SSM
        parameters that block subsequent stack deployments.
        """
        match = re.search(
            r'ConnectOpsReviewConfig:\s*\n'
            r'\s*Type:\s*AWS::SSM::Parameter\s*\n'
            r'\s*DeletionPolicy:\s*\w+\s*\n'
            r'\s*UpdateReplacePolicy:\s*(\w+)',
            cft_content,
        )
        has_update_replace_retain = match and match.group(1) == "Retain"
        assert not has_update_replace_retain, (
            "BUG CONFIRMED: ConnectOpsReviewConfig has UpdateReplacePolicy: Retain — "
            "this prevents clean resource replacement during stack updates"
        )

    def test_terraform_no_prevent_destroy(self, parallel_tf_content: str):
        """aws_ssm_parameter.config lifecycle must NOT have prevent_destroy = true.

        The prevent_destroy lifecycle rule causes terraform destroy to fail
        with an error, blocking clean teardown of the infrastructure.
        """
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        assert ssm_block, (
            "aws_ssm_parameter.config resource not found in parallel.tf"
        )

        lifecycle_block = extract_lifecycle_block(ssm_block)
        has_prevent_destroy = bool(
            re.search(r'prevent_destroy\s*=\s*true', lifecycle_block)
        )
        assert not has_prevent_destroy, (
            "BUG CONFIRMED: aws_ssm_parameter.config has prevent_destroy = true — "
            "this blocks terraform destroy from completing cleanly"
        )


# ---------------------------------------------------------------------------
# Test: Terraform ignore_changes = [value] preservation
# Validates: Requirements 3.1, 3.3
# ---------------------------------------------------------------------------


class TestTerraformIgnoreChangesPreservation:
    """
    Asserts that Terraform `lifecycle { ignore_changes = [value] }` is
    present on the SSM parameter resource. This ensures manual edits to
    the SSM parameter value between deployments are preserved during
    `terraform apply`.

    **Validates: Requirements 3.1, 3.3**
    """

    def test_ssm_parameter_has_lifecycle_block(self, parallel_tf_content: str):
        """aws_ssm_parameter.config must have a lifecycle block.

        The lifecycle block is required to control how Terraform handles
        changes to the SSM parameter value on subsequent applies.
        """
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        assert ssm_block, (
            "aws_ssm_parameter.config resource not found in parallel.tf"
        )
        lifecycle_block = extract_lifecycle_block(ssm_block)
        assert lifecycle_block, (
            "PRESERVATION VIOLATION: aws_ssm_parameter.config is missing its "
            "lifecycle block — manual SSM edits will be overwritten on apply"
        )

    def test_ssm_parameter_ignore_changes_value(self, parallel_tf_content: str):
        """lifecycle must include ignore_changes = [value].

        This directive ensures Terraform does not overwrite the SSM parameter
        value when a user manually edits it between deployments. Without it,
        `terraform apply` would reset the parameter to the original value.
        """
        ssm_block = extract_ssm_parameter_config_block(parallel_tf_content)
        assert ssm_block, (
            "aws_ssm_parameter.config resource not found in parallel.tf"
        )
        lifecycle_block = extract_lifecycle_block(ssm_block)
        assert lifecycle_block, (
            "lifecycle block not found in aws_ssm_parameter.config"
        )

        # Check for ignore_changes containing 'value'
        has_ignore_changes_value = bool(
            re.search(r'ignore_changes\s*=\s*\[.*?value.*?\]', lifecycle_block, re.DOTALL)
        )
        assert has_ignore_changes_value, (
            "PRESERVATION VIOLATION: aws_ssm_parameter.config lifecycle is missing "
            "`ignore_changes = [value]` — user customizations will be lost on apply"
        )


# ---------------------------------------------------------------------------
# Test: prepare_context.py _read_runtime_config() graceful fallback
# Validates: Requirements 3.2, 3.3
# ---------------------------------------------------------------------------


class TestPrepareContextFallbackPreservation:
    """
    Asserts that `_read_runtime_config()` in `prepare_context.py` has a
    try/except structure that falls back to `DEFAULT_CONFIG` on any failure.
    This ensures the Lambda continues to operate correctly even when the
    SSM parameter is missing (e.g., during initial setup or after a
    failed deployment).

    **Validates: Requirements 3.2, 3.3**
    """

    def test_read_runtime_config_has_try_except(self, prepare_context_content: str):
        """_read_runtime_config() must have a try/except error handler.

        The function must catch exceptions from SSM access (including
        ParameterNotFound, permission errors, network issues) to ensure
        the Lambda doesn't crash when the parameter is unavailable.
        """
        tree = ast.parse(prepare_context_content)

        # Find the _read_runtime_config function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_read_runtime_config":
                func_node = node
                break

        assert func_node is not None, (
            "_read_runtime_config() function not found in prepare_context.py"
        )

        # Check that it contains a Try statement
        has_try_except = any(
            isinstance(stmt, ast.Try) for stmt in ast.walk(func_node)
        )
        assert has_try_except, (
            "PRESERVATION VIOLATION: _read_runtime_config() is missing try/except — "
            "SSM failures will crash the Lambda instead of falling back to defaults"
        )

    def test_read_runtime_config_catches_broad_exception(self, prepare_context_content: str):
        """_read_runtime_config() must catch Exception (broad catch).

        A broad except clause ensures ALL SSM-related failures are caught,
        not just specific ones. This is critical because boto3 may raise
        various exceptions (ClientError, EndpointConnectionError, etc.)
        depending on the failure mode.
        """
        tree = ast.parse(prepare_context_content)

        # Find the _read_runtime_config function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_read_runtime_config":
                func_node = node
                break

        assert func_node is not None, (
            "_read_runtime_config() function not found in prepare_context.py"
        )

        # Walk all Try nodes and check for broad Exception handler
        has_broad_except = False
        for node in ast.walk(func_node):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        # Bare except:
                        has_broad_except = True
                    elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                        has_broad_except = True
        assert has_broad_except, (
            "PRESERVATION VIOLATION: _read_runtime_config() does not catch broad "
            "Exception — some SSM failure modes will still crash the Lambda"
        )

    def test_read_runtime_config_returns_default_config_on_failure(
        self, prepare_context_content: str
    ):
        """_read_runtime_config() except block must return DEFAULT_CONFIG.

        When any SSM error occurs, the function must fall back to the
        hardcoded DEFAULT_CONFIG dict so the Lambda can still operate
        with sensible defaults.
        """
        tree = ast.parse(prepare_context_content)

        # Find the _read_runtime_config function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_read_runtime_config":
                func_node = node
                break

        assert func_node is not None, (
            "_read_runtime_config() function not found in prepare_context.py"
        )

        # Look for 'return DEFAULT_CONFIG' inside an except handler
        returns_default_config_in_except = False
        for node in ast.walk(func_node):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    for stmt in ast.walk(handler):
                        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                            if stmt.value.id == "DEFAULT_CONFIG":
                                returns_default_config_in_except = True
        assert returns_default_config_in_except, (
            "PRESERVATION VIOLATION: _read_runtime_config() except block does not "
            "return DEFAULT_CONFIG — Lambda will not fall back gracefully on SSM failure"
        )
