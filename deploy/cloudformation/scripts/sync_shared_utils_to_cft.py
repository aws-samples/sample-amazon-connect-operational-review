#!/usr/bin/env python3
"""
Sync analyzer_common.py and graceful_timeout.py into the CFT template's
SharedUtilsLayerBuilder inline constants, and auto-increment BuildVersion.

This replaces the manual process of:
1. Copy-pasting code into ANALYZER_COMMON_PY / GRACEFUL_TIMEOUT_PY constants
2. Manually incrementing the BuildVersion property

Usage:
    python3 scripts/sync_shared_utils_to_cft.py \
        --source-dir _lambda_source \
        --cft-template CFT-AmazonConnectOperationsReview.yml
"""

import argparse
import re
import sys
from pathlib import Path


def read_source_file(source_dir: Path, filename: str) -> str:
    """Read a source file and return its content."""
    filepath = source_dir / filename
    if not filepath.is_file():
        print(f"ERROR: Source file not found: {filepath}")
        sys.exit(1)
    return filepath.read_text()


def replace_inline_constant(template: str, constant_name: str, new_content: str) -> str:
    """Replace an inline triple-quoted constant in the CFT template.

    The constants look like:
        ANALYZER_COMMON_PY = '''
        <content>
        '''

    We replace everything between the opening ''' and closing '''.
    """
    # Pattern: CONSTANT_NAME = ''' ... ''' (with indentation)
    # The constant is indented inside a ZipFile block, so we need to handle
    # the leading whitespace. The opening line has the form:
    #   <indent>CONSTANT_NAME = '''
    # and the closing line has:
    #   <indent>'''
    pattern = re.compile(
        r"^(\s*)" + re.escape(constant_name) + r" = '''.*?^\1'''",
        re.MULTILINE | re.DOTALL
    )

    match = pattern.search(template)
    if not match:
        print(f"ERROR: Could not find constant '{constant_name}' in template")
        sys.exit(1)

    indent = match.group(1)

    # Indent the source content to match the template's indentation
    indented_lines = []
    for line in new_content.splitlines():
        if line.strip():
            indented_lines.append(indent + line)
        else:
            indented_lines.append("")
    indented_content = "\n".join(indented_lines)

    # Build the replacement block
    replacement = f"{indent}{constant_name} = '''\n{indented_content}\n{indent}'''"

    return template[:match.start()] + replacement + template[match.end():]


def increment_build_version(template: str) -> str:
    """Find and increment the BuildVersion property of SharedUtilsLayerCustomResource."""
    pattern = re.compile(r"""(\s*BuildVersion:\s*)['"](\d+)['"]""")
    match = pattern.search(template)
    if not match:
        print("WARNING: Could not find BuildVersion property in template — skipping increment")
        return template

    old_version = int(match.group(2))
    new_version = old_version + 1
    print(f"  BuildVersion: {old_version} → {new_version}")

    return template[:match.start()] + f"{match.group(1)}'{new_version}'" + template[match.end():]


def main():
    parser = argparse.ArgumentParser(
        description="Sync shared utility modules into CFT template inline constants"
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Path to directory containing analyzer_common.py and graceful_timeout.py",
    )
    parser.add_argument(
        "--cft-template",
        default="CFT-AmazonConnectOperationsReview.yml",
        help="Path to CFT template file (default: CFT-AmazonConnectOperationsReview.yml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    cft_path = Path(args.cft_template)

    if not cft_path.is_file():
        print(f"ERROR: CFT template not found: {cft_path}")
        sys.exit(1)

    print(f"Source directory: {source_dir}")
    print(f"CFT template: {cft_path}")
    print()

    # Read source files
    analyzer_common = read_source_file(source_dir, "analyzer_common.py")
    graceful_timeout = read_source_file(source_dir, "graceful_timeout.py")

    print(f"  analyzer_common.py: {len(analyzer_common)} bytes")
    print(f"  graceful_timeout.py: {len(graceful_timeout)} bytes")
    print()

    # Read template
    template = cft_path.read_text()

    # Replace constants
    print("Replacing inline constants...")
    template = replace_inline_constant(template, "ANALYZER_COMMON_PY", analyzer_common)
    print("  ✓ ANALYZER_COMMON_PY updated")
    template = replace_inline_constant(template, "GRACEFUL_TIMEOUT_PY", graceful_timeout)
    print("  ✓ GRACEFUL_TIMEOUT_PY updated")
    print()

    # Increment BuildVersion
    print("Incrementing BuildVersion...")
    template = increment_build_version(template)
    print()

    if args.dry_run:
        print("DRY RUN — no files written")
    else:
        cft_path.write_text(template)
        print(f"✓ Written: {cft_path}")


if __name__ == "__main__":
    main()
