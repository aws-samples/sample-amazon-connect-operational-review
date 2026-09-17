"""RC4 QB-41 regression tests: QConnectReadOnly policy grants wisdom-namespace actions.

Consolidates the bug-condition, preservation, and CFN↔TF parity properties for
QB-41 into a single module. The QB-41 fix expanded the ``QConnectReadOnly``
policy Action list from 14 to 28 entries in both the CloudFormation template
and the Terraform module by adding a ``wisdom:X`` counterpart for every
existing ``qconnect:X`` action.

Four regression invariants are covered:

* **Bug condition (Property 4)** — every ``qconnect:X`` in the policy has a
  matching ``wisdom:X`` counterpart in both the CFN and TF Action lists.
* **Preservation of qconnect grants (Property 5a)** — the 14 baseline
  ``qconnect:*`` action entries in each file appear in the same order they
  did on unfixed rc.4 (the fix appended the wisdom block below).
* **Preservation of Resource scoping (Property 5b)** — the 2-entry Resource
  list in each file is byte-identical to the rc.4 baseline.
* **CFN↔TF parity (Property 6)** — both Action lists are equal as sets and
  contain exactly 28 unique entries with no duplicates.

Shared IaC parsers live in :mod:`rc4_iac_parsers`.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6.
"""

import pytest
import yaml

from rc4_iac_parsers import (
    load_cfn_qconnect_statement,
    load_tf_qconnect_actions,
    load_tf_qconnect_resources,
    qconnect_actions,
    wisdom_counterpart,
)

# Load the IaC action/resource lists once at collection time so parametrize
# can enumerate concrete action names.
try:
    _CFN_STMT = load_cfn_qconnect_statement()
    _CFN_ACTIONS = list(_CFN_STMT["Action"])
    _CFN_RESOURCES = list(_CFN_STMT["Resource"])
    _TF_ACTIONS = load_tf_qconnect_actions()
    _TF_RESOURCES = load_tf_qconnect_resources()
except (AssertionError, FileNotFoundError, yaml.YAMLError) as exc:  # pragma: no cover
    pytest.fail(f"Failed to load QConnectReadOnly policy data: {exc}")


# ---------------------------------------------------------------------------
# Baselines — the observed qconnect ordering per file and the 2-entry Resource
# list. The QB-41 fix appended the wisdom:* counterparts below the existing
# qconnect:* block, so these baselines must continue to hold after the fix.
# ---------------------------------------------------------------------------


EXPECTED_CFN_QCONNECT_ACTIONS = (
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
)

EXPECTED_TF_QCONNECT_ACTIONS = (
    "qconnect:ListAIAgents",
    "qconnect:GetAIAgent",
    "qconnect:ListAIAgentVersions",
    "qconnect:ListAIPrompts",
    "qconnect:GetAIPrompt",
    "qconnect:ListAIPromptVersions",
    "qconnect:ListAIGuardrails",
    "qconnect:GetAIGuardrail",
    "qconnect:GetAssistant",
    "qconnect:ListAssistants",
    "qconnect:ListKnowledgeBases",
    "qconnect:GetKnowledgeBase",
    "qconnect:ListContents",
    "qconnect:ListAssistantAssociations",
)

# Resource lists differ by file syntax (CFN uses ${AWS::AccountId}; TF uses the
# aws_caller_identity data-source interpolation), so preservation is asserted
# per-file.
EXPECTED_CFN_RESOURCES = (
    "arn:aws:qconnect:*:${AWS::AccountId}:*",
    "arn:aws:wisdom:*:${AWS::AccountId}:*",
)

EXPECTED_TF_RESOURCES = (
    "arn:aws:qconnect:*:${data.aws_caller_identity.current.account_id}:*",
    "arn:aws:wisdom:*:${data.aws_caller_identity.current.account_id}:*",
)


# ---------------------------------------------------------------------------
# Property 4 — Bug condition: every qconnect:X has a wisdom:X counterpart.
# Parametrized on the qconnect:* entries discovered in each file, so a
# regression that drops any single wisdom:* counterpart surfaces as a targeted
# per-action failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("qconnect_action", qconnect_actions(_CFN_ACTIONS))
def test_cfn_action_list_grants_wisdom_counterpart_for_every_qconnect_action(
    qconnect_action,
):
    """For every ``qconnect:X`` in the CFN Action list, ``wisdom:X`` SHALL also
    be present.

    Validates: Requirements 2.1, 2.5.
    """
    counterpart = wisdom_counterpart(qconnect_action)
    assert counterpart in _CFN_ACTIONS, (
        f"QB-41 regression: CFN Action list grants {qconnect_action!r} but "
        f"not its wisdom-namespace counterpart {counterpart!r}. Both must be "
        "granted because AWS's IAM evaluation is inconsistent about the "
        "qconnect↔wisdom aliasing."
    )


@pytest.mark.parametrize("qconnect_action", qconnect_actions(_TF_ACTIONS))
def test_tf_action_list_grants_wisdom_counterpart_for_every_qconnect_action(
    qconnect_action,
):
    """For every ``qconnect:X`` in the TF Action list, ``wisdom:X`` SHALL also
    be present.

    Validates: Requirements 2.2, 2.5.
    """
    counterpart = wisdom_counterpart(qconnect_action)
    assert counterpart in _TF_ACTIONS, (
        f"QB-41 regression: TF Action list grants {qconnect_action!r} but "
        f"not its wisdom-namespace counterpart {counterpart!r}."
    )


# ---------------------------------------------------------------------------
# Property 5a — Preservation: qconnect:* ordering unchanged per file.
# Asserting the tuple equality subsumes any per-action "still present" check.
# ---------------------------------------------------------------------------


def test_cfn_qconnect_actions_appear_in_expected_baseline_order():
    """CFN ``qconnect:*`` actions SHALL match the rc.4 baseline order.

    Validates: Requirement 2.3.
    """
    actual = tuple(qconnect_actions(_CFN_ACTIONS))
    assert actual == EXPECTED_CFN_QCONNECT_ACTIONS, (
        "Preservation violated: CFN QConnectReadOnly qconnect:* ordering "
        "changed.\n"
        f"  Expected: {EXPECTED_CFN_QCONNECT_ACTIONS}\n"
        f"  Actual:   {actual}"
    )


def test_tf_qconnect_actions_appear_in_expected_baseline_order():
    """Terraform ``qconnect:*`` actions SHALL match the rc.4 baseline order.

    Validates: Requirement 2.3.
    """
    actual = tuple(qconnect_actions(_TF_ACTIONS))
    assert actual == EXPECTED_TF_QCONNECT_ACTIONS, (
        "Preservation violated: Terraform QConnectReadOnly qconnect:* "
        "ordering changed.\n"
        f"  Expected: {EXPECTED_TF_QCONNECT_ACTIONS}\n"
        f"  Actual:   {actual}"
    )


# ---------------------------------------------------------------------------
# Property 5b — Preservation: Resource list unchanged per file.
# ---------------------------------------------------------------------------


def test_cfn_resource_list_matches_baseline_verbatim():
    """CFN Resource list SHALL equal the rc.4 baseline exactly, in order.

    Validates: Requirement 2.4.
    """
    actual = tuple(_CFN_RESOURCES)
    assert actual == EXPECTED_CFN_RESOURCES, (
        "Preservation violated: CFN QConnectReadOnly Resource list changed.\n"
        f"  Expected: {EXPECTED_CFN_RESOURCES}\n"
        f"  Actual:   {actual}"
    )


def test_tf_resource_list_matches_baseline_verbatim():
    """Terraform Resource list SHALL equal the rc.4 baseline exactly, in order.

    Validates: Requirement 2.4.
    """
    actual = tuple(_TF_RESOURCES)
    assert actual == EXPECTED_TF_RESOURCES, (
        "Preservation violated: Terraform QConnectReadOnly Resource list "
        "changed.\n"
        f"  Expected: {EXPECTED_TF_RESOURCES}\n"
        f"  Actual:   {actual}"
    )


# ---------------------------------------------------------------------------
# Property 6 — Parity: CFN and TF Action lists are equal as sets, both files
# contain exactly 28 unique entries with no duplicates.
# ---------------------------------------------------------------------------


def test_cfn_and_tf_action_lists_are_equal_as_sets_with_28_unique_entries():
    """CFN and TF Action lists SHALL be equal as sets, each with 28 unique
    entries and no duplicates.

    Validates: Requirements 2.5, 2.6.
    """
    # Duplicate check first — the set-equality assertion is only meaningful
    # if neither list contains duplicates.
    assert len(_CFN_ACTIONS) == len(set(_CFN_ACTIONS)), (
        "Preservation violated: CFN QConnectReadOnly Action list contains "
        "duplicate entries."
    )
    assert len(_TF_ACTIONS) == len(set(_TF_ACTIONS)), (
        "Preservation violated: Terraform QConnectReadOnly Action list "
        "contains duplicate entries."
    )

    cfn_set = frozenset(_CFN_ACTIONS)
    tf_set = frozenset(_TF_ACTIONS)

    only_in_cfn = cfn_set - tf_set
    only_in_tf = tf_set - cfn_set
    assert cfn_set == tf_set, (
        "Parity violated: CFN and Terraform QConnectReadOnly Action sets "
        "diverged.\n"
        f"  In CFN only:  {sorted(only_in_cfn)}\n"
        f"  In TF only:   {sorted(only_in_tf)}"
    )

    assert len(cfn_set) == 28, (
        f"Parity violated: expected 28 unique Action entries (14 qconnect:* "
        f"+ 14 wisdom:*); got {len(cfn_set)}. Either an action was added or "
        "removed outside the expected fix, or the parser is broken."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
