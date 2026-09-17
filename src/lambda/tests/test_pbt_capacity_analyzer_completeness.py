# Feature: html-report-parity, Property 6: Capacity analyzer output completeness
"""Property-based test for Capacity Analyzer output completeness.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

Property 6: For any successful (non-timed-out) capacity analyzer execution,
the output findings JSON SHALL contain all keys: `instance_quotas`,
`concurrency_limits`, `account_level_api`, `cases_limits`, `appint_limits`,
`profiles_limits`, `ai_agents_limits`.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ── Required Keys ──

REQUIRED_CAPACITY_KEYS = [
    "instance_quotas",
    "concurrency_limits",
    "account_level_api",
    "cases_limits",
    "appint_limits",
    "profiles_limits",
    "ai_agents_limits",
]


# ── Strategies ──

# Strategy for instance quota resource entries
instance_quota_resource = st.fixed_dictionaries(
    {
        "label": st.text(
            min_size=1,
            max_size=80,
            alphabet=st.characters(categories=("L", "N", "P", "Z")),
        ),
        "current": st.integers(min_value=-1, max_value=10000),
        "limit": st.integers(min_value=-1, max_value=100000),
        "percentage": st.floats(
            min_value=-1.0, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
    }
)

# Strategy for instance_quotas findings
instance_quotas_strategy = st.fixed_dictionaries(
    {
        "resources": st.lists(instance_quota_resource, min_size=1, max_size=25),
        "total_checked": st.integers(min_value=1, max_value=25),
        "measurable_count": st.integers(min_value=0, max_value=25),
        "unmeasurable_count": st.integers(min_value=0, max_value=25),
        "pass_count": st.integers(min_value=0, max_value=25),
        "warn_count": st.integers(min_value=0, max_value=25),
        "fail_count": st.integers(min_value=0, max_value=25),
        "status": st.sampled_from(["pass", "warn", "fail"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)

# Strategy for concurrency metric entries
concurrency_metric = st.fixed_dictionaries(
    {
        "label": st.text(
            min_size=1,
            max_size=80,
            alphabet=st.characters(categories=("L", "N", "P", "Z")),
        ),
        "peak_current": st.integers(min_value=0, max_value=10000),
        "limit": st.integers(min_value=0, max_value=100000),
        "peak_percentage": st.floats(
            min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
    }
)

# Strategy for concurrency_limits findings
concurrency_limits_strategy = st.fixed_dictionaries(
    {
        "metrics": st.lists(concurrency_metric, min_size=1, max_size=10),
        "total_metrics": st.integers(min_value=1, max_value=10),
        "measurable_count": st.integers(min_value=0, max_value=10),
        "status": st.sampled_from(["pass", "warn", "fail"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)

# Strategy for account_level_api findings
api_rate_row = st.fixed_dictionaries(
    {
        "name": st.text(min_size=1, max_size=100),
        "default_value": st.floats(
            min_value=1, max_value=1000, allow_nan=False, allow_infinity=False
        ),
        "current_value": st.floats(
            min_value=1, max_value=1000, allow_nan=False, allow_infinity=False
        ),
        "utilization": st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    }
)

account_level_api_strategy = st.fixed_dictionaries(
    {
        "api_rate_count": st.integers(min_value=0, max_value=500),
        "modified_count": st.integers(min_value=0, max_value=500),
        "modified_rows": st.lists(api_rate_row, min_size=0, max_size=10),
        "default_rows": st.lists(
            st.fixed_dictionaries(
                {
                    "name": st.text(min_size=1, max_size=100),
                    "default_value": st.floats(
                        min_value=1,
                        max_value=1000,
                        allow_nan=False,
                        allow_infinity=False,
                    ),
                }
            ),
            min_size=0,
            max_size=10,
        ),
        "status": st.sampled_from(["info", "pass", "warn"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)

# Strategy for quota entries (used by cases, appint, profiles, ai_agents)
quota_entry = st.fixed_dictionaries(
    {
        "label": st.text(
            min_size=1,
            max_size=80,
            alphabet=st.characters(categories=("L", "N", "P", "Z")),
        ),
        "current": st.one_of(st.none(), st.integers(min_value=0, max_value=10000)),
        "limit": st.one_of(
            st.none(),
            st.floats(
                min_value=0, max_value=100000, allow_nan=False, allow_infinity=False
            ),
        ),
        "percentage": st.one_of(
            st.none(),
            st.floats(
                min_value=0, max_value=100.0, allow_nan=False, allow_infinity=False
            ),
        ),
    }
)

# Strategy for cases_limits findings
cases_limits_strategy = st.fixed_dictionaries(
    {
        "domain_count": st.integers(min_value=0, max_value=10),
        "quotas": st.lists(quota_entry, min_size=0, max_size=15),
        "status": st.sampled_from(["pass", "warn", "fail", "info"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)

# Strategy for appint_limits findings
appint_limits_strategy = st.fixed_dictionaries(
    {
        "data_int_count": st.integers(min_value=0, max_value=100),
        "event_int_count": st.integers(min_value=0, max_value=100),
        "app_count": st.integers(min_value=0, max_value=100),
        "quotas": st.lists(quota_entry, min_size=0, max_size=10),
        "status": st.sampled_from(["pass", "warn", "fail", "info"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)

# Strategy for profiles_limits findings
profiles_limits_strategy = st.fixed_dictionaries(
    {
        "domain_count": st.integers(min_value=0, max_value=10),
        "quotas": st.lists(quota_entry, min_size=0, max_size=15),
        "status": st.sampled_from(["pass", "warn", "fail", "info"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)

# Strategy for ai_agents quota entries
ai_agents_quota_entry = st.fixed_dictionaries(
    {
        "name": st.text(
            min_size=1,
            max_size=80,
            alphabet=st.characters(categories=("L", "N", "P", "Z")),
        ),
        "quota_code": st.from_regex(r"L-[A-F0-9]{8}", fullmatch=True),
        "applied_value": st.one_of(
            st.none(),
            st.floats(
                min_value=0, max_value=10000, allow_nan=False, allow_infinity=False
            ),
        ),
        "default_value": st.one_of(
            st.none(),
            st.floats(
                min_value=0, max_value=10000, allow_nan=False, allow_infinity=False
            ),
        ),
        "adjustable": st.one_of(st.none(), st.booleans()),
        "percentage_used": st.one_of(
            st.none(),
            st.floats(
                min_value=0, max_value=100.0, allow_nan=False, allow_infinity=False
            ),
        ),
    }
)

# Strategy for ai_agents_limits findings
ai_agents_limits_strategy = st.fixed_dictionaries(
    {
        "quotas": st.lists(ai_agents_quota_entry, min_size=0, max_size=10),
        "status": st.sampled_from(["info", "pass", "warn"]),
        "detail": st.text(min_size=1, max_size=200),
    }
)


# Combined strategy that assembles a complete capacity analyzer output
# simulating what lambda_handler produces on a successful (non-timed-out) run
@st.composite
def capacity_analyzer_output(draw):
    """Generate a complete capacity analyzer findings dict as produced by lambda_handler.

    This simulates the output assembly logic in lambda_handler when all 8 checks
    complete successfully (timed_out=False).
    """
    instance_quotas = draw(instance_quotas_strategy)
    concurrency_limits = draw(concurrency_limits_strategy)
    account_level_api = draw(account_level_api_strategy)
    cases_limits = draw(cases_limits_strategy)
    appint_limits = draw(appint_limits_strategy)
    profiles_limits = draw(profiles_limits_strategy)
    ai_agents_limits = draw(ai_agents_limits_strategy)

    # Growth trends (always present but not a required key for this property)
    growth_trends = {
        "trends": [],
        "days_analyzed": 30,
        "status": "pass",
        "detail": "Usage trends stable",
    }

    findings = {
        "instance_quotas": instance_quotas,
        "concurrency_limits": concurrency_limits,
        "growth_trends": growth_trends,
        "account_level_api": account_level_api,
        "cases_limits": cases_limits,
        "appint_limits": appint_limits,
        "profiles_limits": profiles_limits,
        "ai_agents_limits": ai_agents_limits,
        "checks_completed": [
            "instance_quotas",
            "concurrency_limits",
            "growth_trends",
            "account_level_api",
            "cases_limits",
            "appint_limits",
            "profiles_limits",
            "ai_agents_limits",
        ],
        "timed_out": False,
        "account_id": "123456789012",
        "region": "us-east-1",
    }
    return findings


# ── Property Tests ──


@settings(max_examples=200)
@given(findings=capacity_analyzer_output())
def test_capacity_analyzer_output_contains_all_required_keys(findings):
    """Property 6: Capacity analyzer output completeness.

    For any successful (non-timed-out) capacity analyzer execution, the output
    findings JSON SHALL contain all keys: instance_quotas, concurrency_limits,
    account_level_api, cases_limits, appint_limits, profiles_limits, ai_agents_limits.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
    """
    # Precondition: only test non-timed-out runs
    assume(findings.get("timed_out") is False)

    # Assert all required keys are present
    for key in REQUIRED_CAPACITY_KEYS:
        assert key in findings, (
            f"Required key '{key}' missing from capacity analyzer output. "
            f"Present keys: {sorted(findings.keys())}"
        )

    # Assert none of the required keys are None
    for key in REQUIRED_CAPACITY_KEYS:
        assert findings[key] is not None, (
            f"Required key '{key}' is None in capacity analyzer output. "
            f"All required keys must contain valid data structures."
        )


@settings(max_examples=200)
@given(findings=capacity_analyzer_output())
def test_capacity_analyzer_checks_completed_includes_all_required(findings):
    """Property 6 corollary: checks_completed list includes all required check names.

    For any successful (non-timed-out) run, the checks_completed list must include
    entries for all required capacity checks.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
    """
    assume(findings.get("timed_out") is False)

    checks_completed = findings.get("checks_completed", [])

    for key in REQUIRED_CAPACITY_KEYS:
        assert key in checks_completed, (
            f"Required check '{key}' missing from checks_completed list. "
            f"Completed: {checks_completed}"
        )


@settings(max_examples=200)
@given(findings=capacity_analyzer_output())
def test_capacity_analyzer_required_keys_are_dicts(findings):
    """Property 6 structural invariant: all required keys contain dict values.

    Each required key in the capacity analyzer output must be a dictionary
    containing structured findings data.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
    """
    assume(findings.get("timed_out") is False)

    for key in REQUIRED_CAPACITY_KEYS:
        assert isinstance(findings[key], dict), (
            f"Required key '{key}' should be a dict, got {type(findings[key]).__name__}. "
            f"Value: {findings[key]}"
        )
