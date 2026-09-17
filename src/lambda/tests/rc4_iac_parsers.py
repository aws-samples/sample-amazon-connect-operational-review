"""Shared IaC parser helpers for the rc.4 QConnect/Wisdom test module.

``test_pbt_rc4_qconnect_wisdom.py`` (Properties 4, 5, 6 — bug condition,
preservation, and CFN↔TF parity) loads the ``QConnectReadOnly`` policy
Statement out of the CloudFormation template and out of the Terraform module.
This module owns the parsers so the test file imports a single implementation.

The exposed surface:

* :data:`CFN_PATH`, :data:`TF_PATH` — repo-relative absolute paths to the two
  IaC targets.
* :class:`CFNLoader` — a ``yaml.SafeLoader`` subclass that tolerates
  CloudFormation intrinsic tags (``!Sub``, ``!Ref``, ``!GetAtt``, etc.).
* :func:`load_cfn_qconnect_statement` — returns the ``QConnectReadOnly``
  ``Statement`` dict (with ``Effect``, ``Action``, ``Resource`` fields) from
  the CFN template.
* :func:`load_tf_qconnect_block` — returns the raw HCL body of the
  ``aws_iam_role_policy.shared_lambda_qconnect_readonly`` resource.
* :func:`load_tf_qconnect_actions` — returns the Action list, in file order.
* :func:`load_tf_qconnect_resources` — returns the Resource list, in file order.
* :func:`qconnect_actions` — filter helper: keeps only ``qconnect:*`` entries.
* :func:`wisdom_counterpart` — maps ``qconnect:X`` -> ``wisdom:X``.

Regex-based extraction is used for the Terraform target to avoid pulling in
a full HCL parser. The regexes are pinned to the specific
``shared_lambda_qconnect_readonly`` resource so they will not silently match
some other policy if the file evolves.
"""

import os
import re
from typing import List

import yaml

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
# CloudFormation YAML loader — handles intrinsic tags such as !Sub
# ---------------------------------------------------------------------------


class CFNLoader(yaml.SafeLoader):
    """YAML loader that tolerates CloudFormation intrinsic function tags."""


_CFN_TAGS = [
    "!Ref",
    "!GetAtt",
    "!Sub",
    "!Join",
    "!Select",
    "!Split",
    "!If",
    "!Equals",
    "!Not",
    "!And",
    "!Or",
    "!Condition",
    "!FindInMap",
    "!Base64",
    "!Cidr",
    "!ImportValue",
    "!GetAZs",
    "!Transform",
]


for _tag in _CFN_TAGS:
    CFNLoader.add_constructor(
        _tag,
        lambda loader, node, _t=_tag: (
            loader.construct_scalar(node)
            if isinstance(node, yaml.ScalarNode)
            else loader.construct_sequence(node)
            if isinstance(node, yaml.SequenceNode)
            else loader.construct_mapping(node)
        ),
    )


# ---------------------------------------------------------------------------
# CloudFormation parser
# ---------------------------------------------------------------------------


def load_cfn_qconnect_statement() -> dict:
    """Parse the CFN template and return the QConnectReadOnly Statement dict.

    Walks all resources' ``Properties.Policies`` list and returns the first
    (and only) ``Statement`` entry of the policy whose ``PolicyName`` is
    ``QConnectReadOnly``. The returned dict has ``Effect``, ``Action``
    (list-typed on the current template), and ``Resource`` (list) fields.
    """
    with open(CFN_PATH, "r") as f:
        template = yaml.load(f, Loader=CFNLoader)

    for resource in template.get("Resources", {}).values():
        props = resource.get("Properties") or {}
        for policy in props.get("Policies", []) or []:
            if policy.get("PolicyName") == "QConnectReadOnly":
                statements = policy["PolicyDocument"]["Statement"]
                for stmt in statements:
                    action = stmt.get("Action")
                    if action:
                        return stmt
    raise AssertionError("QConnectReadOnly policy Statement not found in CFN template")


# ---------------------------------------------------------------------------
# Terraform parser
# ---------------------------------------------------------------------------


def load_tf_qconnect_block() -> str:
    """Return the raw HCL body of shared_lambda_qconnect_readonly.

    Uses a targeted regex against the ``aws_iam_role_policy`` resource of that
    exact name to avoid introducing a full HCL parser dependency.
    """
    with open(TF_PATH, "r") as f:
        contents = f.read()

    resource_match = re.search(
        r'resource\s+"aws_iam_role_policy"\s+"shared_lambda_qconnect_readonly"\s*\{'
        r"(?P<body>.*?)^}",
        contents,
        re.DOTALL | re.MULTILINE,
    )
    assert resource_match, (
        f"shared_lambda_qconnect_readonly resource not found in {TF_PATH}"
    )
    return resource_match.group("body")


def load_tf_qconnect_actions() -> List[str]:
    """Return the QConnectReadOnly Action list from Terraform, in file order."""
    body = load_tf_qconnect_block()
    action_match = re.search(
        r"Action\s*=\s*\[(?P<items>.*?)\]",
        body,
        re.DOTALL,
    )
    assert action_match, "Action list not found inside shared_lambda_qconnect_readonly"
    items_block = action_match.group("items")
    actions = re.findall(r'"([^"]+)"', items_block)
    assert actions, "Extracted Action list is empty — TF parser likely broken"
    return actions


def load_tf_qconnect_resources() -> List[str]:
    """Return the QConnectReadOnly Resource list from Terraform, in file order."""
    body = load_tf_qconnect_block()
    resource_match = re.search(
        r"Resource\s*=\s*\[(?P<items>.*?)\]",
        body,
        re.DOTALL,
    )
    assert resource_match, (
        "Resource list not found inside shared_lambda_qconnect_readonly"
    )
    items_block = resource_match.group("items")
    resources = re.findall(r'"([^"]+)"', items_block)
    assert resources, "Extracted Resource list is empty — TF parser likely broken"
    return resources


# ---------------------------------------------------------------------------
# Small helpers used by tests over the parsed action lists
# ---------------------------------------------------------------------------


def qconnect_actions(actions: List[str]) -> List[str]:
    """Filter ``actions`` to just the entries starting with ``qconnect:``."""
    return [a for a in actions if a.startswith("qconnect:")]


def wisdom_counterpart(action: str) -> str:
    """Map ``qconnect:X`` -> ``wisdom:X``. The input must be a qconnect action."""
    assert action.startswith("qconnect:"), action
    return "wisdom:" + action.split(":", 1)[1]
