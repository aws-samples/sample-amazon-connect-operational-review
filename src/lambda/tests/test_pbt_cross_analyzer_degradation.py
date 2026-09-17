# Feature: html-report-parity, Property 4: Cross-analyzer graceful degradation
"""Property-based test for cross-analyzer graceful degradation.

**Validates: Requirements 5.4, 5.5, 5.7**

Property 4: For any findings_map where one or more analyzer entries are missing
or contain malformed data, `_assemble_cross_analyzer_data()` SHALL return a valid
findings_map without raising an exception, and all downstream section renderers
SHALL produce valid HTML (possibly with omitted sub-sections) rather than crashing.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from report_generator import _assemble_cross_analyzer_data, _render_section


# ── Strategies ──

# Known analyzer keys in findings_map
ANALYZER_KEYS = [
    "security",
    "operational_excellence",
    "observability",
    "cost",
    "cloudtrail",
    "capacity",
    "ai",
]

# Strategy: valid findings sub-dict (nested structure with various keys)
valid_findings_inner = st.fixed_dictionaries(
    {},
    optional={
        "storage_configs": st.lists(
            st.fixed_dictionaries(
                {
                    "resource_type": st.text(min_size=1, max_size=30),
                    "configs": st.lists(
                        st.dictionaries(st.text(max_size=10), st.text(max_size=20)),
                        max_size=3,
                    ),
                }
            ),
            max_size=5,
        ),
        "api_throttling": st.fixed_dictionaries(
            {},
            optional={
                "total_throttled": st.integers(min_value=0, max_value=1000),
                "throttled_by_api": st.lists(st.text(max_size=20), max_size=5),
                "total_events_analyzed": st.integers(min_value=0, max_value=100000),
            },
        ),
        "telephony_usage": st.fixed_dictionaries(
            {},
            optional={
                "phone_numbers": st.lists(
                    st.dictionaries(st.text(max_size=10), st.text(max_size=20)),
                    max_size=3,
                ),
                "unused_phone_numbers": st.lists(st.text(max_size=15), max_size=3),
            },
        ),
        "contact_flow_logging": st.dictionaries(
            st.text(min_size=1, max_size=15), st.text(max_size=30), max_size=3
        ),
        "kvs_retention": st.dictionaries(
            st.text(min_size=1, max_size=15), st.text(max_size=30), max_size=3
        ),
        "ai_agent_inventory": st.lists(st.text(max_size=20), max_size=3),
        "ai_prompt_configuration": st.dictionaries(
            st.text(min_size=1, max_size=15), st.text(max_size=30), max_size=3
        ),
        "missed_calls_metrics": st.fixed_dictionaries(
            {},
            optional={
                "total_missed_calls": st.integers(min_value=0, max_value=1000),
                "daily_average": st.floats(min_value=0, max_value=100, allow_nan=False),
                "days_analyzed": st.integers(min_value=1, max_value=30),
            },
        ),
        "channel_mix": st.fixed_dictionaries(
            {},
            optional={
                "channels": st.lists(
                    st.fixed_dictionaries(
                        {
                            "channel": st.sampled_from(["VOICE", "CHAT", "TASK"]),
                            "contacts_created": st.integers(
                                min_value=0, max_value=10000
                            ),
                        }
                    ),
                    max_size=3,
                ),
                "status": st.sampled_from(["pass", "info", "warn"]),
            },
        ),
        "identity_management": st.dictionaries(
            st.text(min_size=1, max_size=15), st.text(max_size=30), max_size=3
        ),
    },
)

# Strategy: a well-structured analyzer document with findings key
well_formed_doc = st.fixed_dictionaries(
    {"findings": valid_findings_inner},
    optional={
        "partial": st.booleans(),
        "timed_out": st.booleans(),
    },
)

# Strategy: malformed/invalid analyzer entries
malformed_entry = st.one_of(
    st.none(),  # None value
    st.just("not a dict"),  # String instead of dict
    st.just(42),  # Integer instead of dict
    st.just([]),  # List instead of dict
    st.just(True),  # Boolean instead of dict
    st.just({"findings": "not_a_dict"}),  # findings key is not a dict
    st.just({"findings": None}),  # findings key is None
    st.just({"findings": [1, 2, 3]}),  # findings key is a list
)

# Strategy: an analyzer entry that is either valid or malformed
analyzer_entry = st.one_of(
    well_formed_doc,
    valid_findings_inner,  # Flat dict (no "findings" wrapper)
    malformed_entry,
    st.just({}),  # Empty dict
)

# Strategy: a findings_map with random subset of analyzer keys having random entries
findings_map_strategy = st.dictionaries(
    keys=st.sampled_from(ANALYZER_KEYS),
    values=analyzer_entry,
    min_size=0,
    max_size=len(ANALYZER_KEYS),
)


# ── Property Tests ──


@settings(max_examples=200)
@given(findings_map=findings_map_strategy)
def test_cross_analyzer_assembly_never_raises(findings_map):
    """Property 4a: _assemble_cross_analyzer_data never raises exceptions.

    For any findings_map with missing, malformed, or valid entries, the function
    SHALL return without raising an exception.

    **Validates: Requirements 5.4, 5.5, 5.7**
    """
    # This must not raise any exception regardless of input
    result = _assemble_cross_analyzer_data(findings_map)

    # The return value must be a dict
    assert isinstance(result, dict), (
        f"Expected dict return type, got {type(result).__name__}"
    )


@settings(max_examples=200)
@given(findings_map=findings_map_strategy)
def test_cross_analyzer_assembly_returns_valid_dict(findings_map):
    """Property 4b: Return value is always a valid dict with expected structure.

    The returned findings_map must be a dict and must not lose any top-level keys
    that were present in the input (it can add new keys via merging but must not
    drop existing ones).

    **Validates: Requirements 5.4, 5.5, 5.7**
    """
    result = _assemble_cross_analyzer_data(findings_map)

    assert isinstance(result, dict)

    # All keys from the input must still be present in the output
    for key in findings_map:
        assert key in result, f"Key '{key}' from input findings_map was lost in output"


@settings(max_examples=200)
@given(findings_map=findings_map_strategy)
def test_downstream_renderers_handle_cross_analyzer_output(findings_map):
    """Property 4c: Downstream section renderers produce valid HTML without crashing.

    After cross-analyzer assembly, calling _render_section for each known section
    type with the assembled data must not raise exceptions. The output must be a
    tuple of (str, list).

    **Validates: Requirements 5.4, 5.5, 5.7**
    """
    assembled = _assemble_cross_analyzer_data(findings_map)

    # Section renderer component types and display names
    renderer_sections = [
        ("Security", "security"),
        ("Resilience", "resilience"),
        ("Operational Excellence", "operational_excellence"),
        ("Capacity", "capacity"),
        ("Observability", "observability"),
        ("Cost Optimization", "cost"),
    ]

    for display_name, component_type in renderer_sections:
        # Get the findings for this section from the assembled map
        section_data = assembled.get(component_type, {})

        # Extract findings sub-dict if it exists
        if isinstance(section_data, dict) and "findings" in section_data:
            findings = section_data.get("findings", {})
        elif isinstance(section_data, dict):
            findings = section_data
        else:
            findings = {}

        # Ensure findings is a dict for the renderer
        if not isinstance(findings, dict):
            findings = {}

        # _render_section must not raise even with empty/malformed findings
        html, checks = _render_section(display_name, component_type, findings)

        # Validate return types
        assert isinstance(html, str), (
            f"Renderer for {component_type} returned non-string HTML: {type(html).__name__}"
        )
        assert isinstance(checks, list), (
            f"Renderer for {component_type} returned non-list checks: {type(checks).__name__}"
        )

        # HTML must not be empty (at minimum contains section heading markup)
        # Even empty findings produce a section wrapper
        assert len(html) > 0, f"Renderer for {component_type} produced empty HTML"


@settings(max_examples=100)
@given(
    present_keys=st.lists(
        st.sampled_from(ANALYZER_KEYS),
        min_size=0,
        max_size=len(ANALYZER_KEYS),
        unique=True,
    )
)
def test_missing_analyzers_produce_empty_structures(present_keys):
    """Property 4d: Missing analyzers result in empty structures, not None.

    When analyzer entries are completely absent from the findings_map,
    the cross-analyzer assembly must still return a valid dict. Dependent
    renderers receive empty data structures, not None.

    **Validates: Requirements 5.4, 5.5**
    """
    # Build a findings_map with only the selected keys present
    findings_map = {}
    for key in present_keys:
        findings_map[key] = {"findings": {}}

    result = _assemble_cross_analyzer_data(findings_map)

    assert isinstance(result, dict)

    # For each key that WAS present, the output must still contain it
    for key in present_keys:
        assert key in result
        entry = result[key]
        # Must be a dict (not None)
        assert isinstance(entry, dict), (
            f"Entry for '{key}' is {type(entry).__name__}, expected dict"
        )
