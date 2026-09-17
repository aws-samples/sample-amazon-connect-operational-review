"""Cross-Path Parity Tests — CFN vs Terraform IAM Policy Names.

This module asserts that the set of inline IAM policy names attached to the
shared Lambda execution role is identical between the CloudFormation template
and the Terraform module.

Specifically:

- CFN side: ``Resources.SharedLambdaRole.Properties.Policies[*].PolicyName``
- TF  side: ``aws_iam_role_policy.*.name`` for every resource whose
  ``role`` attribute references ``aws_iam_role.shared_lambda`` (i.e.
  ``aws_iam_role.shared_lambda.id`` or ``.name``).

Both sets must be equal. Any drift — a policy renamed on only one side, or a
new policy added to only one side — is a regression that this test catches.

Validates: Requirements R7.3 (rc6-followup-defects)

Historical context:

- QB-49 (rc.5): introduced the SharedLambdaRole naming alignment and the
  underlying CFN vs TF IAM parity concern.
- R7 / TF-QB-29 (rc.6): the TF policy ``shared_lambda_s3_write`` was
  originally named ``"S3WriteResults"`` while the corresponding CFN policy
  was named ``"S3WriteAnalyzerResults"``. Fix 7 renamed the TF policy to
  match. This test guards against that class of drift going forward.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml

# Reuse the CFN-tag-tolerant YAML loader from the rc4 helpers.
from rc4_iac_parsers import CFNLoader

# ---------------------------------------------------------------------------
# Paths
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
def cfn_template() -> dict:
    """Parse the full CloudFormation template once per module."""
    with open(CFN_PATH, "r") as f:
        return yaml.load(f, Loader=CFNLoader)


@pytest.fixture(scope="module")
def tf_source() -> str:
    """Read the Terraform module source once per module."""
    with open(TF_PATH, "r") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def _cfn_shared_lambda_role_policy_names(cfn_template: dict) -> set[str]:
    """Return the set of inline policy names on the CFN SharedLambdaRole."""
    resources = cfn_template["Resources"]
    assert "SharedLambdaRole" in resources, (
        "CFN template is missing SharedLambdaRole — cannot compare policy "
        "names across CFN and Terraform."
    )
    role = resources["SharedLambdaRole"]
    assert role.get("Type") == "AWS::IAM::Role", (
        f"CFN SharedLambdaRole has unexpected Type {role.get('Type')!r} — "
        "expected AWS::IAM::Role."
    )
    policies = role.get("Properties", {}).get("Policies", []) or []
    names = {p["PolicyName"] for p in policies if "PolicyName" in p}
    assert names, (
        "CFN SharedLambdaRole.Properties.Policies has no PolicyName entries — "
        "template shape changed."
    )
    return names


# Matches an aws_iam_role_policy resource block, capturing its Terraform
# resource label, its ``name = "..."`` attribute, and its ``role = ...``
# attribute value (unquoted expression). The body is captured non-greedily
# up to the closing brace at the start of a line.
_TF_ROLE_POLICY_RE = re.compile(
    r'resource\s+"aws_iam_role_policy"\s+"(?P<label>[^"]+)"\s*\{'
    r"(?P<body>.*?)^\}",
    re.DOTALL | re.MULTILINE,
)
_TF_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_TF_ROLE_RE = re.compile(r"^\s*role\s*=\s*(.+?)\s*$", re.MULTILINE)


def _tf_shared_lambda_role_policy_names(tf_source: str) -> set[str]:
    """Return the set of ``name`` values for every ``aws_iam_role_policy``
    resource whose ``role`` references ``aws_iam_role.shared_lambda``.
    """
    names: set[str] = set()
    for match in _TF_ROLE_POLICY_RE.finditer(tf_source):
        body = match.group("body")
        role_match = _TF_ROLE_RE.search(body)
        if not role_match:
            continue
        role_expr = role_match.group(1).strip()
        # Only include policies attached to the shared_lambda role.
        if "aws_iam_role.shared_lambda." not in role_expr:
            continue
        name_match = _TF_NAME_RE.search(body)
        assert name_match, (
            "aws_iam_role_policy.{label} is attached to aws_iam_role."
            "shared_lambda but has no `name` attribute — cannot compare "
            "policy names across CFN and Terraform.".format(label=match.group("label"))
        )
        names.add(name_match.group(1))
    assert names, (
        "No aws_iam_role_policy resources attached to aws_iam_role."
        "shared_lambda were found in parallel.tf — Terraform shape changed."
    )
    return names


# ---------------------------------------------------------------------------
# Parity Test
# ---------------------------------------------------------------------------


class TestSharedLambdaRolePolicyNameParity:
    """Assert CFN and Terraform inline policy names for the shared Lambda
    execution role are the same set.

    **Validates: Requirements R7.3 (rc6-followup-defects)**
    """

    def test_shared_lambda_role_policy_names_match_across_cfn_and_tf(
        self, cfn_template: dict, tf_source: str
    ):
        """The set of policy names on the shared Lambda role must be identical
        between the CFN template and the Terraform module.

        A mismatch indicates one deployment path has drifted from the other —
        either a policy was renamed on only one side (like the original R7 /
        TF-QB-29 defect where TF used ``S3WriteResults`` while CFN used
        ``S3WriteAnalyzerResults``), or a policy was added or removed on only
        one side. Either case produces divergent IAM behavior between the two
        deployment paths and must be caught.
        """
        cfn_names = _cfn_shared_lambda_role_policy_names(cfn_template)
        tf_names = _tf_shared_lambda_role_policy_names(tf_source)

        only_in_cfn = cfn_names - tf_names
        only_in_tf = tf_names - cfn_names

        assert not only_in_cfn and not only_in_tf, (
            "CFN vs Terraform shared Lambda role inline policy names have "
            "drifted.\n"
            f"  CFN SharedLambdaRole.Policies[*].PolicyName (count={len(cfn_names)}): "
            f"{sorted(cfn_names)}\n"
            f"  TF  aws_iam_role_policy.*.name for shared_lambda "
            f"(count={len(tf_names)}): {sorted(tf_names)}\n"
            f"  Only in CFN: {sorted(only_in_cfn)}\n"
            f"  Only in TF:  {sorted(only_in_tf)}"
        )
