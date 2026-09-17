"""Property-based tests for report_generator.py rendering invariants.

Consolidated from:
- test_pbt_storage_config_table.py (Property 1: table completeness)
- test_pbt_unconfigured_resource_rendering.py (Property 2: colspan rendering)
- test_pbt_anchor_uniqueness.py (Property 5: anchor uniqueness and stability)

Validates:
- Requirements 1.4: Unconfigured resource type colspan rendering
- Requirements 1.5: Storage config table always has 13 data rows
- Requirements 4.4, 4.5: Anchor link uniqueness and deterministic generation
"""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from report_generator import (
    _render_data_storage_config_table,
    _render_executive_summary,
    STORAGE_RESOURCE_TYPES,
    VALID_CHECK_STATUSES,
)


# ===========================================================================
# Shared Constants
# ===========================================================================

RESOURCE_TYPE_DISPLAY_NAMES = [display for (_api, display) in STORAGE_RESOURCE_TYPES]

AREAS = [
    "Security",
    "Resilience",
    "Operational Excellence",
    "Capacity Analysis",
    "Observability",
    "Cost",
]

AREA_PREFIXES = {
    "Security": "sec",
    "Resilience": "res",
    "Operational Excellence": "opex",
    "Capacity Analysis": "cap",
    "Observability": "obs",
    "Cost": "cost",
}


# ===========================================================================
# Shared Helpers
# ===========================================================================


def _build_full_storage_configs(configured_mask):
    """Build a list of 13 storage config dicts, one per resource type.

    Args:
        configured_mask: list of 13 booleans indicating configured/unconfigured state.
    """
    configs = []
    for i, (api_name, display_name) in enumerate(STORAGE_RESOURCE_TYPES):
        if configured_mask[i]:
            configs.append(
                {
                    "resource_type": display_name,
                    "storage_type": "S3",
                    "destination": f"arn:aws:s3:::bucket-{i}/prefix/",
                    "encryption": "AWS Managed Key",
                    "configured": True,
                }
            )
        else:
            configs.append(
                {
                    "resource_type": display_name,
                    "storage_type": None,
                    "destination": "-",
                    "encryption": "None",
                    "configured": False,
                }
            )
    return configs


def _generate_anchor(area: str, check_name: str) -> str:
    """Generate a deterministic anchor from area and check name."""
    prefix = AREA_PREFIXES.get(area, area[:3].lower())
    slug = check_name.lower().replace(" ", "-").replace("(", "").replace(")", "")
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"{prefix}-{slug}"


# ===========================================================================
# Strategies
# ===========================================================================

# Storage config: list of 13 booleans for configured/unconfigured mask
configured_mask_strategy = st.lists(st.booleans(), min_size=13, max_size=13)

# Unconfigured indices: non-empty subset of indices (at least 1 unconfigured)
unconfigured_indices_strategy = st.frozensets(
    st.integers(min_value=0, max_value=12), min_size=1, max_size=13
)

# Anchor: check name word generation
check_name_word = st.from_regex(r"[A-Z][a-z]{2,10}", fullmatch=True)
check_name_strategy = st.lists(check_name_word, min_size=1, max_size=5).map(
    lambda words: " ".join(words)
)


@st.composite
def unique_checks_strategy(draw):
    """Generate a list of checks where each (area, check) pair is unique."""
    num_checks = draw(st.integers(min_value=2, max_value=20))
    seen_pairs = set()
    checks = []

    for _ in range(num_checks):
        attempts = 0
        while attempts < 50:
            area = draw(st.sampled_from(AREAS))
            check_name = draw(check_name_strategy)
            pair = (area, check_name)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                status = draw(st.sampled_from(list(VALID_CHECK_STATUSES)))
                anchor = _generate_anchor(area, check_name)
                checks.append(
                    {
                        "area": area,
                        "check": check_name,
                        "status": status,
                        "detail": f"Detail for {check_name}",
                        "anchor": anchor,
                    }
                )
                break
            attempts += 1

    assume(len(checks) >= 2)
    return checks


# ===========================================================================
# Property 1: Storage Config Table Completeness (Requirements 1.5)
# ===========================================================================


@settings(max_examples=200)
@given(configured_mask=configured_mask_strategy)
def test_storage_config_table_always_has_13_data_rows(configured_mask):
    """Property 1: For any combination of configured/unconfigured resource types,
    the rendered HTML table must contain exactly 13 <tr> data rows.

    **Validates: Requirements 1.5**
    """
    storage_configs = _build_full_storage_configs(configured_mask)
    html_output = _render_data_storage_config_table(storage_configs)

    assert html_output, "Expected non-empty HTML output for valid storage configs"

    all_tr_tags = re.findall(r"<tr>", html_output, re.IGNORECASE)
    header_rows = re.findall(r"<tr><th>", html_output, re.IGNORECASE)
    data_row_count = len(all_tr_tags) - len(header_rows)

    assert data_row_count == 13, (
        f"Expected exactly 13 data rows, got {data_row_count}. "
        f"Total <tr>: {len(all_tr_tags)}, header rows: {len(header_rows)}. "
        f"Configured mask: {configured_mask}"
    )


# ===========================================================================
# Property 2: Unconfigured Resource Type Rendering (Requirements 1.4)
# ===========================================================================


@settings(max_examples=200)
@given(unconfigured_indices=unconfigured_indices_strategy)
def test_unconfigured_resource_type_renders_colspan_not_configured(
    unconfigured_indices,
):
    """Property 2: For any resource type with configured=False, the rendered HTML row
    SHALL contain a <td> with colspan spanning 3 columns displaying "Not configured".

    **Validates: Requirements 1.4**
    """
    storage_configs = _build_full_storage_configs(
        [i not in unconfigured_indices for i in range(13)]
    )
    html_output = _render_data_storage_config_table(storage_configs)

    assert html_output, "Expected non-empty HTML output for valid storage configs"

    for idx in unconfigured_indices:
        display_name = RESOURCE_TYPE_DISPLAY_NAMES[idx]
        row_pattern = re.compile(
            r"<tr>\s*<td>"
            + re.escape(display_name)
            + r"</td>\s*<td[^>]*colspan\s*=\s*[\"']?3[\"']?[^>]*>\s*Not configured\s*</td>\s*</tr>",
            re.IGNORECASE,
        )
        match = row_pattern.search(html_output)
        assert match is not None, (
            f"Resource type '{display_name}' (index {idx}) is unconfigured but its row "
            f"does not contain a <td> with colspan='3' and 'Not configured' text."
        )


@settings(max_examples=200)
@given(resource_idx=st.integers(min_value=0, max_value=12))
def test_single_unconfigured_resource_has_colspan_and_text(resource_idx):
    """Property 2 (single resource variant): Any single unconfigured resource type
    renders with colspan and 'Not configured' text.

    **Validates: Requirements 1.4**
    """
    configs = _build_full_storage_configs([i != resource_idx for i in range(13)])
    html_output = _render_data_storage_config_table(configs)

    assert html_output, "Expected non-empty HTML output"

    display_name = RESOURCE_TYPE_DISPLAY_NAMES[resource_idx]
    row_pattern = re.compile(
        r"<tr>\s*<td>"
        + re.escape(display_name)
        + r"</td>\s*<td[^>]*colspan\s*=\s*[\"']?3[\"']?[^>]*>[^<]*Not configured[^<]*</td>\s*</tr>",
        re.IGNORECASE,
    )
    match = row_pattern.search(html_output)
    assert match is not None, (
        f"Resource '{display_name}' (index {resource_idx}) should render with "
        f"colspan='3' and 'Not configured' text when configured=False"
    )


# ===========================================================================
# Property 5: Anchor Link Uniqueness and Stability (Requirements 4.4, 4.5)
# ===========================================================================


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(checks=unique_checks_strategy())
def test_anchor_values_are_unique(checks):
    """Property 5a: All anchor values in executive summary check list are unique.

    **Validates: Requirements 4.4, 4.5**
    """
    anchors = [c["anchor"] for c in checks]

    for anchor in anchors:
        assert isinstance(anchor, str), f"Anchor must be a string, got {type(anchor)}"
        assert len(anchor) > 0, "Anchor must be non-empty"

    assert len(anchors) == len(set(anchors)), (
        f"Duplicate anchors found: {[a for a in anchors if anchors.count(a) > 1]}. "
        f"Checks: {[(c['area'], c['check']) for c in checks]}"
    )

    html_output = _render_executive_summary(checks)
    href_anchors = re.findall(r'href="#([^"]+)"', html_output)
    assert len(href_anchors) == len(set(href_anchors)), (
        f"Duplicate href anchors in rendered HTML: "
        f"{[a for a in href_anchors if href_anchors.count(a) > 1]}"
    )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(checks=unique_checks_strategy())
def test_anchor_generation_is_deterministic(checks):
    """Property 5b: Generating anchors twice with same input produces identical values.

    **Validates: Requirements 4.4, 4.5**
    """
    for check in checks:
        anchor_first = check["anchor"]
        anchor_second = _generate_anchor(check["area"], check["check"])
        assert anchor_first == anchor_second, (
            f"Anchor not deterministic for area='{check['area']}', check='{check['check']}': "
            f"first='{anchor_first}', second='{anchor_second}'"
        )

    html_first = _render_executive_summary(checks)
    html_second = _render_executive_summary(checks)

    anchors_first = re.findall(r'href="#([^"]+)"', html_first)
    anchors_second = re.findall(r'href="#([^"]+)"', html_second)

    assert anchors_first == anchors_second, (
        f"Rendered anchor hrefs differ between runs. "
        f"First: {anchors_first}, Second: {anchors_second}"
    )
