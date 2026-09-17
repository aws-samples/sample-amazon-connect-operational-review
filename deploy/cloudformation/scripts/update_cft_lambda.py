#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. This material is AWS Content under the AWS Enterprise Agreement
# or AWS Customer Agreement (as applicable) and is provided under the AWS Intellectual Property License.

"""
Update embedded Lambda function code in CloudFormation template.
Supports both same-repo and remote-repo scenarios.

Parallel architecture targets:
  - ReportGeneratorFunction (default) — report_generator.py
  - PrepareContextFunction — prepare_context.py
  - SecurityAnalyzerFunction — security_analyzer.py
  - ResilienceAnalyzerFunction — resilience_analyzer.py
  - OpExAnalyzerFunction — opex_analyzer.py
  - CapacityAnalyzerFunction — capacity_analyzer.py
  - ObservabilityAnalyzerFunction — observability_analyzer.py
  - CostAnalyzerFunction — cost_analyzer.py
  - CloudTrailAnalyzerParallelFunction — cloudtrail_analyzer.py
  - AIAnalyzerFunction — ai_analyzer.py
"""

import argparse
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Source file → CFT resource name mapping for the parallel architecture
# ---------------------------------------------------------------------------
SOURCE_TO_RESOURCE = {
    "report_generator.py": "ReportGeneratorFunction",
    "prepare_context.py": "PrepareContextFunction",
    "security_analyzer.py": "SecurityAnalyzerFunction",
    "resilience_analyzer.py": "ResilienceAnalyzerFunction",
    "opex_analyzer.py": "OpExAnalyzerFunction",
    "capacity_analyzer.py": "CapacityAnalyzerFunction",
    "observability_analyzer.py": "ObservabilityAnalyzerFunction",
    "cost_analyzer.py": "CostAnalyzerFunction",
    "cloudtrail_analyzer.py": "CloudTrailAnalyzerParallelFunction",
    "ai_analyzer.py": "AIAnalyzerFunction",
}

# Default Lambda source and resource when no arguments are given
DEFAULT_LAMBDA_SOURCE = "report_generator.py"
DEFAULT_RESOURCE_NAME = "ReportGeneratorFunction"


class Colors:
    """ANSI color codes for terminal output"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def print_colored(message, color=Colors.NC):
    """Print colored message to terminal"""
    print(f"{color}{message}{Colors.NC}")


def validate_file_exists(filepath):
    """Validate that a file exists"""
    if not os.path.isfile(filepath):
        print_colored(f"Error: File not found: {filepath}", Colors.RED)
        sys.exit(1)


def create_backup(filepath):
    """Create a backup of the file with timestamp"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.backup.{timestamp}"
    shutil.copy2(filepath, backup_path)
    print_colored(f"✓ Backup created: {backup_path}", Colors.GREEN)
    return backup_path


def fetch_remote_lambda(repo_url, branch, file_path):
    """Fetch Lambda code from remote repository"""
    print_colored(f"Fetching from remote repository...", Colors.BLUE)
    print_colored(f"  Repository: {repo_url}", Colors.YELLOW)
    print_colored(f"  Branch: {branch}", Colors.YELLOW)
    print_colored(f"  File: {file_path}", Colors.YELLOW)
    
    # Create temporary directory
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Clone repository (shallow clone for speed)
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', '--branch', branch, repo_url, temp_dir],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print_colored(f"Error cloning repository: {result.stderr}", Colors.RED)
            sys.exit(1)
        
        # Get commit info
        commit_hash = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=temp_dir,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        commit_date = subprocess.run(
            ['git', 'log', '-1', '--format=%cd', '--date=short'],
            cwd=temp_dir,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        print_colored(f"✓ Fetched commit: {commit_hash} ({commit_date})", Colors.GREEN)
        
        # Read Lambda source
        lambda_path = os.path.join(temp_dir, file_path)
        if not os.path.isfile(lambda_path):
            print_colored(f"Error: Lambda file not found in repo: {file_path}", Colors.RED)
            sys.exit(1)
        
        with open(lambda_path, 'r') as f:
            lambda_code = f.read()
        
        return lambda_code, commit_hash, commit_date
        
    finally:
        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def read_lambda_source(filepath):
    """Read Lambda source code from local file"""
    validate_file_exists(filepath)
    with open(filepath, 'r') as f:
        return f.read()


def check_code_size(code, filepath):
    """Check if code exceeds CloudFormation inline limit"""
    INLINE_LIMIT = 4194304  # 4 MB
    size = len(code.encode('utf-8'))
    size_kb = size / 1024
    size_mb = size / 1024 / 1024
    
    print_colored(f"\n📊 Code Size Check:", Colors.BLUE)
    print(f"  File: {filepath}")
    print(f"  Size: {size:,} bytes ({size_kb:.2f} KB / {size_mb:.4f} MB)")
    print(f"  CloudFormation ZipFile limit: {INLINE_LIMIT:,} bytes (4 MB)")
    
    if size > INLINE_LIMIT:
        over_by = size - INLINE_LIMIT
        over_by_mb = over_by / 1024 / 1024
        ratio = size / INLINE_LIMIT
        print_colored(f"\n⚠️  WARNING: Code exceeds CloudFormation inline limit!", Colors.RED)
        print_colored(f"  Your code is {ratio:.1f}x larger than the limit", Colors.YELLOW)
        print_colored(f"  Over by: {over_by:,} bytes ({over_by_mb:.2f} MB)", Colors.YELLOW)
        print_colored(f"\n❌ CloudFormation deployment will FAIL with inline code (ZipFile)", Colors.RED)
        print_colored(f"\nYou MUST use S3 or Container deployment:", Colors.YELLOW)
        print("  1. S3 deployment: scripts/deploy-lambda-s3.sh")
        print("  2. Container: deployments/container/")
        print("  3. AWS SAM: deployments/sam/")
        print("\nSee: docs/CLOUDFORMATION-SIZE-LIMITS.md")
        print_colored("\n⚠️  This script will update the template, but deployment will fail!", Colors.RED)
        
        response = input("\nContinue anyway? (y/N): ")
        if response.lower() != 'y':
            print_colored("Aborted.", Colors.YELLOW)
            sys.exit(1)
    else:
        headroom = INLINE_LIMIT - size
        headroom_mb = headroom / 1024 / 1024
        percent_used = (size * 100) / INLINE_LIMIT
        print_colored(f"✓ Within CloudFormation inline limit", Colors.GREEN)
        print(f"  Headroom: {headroom:,} bytes ({headroom_mb:.2f} MB)")
        print(f"  Usage: {percent_used:.1f}% of limit")
        print_colored(f"\n✓ You can use inline code (ZipFile) in CloudFormation", Colors.GREEN)
        
        if size > 3145728:  # 75% of 4MB
            print_colored(f"\n⚠️  Warning: Approaching limit (>{percent_used:.0f}%)", Colors.YELLOW)
            print("  Consider using S3 deployment for future growth")


def indent_lambda_code(code, indent_spaces=10):
    """Indent Lambda code for YAML embedding"""
    lines = code.split('\n')
    indent = ' ' * indent_spaces
    return '\n'.join(indent + line if line.strip() else '' for line in lines)


def find_resource_scope(lines, resource_name):
    """Find the start and end line indices for a named CFT resource.
    
    Returns (start, end) where start is the line with the resource name
    and end is the line where the next top-level resource begins (or EOF).
    """
    resource_start = None
    for i, line in enumerate(lines):
        # Resource names in CFT are at 2-space indent (top-level under Resources:)
        stripped = line.rstrip()
        if stripped == f"  {resource_name}:" or stripped.startswith(f"  {resource_name}:"):
            resource_start = i
            break
    
    if resource_start is None:
        return None, None
    
    # Find end of this resource (next line at 2-space indent that isn't deeper)
    resource_end = len(lines)
    for i in range(resource_start + 1, len(lines)):
        line = lines[i]
        # A new top-level resource or section starts at exactly 2-space indent
        # (not blank, not deeper indentation, not a comment at root level)
        if line.strip() and not line.startswith('   ') and line.startswith('  ') and not line.strip().startswith('#'):
            resource_end = i
            break
        # Also stop at root-level keys (0-indent, like Outputs:)
        if line.strip() and not line.startswith(' ') and not line.strip().startswith('#'):
            resource_end = i
            break
    
    return resource_start, resource_end


def update_cft_template(cft_path, lambda_code, resource_name=None):
    """Update CloudFormation template with new Lambda code.
    
    Args:
        cft_path: Path to the CloudFormation template file.
        lambda_code: The Lambda source code to embed.
        resource_name: CFT resource name to target. If provided,
            searches for ZipFile: | within that resource's scope only.
            Defaults to 'ReportGeneratorFunction' (the main report
            generator in the parallel architecture).
    """
    # Default to the ReportGenerator Lambda — the primary target in the
    # parallel architecture that replaced the monolithic ops review function.
    if resource_name is None:
        resource_name = DEFAULT_RESOURCE_NAME
    
    validate_file_exists(cft_path)
    
    with open(cft_path, 'r') as f:
        lines = f.readlines()
    
    # Determine search scope
    scope_start, scope_end = find_resource_scope(lines, resource_name)
    if scope_start is None:
        print_colored(f"Error: Resource '{resource_name}' not found in template", Colors.RED)
        sys.exit(1)
    print_colored(f"Targeting resource: {resource_name} (lines {scope_start + 1}-{scope_end})", Colors.BLUE)
    
    # Find ZipFile section within scope
    zipfile_line = None
    for i in range(scope_start, scope_end):
        if 'ZipFile: |' in lines[i]:
            zipfile_line = i
            break
    
    if zipfile_line is None:
        print_colored(f"Error: Could not find 'ZipFile: |' within resource '{resource_name}'", Colors.RED)
        sys.exit(1)
    
    # Determine the indentation level of the ZipFile: | line
    zipfile_indent = len(lines[zipfile_line]) - len(lines[zipfile_line].lstrip())
    
    # Find end of Lambda code section
    # The code block ends when we encounter a line at the same or lesser indent level
    # (that isn't blank), which means we've left the ZipFile content
    zipfile_end = None
    for i in range(zipfile_line + 1, len(lines)):
        line = lines[i]
        # Skip blank lines
        if not line.strip():
            continue
        # Content indented deeper than ZipFile: | is part of the code block
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= zipfile_indent:
            zipfile_end = i
            break
    
    if zipfile_end is None:
        # If we hit EOF, the ZipFile section extends to the end
        zipfile_end = len(lines)
    
    print_colored(f"Found Lambda code section: lines {zipfile_line + 1} to {zipfile_end}", Colors.BLUE)
    
    # Indent Lambda code
    indented_code = indent_lambda_code(lambda_code)
    
    # Build new template
    new_lines = (
        lines[:zipfile_line + 1] +  # Everything before and including ZipFile: |
        [indented_code + '\n'] +     # New Lambda code
        lines[zipfile_end:]          # Everything after old Lambda code
    )
    
    # Write updated template
    with open(cft_path, 'w') as f:
        f.writelines(new_lines)
    
    print_colored(f"✓ Successfully updated {cft_path}", Colors.GREEN)


def main():
    parser = argparse.ArgumentParser(
        description='Update embedded Lambda function code in CloudFormation template',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update the ReportGenerator Lambda (default target)
  python3 update_cft_lambda.py
  
  # Update a specific analyzer Lambda (auto-detects resource name from source file)
  python3 update_cft_lambda.py --lambda-source cloudtrail_analyzer.py
  
  # Explicitly target a different Lambda resource by name
  python3 update_cft_lambda.py --lambda-source opex_analyzer.py \\
    --resource-name OpExAnalyzerFunction
  
  # Remote repo scenario (targets ReportGenerator by default)
  python3 update_cft_lambda.py --remote \\
    --repo-url https://github.com/org/repo.git \\
    --branch main \\
    --remote-path report_generator.py
  
  # Remote repo targeting a specific analyzer
  python3 update_cft_lambda.py --remote \\
    --repo-url https://github.com/org/repo.git \\
    --branch main \\
    --remote-path cloudtrail_analyzer.py \\
    --resource-name CloudTrailAnalyzerParallelFunction

Resource name mapping (auto-detected from --lambda-source filename):
  report_generator.py       → ReportGeneratorFunction (default)
  prepare_context.py        → PrepareContextFunction
  security_analyzer.py      → SecurityAnalyzerFunction
  resilience_analyzer.py    → ResilienceAnalyzerFunction
  opex_analyzer.py          → OpExAnalyzerFunction
  capacity_analyzer.py      → CapacityAnalyzerFunction
  observability_analyzer.py → ObservabilityAnalyzerFunction
  cost_analyzer.py          → CostAnalyzerFunction
  cloudtrail_analyzer.py    → CloudTrailAnalyzerParallelFunction
  ai_analyzer.py            → AIAnalyzerFunction
        """
    )
    
    parser.add_argument(
        '--lambda-source',
        default=DEFAULT_LAMBDA_SOURCE,
        help=f'Path to Lambda source file (default: {DEFAULT_LAMBDA_SOURCE})'
    )
    
    parser.add_argument(
        '--cft-template',
        default='CFT-AmazonConnectOperationsReview-Updated.yml',
        help='Path to CloudFormation template (default: CFT-AmazonConnectOperationsReview-Updated.yml)'
    )
    
    parser.add_argument(
        '--remote',
        action='store_true',
        help='Fetch Lambda code from remote repository'
    )
    
    parser.add_argument(
        '--repo-url',
        help='Remote repository URL (required if --remote is used)'
    )
    
    parser.add_argument(
        '--branch',
        default='main',
        help='Remote repository branch (default: main)'
    )
    
    parser.add_argument(
        '--remote-path',
        help='Path to Lambda file in remote repo (required if --remote is used)'
    )
    
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Skip creating backup of CFT template'
    )
    
    parser.add_argument(
        '--resource-name',
        help=f'CFT resource name to target (default: {DEFAULT_RESOURCE_NAME}). '
             'Auto-detected from --lambda-source filename when not specified. '
             'Use this to override, e.g., --resource-name CloudTrailAnalyzerParallelFunction.'
    )
    
    args = parser.parse_args()
    
    print_colored("=== CloudFormation Lambda Code Updater ===\n", Colors.GREEN)
    
    # Validate remote arguments
    if args.remote:
        if not args.repo_url or not args.remote_path:
            print_colored("Error: --repo-url and --remote-path are required when using --remote", Colors.RED)
            sys.exit(1)
    
    # Create backup
    if not args.no_backup:
        create_backup(args.cft_template)
    
    # Get Lambda code
    if args.remote:
        lambda_code, commit_hash, commit_date = fetch_remote_lambda(
            args.repo_url,
            args.branch,
            args.remote_path
        )
        source_info = f"{args.repo_url}@{commit_hash}"
    else:
        print_colored(f"Reading Lambda code from: {args.lambda_source}", Colors.BLUE)
        lambda_code = read_lambda_source(args.lambda_source)
        source_info = args.lambda_source
    
    # Check code size and warn if over limit
    check_code_size(lambda_code, source_info)
    
    # Update CFT template
    print_colored("\nUpdating CloudFormation template...", Colors.YELLOW)
    
    # Auto-detect resource name from source filename when --resource-name is not specified
    resource_name = args.resource_name
    if resource_name is None:
        # Determine the source filename (may be a full path)
        if args.remote:
            source_filename = os.path.basename(args.remote_path)
        else:
            source_filename = os.path.basename(args.lambda_source)
        
        # Look up the resource name from the mapping
        resource_name = SOURCE_TO_RESOURCE.get(source_filename)
        if resource_name:
            print_colored(f"Auto-detected resource: {source_filename} → {resource_name}", Colors.BLUE)
        else:
            # Fall back to the default if the filename isn't in the mapping
            resource_name = DEFAULT_RESOURCE_NAME
            print_colored(
                f"⚠️  Source '{source_filename}' not in resource mapping, "
                f"using default: {resource_name}",
                Colors.YELLOW
            )
    
    update_cft_template(args.cft_template, lambda_code, resource_name=resource_name)
    
    # Success message
    print_colored(f"\n✓ Lambda code updated from: {source_info}", Colors.GREEN)
    print_colored("\nNext steps:", Colors.YELLOW)
    print(f"1. Review changes: git diff {args.cft_template}")
    print(f"2. Validate template: aws cloudformation validate-template --template-body file://{args.cft_template}")
    print("3. Commit and deploy the updated stack")


if __name__ == '__main__':
    main()
