#!/usr/bin/env python3
"""
Sync state-machine-definition.asl.json from the code repo into the CFT
template's DefinitionString block, translating placeholder references.

The canonical ASL uses generic placeholders (e.g., ${PrepareContextFunctionArn}).
CFT uses CloudFormation !Sub references (e.g., ${PrepareContextFunction.Arn}).

This script:
1. Reads the canonical ASL JSON from the source directory
2. Translates ${XxxFunctionArn} → ${XxxFunction.Arn} for CFT !Sub
3. Replaces the DefinitionString content in the CFT template

Usage:
    python3 scripts/sync_asl_to_cft.py \
        --source-dir _lambda_source \
        --cft-template CFT-AmazonConnectOperationsReview.yml
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Mapping from canonical placeholder names to CFT resource names
# Format: placeholder stem (without "Arn" suffix) → CFT logical resource name
PLACEHOLDER_TO_CFT_RESOURCE = {
    "PrepareContextFunction": "PrepareContextFunction",
    "SecurityAnalyzerFunction": "SecurityAnalyzerFunction",
    "ResilienceAnalyzerFunction": "ResilienceAnalyzerFunction",
    "CloudTrailAnalyzerFunction": "CloudTrailAnalyzerParallelFunction",
    "OperationalExcellenceAnalyzerFunction": "OpExAnalyzerFunction",
    "CapacityAnalyzerFunction": "CapacityAnalyzerFunction",
    "ObservabilityAnalyzerFunction": "ObservabilityAnalyzerFunction",
    "CostAnalyzerFunction": "CostAnalyzerFunction",
    "AIAnalyzerFunction": "AIAnalyzerFunction",
    "ReportGeneratorFunction": "ReportGeneratorFunction",
}


def translate_placeholders(asl_content: str) -> str:
    """Translate ${XxxFunctionArn} to ${XxxFunction.Arn} for CFT !Sub."""

    def replace_match(match):
        placeholder = match.group(1)  # e.g., "PrepareContextFunctionArn"
        # Strip the trailing "Arn" to get the stem
        if placeholder.endswith("Arn"):
            stem = placeholder[:-3]  # e.g., "PrepareContextFunction"
        else:
            # Unknown placeholder — leave as-is
            return match.group(0)

        cft_resource = PLACEHOLDER_TO_CFT_RESOURCE.get(stem)
        if cft_resource:
            return f"${{{cft_resource}.Arn}}"
        else:
            print(f"  WARNING: No CFT resource mapping for placeholder '{placeholder}'")
            return match.group(0)

    # Match ${...Arn} placeholders but NOT $$. references (ASL context paths)
    # Pattern: ${ followed by word chars, ending with } — but not preceded by $
    translated = re.sub(r'(?<!\$)\$\{(\w+)\}', replace_match, asl_content)
    return translated


def indent_for_yaml(content: str, indent: int = 8) -> str:
    """Indent JSON content for embedding in YAML DefinitionString."""
    lines = content.splitlines()
    prefix = " " * indent
    return "\n".join(prefix + line for line in lines)


def replace_definition_string(template: str, new_asl: str) -> str:
    """Replace the DefinitionString content in the CFT template.

    Finds the `DefinitionString: !Sub |` line and replaces everything
    between it and the next property at the same or lesser indent level.
    """
    lines = template.splitlines(keepends=True)

    # Find the DefinitionString: !Sub | line
    def_line_idx = None
    def_indent = None
    for i, line in enumerate(lines):
        if "DefinitionString:" in line and "!Sub" in line:
            def_line_idx = i
            def_indent = len(line) - len(line.lstrip())
            break

    if def_line_idx is None:
        print("ERROR: Could not find 'DefinitionString: !Sub |' in template")
        sys.exit(1)

    # Find end of the DefinitionString block
    # Content is indented deeper than the DefinitionString line
    content_indent = def_indent + 2  # YAML block scalar content indent
    end_idx = None
    for i in range(def_line_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= def_indent:
            end_idx = i
            break

    if end_idx is None:
        end_idx = len(lines)

    # Build the indented ASL content
    indented_asl = indent_for_yaml(new_asl, content_indent)

    # Replace
    new_lines = (
        lines[:def_line_idx + 1]
        + [indented_asl + "\n"]
        + lines[end_idx:]
    )

    return "".join(new_lines)


def main():
    parser = argparse.ArgumentParser(
        description="Sync ASL state machine definition into CFT template"
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Directory containing state-machine-definition.asl.json",
    )
    parser.add_argument(
        "--cft-template",
        default="CFT-AmazonConnectOperationsReview.yml",
        help="Path to CFT template (default: CFT-AmazonConnectOperationsReview.yml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print translated ASL without writing",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    cft_path = Path(args.cft_template)
    asl_path = source_dir / "state-machine-definition.asl.json"

    if not asl_path.is_file():
        print(f"ERROR: ASL file not found: {asl_path}")
        sys.exit(1)
    if not cft_path.is_file():
        print(f"ERROR: CFT template not found: {cft_path}")
        sys.exit(1)

    print(f"Source ASL: {asl_path}")
    print(f"CFT template: {cft_path}")
    print()

    # Read and validate ASL
    asl_content = asl_path.read_text()
    try:
        json.loads(asl_content)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in ASL file: {e}")
        sys.exit(1)
    print(f"  ASL size: {len(asl_content)} bytes")

    # Translate placeholders
    print("Translating placeholders...")
    translated = translate_placeholders(asl_content)

    # Count translations
    orig_placeholders = re.findall(r'\$\{(\w+Arn)\}', asl_content)
    print(f"  Translated {len(orig_placeholders)} placeholder(s)")
    print()

    if args.dry_run:
        print("DRY RUN — translated ASL:")
        print(translated[:500])
        print("...")
        return

    # Read template and replace
    template = cft_path.read_text()
    updated = replace_definition_string(template, translated)
    cft_path.write_text(updated)
    print(f"✓ Updated DefinitionString in {cft_path}")


if __name__ == "__main__":
    main()
