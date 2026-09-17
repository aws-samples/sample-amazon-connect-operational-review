"""
qconnect Property Tests — Bug Condition and Preservation

This module combines two concerns for the qconnect IAM configuration:

1. Bug Condition (TestQConnectARNBugCondition): Asserts that IAM policy Resource
   fields for qconnect actions include the `arn:aws:qconnect:*` namespace, not
   just the legacy `wisdom` namespace. On unfixed code, these tests FAIL.

2. Preservation (TestQConnectActionListPreservation, TestGracefulSkipPreservation):
   Asserts that the exact set of 14 qconnect actions remains in both CFN and
   Terraform policies, and that analyzers gracefully handle instances without
   Q-in-Connect integration.

Validates: Requirements 2.4, 2.5, 2.6, 3.4, 3.5, 3.6
"""

import ast
import re

import pytest

from _helpers import (
    extract_qconnect_actions_from_cfn,
    extract_qconnect_actions_from_tf,
    extract_qconnect_policy_resource_cfn,
    extract_qconnect_policy_resource_tf,
)

# ---------------------------------------------------------------------------
# Expected qconnect actions — canonical set for BOTH CFN and Terraform.
# Both deployment paths authorize the same 14 actions for the shared Lambda role.
# ---------------------------------------------------------------------------

EXPECTED_QCONNECT_ACTIONS = {
    "qconnect:ListAssistants",
    "qconnect:GetAssistant",
    "qconnect:ListKnowledgeBases",
    "qconnect:GetKnowledgeBase",
    "qconnect:ListAIAgents",
    "qconnect:GetAIAgent",
    "qconnect:ListAIAgentVersions",
    "qconnect:ListAIPrompts",
    "qconnect:GetAIPrompt",
    "qconnect:ListAIPromptVersions",
    "qconnect:ListAIGuardrails",
    "qconnect:GetAIGuardrail",
    "qconnect:ListContents",
    "qconnect:ListAssistantAssociations",
}


# ---------------------------------------------------------------------------
# Helpers (module-specific)
# ---------------------------------------------------------------------------


def find_function_node(source: str, func_name: str) -> ast.FunctionDef | None:
    """Find a function definition by name in parsed source."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


# ---------------------------------------------------------------------------
# Test: qconnect ARN Namespace Mismatch (Bug Condition)
# Validates: Requirements 2.4, 2.5, 2.6
#
# These tests assert the DESIRED state (qconnect namespace present in Resource).
# On unfixed code they FAIL — confirming the bug exists.
# ---------------------------------------------------------------------------


class TestQConnectARNBugCondition:
    """
    Asserts that IAM policy Resource fields for qconnect actions include
    the `arn:aws:qconnect:*` namespace, not just the legacy `wisdom` namespace.

    **Validates: Requirements 2.4, 2.5, 2.6**
    """

    def test_cft_resource_includes_qconnect_namespace(self, cft_content: str):
        """CFN IAM policy Resource for qconnect actions must include arn:aws:qconnect:*.

        The legacy `wisdom` ARN namespace does not match the qconnect service
        endpoint's resource ARN format, causing AccessDeniedException when
        invoking qconnect:GetAssistant or qconnect:ListAssistantAssociations.
        """
        resource_value = extract_qconnect_policy_resource_cfn(cft_content)
        assert resource_value, (
            "Could not find qconnect policy Resource field in CFN template"
        )

        has_qconnect_arn = "arn:aws:qconnect:" in resource_value
        assert has_qconnect_arn, (
            "BUG CONFIRMED: CFN IAM policy Resource uses only legacy wisdom namespace — "
            f"found: {resource_value!r} — "
            "missing arn:aws:qconnect:* which causes AccessDeniedException for "
            "qconnect:GetAssistant and qconnect:ListAssistantAssociations"
        )

    def test_terraform_resource_includes_qconnect_namespace(
        self, parallel_tf_content: str
    ):
        """Terraform IAM policy Resource for qconnect actions must include arn:aws:qconnect:*.

        The legacy `wisdom` ARN namespace does not match the qconnect service
        endpoint's resource ARN format, causing AccessDeniedException when
        invoking qconnect:GetAssistant or qconnect:ListAssistantAssociations.
        """
        resource_value = extract_qconnect_policy_resource_tf(parallel_tf_content)
        assert resource_value, (
            "Could not find qconnect policy Resource field in parallel.tf"
        )

        has_qconnect_arn = "arn:aws:qconnect:" in resource_value
        assert has_qconnect_arn, (
            "BUG CONFIRMED: Terraform IAM policy Resource uses only legacy wisdom namespace — "
            f"found: {resource_value!r} — "
            "missing arn:aws:qconnect:* which causes AccessDeniedException for "
            "qconnect:GetAssistant and qconnect:ListAssistantAssociations"
        )


# ---------------------------------------------------------------------------
# Test: qconnect Action List Preservation
# Validates: Requirements 3.5
#
# Each deployment path must authorize the exact canonical set of 14 actions.
# Since both are compared against a single constant, parity is guaranteed
# when both tests pass — no separate parity test needed.
# ---------------------------------------------------------------------------


class TestQConnectActionListPreservation:
    """
    Asserts that the exact set of 14 qconnect:* actions remains in both
    CFN and Terraform IAM policies after code changes.

    **Validates: Requirements 3.5**
    """

    def test_cfn_qconnect_actions_exact_set(self, cft_content: str):
        """CFN qconnect actions must match the canonical 14-action set exactly."""
        actual = extract_qconnect_actions_from_cfn(cft_content)
        assert actual == EXPECTED_QCONNECT_ACTIONS, (
            "PRESERVATION VIOLATION: CFN qconnect actions differ from baseline — "
            f"added: {sorted(actual - EXPECTED_QCONNECT_ACTIONS)} — "
            f"removed: {sorted(EXPECTED_QCONNECT_ACTIONS - actual)}"
        )

    def test_terraform_qconnect_actions_exact_set(self, parallel_tf_content: str):
        """Terraform qconnect actions must match the canonical 14-action set exactly."""
        actual = extract_qconnect_actions_from_tf(parallel_tf_content)
        assert actual == EXPECTED_QCONNECT_ACTIONS, (
            "PRESERVATION VIOLATION: Terraform qconnect actions differ from baseline — "
            f"added: {sorted(actual - EXPECTED_QCONNECT_ACTIONS)} — "
            f"removed: {sorted(EXPECTED_QCONNECT_ACTIONS - actual)}"
        )


# ---------------------------------------------------------------------------
# Test: Graceful Skip for Instances Without Q-in-Connect
# Validates: Requirements 3.4, 3.6
# ---------------------------------------------------------------------------


class TestGracefulSkipPreservation:
    """
    Asserts that the AI and resilience analyzers gracefully skip Q-in-Connect
    checks when no WISDOM_ASSISTANT integration is found.

    **Validates: Requirements 3.4, 3.6**
    """

    def test_ai_analyzer_handles_no_assistant(self, ai_analyzer_content: str):
        """AI analyzer must check for None assistant_id and produce an info finding.

        When discover_assistant_id() returns None, the analyzer must skip all
        AI checks and return an informational finding (not crash or error).
        """
        has_none_check = bool(
            re.search(
                r"if\s+assistant_id\s+is\s+None|if\s+not\s+assistant_id",
                ai_analyzer_content,
            )
        )
        assert has_none_check, (
            "PRESERVATION VIOLATION: ai_analyzer.py does not check for None "
            "assistant_id — instances without Q-in-Connect will error"
        )

        has_info_finding = bool(
            re.search(
                r'assistant_discovery.*?status.*?info|'
                r'"status":\s*"info".*?assistant',
                ai_analyzer_content,
                re.DOTALL,
            )
        )
        assert has_info_finding, (
            "PRESERVATION VIOLATION: ai_analyzer.py does not produce an info "
            "finding when no assistant is found"
        )

    def test_resilience_analyzer_returns_none_on_no_assistant(
        self, resilience_analyzer_content: str
    ):
        """Resilience analyzer must return (None, None) when no assistant found."""
        has_graceful_return = bool(
            re.search(
                r"if\s+not\s+assistant_id.*?return\s+None\s*,\s*None",
                resilience_analyzer_content,
                re.DOTALL,
            )
        )
        assert has_graceful_return, (
            "PRESERVATION VIOLATION: resilience_analyzer.py does not return "
            "(None, None) when no assistant_id is found"
        )

    def test_discover_assistant_id_catches_error_and_returns_none(
        self, analyzer_common_content: str
    ):
        """discover_assistant_id() must catch ClientError and return None.

        This validates both the error-handling structure and the graceful None
        return, ensuring the calling analyzer can detect absence of Q-in-Connect
        without propagating exceptions.
        """
        func_node = find_function_node(
            analyzer_common_content, "discover_assistant_id"
        )
        assert func_node is not None, (
            "discover_assistant_id() function not found in analyzer_common.py"
        )

        has_client_error_handler = False
        returns_none_in_except = False

        for node in ast.walk(func_node):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    # Check for ClientError handler
                    if handler.type:
                        type_name = (
                            handler.type.id
                            if isinstance(handler.type, ast.Name)
                            else getattr(handler.type, "attr", "")
                        )
                        if type_name == "ClientError":
                            has_client_error_handler = True

                    # Check for `return None` in except body
                    for stmt in ast.walk(handler):
                        if isinstance(stmt, ast.Return):
                            if stmt.value is None or (
                                isinstance(stmt.value, ast.Constant)
                                and stmt.value.value is None
                            ):
                                returns_none_in_except = True

        assert has_client_error_handler, (
            "PRESERVATION VIOLATION: discover_assistant_id() does not catch "
            "ClientError — unsupported regions will crash the analyzer"
        )
        assert returns_none_in_except, (
            "PRESERVATION VIOLATION: discover_assistant_id() except handlers "
            "do not return None — errors will propagate instead of allowing "
            "graceful skip"
        )
