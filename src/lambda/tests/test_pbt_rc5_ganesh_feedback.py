"""Bug Condition Exploration Tests — rc.5 Ganesh Feedback (QB-49..53).

These tests encode the EXPECTED behavior for the five defects flagged in
Ganesh's rc.5 review. Every test in this module MUST FAIL on unfixed rc.5
code — the failures are the evidence that each bug exists. When the fixes
land, these tests turn green.

DO NOT edit the tests to make them pass on unfixed code. DO NOT edit the
production code from within this test file. The tests are the contract
between the bug report and the implementation.

Bugs and expected counterexamples on rc.5 code:

- QB-49 — Resource naming prefix inconsistency between CFT and Terraform.
  Counterexample: CFT `SharedLambdaRole.Properties` has no `RoleName`; TF
  `aws_iam_role.shared_lambda` uses `name_prefix`, not `name`.

- QB-50 — Executive Summary hyperlinks for AI findings resolve to nowhere.
  Counterexample: rendered HTML from `_render_ai_structured_finding` has no
  `id="ai-..."` attribute; `_render_executive_summary` source lacks entries
  named `"AI: Agent Logging"` etc. in `check_order`.

- QB-51 — AI Agents / AI Prompts / AI Guardrails render no per-row tables.
  Counterexample: rendered HTML for a finding with non-empty
  `_raw_agents` / `prompts` / `guardrails` contains no `<table>` element.

- QB-52 — AI Agent Logging routed to Operational Excellence instead of
  Observability. Counterexample: `lambda_handler`'s returned findings dict
  has no `"observability"` key, and the `agent_logging` finding is under
  `findings["operational_excellence"]`.

- QB-53 — "AI: Hard Limits" section adds noise without actionable signal.
  Counterexample: `TOTAL_CHECKS == 6`; `hard_limits` and
  `knowledge_base_capacity` findings are emitted for typical instances.

Validates: Requirements 1.1–1.11, 2.1, 2.3, 2.5, 3.1, 3.2, 3.3, 4.1, 4.2,
4.5, 6.1, 6.4, 6.5
"""

from __future__ import annotations

import inspect
import os
import re
import time
from unittest.mock import MagicMock, patch

import pytest
import yaml
from botocore.exceptions import ClientError

# Reuse the CFN-tag-tolerant YAML loader from the rc4 test helpers so we can
# parse the CloudFormation template without choking on `!Sub`, `!Ref`, etc.
from rc4_iac_parsers import CFNLoader

import ai_analyzer
from ai_analyzer import lambda_handler
from report_generator import (
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cfn_resources() -> dict:
    """Parse the CloudFormation template once per module."""
    with open(CFN_PATH, "r") as f:
        template = yaml.load(f, Loader=CFNLoader)
    return template["Resources"]


@pytest.fixture(scope="module")
def tf_source() -> str:
    """Read the Terraform module source once per module."""
    with open(TF_PATH, "r") as f:
        return f.read()


def _extract_tf_resource_block(source: str, resource_type: str, name: str) -> str:
    """Return the raw HCL body of the named resource block.

    Uses a targeted regex that matches the exact resource type/name pair to
    avoid picking up an unrelated block if the file changes shape.
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


# ═══════════════════════════════════════════════════════════════════════════
# QB-49 — Consistent ConnectOpsReview- prefix across CFT and Terraform
# Property 1: Bug Condition
# Validates: Requirements 1.1–1.11
# ═══════════════════════════════════════════════════════════════════════════


class TestQB49CfnRoleNames:
    """CFT IAM roles have explicit RoleName starting with ConnectOpsReview-."""

    def test_shared_lambda_role_has_role_name(self, cfn_resources):
        """SharedLambdaRole.Properties.RoleName starts with the fixed prefix.

        Validates: Requirements 1.1
        """
        props = cfn_resources["SharedLambdaRole"]["Properties"]
        role_name = props.get("RoleName")
        assert role_name is not None, (
            "SharedLambdaRole.Properties.RoleName is absent — "
            "CFN auto-names the role with the stack-name prefix"
        )
        assert role_name.startswith("ConnectOpsReview-SharedLambdaRole-"), (
            f"Expected RoleName to start with 'ConnectOpsReview-SharedLambdaRole-', "
            f"got: {role_name!r}"
        )

    def test_layer_builder_role_has_role_name(self, cfn_resources):
        """LayerBuilderRole.Properties.RoleName starts with the fixed prefix.

        Validates: Requirements 1.2
        """
        props = cfn_resources["LayerBuilderRole"]["Properties"]
        role_name = props.get("RoleName")
        assert role_name is not None, (
            "LayerBuilderRole.Properties.RoleName is absent — "
            "CFN auto-names the role with the stack-name prefix"
        )
        assert role_name.startswith("ConnectOpsReview-LayerBuilderRole-"), (
            f"Expected RoleName to start with 'ConnectOpsReview-LayerBuilderRole-', "
            f"got: {role_name!r}"
        )

    def test_state_machine_execution_role_has_role_name(self, cfn_resources):
        """StateMachineExecutionRole.Properties.RoleName starts with the fixed prefix.

        Validates: Requirements 1.3
        """
        props = cfn_resources["StateMachineExecutionRole"]["Properties"]
        role_name = props.get("RoleName")
        assert role_name is not None, (
            "StateMachineExecutionRole.Properties.RoleName is absent — "
            "CFN auto-names the role with the stack-name prefix"
        )
        assert role_name.startswith("ConnectOpsReview-StateMachineExecRole-"), (
            f"Expected RoleName to start with "
            f"'ConnectOpsReview-StateMachineExecRole-', got: {role_name!r}"
        )

    def test_scheduler_sfn_role_has_role_name(self, cfn_resources):
        """SchedulerSFNRole.Properties.RoleName starts with the fixed prefix.

        Validates: Requirements 1.4
        """
        props = cfn_resources["SchedulerSFNRole"]["Properties"]
        role_name = props.get("RoleName")
        assert role_name is not None, (
            "SchedulerSFNRole.Properties.RoleName is absent — "
            "CFN auto-names the role with the stack-name prefix"
        )
        assert role_name.startswith("ConnectOpsReview-SchedulerRole-"), (
            f"Expected RoleName to start with 'ConnectOpsReview-SchedulerRole-', "
            f"got: {role_name!r}"
        )


class TestQB49CfnStateMachineAndLogGroup:
    """CFT state machine + state-machine log group have fixed-prefix names."""

    def test_orchestrator_state_machine_name(self, cfn_resources):
        """OrchestratorStateMachine.Properties.StateMachineName equals the fixed value.

        Validates: Requirements 1.5
        """
        props = cfn_resources["OrchestratorStateMachine"]["Properties"]
        state_machine_name = props.get("StateMachineName")
        assert state_machine_name == "ConnectOpsReview-Orchestrator", (
            f"Expected StateMachineName == 'ConnectOpsReview-Orchestrator', "
            f"got: {state_machine_name!r}"
        )

    def test_state_machine_log_group_name(self, cfn_resources):
        """StateMachineLogGroup.Properties.LogGroupName equals the fixed value.

        Validates: Requirements 1.6
        """
        props = cfn_resources["StateMachineLogGroup"]["Properties"]
        log_group_name = props.get("LogGroupName")
        assert log_group_name == "/aws/states/ConnectOpsReview-Orchestrator", (
            f"Expected LogGroupName == '/aws/states/ConnectOpsReview-Orchestrator', "
            f"got: {log_group_name!r}"
        )


class TestQB49TfNames:
    """Terraform IAM roles + state machine + log group use `name` (not `name_prefix`).

    Each affected resource is asserted to (a) have a `name` attribute starting
    with the fixed `ConnectOpsReview-` prefix and (b) NOT use `name_prefix`,
    which produces a random-hash suffix that never matches the CFN name shape.
    """

    def test_shared_lambda_role_uses_name(self, tf_source):
        """aws_iam_role.shared_lambda has `name` (not `name_prefix`).

        Validates: Requirements 1.7
        """
        body = _extract_tf_resource_block(tf_source, "aws_iam_role", "shared_lambda")
        assert re.search(r"^\s*name_prefix\s*=", body, re.MULTILINE) is None, (
            "aws_iam_role.shared_lambda still uses `name_prefix` — should be `name`"
        )
        name_match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert name_match is not None, (
            "aws_iam_role.shared_lambda has no `name` attribute"
        )
        assert name_match.group("value").startswith(
            "ConnectOpsReview-SharedLambdaRole-"
        ), (
            "aws_iam_role.shared_lambda name does not start with "
            f"'ConnectOpsReview-SharedLambdaRole-': {name_match.group('value')!r}"
        )

    def test_state_machine_exec_role_uses_name(self, tf_source):
        """aws_iam_role.state_machine_exec has `name` (not `name_prefix`).

        Validates: Requirements 1.8
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_iam_role", "state_machine_exec"
        )
        assert re.search(r"^\s*name_prefix\s*=", body, re.MULTILINE) is None, (
            "aws_iam_role.state_machine_exec still uses `name_prefix`"
        )
        name_match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert name_match is not None, (
            "aws_iam_role.state_machine_exec has no `name` attribute"
        )
        assert name_match.group("value").startswith(
            "ConnectOpsReview-StateMachineExecRole-"
        ), (
            "aws_iam_role.state_machine_exec name does not start with "
            f"'ConnectOpsReview-StateMachineExecRole-': {name_match.group('value')!r}"
        )

    def test_scheduler_sfn_role_uses_name(self, tf_source):
        """aws_iam_role.scheduler_sfn has `name` (not `name_prefix`).

        Validates: Requirements 1.9
        """
        body = _extract_tf_resource_block(tf_source, "aws_iam_role", "scheduler_sfn")
        assert re.search(r"^\s*name_prefix\s*=", body, re.MULTILINE) is None, (
            "aws_iam_role.scheduler_sfn still uses `name_prefix`"
        )
        name_match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert name_match is not None, (
            "aws_iam_role.scheduler_sfn has no `name` attribute"
        )
        assert name_match.group("value").startswith(
            "ConnectOpsReview-SchedulerRole-"
        ), (
            "aws_iam_role.scheduler_sfn name does not start with "
            f"'ConnectOpsReview-SchedulerRole-': {name_match.group('value')!r}"
        )

    def test_orchestrator_state_machine_name(self, tf_source):
        """aws_sfn_state_machine.orchestrator has `name = "ConnectOpsReview-Orchestrator"`.

        Validates: Requirements 1.10
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_sfn_state_machine", "orchestrator"
        )
        assert re.search(r"^\s*name_prefix\s*=", body, re.MULTILINE) is None, (
            "aws_sfn_state_machine.orchestrator still uses `name_prefix`"
        )
        name_match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert name_match is not None, (
            "aws_sfn_state_machine.orchestrator has no `name` attribute"
        )
        assert name_match.group("value") == "ConnectOpsReview-Orchestrator", (
            "aws_sfn_state_machine.orchestrator name is not "
            f"'ConnectOpsReview-Orchestrator': {name_match.group('value')!r}"
        )

    def test_state_machine_log_group_name(self, tf_source):
        """aws_cloudwatch_log_group.state_machine has the fixed log-group name.

        Validates: Requirements 1.11
        """
        body = _extract_tf_resource_block(
            tf_source, "aws_cloudwatch_log_group", "state_machine"
        )
        assert re.search(r"^\s*name_prefix\s*=", body, re.MULTILINE) is None, (
            "aws_cloudwatch_log_group.state_machine still uses `name_prefix`"
        )
        name_match = re.search(
            r'^\s*name\s*=\s*"(?P<value>[^"]*)"',
            body,
            re.MULTILINE,
        )
        assert name_match is not None, (
            "aws_cloudwatch_log_group.state_machine has no `name` attribute"
        )
        assert (
            name_match.group("value") == "/aws/states/ConnectOpsReview-Orchestrator"
        ), (
            "aws_cloudwatch_log_group.state_machine name is not "
            f"'/aws/states/ConnectOpsReview-Orchestrator': "
            f"{name_match.group('value')!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-50 — Executive Summary hyperlinks resolve to rendered findings
# Property 3: Bug Condition
# Validates: Requirements 2.1, 2.3, 2.5
# ═══════════════════════════════════════════════════════════════════════════


# The AI check names emitted by _render_ai_findings_block are formatted as
# f"AI: {check_name.replace('_', ' ').title()}" — see report_generator.py.
# QB-50 requires check_order to reference these exact strings so that
# get_check_sort_key returns a non-fallthrough index.
AI_CHECK_NAMES = [
    ("agent_inventory", "AI: Agent Inventory"),
    ("prompt_configuration", "AI: Prompt Configuration"),
    ("guardrails", "AI: Guardrails"),
    ("domain_encryption", "AI: Domain Encryption"),
    ("agent_logging", "AI: Agent Logging"),
]

# QB-50 test intentionally excludes "AI: Hard Limits" and
# "AI: Knowledge Base Capacity" — QB-53 removes those checks entirely.


class TestQB50IdAttribute:
    """`_render_ai_structured_finding` emits `id="ai-{check_name}"` on the outer div."""

    @pytest.mark.parametrize("check_name,_display", AI_CHECK_NAMES)
    def test_finding_html_has_matching_id(self, check_name, _display):
        """Rendered HTML for every AI check contains `id="ai-{check_name}"`.

        Validates: Requirements 2.1
        """
        finding = {
            "check_name": check_name,
            "status": "warn",
            "detail": "Synthetic finding for anchor test.",
            "recommendation": None,
            "data": {},
        }
        html = _render_ai_structured_finding(finding)
        expected_id_attr = f'id="ai-{check_name}"'
        assert expected_id_attr in html, (
            f"Rendered HTML for check_name={check_name!r} contains no "
            f"{expected_id_attr!r}. Executive Summary anchor 'ai-{check_name}' "
            "will not resolve."
        )


class TestQB50CheckOrderMapping:
    """`check_order` in `_render_executive_summary` maps the actual AI check names."""

    @pytest.fixture(scope="class")
    @staticmethod
    def executive_summary_source() -> str:
        return inspect.getsource(_render_executive_summary)

    @pytest.mark.parametrize("_check_name,display_name", AI_CHECK_NAMES)
    def test_check_order_contains_ai_check_name(
        self, executive_summary_source, _check_name, display_name
    ):
        """check_order lists the display name emitted by _render_ai_findings_block.

        Validates: Requirements 2.3
        """
        # The check_order dict is defined inline in _render_executive_summary as
        # a literal `check_order = { "Security": [..., "AI: ..."], ... }`.
        # We check the raw quoted string appears in the function body.
        quoted = f'"{display_name}"'
        assert quoted in executive_summary_source, (
            f"check_order in _render_executive_summary does not list "
            f"{quoted}. Runtime AI check rows fall through to check_idx=99."
        )

    def test_check_order_does_not_use_stale_names(self, executive_summary_source):
        """check_order should no longer use the pre-parallel `Connect AI ...` names.

        The pre-parallel monolith used `Connect AI Agent Logging` etc.; the
        parallel port renamed the emitted rows to `AI: Agent Logging` etc. but
        forgot to update check_order. Fixed code must not carry both forms.

        Validates: Requirements 2.3
        """
        stale_names = [
            '"Connect AI Guardrails"',
            '"Connect AI Domain Encryption"',
            '"Connect AI Agent Inventory"',
            '"Connect AI Prompt Configuration"',
            '"Connect AI Agent Logging"',
        ]
        leftover = [n for n in stale_names if n in executive_summary_source]
        assert leftover == [], (
            "check_order still lists the pre-parallel names: "
            f"{leftover}. These do not match the runtime AI check-row names."
        )


class TestQB50SortKey:
    """AI check rows sort with a non-fallthrough `check_idx` (< 99).

    This is a behavioral test: we render Executive Summary with synthetic AI
    checks and verify the sort ordering by scanning the rendered output. The
    check_order dict is local to `_render_executive_summary` so we do not
    import it directly.
    """

    def test_ai_checks_sort_before_fallthrough(self):
        """AI checks land inside their pillar row group, not at the end.

        We construct a checks list where each pillar has a known non-AI check
        (Identity Management under Security, Contact Flow Logging under
        Observability, etc.) plus the corresponding AI check. If check_order
        maps the AI check name, it sorts alongside the non-AI checks; if it
        falls through to check_idx=99, it sorts AFTER an unknown-name check
        with the same status. We verify by using an "unknown check name"
        canary and asserting the AI check row appears BEFORE the canary in
        the rendered output.

        Validates: Requirements 2.5
        """
        checks = [
            # Security pillar
            {
                "area": "Security",
                "check": "AI: Guardrails",
                "status": "info",
                "detail": "AI check",
                "anchor": "ai-guardrails",
            },
            {
                "area": "Security",
                "check": "UnknownCanaryCheck",
                "status": "info",
                "detail": "canary",
                "anchor": "canary",
            },
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "info",
                "detail": "known",
                "anchor": "identity",
            },
        ]
        html = _render_executive_summary(checks)

        # In the rendered HTML, sorted_checks are laid out in order. AI:
        # Guardrails should appear BEFORE the UnknownCanaryCheck because the
        # canary always maps to check_idx=99. On unfixed code both fall
        # through to 99 and the tie is broken by insertion order — the AI
        # check happens to come first there too, so this test alone is not
        # enough. We combine it with a check that the AI check appears
        # BEFORE an unknown check inserted AFTER it in `checks`. On unfixed
        # code, insertion order preservation means the AI still comes first
        # (giving a false green), so we also test with the AI check inserted
        # AFTER the canary — that's the real signal.
        checks_ai_after = [
            {
                "area": "Security",
                "check": "UnknownCanaryCheck",
                "status": "info",
                "detail": "canary",
                "anchor": "canary",
            },
            {
                "area": "Security",
                "check": "AI: Guardrails",
                "status": "info",
                "detail": "AI check",
                "anchor": "ai-guardrails",
            },
            {
                "area": "Security",
                "check": "Identity Management",
                "status": "info",
                "detail": "known",
                "anchor": "identity",
            },
        ]
        html_after = _render_executive_summary(checks_ai_after)

        ai_pos_after = html_after.find("AI: Guardrails")
        canary_pos_after = html_after.find("UnknownCanaryCheck")

        assert ai_pos_after != -1, "AI: Guardrails row not rendered"
        assert canary_pos_after != -1, "UnknownCanaryCheck row not rendered"
        assert ai_pos_after < canary_pos_after, (
            "AI: Guardrails still sorts AFTER an unknown-name check when "
            "inserted second — check_order does not resolve the AI check "
            "name, and both rows share check_idx=99 (stable insertion "
            "ordering only). Sort-key fallthrough is not fixed."
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-51 — AI Agents / AI Prompts / AI Guardrails render <table> elements
# Property 5: Bug Condition
# Validates: Requirements 3.1, 3.2, 3.3
# ═══════════════════════════════════════════════════════════════════════════


class TestQB51TabularFindings:
    """Tabular AI findings render <table> elements with per-row data."""

    def test_agent_inventory_renders_table(self):
        """agent_inventory finding with `_raw_agents` renders a per-agent table.

        Validates: Requirements 3.1
        """
        finding = {
            "check_name": "agent_inventory",
            "status": "info",
            "detail": "1 AI agent(s) found",
            "recommendation": None,
            "data": {
                "_raw_agents": [
                    {
                        "name": "test-agent-1",
                        "type": "MANUAL",
                        "visibility": "PUBLISHED",
                    }
                ]
            },
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" in html, (
            "agent_inventory finding with non-empty _raw_agents rendered "
            "no <table> element. The per-agent inventory table is missing."
        )
        assert "test-agent-1" in html, (
            "agent_inventory finding rendered but did not include the "
            "agent name from _raw_agents — the per-row data was dropped."
        )

    def test_prompt_configuration_renders_table(self):
        """prompt_configuration finding with `prompts` renders a per-prompt table.

        Validates: Requirements 3.2
        """
        finding = {
            "check_name": "prompt_configuration",
            "status": "info",
            "detail": "1 AI prompt(s) found",
            "recommendation": None,
            "data": {
                "prompts": [
                    {
                        "name": "test-prompt-1",
                        "type": "AGENT",
                        "model_id": "claude-x",
                        "visibility": "PUBLISHED",
                    }
                ],
                "models_used": ["claude-x"],
            },
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" in html, (
            "prompt_configuration finding with non-empty prompts rendered "
            "no <table> element. The per-prompt table is missing."
        )
        assert "test-prompt-1" in html, (
            "prompt_configuration finding rendered but did not include the "
            "prompt name from data.prompts."
        )
        assert "claude-x" in html, (
            "prompt_configuration finding rendered but did not include the "
            "model id — data.models_used and per-prompt model column dropped."
        )

    def test_guardrails_renders_table(self):
        """guardrails finding with `guardrails` list renders a filter-coverage table.

        Validates: Requirements 3.3
        """
        finding = {
            "check_name": "guardrails",
            "status": "warn",
            "detail": "1 guardrail(s) found, 1 missing filter",
            "recommendation": None,
            "data": {
                "guardrails": [
                    {
                        "name": "test-gr-1",
                        "status": "READY",
                        "visibility": "PUBLISHED",
                        "content_filter": True,
                        "topic_policy": False,
                        "pii_filter": True,
                        "word_filter": True,
                        "grounding": True,
                    }
                ],
                "missing_filters": ["Denied Topics"],
                "agent_associations": {},
            },
        }
        html = _render_ai_structured_finding(finding)
        assert "<table" in html, (
            "guardrails finding with non-empty guardrails list rendered "
            "no <table> element. The filter-coverage table is missing."
        )
        assert "test-gr-1" in html, (
            "guardrails finding rendered but did not include the guardrail "
            "name from data.guardrails."
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-52 — AI Agent Logging routed to Observability pillar
# Property 7: Bug Condition
# Validates: Requirements 4.1, 4.2
# ═══════════════════════════════════════════════════════════════════════════


VALID_EVENT = {
    "reviewId": "test-review-qb52",
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


def _make_client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": f"Simulated {code}"}},
        "SomeOperation",
    )


def _agent_logging_route_findings(result: dict) -> tuple[list, list]:
    """Return (observability_findings, opex_findings) filtered to agent_logging."""
    findings = result["findings"]
    obs = findings.get("observability", [])
    opex = findings.get("operational_excellence", [])
    obs_agent_logging = [f for f in obs if f.get("check_name") == "agent_logging"]
    opex_agent_logging = [f for f in opex if f.get("check_name") == "agent_logging"]
    return obs_agent_logging, opex_agent_logging


class TestQB52AgentLoggingRouting:
    """`agent_logging` finding routes to `findings["observability"]` in all paths."""

    def _base_patches(self):
        """Return the set of patches every QB-52 test needs.

        The AI analyzer runs six checks. To isolate `agent_logging`'s routing,
        we mock every other check to return a pass finding, mock
        `discover_assistant_id` to return a fixed id, mock `boto3.client` to
        avoid any real AWS interaction, and mock `persist_to_s3` to avoid S3.
        We also patch `check_time_budget` to a no-op so we never hit
        TimeBudgetExceeded during the fake run.
        """
        return {
            "check_ai_agent_inventory": _pass_finding("agent_inventory"),
            "check_ai_prompt_configuration": _pass_finding("prompt_configuration"),
            "check_q_guardrails": _pass_finding("guardrails"),
            "check_q_domain_encryption": _pass_finding("domain_encryption"),
        }

    def _run_with_agent_logging_side_effect(self, agent_logging_side_effect):
        """Run lambda_handler once with a specific side effect for agent_logging.

        agent_logging_side_effect is either a return value (dict/list) or an
        Exception subclass to raise. We return the handler's result dict so the
        caller can assert on its findings.
        """
        patches = self._base_patches()
        with (
            patch("ai_analyzer.boto3.client"),
            patch(
                "ai_analyzer.discover_assistant_id",
                return_value="test-assistant-id",
            ),
            patch("ai_analyzer.check_time_budget"),
            patch("ai_analyzer.persist_to_s3", return_value="mock-s3-key"),
            patch(
                "ai_analyzer.check_ai_agent_inventory",
                return_value=patches["check_ai_agent_inventory"],
            ),
            patch(
                "ai_analyzer.check_ai_prompt_configuration",
                return_value=patches["check_ai_prompt_configuration"],
            ),
            patch(
                "ai_analyzer.check_q_guardrails",
                return_value=patches["check_q_guardrails"],
            ),
            patch(
                "ai_analyzer.check_q_domain_encryption",
                return_value=patches["check_q_domain_encryption"],
            ),
            patch("ai_analyzer.check_ai_agent_logging") as mock_logging,
        ):
            if isinstance(agent_logging_side_effect, Exception) or (
                isinstance(agent_logging_side_effect, type)
                and issubclass(agent_logging_side_effect, BaseException)
            ):
                mock_logging.side_effect = agent_logging_side_effect
            else:
                mock_logging.return_value = agent_logging_side_effect

            return lambda_handler(VALID_EVENT, None)

    def test_success_path_routes_to_observability(self):
        """Success case: `agent_logging` finding is under observability, not opex.

        Validates: Requirements 4.1, 4.2
        """
        result = self._run_with_agent_logging_side_effect(
            _pass_finding("agent_logging")
        )

        findings = result["findings"]
        assert "observability" in findings, (
            "findings dict has no 'observability' key — the AI analyzer "
            "does not initialize a fourth pillar for logging findings."
        )
        obs_agent_logging, opex_agent_logging = _agent_logging_route_findings(result)
        assert len(obs_agent_logging) == 1, (
            f"Expected exactly one agent_logging finding in "
            f"findings['observability'], got {len(obs_agent_logging)}: "
            f"{obs_agent_logging!r}"
        )
        assert len(opex_agent_logging) == 0, (
            "Expected zero agent_logging findings in "
            f"findings['operational_excellence'], got {len(opex_agent_logging)}: "
            f"{opex_agent_logging!r}"
        )

    def test_access_denied_routes_to_observability(self):
        """AccessDeniedException: error/info finding is under observability.

        Validates: Requirements 4.1, 4.2
        """
        result = self._run_with_agent_logging_side_effect(
            _make_client_error("AccessDeniedException")
        )
        findings = result["findings"]
        assert "observability" in findings, (
            "findings dict has no 'observability' key on AccessDenied path"
        )
        obs_agent_logging, opex_agent_logging = _agent_logging_route_findings(result)
        assert len(obs_agent_logging) == 1, (
            "AccessDeniedException path did not append an agent_logging "
            f"finding to observability: {obs_agent_logging!r}"
        )
        assert len(opex_agent_logging) == 0, (
            "AccessDeniedException path leaked agent_logging into "
            f"operational_excellence: {opex_agent_logging!r}"
        )

    def test_resource_not_found_routes_to_observability(self):
        """ResourceNotFoundException: info finding is under observability.

        Validates: Requirements 4.1, 4.2
        """
        result = self._run_with_agent_logging_side_effect(
            _make_client_error("ResourceNotFoundException")
        )
        findings = result["findings"]
        assert "observability" in findings, (
            "findings dict has no 'observability' key on ResourceNotFound path"
        )
        obs_agent_logging, opex_agent_logging = _agent_logging_route_findings(result)
        assert len(obs_agent_logging) == 1, (
            "ResourceNotFoundException path did not append an agent_logging "
            f"finding to observability: {obs_agent_logging!r}"
        )
        assert len(opex_agent_logging) == 0, (
            "ResourceNotFoundException path leaked agent_logging into "
            f"operational_excellence: {opex_agent_logging!r}"
        )

    def test_generic_exception_routes_to_observability(self):
        """Generic exception: error finding is under observability.

        Validates: Requirements 4.1, 4.2
        """
        result = self._run_with_agent_logging_side_effect(
            RuntimeError("Simulated unexpected error")
        )
        findings = result["findings"]
        assert "observability" in findings, (
            "findings dict has no 'observability' key on generic exception path"
        )
        obs_agent_logging, opex_agent_logging = _agent_logging_route_findings(result)
        assert len(obs_agent_logging) == 1, (
            "Generic exception path did not append an agent_logging "
            f"finding to observability: {obs_agent_logging!r}"
        )
        assert len(opex_agent_logging) == 0, (
            "Generic exception path leaked agent_logging into "
            f"operational_excellence: {opex_agent_logging!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# QB-53 — AI Hard Limits section is absent
# Property 9: Bug Condition
# Validates: Requirements 6.1, 6.4, 6.5
# ═══════════════════════════════════════════════════════════════════════════


class TestQB53HardLimitsRemoved:
    """`hard_limits` and `knowledge_base_capacity` findings are not emitted."""

    def test_total_checks_equals_five(self):
        """ai_analyzer.TOTAL_CHECKS is 5, not 6.

        Validates: Requirements 6.3
        """
        assert ai_analyzer.TOTAL_CHECKS == 5, (
            f"ai_analyzer.TOTAL_CHECKS == {ai_analyzer.TOTAL_CHECKS}, "
            "expected 5. hard_limits is still counted as a check."
        )

    def _run_full_analyzer(self):
        """Invoke lambda_handler with the five surviving checks passing.

        Under rc.5 code, `check_q_hard_limits` would still be invoked as
        Check 6, emitting both a hard_limits finding (op-ex) and — because
        our mock simulates 1 assistant + 1 KB — a knowledge_base_capacity
        finding (resilience). The exploration test asserts that neither
        appears anywhere in the returned findings, which means either the
        check has been deleted OR the mock never triggers it. We patch
        `check_q_hard_limits` to raise AttributeError so, on fixed code
        where the check is removed and never invoked, the mock never fires
        (fixed code will not import or invoke it); on unfixed code the mock
        WILL fire and its return value (which we set to a list containing
        both findings) will be routed by lambda_handler.
        """
        hard_limits_result = [
            {
                "check_name": "hard_limits",
                "status": "pass",
                "detail": "Assistants: 1/5 (20%); Knowledge Bases: 1/10 (10%)",
                "recommendation": None,
                "data": {"resources": []},
            },
            {
                "check_name": "knowledge_base_capacity",
                "status": "pass",
                "detail": "All 1 knowledge base(s) have healthy article capacity",
                "recommendation": None,
                "data": {},
            },
        ]

        with (
            patch("ai_analyzer.boto3.client"),
            patch(
                "ai_analyzer.discover_assistant_id",
                return_value="test-assistant-id",
            ),
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
            # Patch check_q_hard_limits: on rc.5 it exists and lambda_handler
            # invokes it — this mock supplies the two findings the check would
            # normally emit. On fixed code the check is deleted; if
            # lambda_handler still tries to reference it, this patch keeps the
            # test infrastructure stable, but no invocation should happen.
            patch(
                "ai_analyzer.check_q_hard_limits",
                return_value=hard_limits_result,
                create=True,
            ),
        ):
            return lambda_handler(VALID_EVENT, None)

    def test_no_hard_limits_finding_emitted(self):
        """No hard_limits finding appears in any pillar.

        Validates: Requirements 6.1, 6.4
        """
        result = self._run_full_analyzer()
        findings = result["findings"]
        offenders = []
        for pillar in (
            "security",
            "operational_excellence",
            "resilience",
            "observability",
        ):
            for f in findings.get(pillar, []):
                if f.get("check_name") == "hard_limits":
                    offenders.append((pillar, f))
        assert offenders == [], (
            "hard_limits finding still emitted under: "
            f"{[p for p, _ in offenders]}. "
            "check_q_hard_limits and its Check 6 block must be removed."
        )

    def test_no_knowledge_base_capacity_finding_emitted(self):
        """No knowledge_base_capacity finding appears in any pillar.

        Validates: Requirements 6.4
        """
        result = self._run_full_analyzer()
        findings = result["findings"]
        offenders = []
        for pillar in (
            "security",
            "operational_excellence",
            "resilience",
            "observability",
        ):
            for f in findings.get(pillar, []):
                if f.get("check_name") == "knowledge_base_capacity":
                    offenders.append((pillar, f))
        assert offenders == [], (
            "knowledge_base_capacity finding still emitted under: "
            f"{[p for p, _ in offenders]}. "
            "check_q_hard_limits and its Check 6 block must be removed."
        )

    def test_checks_completed_excludes_hard_limits(self):
        """metadata.checks_completed does not include 'hard_limits'.

        Validates: Requirements 6.5
        """
        result = self._run_full_analyzer()
        checks_completed = (
            result["findings"].get("metadata", {}).get("checks_completed", [])
        )
        assert "hard_limits" not in checks_completed, (
            "metadata.checks_completed still contains 'hard_limits': "
            f"{checks_completed!r}. Check 6 (hard_limits) must be removed."
        )
