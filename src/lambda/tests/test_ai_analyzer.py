"""Tests for individual check functions in ai_analyzer.py.

Validates task 8.2: Unit tests for check_ai_agent_inventory,
check_ai_prompt_configuration, check_q_guardrails, and
check_q_domain_encryption.
Requirements: 3.1, 3.2, 3.3
"""

import sys
import os
import re
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from unittest.mock import MagicMock, patch
from ai_analyzer import (
    check_ai_agent_inventory,
    check_ai_prompt_configuration,
    check_q_guardrails,
    check_q_domain_encryption,
)
from graceful_timeout import TimeBudgetExceeded

START_TIME = time.time()
TIME_BUDGET = 300
ASSISTANT_ID = "test-assistant-123"
INSTANCE_ID = "test-instance-id"

HTML_PATTERN = re.compile(r"<[a-zA-Z/][^>]*>")

VALID_STATUSES = {"pass", "fail", "warn", "info", "error"}


@pytest.fixture(autouse=True)
def patch_time_budget():
    """Patch check_time_budget and time.sleep to prevent timing issues in tests."""
    with (
        patch("ai_analyzer.check_time_budget") as mock_ctb,
        patch("ai_analyzer.time.sleep"),
    ):
        yield mock_ctb


def assert_finding_structure(finding):
    """Assert a finding dict has all required keys and valid values per Req 3.1, 3.2."""
    assert "check_name" in finding
    assert "status" in finding
    assert "detail" in finding
    assert "recommendation" in finding
    assert "data" in finding
    assert finding["status"] in VALID_STATUSES
    assert len(finding["detail"]) <= 1024
    # Req 3.2: No HTML
    assert not HTML_PATTERN.search(finding["detail"]), (
        f"HTML found in detail: {finding['detail']}"
    )
    if finding["recommendation"]:
        assert not HTML_PATTERN.search(finding["recommendation"]), (
            "HTML found in recommendation"
        )


# ===========================================================================
# check_ai_agent_inventory
# ===========================================================================


class TestCheckAiAgentInventoryPass:
    """Tests for pass scenario when agents are found without MCP sprawl."""

    def test_agents_found_no_sprawl(self):
        """When agents exist with ≤15 MCP tools, returns pass."""
        mock_qc = MagicMock()
        mock_connect = MagicMock()
        mock_qc.list_ai_agents.__name__ = "list_ai_agents"
        mock_qc.list_ai_agents.return_value = {
            "aiAgentSummaries": [
                {"aiAgentId": "agent-1", "name": "Agent1", "status": "ACTIVE"}
            ]
        }
        mock_qc.get_ai_agent.return_value = {
            "aiAgent": {
                "name": "Agent1",
                "type": "ORCHESTRATION",
                "configuration": {
                    "orchestrationAIAgentConfiguration": {
                        "toolConfigurations": [
                            {
                                "toolType": "MODEL_CONTEXT_PROTOCOL",
                                "toolName": f"tool-{i}",
                            }
                            for i in range(5)
                        ]
                    }
                },
            }
        }
        mock_connect.list_security_profiles.return_value = {
            "SecurityProfileSummaryList": []
        }

        result = check_ai_agent_inventory(
            mock_qc, mock_connect, ASSISTANT_ID, INSTANCE_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "agent_inventory"
        assert result["status"] == "pass"
        assert result["data"]["agent_count"] == 1
        assert result["data"]["mcp_heavy_agents"] == []


class TestCheckAiAgentInventoryWarn:
    """Tests for warn scenario when MCP tool sprawl is detected."""

    def test_mcp_sprawl_detected(self):
        """When an agent has >15 MCP tools, returns warn."""
        mock_qc = MagicMock()
        mock_connect = MagicMock()
        mock_qc.list_ai_agents.__name__ = "list_ai_agents"
        mock_qc.list_ai_agents.return_value = {
            "aiAgentSummaries": [
                {"aiAgentId": "agent-1", "name": "SprawlAgent", "status": "ACTIVE"}
            ]
        }
        mock_qc.get_ai_agent.return_value = {
            "aiAgent": {
                "name": "SprawlAgent",
                "type": "ORCHESTRATION",
                "configuration": {
                    "orchestrationAIAgentConfiguration": {
                        "toolConfigurations": [
                            {
                                "toolType": "MODEL_CONTEXT_PROTOCOL",
                                "toolName": f"tool-{i}",
                            }
                            for i in range(20)
                        ]
                    }
                },
            }
        }
        mock_connect.list_security_profiles.return_value = {
            "SecurityProfileSummaryList": []
        }

        result = check_ai_agent_inventory(
            mock_qc, mock_connect, ASSISTANT_ID, INSTANCE_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "agent_inventory"
        assert result["status"] == "warn"
        assert len(result["data"]["mcp_heavy_agents"]) == 1
        assert result["recommendation"] is not None


class TestCheckAiAgentInventoryInfo:
    """Tests for info scenario when no agents are found."""

    def test_no_agents_found(self):
        """When no agents exist, returns info."""
        mock_qc = MagicMock()
        mock_connect = MagicMock()
        mock_qc.list_ai_agents.__name__ = "list_ai_agents"
        mock_qc.list_ai_agents.return_value = {"aiAgentSummaries": []}

        result = check_ai_agent_inventory(
            mock_qc, mock_connect, ASSISTANT_ID, INSTANCE_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "agent_inventory"
        assert result["status"] == "info"
        assert result["data"]["agent_count"] == 0


class TestCheckAiAgentInventoryError:
    """Tests for error handling in agent inventory check."""

    def test_time_budget_exceeded_propagates(self, patch_time_budget):
        """TimeBudgetExceeded propagates up the call stack."""
        mock_qc = MagicMock()
        mock_connect = MagicMock()
        patch_time_budget.side_effect = TimeBudgetExceeded("timeout")

        with pytest.raises(TimeBudgetExceeded):
            check_ai_agent_inventory(
                mock_qc,
                mock_connect,
                ASSISTANT_ID,
                INSTANCE_ID,
                START_TIME,
                TIME_BUDGET,
            )


# ===========================================================================
# check_ai_prompt_configuration
# ===========================================================================


class TestCheckAiPromptConfigurationPass:
    """Tests for pass scenario when prompts are found."""

    def test_prompts_found(self):
        """When prompts exist with valid template text, returns pass with model distribution."""
        mock_qc = MagicMock()
        mock_qc.list_ai_prompts.__name__ = "list_ai_prompts"
        mock_qc.list_ai_prompts.return_value = {
            "aiPromptSummaries": [
                {"aiPromptId": "prompt-1", "name": "Prompt1"},
                {"aiPromptId": "prompt-2", "name": "Prompt2"},
            ]
        }
        mock_qc.get_ai_prompt.side_effect = [
            {
                "aiPrompt": {
                    "name": "Prompt1",
                    "type": "ORCHESTRATION",
                    "modelId": "anthropic.claude-3:1",
                    "templateConfiguration": {
                        "textFullAIPromptEditTemplateConfiguration": {
                            "text": "You are a helpful assistant."
                        }
                    },
                }
            },
            {
                "aiPrompt": {
                    "name": "Prompt2",
                    "type": "SELF_SERVICE",
                    "modelId": "anthropic.claude-3:1",
                    "templateConfiguration": {
                        "textFullAIPromptEditTemplateConfiguration": {
                            "text": "Answer user questions accurately."
                        }
                    },
                }
            },
        ]

        result = check_ai_prompt_configuration(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "prompt_configuration"
        assert result["status"] == "pass"
        assert result["data"]["prompt_count"] == 2
        assert "anthropic.claude-3" in result["data"]["model_distribution"]


class TestCheckAiPromptConfigurationInfo:
    """Tests for info scenario when no prompts are found."""

    def test_no_prompts(self):
        """When no prompts exist, returns info."""
        mock_qc = MagicMock()
        mock_qc.list_ai_prompts.__name__ = "list_ai_prompts"
        mock_qc.list_ai_prompts.return_value = {"aiPromptSummaries": []}

        result = check_ai_prompt_configuration(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "prompt_configuration"
        assert result["status"] == "info"
        assert result["data"]["prompt_count"] == 0


class TestCheckAiPromptConfigurationError:
    """Tests for error handling in prompt configuration check."""

    def test_get_ai_prompt_error_handled_gracefully(self):
        """When get_ai_prompt raises, the prompt is flagged as incomplete (warn)."""
        mock_qc = MagicMock()
        mock_qc.list_ai_prompts.__name__ = "list_ai_prompts"
        mock_qc.list_ai_prompts.return_value = {
            "aiPromptSummaries": [{"aiPromptId": "prompt-1", "name": "Prompt1"}]
        }
        mock_qc.get_ai_prompt.side_effect = Exception("Throttled")

        result = check_ai_prompt_configuration(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "prompt_configuration"
        # Returns warn because we cannot confirm the prompt has valid template text
        assert result["status"] == "warn"
        assert result["data"]["prompt_count"] == 1

    def test_time_budget_exceeded_propagates(self, patch_time_budget):
        """TimeBudgetExceeded propagates up the call stack."""
        mock_qc = MagicMock()
        patch_time_budget.side_effect = TimeBudgetExceeded("timeout")

        with pytest.raises(TimeBudgetExceeded):
            check_ai_prompt_configuration(mock_qc, ASSISTANT_ID, START_TIME, -1)


# ===========================================================================
# check_q_guardrails
# ===========================================================================


class TestCheckQGuardrailsPass:
    """Tests for pass scenario with full guardrail coverage."""

    def test_all_guardrails_published_full_coverage(self):
        """Published guardrails with all filters and all agents protected returns pass."""
        mock_qc = MagicMock()
        mock_qc.list_ai_guardrails.__name__ = "list_ai_guardrails"
        mock_qc.list_ai_agents.__name__ = "list_ai_agents"
        mock_qc.list_ai_guardrails.return_value = {
            "aiGuardrailSummaries": [
                {
                    "aiGuardrailId": "gr-1",
                    "name": "MainGuardrail",
                    "status": "ACTIVE",
                    "visibilityStatus": "PUBLISHED",
                }
            ]
        }
        mock_qc.get_ai_guardrail.return_value = {
            "aiGuardrail": {
                "contentPolicyConfig": {"filtersConfig": [{"type": "HATE"}]},
                "topicPolicyConfig": {"topicsConfig": [{"name": "Violence"}]},
                "sensitiveInformationPolicyConfig": {
                    "piiEntitiesConfig": [{"type": "EMAIL"}],
                    "regexesConfig": [],
                },
                "wordPolicyConfig": {
                    "wordsConfig": [{"text": "bad"}],
                    "managedWordListsConfig": [],
                },
                "contextualGroundingPolicyConfig": {
                    "filtersConfig": [{"type": "GROUNDING"}]
                },
            }
        }
        # All agents have published guardrails
        mock_qc.list_ai_agents.return_value = {
            "aiAgentSummaries": [
                {
                    "aiAgentId": "agent-1",
                    "name": "Agent1",
                    "visibilityStatus": "PUBLISHED",
                }
            ]
        }
        mock_qc.get_ai_agent.return_value = {
            "aiAgent": {
                "configuration": {
                    "orchestrationAIAgentConfiguration": {
                        "orchestrationAIGuardrailId": "gr-1"
                    }
                }
            }
        }

        result = check_q_guardrails(mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET)

        assert_finding_structure(result)
        assert result["check_name"] == "guardrails"
        assert result["status"] == "pass"
        assert result["data"]["published_count"] == 1


class TestCheckQGuardrailsWarn:
    """Tests for warn scenario with missing filters or unprotected agents."""

    def test_missing_filters_on_published_guardrail(self):
        """Published guardrail missing content filter returns warn."""
        mock_qc = MagicMock()
        mock_qc.list_ai_guardrails.__name__ = "list_ai_guardrails"
        mock_qc.list_ai_agents.__name__ = "list_ai_agents"
        mock_qc.list_ai_guardrails.return_value = {
            "aiGuardrailSummaries": [
                {
                    "aiGuardrailId": "gr-1",
                    "name": "PartialGuardrail",
                    "status": "ACTIVE",
                    "visibilityStatus": "PUBLISHED",
                }
            ]
        }
        # Missing content filter and grounding
        mock_qc.get_ai_guardrail.return_value = {
            "aiGuardrail": {
                "contentPolicyConfig": {"filtersConfig": []},
                "topicPolicyConfig": {"topicsConfig": [{"name": "Harmful"}]},
                "sensitiveInformationPolicyConfig": {
                    "piiEntitiesConfig": [{"type": "SSN"}],
                    "regexesConfig": [],
                },
                "wordPolicyConfig": {
                    "wordsConfig": [{"text": "block"}],
                    "managedWordListsConfig": [],
                },
                "contextualGroundingPolicyConfig": {"filtersConfig": []},
            }
        }
        # Agent without guardrail
        mock_qc.list_ai_agents.return_value = {
            "aiAgentSummaries": [
                {
                    "aiAgentId": "agent-1",
                    "name": "UnprotectedAgent",
                    "visibilityStatus": "PUBLISHED",
                }
            ]
        }
        mock_qc.get_ai_agent.return_value = {
            "aiAgent": {"configuration": {"orchestrationAIAgentConfiguration": {}}}
        }

        result = check_q_guardrails(mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET)

        assert_finding_structure(result)
        assert result["check_name"] == "guardrails"
        assert result["status"] == "warn"
        assert result["recommendation"] is not None
        assert len(result["data"]["missing_filters"]) > 0


class TestCheckQGuardrailsFail:
    """Tests for fail scenario with no published guardrails."""

    def test_no_published_guardrails(self):
        """Guardrails exist but none are published returns fail."""
        mock_qc = MagicMock()
        mock_qc.list_ai_guardrails.__name__ = "list_ai_guardrails"
        mock_qc.list_ai_agents.__name__ = "list_ai_agents"
        mock_qc.list_ai_guardrails.return_value = {
            "aiGuardrailSummaries": [
                {
                    "aiGuardrailId": "gr-1",
                    "name": "DraftGuardrail",
                    "status": "ACTIVE",
                    "visibilityStatus": "DRAFT",
                }
            ]
        }
        mock_qc.get_ai_guardrail.return_value = {"aiGuardrail": {}}
        mock_qc.list_ai_agents.return_value = {"aiAgentSummaries": []}

        result = check_q_guardrails(mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET)

        assert_finding_structure(result)
        assert result["check_name"] == "guardrails"
        assert result["status"] == "fail"
        assert "No published AI guardrails" in result["detail"]
        assert result["recommendation"] is not None


class TestCheckQGuardrailsInfo:
    """Tests for info scenario when no guardrails exist."""

    def test_no_guardrails_at_all(self):
        """When no guardrails configured, returns info."""
        mock_qc = MagicMock()
        mock_qc.list_ai_guardrails.__name__ = "list_ai_guardrails"
        mock_qc.list_ai_guardrails.return_value = {"aiGuardrailSummaries": []}

        result = check_q_guardrails(mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET)

        assert_finding_structure(result)
        assert result["check_name"] == "guardrails"
        assert result["status"] == "info"
        assert result["data"]["guardrail_count"] == 0


class TestCheckQGuardrailsError:
    """Tests for error handling in guardrails check."""

    def test_exception_returns_error(self):
        """When an unexpected exception occurs, returns error finding."""
        mock_qc = MagicMock()
        mock_qc.list_ai_guardrails.__name__ = "list_ai_guardrails"
        # Trigger outer exception handler by raising from paginate_api_call
        with patch("ai_analyzer.paginate_api_call", side_effect=TypeError("Throttled")):
            result = check_q_guardrails(mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET)

        assert_finding_structure(result)
        assert result["check_name"] == "guardrails"
        assert result["status"] == "error"
        assert "Throttled" in result["detail"]
        assert result["data"] is None

    def test_time_budget_exceeded_propagates(self, patch_time_budget):
        """TimeBudgetExceeded propagates up the call stack."""
        mock_qc = MagicMock()
        patch_time_budget.side_effect = TimeBudgetExceeded("timeout")

        with pytest.raises(TimeBudgetExceeded):
            check_q_guardrails(mock_qc, ASSISTANT_ID, START_TIME, -1)


# ===========================================================================
# check_q_domain_encryption
# ===========================================================================


class TestCheckQDomainEncryptionPass:
    """Tests for pass scenario when CMK is configured."""

    def test_cmk_configured(self):
        """When a customer-managed KMS key is found, returns pass."""
        mock_qc = MagicMock()
        mock_qc.get_assistant.return_value = {
            "assistant": {
                "name": "TestAssistant",
                "status": "ACTIVE",
                "type": "AGENT",
                "serverSideEncryptionConfiguration": {
                    "kmsKeyId": "arn:aws:kms:us-east-1:123456789012:key/abc-123"
                },
            }
        }

        result = check_q_domain_encryption(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "domain_encryption"
        assert result["status"] == "pass"
        assert result["data"]["has_cmk"] is True
        assert result["recommendation"] is None


class TestCheckQDomainEncryptionWarn:
    """Tests for warn scenario when no CMK is configured."""

    def test_no_cmk(self):
        """When no customer-managed KMS key, returns warn."""
        mock_qc = MagicMock()
        mock_qc.get_assistant.return_value = {
            "assistant": {
                "name": "TestAssistant",
                "status": "ACTIVE",
                "type": "AGENT",
                "serverSideEncryptionConfiguration": {},
            }
        }

        result = check_q_domain_encryption(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "domain_encryption"
        assert result["status"] == "warn"
        assert result["data"]["has_cmk"] is False
        assert result["recommendation"] is not None

    def test_no_encryption_config_at_all(self):
        """When serverSideEncryptionConfiguration is absent, returns warn."""
        mock_qc = MagicMock()
        mock_qc.get_assistant.return_value = {
            "assistant": {
                "name": "TestAssistant",
                "status": "ACTIVE",
                "type": "AGENT",
            }
        }

        result = check_q_domain_encryption(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "domain_encryption"
        assert result["status"] == "warn"


class TestCheckQDomainEncryptionError:
    """Tests for error handling in domain encryption check."""

    def test_exception_returns_error(self):
        """When get_assistant raises an exception, returns error."""
        mock_qc = MagicMock()
        mock_qc.get_assistant.side_effect = Exception("AccessDenied")

        result = check_q_domain_encryption(
            mock_qc, ASSISTANT_ID, START_TIME, TIME_BUDGET
        )

        assert_finding_structure(result)
        assert result["check_name"] == "domain_encryption"
        assert result["status"] == "error"
        assert "AccessDenied" in result["detail"]
        assert result["data"] is None

    def test_time_budget_exceeded_propagates(self, patch_time_budget):
        """TimeBudgetExceeded propagates up the call stack."""
        mock_qc = MagicMock()
        patch_time_budget.side_effect = TimeBudgetExceeded("timeout")

        with pytest.raises(TimeBudgetExceeded):
            check_q_domain_encryption(mock_qc, ASSISTANT_ID, START_TIME, -1)


# --- Imports and constants for handler/error-isolation/logging tests ---
from ai_analyzer import (
    lambda_handler,
    DEFAULT_MAX_SECONDS,
    TOTAL_CHECKS,
    check_ai_agent_logging,
)
from botocore.exceptions import ClientError
from graceful_timeout import TimeBudgetExceeded
import time

VALID_EVENT = {
    "reviewId": "test-review-123",
    "instanceId": "test-instance-id",
    "instanceArn": "arn:aws:connect:us-east-1:123456789012:instance/test-instance-id",
    "accountId": "123456789012",
    "awsRegion": "us-east-1",
    "daysBack": 7,
    "componentType": "ai",
    "s3ReportingBucket": "test-bucket",
}

ASSISTANT_ID = "test-assistant-123"
ACCOUNT_ID = "123456789012"
AWS_REGION = "us-east-1"
START_TIME = time.time()
TIME_BUDGET = 300


def _make_client_error(code):
    return ClientError(
        {"Error": {"Code": code, "Message": f"Simulated {code}"}},
        "SomeOperation",
    )


class TestHandlerValidation:
    """Test that missing required fields produce error_result."""

    def test_missing_single_field(self):
        """Missing a single required field returns error_result with field name."""
        event = {**VALID_EVENT}
        del event["reviewId"]

        result = lambda_handler(event, None)

        assert result["status"] == "error"
        assert result["componentType"] == "ai"
        assert "reviewId" in result["error"]

    def test_missing_multiple_fields(self):
        """Missing multiple required fields returns error_result with all field names."""
        event = {**VALID_EVENT}
        del event["instanceId"]
        del event["accountId"]

        result = lambda_handler(event, None)

        assert result["status"] == "error"
        assert "instanceId" in result["error"]
        assert "accountId" in result["error"]

    def test_completely_empty_event(self):
        """Empty event returns error_result listing all required fields."""
        result = lambda_handler({}, None)

        assert result["status"] == "error"
        assert result["componentType"] == "ai"
        # All required fields should be mentioned
        for field in [
            "reviewId",
            "instanceId",
            "instanceArn",
            "accountId",
            "awsRegion",
            "daysBack",
            "componentType",
        ]:
            assert field in result["error"]

    def test_error_message_includes_field_names(self):
        """Error message from validation includes the missing field names."""
        event = {**VALID_EVENT}
        del event["daysBack"]
        del event["componentType"]

        result = lambda_handler(event, None)

        assert result["status"] == "error"
        assert "daysBack" in result["error"]
        assert "componentType" in result["error"]

    def test_component_type_defaults_to_ai_on_empty_event(self):
        """When componentType is missing from event, error_result uses default 'ai'."""
        event = {}
        result = lambda_handler(event, None)

        assert result["componentType"] == "ai"


class TestHandlerSuccessFlow:
    """Test valid payload with mocked checks produces success_result."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_success_result_returned(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """Valid payload with assistant found returns success_result."""
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = (
            "data/ai/year=2024/month=01/day=01/test-review-123.json"
        )

        result = lambda_handler(VALID_EVENT, None)

        assert result["status"] == "success"
        assert result["componentType"] == "ai"
        assert "durationMs" in result
        assert "s3ResultKey" in result

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_findings_has_all_pillar_keys_and_metadata(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """Findings structure has security, operational_excellence, resilience, and metadata."""
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = (
            "data/ai/year=2024/month=01/day=01/test-review-123.json"
        )

        result = lambda_handler(VALID_EVENT, None)

        findings = result["findings"]
        assert "security" in findings
        assert "operational_excellence" in findings
        assert "resilience" in findings
        assert "metadata" in findings

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_checks_completed_includes_all_checks(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """All 5 checks plus assistant_discovery appear in checks_completed."""
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = (
            "data/ai/year=2024/month=01/day=01/test-review-123.json"
        )

        result = lambda_handler(VALID_EVENT, None)

        checks = result["findings"]["metadata"]["checks_completed"]
        assert "assistant_discovery" in checks
        assert "agent_inventory" in checks
        assert "prompt_configuration" in checks
        assert "guardrails" in checks
        assert "domain_encryption" in checks
        assert "agent_logging" in checks


class TestHandlerDefaultMaxSeconds:
    """Test that default maxSeconds (540) is used when not provided."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.compute_time_budget")
    @patch("ai_analyzer.boto3.client")
    def test_default_max_seconds_when_not_provided(
        self,
        mock_boto_client,
        mock_compute_budget,
        mock_discover,
        mock_persist,
    ):
        """When maxSeconds is not in event, compute_time_budget is called with 540."""
        mock_compute_budget.return_value = 480
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        event = {**VALID_EVENT}
        # Ensure maxSeconds is not in event
        event.pop("maxSeconds", None)

        lambda_handler(event, None)

        mock_compute_budget.assert_called_once_with(DEFAULT_MAX_SECONDS)

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.compute_time_budget")
    @patch("ai_analyzer.boto3.client")
    def test_explicit_max_seconds_is_used(
        self,
        mock_boto_client,
        mock_compute_budget,
        mock_discover,
        mock_persist,
    ):
        """When maxSeconds is explicitly provided, it's passed to compute_time_budget."""
        mock_compute_budget.return_value = 240
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        event = {**VALID_EVENT, "maxSeconds": 300}

        lambda_handler(event, None)

        mock_compute_budget.assert_called_once_with(300)


class TestHandlerAssistantShortCircuit:
    """Test short-circuit when no assistant found."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_no_assistant_returns_success(
        self,
        mock_boto_client,
        mock_discover,
        mock_persist,
    ):
        """When discover_assistant_id returns None, handler returns success_result."""
        mock_discover.return_value = None
        mock_persist.return_value = (
            "data/ai/year=2024/month=01/day=01/test-review-123.json"
        )

        result = lambda_handler(VALID_EVENT, None)

        assert result["status"] == "success"
        assert result["componentType"] == "ai"

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_no_assistant_has_info_finding(
        self,
        mock_boto_client,
        mock_discover,
        mock_persist,
    ):
        """Short-circuit adds info finding with check_name='assistant_discovery'."""
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        op_ex = result["findings"]["operational_excellence"]
        assert len(op_ex) == 1
        assert op_ex[0]["check_name"] == "assistant_discovery"
        assert op_ex[0]["status"] == "info"

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_no_assistant_metadata_assistant_id_is_none(
        self,
        mock_boto_client,
        mock_discover,
        mock_persist,
    ):
        """When no assistant, metadata.assistant_id is None."""
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        assert result["findings"]["metadata"]["assistant_id"] is None

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_no_assistant_checks_completed_only_discovery(
        self,
        mock_boto_client,
        mock_discover,
        mock_persist,
    ):
        """When no assistant, checks_completed is ['assistant_discovery']."""
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        assert result["findings"]["metadata"]["checks_completed"] == [
            "assistant_discovery"
        ]

    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_no_assistant_does_not_call_checks(
        self,
        mock_boto_client,
        mock_discover,
        mock_persist,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
    ):
        """When no assistant found, no qconnect checks are called."""
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        lambda_handler(VALID_EVENT, None)

        mock_inventory.assert_not_called()
        mock_prompt.assert_not_called()
        mock_guardrails.assert_not_called()
        mock_encryption.assert_not_called()
        mock_logging.assert_not_called()

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_no_assistant_calls_persist_to_s3(
        self,
        mock_boto_client,
        mock_discover,
        mock_persist,
    ):
        """When no assistant, persist_to_s3 is called before returning."""
        mock_discover.return_value = None
        mock_persist.return_value = "some-key"

        lambda_handler(VALID_EVENT, None)

        mock_persist.assert_called_once()


class TestErrorIsolationGenericException:
    """Validates Req 12.1, 12.4: one check raises generic exception → others still execute."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_one_check_exception_others_still_execute(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """When agent_inventory raises a generic exception, other checks still run.

        Validates: Requirements 12.1, 12.4
        """
        mock_discover.return_value = "test-assistant-id"
        # agent_inventory raises an unexpected exception
        mock_inventory.side_effect = RuntimeError("Something went wrong")
        # Other checks return normally
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        # All other checks were called
        mock_prompt.assert_called_once()
        mock_guardrails.assert_called_once()
        mock_encryption.assert_called_once()
        mock_logging.assert_called_once()
        # Result is success (not error)
        assert result["status"] == "success"

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_generic_exception_records_error_finding(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """Generic exception in a check records an error finding with the check name.

        Validates: Requirements 12.4
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.side_effect = RuntimeError("Unexpected failure")
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        # Find the error finding for agent_inventory
        op_ex = result["findings"]["operational_excellence"]
        error_findings = [
            f
            for f in op_ex
            if f["check_name"] == "agent_inventory" and f["status"] == "error"
        ]
        assert len(error_findings) == 1
        assert "Unexpected failure" in error_findings[0]["detail"]

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_middle_check_exception_others_still_execute(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """When guardrails (check 3) raises, checks 4-5 still execute.

        Validates: Requirements 12.1
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        # guardrails raises
        mock_guardrails.side_effect = ValueError("Guardrails check error")
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        # Subsequent checks were called
        mock_encryption.assert_called_once()
        mock_logging.assert_called_once()
        assert result["status"] == "success"


class TestErrorIsolationClientError:
    """Validates Req 12.1: ClientError (AccessDenied/ResourceNotFound) → info finding, continue."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_access_denied_records_info_finding(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """AccessDeniedException in a check records info finding and continues.

        Validates: Requirements 12.1
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.side_effect = _make_client_error("AccessDeniedException")
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        # Info finding recorded
        op_ex = result["findings"]["operational_excellence"]
        info_findings = [
            f
            for f in op_ex
            if f["check_name"] == "agent_inventory" and f["status"] == "info"
        ]
        assert len(info_findings) == 1
        assert "AccessDeniedException" in info_findings[0]["detail"]
        # Other checks still ran
        mock_prompt.assert_called_once()
        mock_guardrails.assert_called_once()

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_resource_not_found_records_info_finding(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """ResourceNotFoundException in a check records info finding and continues.

        Validates: Requirements 12.1
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        # guardrails raises ResourceNotFoundException
        mock_guardrails.side_effect = _make_client_error("ResourceNotFoundException")
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        # Info finding recorded in security pillar for guardrails
        security = result["findings"]["security"]
        info_findings = [
            f
            for f in security
            if f["check_name"] == "guardrails" and f["status"] == "info"
        ]
        assert len(info_findings) == 1
        assert "ResourceNotFoundException" in info_findings[0]["detail"]
        # Subsequent checks still ran
        mock_encryption.assert_called_once()
        mock_logging.assert_called_once()


class TestChecksCompletedTracking:
    """Validates Req 12.5: checks_completed only contains successful checks."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_failed_check_not_in_checks_completed(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """Failed check name does NOT appear in metadata.checks_completed.

        Validates: Requirements 12.5
        """
        mock_discover.return_value = "test-assistant-id"
        # agent_inventory raises (should NOT be in checks_completed)
        mock_inventory.side_effect = RuntimeError("Fail")
        # prompt_configuration raises AccessDenied (should NOT be in checks_completed)
        mock_prompt.side_effect = _make_client_error("AccessDeniedException")
        mock_guardrails.return_value = {
            "check_name": "guardrails",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_encryption.return_value = {
            "check_name": "domain_encryption",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_logging.return_value = {
            "check_name": "agent_logging",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        checks = result["findings"]["metadata"]["checks_completed"]
        # Failed checks should NOT be in the list
        assert "agent_inventory" not in checks
        assert "prompt_configuration" not in checks
        # Successful checks should be present
        assert "assistant_discovery" in checks
        assert "guardrails" in checks
        assert "domain_encryption" in checks
        assert "agent_logging" in checks

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_all_checks_fail_only_discovery_in_completed(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """When all 5 checks fail, only assistant_discovery is in checks_completed.

        Validates: Requirements 12.5
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.side_effect = RuntimeError("Fail")
        mock_prompt.side_effect = RuntimeError("Fail")
        mock_guardrails.side_effect = RuntimeError("Fail")
        mock_encryption.side_effect = RuntimeError("Fail")
        mock_logging.side_effect = RuntimeError("Fail")
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        checks = result["findings"]["metadata"]["checks_completed"]
        assert checks == ["assistant_discovery"]


class TestTimeBudgetExceededBehavior:
    """Validates Req 4.3, 4.4, 4.5: TimeBudgetExceeded → partial result with correct metadata."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_before_first_check_returns_partial(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_persist,
    ):
        """TimeBudgetExceeded before first check → partial result with timed_out=True.

        Validates: Requirements 4.3, 4.4, 4.5
        """
        mock_discover.return_value = "test-assistant-id"
        # First call to check_time_budget (before agent_inventory) raises
        mock_check_time.side_effect = TimeBudgetExceeded("Budget exceeded")
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        assert result["status"] == "success"
        assert result["partial"] is True
        assert result["findings"]["metadata"]["timed_out"] is True

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_before_first_check_all_pillars_present_as_empty(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_persist,
    ):
        """TimeBudgetExceeded before first check → all pillar keys present as empty lists.

        Validates: Requirements 4.5
        """
        mock_discover.return_value = "test-assistant-id"
        mock_check_time.side_effect = TimeBudgetExceeded("Budget exceeded")
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        findings = result["findings"]
        assert "security" in findings
        assert "operational_excellence" in findings
        assert "resilience" in findings
        assert findings["security"] == []
        assert findings["operational_excellence"] == []
        assert findings["resilience"] == []

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_after_some_checks_preserves_findings(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """TimeBudgetExceeded after 2 checks → partial result with findings from completed checks.

        Validates: Requirements 4.3, 4.4
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }

        # Let first 2 check_time_budget calls pass, then raise on 3rd
        call_count = [0]

        def time_budget_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 3:
                raise TimeBudgetExceeded("Budget exceeded after 2 checks")

        mock_check_time.side_effect = time_budget_side_effect
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        assert result["status"] == "success"
        assert result["partial"] is True
        # Findings from first 2 checks should be present
        op_ex = result["findings"]["operational_excellence"]
        check_names = [f["check_name"] for f in op_ex]
        assert "agent_inventory" in check_names
        assert "prompt_configuration" in check_names
        # checks_completed should include the completed checks
        checks = result["findings"]["metadata"]["checks_completed"]
        assert "assistant_discovery" in checks
        assert "agent_inventory" in checks
        assert "prompt_configuration" in checks

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_collected_count_matches_checks_completed(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """Partial result collectedCount matches len(checks_completed).

        Validates: Requirements 4.4
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }
        mock_prompt.return_value = {
            "check_name": "prompt_configuration",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }

        call_count = [0]

        def time_budget_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 3:
                raise TimeBudgetExceeded("Budget exceeded")

        mock_check_time.side_effect = time_budget_side_effect
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        checks = result["findings"]["metadata"]["checks_completed"]
        # collectedCount should match the number of completed checks
        assert result["collectedCount"] == len(checks)

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_total_estimated_is_checks_plus_discovery(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_persist,
    ):
        """Partial result totalEstimated equals TOTAL_CHECKS + 1 (discovery).

        Validates: Requirements 4.4
        """
        mock_discover.return_value = "test-assistant-id"
        mock_check_time.side_effect = TimeBudgetExceeded("Budget exceeded")
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        assert result["totalEstimated"] == TOTAL_CHECKS + 1

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_returns_success_not_error(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_persist,
    ):
        """TimeBudgetExceeded returns success_result (not error_result), with partial=True.

        Validates: Requirements 4.3, 4.4
        """
        mock_discover.return_value = "test-assistant-id"
        mock_check_time.side_effect = TimeBudgetExceeded("Budget exceeded")
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        # Should be a success result, NOT an error result
        assert result["status"] == "success"
        assert result["partial"] is True
        # Should NOT have an 'error' key
        assert "error" not in result

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_timeout_persists_partial_to_s3(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_persist,
    ):
        """TimeBudgetExceeded calls persist_to_s3 with partial=True before returning.

        Validates: Requirements 4.3
        """
        mock_discover.return_value = "test-assistant-id"
        mock_check_time.side_effect = TimeBudgetExceeded("Budget exceeded")
        mock_persist.return_value = "some-key"

        lambda_handler(VALID_EVENT, None)

        mock_persist.assert_called_once()
        call_kwargs = mock_persist.call_args
        # persist_to_s3 should be called with partial=True
        assert call_kwargs[1].get("partial") is True or (
            len(call_kwargs[0]) > 2 and call_kwargs[0][2] is True
        )


class TestAllPillarKeysAlwaysPresent:
    """Validates Req 4.5: All three pillar keys always present even in partial/error scenarios."""

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_all_pillars_present_when_all_checks_fail(
        self,
        mock_boto_client,
        mock_discover,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """All three pillar keys present even when every check fails.

        Validates: Requirements 4.5
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.side_effect = RuntimeError("Fail")
        mock_prompt.side_effect = RuntimeError("Fail")
        mock_guardrails.side_effect = RuntimeError("Fail")
        mock_encryption.side_effect = RuntimeError("Fail")
        mock_logging.side_effect = RuntimeError("Fail")
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        findings = result["findings"]
        assert "security" in findings
        assert "operational_excellence" in findings
        assert "resilience" in findings
        # Each should be a list (even if containing error findings)
        assert isinstance(findings["security"], list)
        assert isinstance(findings["operational_excellence"], list)
        assert isinstance(findings["resilience"], list)

    @patch("ai_analyzer.persist_to_s3")
    @patch("ai_analyzer.check_ai_agent_logging")
    @patch("ai_analyzer.check_q_domain_encryption")
    @patch("ai_analyzer.check_q_guardrails")
    @patch("ai_analyzer.check_ai_prompt_configuration")
    @patch("ai_analyzer.check_ai_agent_inventory")
    @patch("ai_analyzer.check_time_budget")
    @patch("ai_analyzer.discover_assistant_id")
    @patch("ai_analyzer.boto3.client")
    def test_all_pillars_present_on_timeout_after_partial_execution(
        self,
        mock_boto_client,
        mock_discover,
        mock_check_time,
        mock_inventory,
        mock_prompt,
        mock_guardrails,
        mock_encryption,
        mock_logging,
        mock_persist,
    ):
        """All three pillar keys present when timeout occurs mid-way through checks.

        Validates: Requirements 4.5
        """
        mock_discover.return_value = "test-assistant-id"
        mock_inventory.return_value = {
            "check_name": "agent_inventory",
            "status": "pass",
            "detail": "ok",
            "recommendation": None,
            "data": None,
        }

        call_count = [0]

        def time_budget_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise TimeBudgetExceeded("Budget exceeded")

        mock_check_time.side_effect = time_budget_side_effect
        mock_persist.return_value = "some-key"

        result = lambda_handler(VALID_EVENT, None)

        findings = result["findings"]
        assert "security" in findings
        assert "operational_excellence" in findings
        assert "resilience" in findings


class TestCheckAiAgentLoggingPass:
    """Tests for the pass scenario when matching deliveries are found."""

    def test_direct_match_in_source_name(self):
        """When assistant_id appears directly in deliverySourceName, returns pass."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {
            "deliveries": [
                {
                    "id": "dlv-001",
                    "deliverySourceName": f"WdDeliverySource-{ASSISTANT_ID}",
                    "deliveryDestinationType": "S3",
                }
            ]
        }

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert result["check_name"] == "agent_logging"
        assert result["status"] == "pass"
        assert "1 delivery/deliveries found" in result["detail"]
        assert result["data"]["deliveries_found"] == 1
        assert result["data"]["deliveries"][0]["id"] == "dlv-001"
        assert (
            result["data"]["assistant_arn"]
            == f"arn:aws:wisdom:{AWS_REGION}:{ACCOUNT_ID}:assistant/{ASSISTANT_ID}"
        )

    def test_match_via_delivery_source_arn(self):
        """When source name contains WdDeliverySource and ARN matches, returns pass."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {
            "deliveries": [
                {
                    "id": "dlv-002",
                    "deliverySourceName": "WdDeliverySource-generic",
                    "deliveryDestinationType": "CloudWatchLogs",
                }
            ]
        }
        mock_logs.describe_delivery_sources.return_value = {
            "deliverySources": [
                {
                    "name": "WdDeliverySource-generic",
                    "resourceArn": f"arn:aws:wisdom:{AWS_REGION}:{ACCOUNT_ID}:assistant/{ASSISTANT_ID}",
                }
            ]
        }

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert result["status"] == "pass"
        assert result["data"]["deliveries_found"] == 1

    def test_multiple_matching_deliveries(self):
        """When multiple deliveries match, all are returned."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {
            "deliveries": [
                {
                    "id": "dlv-001",
                    "deliverySourceName": f"src-{ASSISTANT_ID}-a",
                    "deliveryDestinationType": "S3",
                },
                {
                    "id": "dlv-002",
                    "deliverySourceName": f"src-{ASSISTANT_ID}-b",
                    "deliveryDestinationType": "CloudWatchLogs",
                },
            ]
        }

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert result["status"] == "pass"
        assert result["data"]["deliveries_found"] == 2
        assert len(result["data"]["deliveries"]) == 2


class TestCheckAiAgentLoggingWarn:
    """Tests for the warn scenario when no matching deliveries are found."""

    def test_no_deliveries_at_all(self):
        """When describe_deliveries returns empty list, returns warn."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {"deliveries": []}

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert result["check_name"] == "agent_logging"
        assert result["status"] == "warn"
        assert "No CloudWatch Vended Logs delivery configured" in result["detail"]
        assert result["recommendation"] is not None
        assert result["data"]["deliveries_found"] == 0
        assert result["data"]["deliveries"] == []

    def test_deliveries_exist_but_none_match(self):
        """When deliveries exist but none match assistant_id, returns warn."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {
            "deliveries": [
                {
                    "id": "dlv-other",
                    "deliverySourceName": "some-other-source",
                    "deliveryDestinationType": "S3",
                }
            ]
        }

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert result["status"] == "warn"
        assert result["data"]["deliveries_found"] == 0


class TestCheckAiAgentLoggingError:
    """Tests for error handling."""

    def test_api_exception_returns_error(self):
        """When describe_deliveries raises an exception, returns error finding."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.side_effect = Exception("AccessDenied")

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert result["check_name"] == "agent_logging"
        assert result["status"] == "error"
        assert "AccessDenied" in result["detail"]
        assert result["data"] is None

    def test_time_budget_exceeded_is_reraised(self, patch_time_budget):
        """When check_time_budget raises TimeBudgetExceeded, it propagates."""
        patch_time_budget.side_effect = TimeBudgetExceeded("Budget exceeded")
        mock_logs = MagicMock()

        with pytest.raises(TimeBudgetExceeded):
            check_ai_agent_logging(
                mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, -1
            )

    def test_delivery_source_error_is_gracefully_handled(self):
        """When describe_delivery_sources fails for one source, continues without crashing."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {
            "deliveries": [
                {
                    "id": "dlv-003",
                    "deliverySourceName": "WdDeliverySource-something",
                    "deliveryDestinationType": "S3",
                }
            ]
        }
        mock_logs.describe_delivery_sources.side_effect = Exception("Throttled")

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        # Should still return a result (warn, since the match couldn't be confirmed)
        assert result["status"] == "warn"
        assert result["data"]["deliveries_found"] == 0


class TestCheckAiAgentLoggingStructure:
    """Tests for output structure compliance."""

    def test_output_has_required_keys(self):
        """Result dict must have all Structured_Finding keys."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {"deliveries": []}

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert "check_name" in result
        assert "status" in result
        assert "detail" in result
        assert "recommendation" in result
        assert "data" in result

    def test_detail_max_1024_chars(self):
        """Detail field must be at most 1024 characters."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {"deliveries": []}

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert len(result["detail"]) <= 1024

    def test_no_html_in_detail(self):
        """Detail field must not contain HTML tags."""
        mock_logs = MagicMock()
        mock_logs.describe_deliveries.return_value = {
            "deliveries": [
                {
                    "id": "dlv-001",
                    "deliverySourceName": f"src-{ASSISTANT_ID}",
                    "deliveryDestinationType": "S3",
                }
            ]
        }

        result = check_ai_agent_logging(
            mock_logs, ASSISTANT_ID, ACCOUNT_ID, AWS_REGION, START_TIME, TIME_BUDGET
        )

        assert "<" not in result["detail"]
        assert ">" not in result["detail"]
