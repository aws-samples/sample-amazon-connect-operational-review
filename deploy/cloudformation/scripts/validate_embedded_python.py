#!/usr/bin/env python3
"""Validate embedded Python code in CloudFormation Lambda ZipFile blocks.

Parses the CFT template, extracts all inline Lambda code (Code.ZipFile properties
under AWS::Lambda::Function resources), and runs py_compile on each one.
"""

import os
import py_compile
import sys
import tempfile

import yaml


TEMPLATE_FILE = "CFT-AmazonConnectOperationsReview.yml"

# CloudFormation intrinsic function tags that yaml.safe_load doesn't understand.
# We register constructors that pass through the values so we can parse the template.
CFN_TAGS = [
    "!Ref",
    "!Sub",
    "!GetAtt",
    "!Select",
    "!Split",
    "!Join",
    "!Equals",
    "!If",
    "!Not",
    "!And",
    "!Or",
    "!Condition",
    "!FindInMap",
    "!Base64",
    "!Cidr",
    "!GetAZs",
    "!ImportValue",
    "!Transform",
]


def _cfn_constructor(loader, tag_suffix, node):
    """Generic constructor for CloudFormation intrinsic function tags."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


# Register all CFN tags with the SafeLoader
for tag in CFN_TAGS:
    yaml.add_constructor(tag, lambda loader, node, t=tag: _cfn_constructor(loader, t, node), yaml.SafeLoader)

# Also handle any unknown tags gracefully
yaml.add_multi_constructor("!", _cfn_constructor, yaml.SafeLoader)


def find_zipfile_blocks(template: dict) -> list[tuple[str, str]]:
    """Walk the resource tree and extract ZipFile code blocks.

    Returns a list of (logical_id, code_string) tuples.
    """
    results = []
    resources = template.get("Resources", {})

    for logical_id, resource in resources.items():
        if resource.get("Type") != "AWS::Lambda::Function":
            continue

        properties = resource.get("Properties", {})
        code = properties.get("Code", {})

        # Code.ZipFile contains the inline Python source
        if isinstance(code, dict) and "ZipFile" in code:
            zipfile_value = code["ZipFile"]
            if isinstance(zipfile_value, str):
                results.append((logical_id, zipfile_value))

    return results


def validate_code(logical_id: str, code: str) -> bool:
    """Write code to a temp file, compile it, and report result."""
    fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix=f"validate_{logical_id}_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)

        py_compile.compile(tmp_path, doraise=True)
        return True
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {e}", file=sys.stderr)
        return False
    finally:
        os.unlink(tmp_path)


def main() -> int:
    # Resolve template path relative to repo root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    template_path = os.path.join(repo_root, TEMPLATE_FILE)

    if not os.path.isfile(template_path):
        # Also check current working directory
        if os.path.isfile(TEMPLATE_FILE):
            template_path = os.path.abspath(TEMPLATE_FILE)
        else:
            print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
            return 1

    print(f"Parsing template: {template_path}")

    with open(template_path, "r") as f:
        template = yaml.safe_load(f)

    blocks = find_zipfile_blocks(template)

    if not blocks:
        print("WARNING: No ZipFile blocks found in template")
        return 1

    print(f"Found {len(blocks)} Lambda ZipFile block(s)\n")

    passed = 0
    failed = 0

    for logical_id, code in blocks:
        print(f"  Validating: {logical_id} ({len(code)} chars) ... ", end="")
        if validate_code(logical_id, code):
            print("OK")
            passed += 1
        else:
            print("FAILED")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
