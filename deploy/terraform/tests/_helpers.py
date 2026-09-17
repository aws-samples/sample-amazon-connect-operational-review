"""
Shared helper functions for IaC property tests.

These functions extract and parse specific blocks from Terraform and
CloudFormation files for use in property assertions.
"""

import re


def extract_ssm_parameter_config_block(tf_content: str) -> str:
    """Extract the aws_ssm_parameter 'config' resource block from Terraform."""
    match = re.search(
        r'resource\s+"aws_ssm_parameter"\s+"config"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        tf_content,
        re.DOTALL,
    )
    return match.group(1) if match else ""


def extract_lifecycle_block(resource_block: str) -> str:
    """Extract the lifecycle block from a Terraform resource block."""
    match = re.search(
        r'lifecycle\s*\{([^}]*)\}',
        resource_block,
        re.DOTALL,
    )
    return match.group(1) if match else ""


def extract_lambda_function_blocks(tf_content: str) -> list[dict]:
    """Extract all aws_lambda_function resources and their role attributes.

    Returns a list of dicts with 'name' (resource name) and 'role' (role attribute value).
    """
    results = []
    pattern = re.compile(
        r'resource\s+"aws_lambda_function"\s+"(\w+)"\s*\{(.*?)(?=\nresource\s+"|\Z)',
        re.DOTALL,
    )
    for match in pattern.finditer(tf_content):
        resource_name = match.group(1)
        block_content = match.group(2)

        role_match = re.search(
            r'^\s*role\s*=\s*(.+?)$',
            block_content,
            re.MULTILINE,
        )
        if role_match:
            role_value = role_match.group(1).strip()
            results.append({"name": resource_name, "role": role_value})
    return results


def extract_qconnect_actions_from_cfn(cft_content: str) -> set:
    """Extract all qconnect:* action names from the CFN template."""
    return set(re.findall(r"- (qconnect:\w+)", cft_content))


def extract_qconnect_actions_from_tf(tf_content: str) -> set:
    """Extract all qconnect:* action names from the Terraform file."""
    return set(re.findall(r'"(qconnect:\w+)"', tf_content))


def extract_qconnect_policy_resource_cfn(cft_content: str) -> str:
    """Extract the Resource field for the qconnect policy statement in CFN.

    Anchors on qconnect:ListAssistantAssociations, non-greedy skips through any
    intervening actions/comments (e.g. paired wisdom:* actions), and captures
    the Resource block up to the next sibling policy or dedent.
    """
    match = re.search(
        r'qconnect:ListAssistantAssociations[\s\S]*?Resource:\s*\n'
        r'([\s\S]*?)(?=\n\s*- PolicyName|\n\s{0,10}[A-Za-z]+:)',
        cft_content,
    )
    return match.group(1).strip() if match else ""


def extract_qconnect_policy_resource_tf(tf_content: str) -> str:
    """Extract the Resource field for the qconnect policy statement in Terraform.

    Searches for the block that has qconnect actions and captures the Resource value.
    Handles both single-line string and multi-line list formats.
    """
    match = re.search(
        r'"qconnect:ListAssistantAssociations"[\s\S]*?\]\s*\n'
        r'\s*Resource\s*=\s*(.+)',
        tf_content,
    )
    if not match:
        return ""
    first_line = match.group(1).strip()
    if not first_line.startswith("["):
        return first_line
    start_pos = match.start(1) + match.group(1).index("[")
    bracket_depth = 0
    for i in range(start_pos, len(tf_content)):
        if tf_content[i] == "[":
            bracket_depth += 1
        elif tf_content[i] == "]":
            bracket_depth -= 1
            if bracket_depth == 0:
                return tf_content[start_pos : i + 1].strip()
    return first_line
