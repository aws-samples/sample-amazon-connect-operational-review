#!/usr/bin/env python3
"""
Sync analyzer-config.json to CloudFormation template.

Reads the analyzer configuration (single source of truth for Lambda specs)
and updates the corresponding Lambda resource definitions in the CFT template.
Syncs: function names, handlers, memory, timeouts, and runtime.

Usage:
    python3 sync_analyzer_config_to_cft.py \
        --config /path/to/analyzer-config.json \
        --cft-template CFT-AmazonConnectOperationsReview.yml
"""

import argparse
import json
import re
import sys
from pathlib import Path


# Maps componentType in analyzer-config.json to CFT resource logical IDs
ANALYZER_RESOURCE_MAP = {
    "cloudtrail": "CloudTrailAnalyzerFunction",
    "security": "SecurityAnalyzerFunction",
    "resilience": "ResilienceAnalyzerFunction",
    "operational_excellence": "OpExAnalyzerFunction",
    "capacity": "CapacityAnalyzerFunction",
    "observability": "ObservabilityAnalyzerFunction",
    "cost": "CostAnalyzerFunction",
    "ai": "AIAnalyzerFunction",
}

REPORT_GENERATOR_RESOURCE = "ReportGeneratorFunction"

# CFT resource logical IDs for Step Functions Task state timeouts
# (these are embedded in the ASL definition within the CFT, not separate resources)
SF_TIMEOUT_MAP = {
    "cloudtrail": "CloudTrailAnalyzer",
    "security": "SecurityAnalyzer",
    "resilience": "ResilienceAnalyzer",
    "operational_excellence": "OpExAnalyzer",
    "capacity": "CapacityAnalyzer",
    "observability": "ObservabilityAnalyzer",
    "cost": "CostAnalyzer",
    "ai": "AIAnalyzer",
}


def load_config(config_path: str) -> dict:
    """Load and validate analyzer-config.json."""
    with open(config_path) as f:
        config = json.load(f)

    required_keys = ["version", "defaults", "analyzers", "reportGenerator"]
    missing = [k for k in required_keys if k not in config]
    if missing:
        print(f"❌ analyzer-config.json missing required keys: {', '.join(missing)}")
        sys.exit(1)

    return config


def load_cft(cft_path: str) -> str:
    """Load CFT template as raw text (preserving YAML formatting)."""
    with open(cft_path) as f:
        return f.read()


def save_cft(cft_path: str, content: str) -> None:
    """Save updated CFT template."""
    with open(cft_path, "w") as f:
        f.write(content)


def update_lambda_property(content: str, resource_name: str, prop_name: str, new_value) -> str:
    """
    Update a scalar property value for a Lambda function resource in the CFT.

    Handles YAML patterns like:
      ResourceName:
        Type: AWS::Lambda::Function
        Properties:
          FunctionName: "old-value"
          Handler: "old.handler"
          Timeout: 300
          MemorySize: 256
          Runtime: python3.11
    """
    # Find the resource block
    resource_pattern = rf"(  {resource_name}:\s*\n\s+Type:\s*AWS::Lambda::Function\s*\n\s+Properties:.*?)"
    resource_match = re.search(resource_pattern, content, re.DOTALL)
    if not resource_match:
        print(f"  ⚠️  Resource {resource_name} not found in template")
        return content

    # Find the property within the resource's Properties block
    # We need to find the property after the resource declaration
    resource_start = resource_match.start()

    # Search for the property line after the resource start
    if isinstance(new_value, str):
        # String values — match quoted or unquoted
        prop_pattern = rf"({prop_name}:\s*)['\"]?[^'\"\n]*['\"]?"
        replacement = rf"\g<1>\"{new_value}\""
    elif isinstance(new_value, int):
        # Integer values
        prop_pattern = rf"({prop_name}:\s*)\d+"
        replacement = rf"\g<1>{new_value}"
    else:
        return content

    # Only replace within the resource block (find next resource or end)
    # Look for the next top-level resource (2-space indent + name + colon)
    next_resource = re.search(r"\n  [A-Z]\w+:\s*\n", content[resource_start + 10:])
    if next_resource:
        resource_end = resource_start + 10 + next_resource.start()
    else:
        resource_end = len(content)

    resource_block = content[resource_start:resource_end]
    updated_block = re.sub(prop_pattern, replacement, resource_block, count=1)

    if resource_block != updated_block:
        content = content[:resource_start] + updated_block + content[resource_end:]

    return content


def sync_analyzer(content: str, component_type: str, analyzer_config: dict, defaults: dict) -> str:
    """Sync a single analyzer's config to the CFT template."""
    resource_name = ANALYZER_RESOURCE_MAP.get(component_type)
    if not resource_name:
        print(f"  ⚠️  No resource mapping for component type: {component_type}")
        return content

    # Resolve values with defaults
    function_name = analyzer_config.get("functionName", f"ConnectOpsReview-{component_type}")
    handler = analyzer_config.get("handler", f"{component_type}_analyzer.lambda_handler")
    memory_mb = analyzer_config.get("memoryMB", defaults.get("memoryMB", 256))
    timeout_seconds = analyzer_config.get("timeoutSeconds", defaults.get("timeoutSeconds", 300))
    runtime = analyzer_config.get("runtime", defaults.get("runtime", "python3.12"))

    print(f"  Syncing {component_type} → {resource_name}")
    print(f"    FunctionName: {function_name}")
    print(f"    Handler: {handler}")
    print(f"    MemorySize: {memory_mb}")
    print(f"    Timeout: {timeout_seconds}")
    print(f"    Runtime: {runtime}")

    content = update_lambda_property(content, resource_name, "FunctionName", function_name)
    content = update_lambda_property(content, resource_name, "Handler", handler)
    content = update_lambda_property(content, resource_name, "MemorySize", memory_mb)
    content = update_lambda_property(content, resource_name, "Timeout", timeout_seconds)
    content = update_lambda_property(content, resource_name, "Runtime", runtime)

    return content


def sync_report_generator(content: str, rg_config: dict, defaults: dict) -> str:
    """Sync Report Generator config to the CFT template."""
    resource_name = REPORT_GENERATOR_RESOURCE

    function_name = rg_config.get("functionName", "ConnectOpsReview-ReportGenerator")
    handler = rg_config.get("handler", "report_generator.lambda_handler")
    memory_mb = rg_config.get("memoryMB", defaults.get("memoryMB", 256))
    timeout_seconds = rg_config.get("timeoutSeconds", defaults.get("timeoutSeconds", 300))
    runtime = rg_config.get("runtime", defaults.get("runtime", "python3.12"))

    print(f"  Syncing reportGenerator → {resource_name}")
    print(f"    FunctionName: {function_name}")
    print(f"    Handler: {handler}")
    print(f"    MemorySize: {memory_mb}")
    print(f"    Timeout: {timeout_seconds}")
    print(f"    Runtime: {runtime}")

    content = update_lambda_property(content, resource_name, "FunctionName", function_name)
    content = update_lambda_property(content, resource_name, "Handler", handler)
    content = update_lambda_property(content, resource_name, "MemorySize", memory_mb)
    content = update_lambda_property(content, resource_name, "Timeout", timeout_seconds)
    content = update_lambda_property(content, resource_name, "Runtime", runtime)

    return content


def sync_state_machine_timeout(content: str, sm_config: dict) -> str:
    """Sync state machine execution timeout."""
    timeout = sm_config.get("executionTimeoutSeconds", 1200)
    # The state machine timeout is typically in the ASL definition or as a CFT property
    # Update TimeoutSeconds in the state machine resource if present
    pattern = r"(TimeoutSeconds:\s*)\d+"
    # Only update within the state machine resource context
    # This is a best-effort update — the ASL file is the true source
    print(f"  State machine execution timeout: {timeout}s")
    return content


def main():
    parser = argparse.ArgumentParser(description="Sync analyzer-config.json to CFT template")
    parser.add_argument("--config", required=True, help="Path to analyzer-config.json")
    parser.add_argument("--cft-template", required=True, help="Path to CFT YAML template")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Syncing analyzer-config.json → CloudFormation template")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    config = load_config(args.config)
    content = load_cft(args.cft_template)
    original_content = content

    defaults = config.get("defaults", {})
    analyzers = config.get("analyzers", {})
    report_generator = config.get("reportGenerator", {})
    state_machine = config.get("stateMachine", {})

    print(f"\n  Config version: {config.get('version', 'unknown')}")
    print(f"  Analyzers to sync: {len(analyzers)}")
    print(f"  Default runtime: {defaults.get('runtime', 'python3.12')}")
    print()

    # Sync each analyzer
    for component_type, analyzer_config in analyzers.items():
        content = sync_analyzer(content, component_type, analyzer_config, defaults)
        print()

    # Sync report generator
    content = sync_report_generator(content, report_generator, defaults)
    print()

    # Sync state machine timeout (informational)
    content = sync_state_machine_timeout(content, state_machine)
    print()

    # Write results
    if content == original_content:
        print("ℹ️  No changes needed — CFT template already in sync")
    elif args.dry_run:
        print("🔍 Dry run — changes not written")
        # Show a summary of what would change
        original_lines = original_content.splitlines()
        updated_lines = content.splitlines()
        changes = sum(1 for a, b in zip(original_lines, updated_lines) if a != b)
        print(f"   {changes} line(s) would be modified")
    else:
        save_cft(args.cft_template, content)
        print("✅ CFT template updated successfully")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
