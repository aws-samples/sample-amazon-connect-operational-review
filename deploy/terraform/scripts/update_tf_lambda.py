#!/usr/bin/env python3
"""
Copy Lambda source files into lambda_packages/ for Terraform's archive_file.

This is the Terraform equivalent of the CFT repo's update_cft_lambda_multi.py.
Instead of embedding code inline in YAML, it copies source files so that
Terraform's archive_file data source can zip and hash them.

Usage:
  # Copy all mapped functions from checked-out source
  python3 scripts/update_tf_lambda.py --source-dir _lambda_source

  # Copy a single function
  python3 scripts/update_tf_lambda.py --source-dir _lambda_source --only report_generator.py
"""

import argparse
import os
import shutil
import sys
import yaml
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
DEFAULT_MAPPING = SCRIPT_DIR / "function-mapping.yaml"
DEFAULT_DEST = SCRIPT_DIR.parent / "lambda_packages"


def load_mapping(path):
    """Load the function-mapping.yaml file."""
    with open(path) as f:
        return yaml.safe_load(f)


def copy_source(source_path, dest_dir, filename):
    """Copy a single source file to the destination directory."""
    dest_path = dest_dir / filename
    shutil.copy2(source_path, dest_path)
    return dest_path


def main():
    parser = argparse.ArgumentParser(
        description="Copy Lambda source files into lambda_packages/ for Terraform"
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Path to checked-out code repo (e.g., _lambda_source)",
    )
    parser.add_argument(
        "--mapping",
        default=str(DEFAULT_MAPPING),
        help="Path to function-mapping.yaml",
    )
    parser.add_argument(
        "--dest-dir",
        default=str(DEFAULT_DEST),
        help="Destination directory for Lambda source files",
    )
    parser.add_argument(
        "--only",
        help="Copy only this source file (skip others)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Ignored (kept for CLI compatibility with CFT script)",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    dest_dir = Path(args.dest_dir)
    mapping = load_mapping(args.mapping)

    # Ensure destination exists
    dest_dir.mkdir(parents=True, exist_ok=True)

    updated = 0
    skipped = 0

    print(f"\n📦 Updating lambda_packages/ from {source_dir}\n")

    # Copy shared layer directory if present
    layer = mapping.get("shared_layer")
    if layer and not args.only:
        shared_src = source_dir / layer["source_dir"]
        shared_dest = dest_dir / layer["source_dir"]
        if shared_src.is_dir():
            print(f"Layer: {layer['source_dir']}/ → {layer['tf_resource']}")
            if shared_dest.exists():
                shutil.rmtree(shared_dest)
            shutil.copytree(shared_src, shared_dest)
            py_count = len(list(shared_dest.glob("*.py")))
            print(f"  ✅ Copied {py_count} Python file(s)")
            updated += 1
        else:
            print(f"  ⏭️  {layer['source_dir']}/ not found in source dir — skipped")
            skipped += 1

    # Copy function source files
    for func in mapping.get("functions", []):
        source_name = func["source"]
        tf_resource = func["tf_resource"]

        if args.only and source_name != args.only:
            continue

        source_path = source_dir / source_name
        if not source_path.is_file():
            print(f"  ⏭️  {source_name} not found in source dir — skipped")
            skipped += 1
            continue

        print(f"Function: {source_name} → {tf_resource}")
        dest_path = copy_source(source_path, dest_dir, source_name)
        print(f"  ✅ Copied to {dest_path}")
        updated += 1

    # Summary
    status = "✅" if updated else "⚠️"
    print(f"\n{status}  Done: {updated} updated, {skipped} skipped\n")

    if updated == 0 and skipped > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
