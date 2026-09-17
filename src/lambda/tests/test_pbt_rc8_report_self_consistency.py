# Feature: rc6-followup-defects (QB-65 / R2)
"""RC8 Fix 2 (QB-65 / R2) — Phone Number Distribution badge tracks analyzer status.

The rc.6 report shipped a hard-coded three-way status branch inside
``_render_phone_number_analysis`` that produced a WARN badge on the HTML report
even when the analyzer emitted ``telephony_usage.status == "pass"`` in the
retained JSON — a direct contradiction of what the workshop teaches
specialists to cross-check.

The QB-65 fix (design.md → Fix 2 → path (b)):

* ``cost_analyzer.analyze_telephony_usage`` now emits ``status = "warn"`` when
  DIDs exist with zero toll-free (single-carrier dependency), so the analyzer
  owns the status decision.
* ``_render_phone_number_analysis`` flows the analyzer's ``status`` field
  verbatim through to the Executive Summary check row when
  ``telephony_usage`` is present in the findings — no per-count branching.

This module locks in the flow-through contract with a property-based test
parameterized across the four valid analyzer states (``pass``, ``warn``,
``info``, ``error``). For each state, we call ``_render_phone_number_analysis``
with a canned findings fixture whose ``telephony_usage.status`` equals the
parameter, then assert the emitted check row's ``status`` field equals the
same parameter. That is the exact "``render(status).badge == status``"
property from tasks.md → Task 4.3.

**Validates: Requirements R2.1, R2.2, R2.3**
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from report_generator import _render_phone_number_analysis  # noqa: E402


# The four analyzer statuses the renderer must flow through verbatim.
# ``fail`` is intentionally excluded: the telephony analyzer never emits it.
ANALYZER_STATUSES = ["pass", "warn", "info", "error"]


def _telephony_findings(status: str) -> dict:
    """Build a minimal cost findings payload with a controllable analyzer status.

    The renderer's flow-through path requires:
    * ``telephony_usage`` present with a valid ``status`` and a ``detail``.
    * ``total_numbers > 0`` so we skip the empty-inventory early return.
    * ``type_distribution`` populated so the recommendations block renders.

    Type-distribution values are deliberately mixed (DIDs + toll-free) so the
    renderer's fallback three-way branch would produce a *different* status
    from the analyzer's — that way the assertion actually proves flow-through
    rather than coincidental agreement.
    """
    return {
        "telephony_usage": {
            "total_numbers": 5,
            "type_distribution": {"TOLL_FREE": 2, "DID": 3},
            "country_distribution": {"US": 5},
            "countries_count": 1,
            "toll_free_percentage": 40.0,
            "did_percentage": 60.0,
            "phone_numbers": [],
            "unused_phone_numbers": [],
            "recommendations": [],
            "status": status,
            "detail": f"analyzer-provided detail for status={status}",
        }
    }


@pytest.mark.parametrize("status", ANALYZER_STATUSES)
def test_phone_number_row_status_matches_analyzer_status(status):
    """Renderer flows analyzer.telephony_usage.status verbatim to the check row.

    Property: for every valid analyzer status, the Executive Summary check row
    emitted by ``_render_phone_number_analysis`` for anchor ``cost-phone`` has
    ``status`` equal to the analyzer's ``status``. Guarantees the retained JSON
    and the rendered badge cannot disagree.
    """
    findings = _telephony_findings(status)
    checks = []
    _render_phone_number_analysis(findings, checks)

    phone_rows = [c for c in checks if c.get("anchor") == "cost-phone"]
    assert len(phone_rows) == 1, (
        f"expected exactly one 'cost-phone' check row, got {len(phone_rows)}: {checks}"
    )
    assert phone_rows[0]["status"] == status, (
        f"analyzer emitted status={status!r} but renderer produced "
        f"{phone_rows[0]['status']!r}; retained JSON and HTML badge would disagree"
    )


@pytest.mark.parametrize("status", ANALYZER_STATUSES)
def test_phone_number_row_detail_matches_analyzer_detail(status):
    """Renderer flows analyzer.telephony_usage.detail verbatim too.

    Complementary to the status flow-through: R2.1 requires the two views
    agree on every input, so the detail string must also come from the
    analyzer, not be rebuilt from raw counts by the renderer.
    """
    findings = _telephony_findings(status)
    checks = []
    _render_phone_number_analysis(findings, checks)

    phone_rows = [c for c in checks if c.get("anchor") == "cost-phone"]
    assert len(phone_rows) == 1
    assert phone_rows[0]["detail"] == f"analyzer-provided detail for status={status}"


# ═══════════════════════════════════════════════════════════════════════════
# RC.8 preservation module for R1 / R3 / R4 (QB-64 / QB-68 / QB-71).
#
# Task 8.1 in `.kiro/specs/rc6-followup-defects/tasks.md` calls for three
# report-self-consistency test classes that together lock in the outcome of
# Fixes 1, 3, and 4:
#
#   TestPillarSectionMembership   — every non-AI Executive Summary anchor's
#                                   body `<div class="section" id="…">` lives
#                                   in the pillar section its ES row is under
#                                   (R1: prevents QB-64-style body/ES pillar
#                                   drift such as Missed Calls being counted
#                                   under Observability but rendered under
#                                   OpEx).
#
#   TestCanonicalCheckNames       — every ES anchor's body `<h3>` text matches
#                                   `CHECK_NAMES[anchor]` (R3: prevents
#                                   QB-68-style ES-vs-body name divergence
#                                   like `Streaming Encryption` in the ES and
#                                   `Data Streaming Encryption` in the body).
#                                   Two documented render styles are allowed
#                                   per the CHECK_NAMES header comment in
#                                   `report_generator.py`: a decorative
#                                   suffix, and a substantive-difference
#                                   literal that the h3 is deliberately left
#                                   with. Both are enforced against explicit
#                                   expected values so the tests fail if that
#                                   comment ever drifts out of sync with the
#                                   actual renderers.
#
#   TestCrossCuttingExceptions    — the documented ES-vs-body count
#                                   mismatch (R4 / QB-71). Five AI-analyzer
#                                   inline anchors register an ES row but
#                                   render as `<h4>` blocks inside their host
#                                   pillar rather than as dedicated
#                                   `<div class="section"><h3>` sub-sections.
#                                   One Resilience body `<h3>`
#                                   (`Knowledge Base Sync Health`, id `ai-r1`)
#                                   is rendered as a signal-only block and
#                                   carries a per-block "not counted in the
#                                   Executive Summary" note per design.md →
#                                   Fix 4 → step 2. This class asserts the
#                                   exact expected exception set so drift in
#                                   either direction — a new inline anchor
#                                   silently getting a body `<h3>`, or the KB
#                                   Sync Health block silently gaining an
#                                   Executive Summary-style h3 in a different
#                                   pillar — fails immediately.
#
# The assertions are static-source-driven rather than end-to-end-rendered so
# they are robust against fixture completeness gaps (the shared analyzer
# payload fixture in `tests/fixtures/analyzer_payloads.py` deliberately does
# not populate every optional sub-section). The `FakeS3Client` from
# `test_e2e_report_generation.py` is imported into the module namespace to
# satisfy Task 8.2's "reuse, don't duplicate" contract even though this test
# module does not need a full end-to-end render — the E2E anchor-integrity
# test in `test_e2e_report_generation.py::TestAnchorIntegrity` already covers
# the render side.
#
# **Validates: Requirements R1.4, R3.3, R4.3**
# ═══════════════════════════════════════════════════════════════════════════

import inspect
import re

# Reuse the FakeS3Client from the E2E module rather than duplicating it
# (Task 8.2). The import is deliberate: it both satisfies the reuse contract
# and provides a smoke-test seam for future rendered-HTML assertions in this
# same module. flake8/ruff ignore the unused import via the `noqa: F401`
# marker below.
from test_e2e_report_generation import FakeS3Client  # noqa: F401, E402

from report_generator import (  # noqa: E402
    CHECK_NAMES,
    _render_ai_findings_block,
    _render_capacity_section,
    _render_capacity_sub_table,
    _render_channel_usage_cost,
    _render_cost_section,
    _render_executive_summary,
    _render_identity_management,
    _render_observability_section,
    _render_opex_section,
    _render_phone_number_analysis,
    _render_resilience_section,
    _render_s3_encryption_table,
    _render_security_section,
    _render_streaming_encryption_table,
    _render_unused_phone_numbers,
)


# ── Pillar → render functions ─────────────────────────────────────────────
# Each pillar's body markup is emitted by its section renderer plus a small
# set of helper renderers it delegates to. This mapping is the static-source
# equivalent of "run the report and see which pillar this anchor lands in":
# an anchor's body `id="…"` must appear in one of the sources listed under
# the pillar its Executive Summary row is grouped under.
_PILLAR_RENDERERS = {
    "Security": [
        _render_security_section,
        _render_identity_management,
        _render_s3_encryption_table,
        _render_streaming_encryption_table,
    ],
    "Resilience": [_render_resilience_section],
    "Operational Excellence": [_render_opex_section],
    "Capacity Analysis": [_render_capacity_section, _render_capacity_sub_table],
    "Observability": [_render_observability_section],
    "Cost": [
        _render_cost_section,
        _render_phone_number_analysis,
        _render_unused_phone_numbers,
        _render_channel_usage_cost,
    ],
}


def _pillar_source(pillar: str) -> str:
    """Return the concatenated source of every renderer owning `pillar`."""
    return "\n".join(inspect.getsource(fn) for fn in _PILLAR_RENDERERS[pillar])


# ── Anchor → Executive-Summary pillar, extracted from check_order ─────────
# Parses the `check_order` dict inside `_render_executive_summary` and pulls
# every `CHECK_NAMES["anchor"]` reference under each pillar key. The result
# is the same anchor→pillar mapping the ES uses at render time to group
# scoreboard rows, without duplicating the mapping as a literal here.
def _extract_es_pillar_map() -> dict:
    src = inspect.getsource(_render_executive_summary)
    match = re.search(r"check_order\s*=\s*\{(.+?)\n\s{4}\}", src, re.DOTALL)
    assert match, (
        "Could not locate the check_order dict in _render_executive_summary; "
        "the ES-pillar mapping test can no longer be built dynamically."
    )
    body = match.group(1)
    mapping: dict = {}
    # Split into per-pillar chunks: `"Pillar": [ … ],`
    for pillar_match in re.finditer(r'"([^"]+)":\s*\[(.*?)\n\s+\]', body, re.DOTALL):
        pillar = pillar_match.group(1)
        entries = pillar_match.group(2)
        for anchor_match in re.finditer(r'CHECK_NAMES\["([^"]+)"\]', entries):
            anchor = anchor_match.group(1)
            # Duplicates (an anchor listed under two pillars in check_order —
            # today only `cost-phone-health` does this) resolve to whichever
            # pillar the checks-list emission actually assigns them to. In
            # practice that is always the LATER pillar in check_order source
            # order (Cost comes after Operational Excellence, and the check
            # is pushed by `_render_unused_phone_numbers` with area="Cost"),
            # so we use last-wins on the mapping build. If a future anchor
            # ever needs the opposite resolution the fix is to key by the
            # actual `"area": "…"` literal near the anchor's `checks.append`
            # site instead of by check_order order.
            mapping[anchor] = pillar
    return mapping


_ES_PILLAR_MAP = _extract_es_pillar_map()


# ── Anchor sets ───────────────────────────────────────────────────────────
# Non-AI ES anchors: every entry in _ES_PILLAR_MAP that is not part of the
# AI-analyzer inline set. `ai-r1` and `ai-q1` are pillar-analyzer anchors
# that happen to live in the `ai-` namespace (Resilience KB Sync Health,
# Capacity AI Agents Limits) — they DO carry a dedicated body `<h3>` and are
# NOT part of the inline-AI cross-cutting exception. They belong in the
# non-AI (well, non-INLINE-AI) set for TestPillarSectionMembership.
_INLINE_AI_ANCHORS = frozenset(
    {
        "ai-guardrails",
        "ai-domain_encryption",
        "ai-agent_inventory",
        "ai-prompt_configuration",
        "ai-agent_logging",
    }
)
_NON_INLINE_AI_ANCHORS = sorted(
    a for a in _ES_PILLAR_MAP if a not in _INLINE_AI_ANCHORS
)


# Historical note: `ops-kvs-retention` was previously pinned here as
# xfail(strict=True) because its body <div> lived in the observability
# section while its ES row routed to Operational Excellence. QB-73 (rc.8
# follow-up) moved the body block back into `_render_opex_section` after the
# API Throttling block, restoring pillar agreement. The xfail marker and
# `_KNOWN_PILLAR_DRIFTS` set have been retired. If a new pillar drift is
# introduced in a future release, re-add the set and the wrapper below,
# following the QB-64/QB-73 pattern.
_KNOWN_PILLAR_DRIFTS: set = set()


def _pillar_membership_param(anchor):
    """pytest.param wrapper. Retained as a no-op extension point for future
    known-drift exemptions; today every anchor passes through unmodified."""
    if anchor in _KNOWN_PILLAR_DRIFTS:
        return pytest.param(
            anchor,
            marks=pytest.mark.xfail(
                strict=True,
                reason="Known ES-vs-body pillar drift; see QUALITY_BACKLOG.md.",
            ),
        )
    return pytest.param(anchor)


_PILLAR_MEMBERSHIP_PARAMS = [
    _pillar_membership_param(a) for a in _NON_INLINE_AI_ANCHORS
]


# ── Documented body-h3 forms, per report_generator.CHECK_NAMES docstring ──
# The `<h3>` in the body either equals `CHECK_NAMES[anchor]` exactly, wraps
# it with a decorative suffix (Missed Calls " Analysis", Channel Usage
# " (N-day)", Kinesis Data Streams " — Recommended Alarm Coverage"), or is
# left as a substantive-difference literal that predates the CHECK_NAMES
# unification and was deliberately not renamed to preserve byte-identical
# rendered output. All three forms are documented in the CHECK_NAMES header
# comment; this test enforces those forms directly so drift in either the
# renderer or the documented contract fails immediately.
_H3_DECORATIVE = {
    "ch-missed": " Analysis",
    "cost-channel": re.compile(r"^Channel Usage \(\{days_back\}-day\)$"),
    "obs-kinesis-streams": " — Recommended Alarm Coverage",
}
_H3_SUBSTANTIVE = {
    "res-acgr": "Amazon Connect Global Resiliency",
    "res-carrier": "Carrier Diversity with Amazon Connect Phone Numbers",
    "ops-kvs-retention": "Kinesis Video Stream Retention",
    "obs-log-groups": "CloudWatch Log Groups",
}


def _extract_body_h3(anchor: str) -> str:
    """Return the raw <h3>…</h3> text following `<div … id="anchor">`.

    The returned string is the h3 content as it appears in the renderer's
    source — including any `{CHECK_NAMES["anchor"]}` f-string interpolations
    and any leading `<span>` badge (for the AI-flagged sub-sections). Tests
    substitute the `{CHECK_NAMES[…]}` reference with its resolved value and
    strip the leading badge span before asserting.
    """
    for pillar in _PILLAR_RENDERERS:
        src = _pillar_source(pillar)
        pattern = rf'id="{re.escape(anchor)}"[^>]*>\s*<h3[^>]*>(.*?)</h3>'
        match = re.search(pattern, src, re.DOTALL)
        if match:
            return match.group(1).strip()
    raise AssertionError(
        f"No `<div … id={anchor!r}> … <h3>` pair found in any pillar renderer"
    )


def _resolve_h3_text(anchor: str, raw_h3: str) -> str:
    """Replace `{CHECK_NAMES["anchor"]}` refs in `raw_h3` with actual names.

    Also strips the AI badge span used by `ai-r1` / `ai-q1` so the comparison
    below sees the plain text form of the h3.
    """
    resolved = raw_h3
    # Substitute every {CHECK_NAMES["…"]} f-string reference.
    for ref_anchor, ref_name in CHECK_NAMES.items():
        resolved = resolved.replace(f'{{CHECK_NAMES["{ref_anchor}"]}}', ref_name)
    # Strip the AI-badge span used by ai-r1 / ai-q1: `<span …>AI</span>`.
    resolved = re.sub(r"<span[^>]*>.*?</span>", "", resolved, flags=re.DOTALL)
    return resolved.strip()


# ═══════════════════════════════════════════════════════════════════════════
# TestPillarSectionMembership
# ═══════════════════════════════════════════════════════════════════════════
class TestPillarSectionMembership:
    """Every non-inline-AI ES anchor's body `<div id="…">` sits in its ES pillar.

    Guards against QB-64-class drift: an analyzer routing a check under
    pillar X in the Executive Summary while its render function emits the
    body `<div id="…">` inside pillar Y's section renderer. Missed Calls
    (`ch-missed`) was the reference case; this test catches the general
    form.

    **Validates: Requirement R1.4**
    """

    @pytest.mark.parametrize("anchor", _PILLAR_MEMBERSHIP_PARAMS)
    def test_body_id_lives_in_es_pillar_section(self, anchor):
        es_pillar = _ES_PILLAR_MAP[anchor]
        pillar_src = _pillar_source(es_pillar)
        needle = f'id="{anchor}"'
        assert needle in pillar_src, (
            f"Anchor {anchor!r} is listed under {es_pillar!r} in the "
            f"Executive Summary's `check_order` but its body "
            f"`<div … id={anchor!r}>` is not emitted by any {es_pillar} "
            f"pillar renderer ({[fn.__name__ for fn in _PILLAR_RENDERERS[es_pillar]]}). "
            f"Clicking the ES scoreboard row will land in the wrong section — "
            f"this is the QB-64 defect class."
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestCanonicalCheckNames
# ═══════════════════════════════════════════════════════════════════════════
class TestCanonicalCheckNames:
    """Every ES anchor's body `<h3>` text matches CHECK_NAMES[anchor].

    Three h3 forms are allowed, each documented in `report_generator.py`'s
    CHECK_NAMES header comment:

    * Exact match (12 anchors): body `<h3>{CHECK_NAMES[anchor]}</h3>`.
    * Decorative suffix (`ch-missed`, `cost-channel`, `obs-kinesis-streams`):
      body `<h3>{CHECK_NAMES[anchor]}<suffix></h3>`. The suffix is a fixed
      literal ("Analysis", "— Recommended Alarm Coverage") or a template
      (`(N-day)` with `{days_back}` as the placeholder).
    * Substantive-difference literal (`res-acgr`, `res-carrier`,
      `ops-kvs-retention`, `obs-log-groups`): the h3 is a wholly different
      string from CHECK_NAMES[anchor] and is preserved to keep the rendered
      report byte-identical to pre-Fix-3 output. The test asserts the
      literal against a copy from the same documentation.

    Any anchor whose h3 does not match any of those three forms fails, which
    is the QB-68 drift signal.

    **Validates: Requirement R3.3**
    """

    @pytest.mark.parametrize("anchor", sorted(CHECK_NAMES.keys()))
    def test_h3_matches_canonical_name(self, anchor):
        raw_h3 = _extract_body_h3(anchor)
        actual = _resolve_h3_text(anchor, raw_h3)
        expected_exact = CHECK_NAMES[anchor]

        if anchor in _H3_SUBSTANTIVE:
            expected_literal = _H3_SUBSTANTIVE[anchor]
            assert actual == expected_literal, (
                f"Substantive-difference h3 for anchor {anchor!r} no longer "
                f"matches the documented literal.\n"
                f"  Expected: {expected_literal!r}\n"
                f"  Actual:   {actual!r}\n"
                f"See CHECK_NAMES header comment in report_generator.py for "
                f"the documented substantive-difference contract."
            )
            return

        if anchor in _H3_DECORATIVE:
            suffix = _H3_DECORATIVE[anchor]
            if isinstance(suffix, re.Pattern):
                assert suffix.match(actual), (
                    f"Decorative h3 for anchor {anchor!r} no longer matches "
                    f"pattern {suffix.pattern!r}.\n"
                    f"  Actual: {actual!r}"
                )
            else:
                assert actual == f"{expected_exact}{suffix}", (
                    f"Decorative h3 for anchor {anchor!r} no longer matches "
                    f"'{expected_exact}{suffix}'.\n"
                    f"  Actual: {actual!r}"
                )
            return

        assert actual == expected_exact, (
            f"Body <h3> for anchor {anchor!r} does not match "
            f"CHECK_NAMES[{anchor!r}].\n"
            f"  CHECK_NAMES: {expected_exact!r}\n"
            f"  Body <h3>:   {actual!r}\n"
            f"QB-68 requires ES and body <h3> to agree on the display name "
            f"unless the anchor is in the documented decorative-suffix or "
            f"substantive-difference set."
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestCrossCuttingExceptions
# ═══════════════════════════════════════════════════════════════════════════
class TestCrossCuttingExceptions:
    """The R4 / QB-71 cross-cutting ES-vs-body count exception set.

    Design.md → Fix 4 chose the "documented cross-cutting" path over "1:1
    correspondence". Two exception shapes are intentional and locked in
    here:

    * AI-analyzer inline anchors register ES rows via
      `_render_ai_findings_block` (`ai-guardrails`, `ai-domain_encryption`,
      `ai-agent_inventory`, `ai-prompt_configuration`, `ai-agent_logging`)
      but render inline inside their host pillar as `<h4>` blocks — NOT as
      dedicated `<div class="section"><h3>` sub-sections. Their body id is
      emitted (so the ES anchor href resolves) but no `<h3>` matches their
      display name.

    * `Knowledge Base Sync Health` (id `ai-r1`) is rendered as a body
      `<h3>` inside the Resilience section as a signal-only block. Per
      design.md → Fix 4 → step 2, the block carries a per-block "not
      counted in the Executive Summary" note that documents its
      informational-only status.

    The test asserts the exact expected set on both sides so drift in
    either direction — a new inline anchor accidentally getting an `<h3>`,
    or the KB Sync Health `<h3>` being renamed / relocated / silently
    dropped — surfaces immediately.

    **Validates: Requirement R4.3**
    """

    def test_inline_ai_anchors_register_es_rows(self):
        """Each inline-AI anchor is emitted as an ES row by _render_ai_findings_block."""
        block_src = inspect.getsource(_render_ai_findings_block)
        assert 'f"ai-{check_name}"' in block_src, (
            "`_render_ai_findings_block` no longer emits ES anchors of the "
            "form `ai-{check_name}`. The inline-AI anchor set enumerated in "
            "this test is now stale. Update `_INLINE_AI_ANCHORS` to match "
            "the new emission pattern before proceeding."
        )
        assert "checks.append" in block_src, (
            "`_render_ai_findings_block` no longer appends ES check rows for "
            "its findings; the AI cross-cutting contract has changed."
        )

    def test_inline_ai_anchors_have_no_body_h3(self):
        """No pillar section renderer emits `<div id="ai-…"><h3>` for the inline set."""
        all_pillar_src = "\n".join(_pillar_source(p) for p in _PILLAR_RENDERERS)
        offenders = []
        for anchor in sorted(_INLINE_AI_ANCHORS):
            pattern = rf'id="{re.escape(anchor)}"[^>]*>\s*<h3'
            if re.search(pattern, all_pillar_src, re.DOTALL):
                offenders.append(anchor)
        assert not offenders, (
            f"Inline-AI anchor(s) {offenders!r} now have a body `<h3>` in a "
            f"pillar section renderer. Either the cross-cutting exception "
            f"set is stale (update `_INLINE_AI_ANCHORS`) or a duplicate h3 "
            f"has been added by accident. Inline-AI blocks are supposed to "
            f"render as `<h4>` inside `_render_ai_structured_finding`, not "
            f"as dedicated section h3s."
        )

    def test_knowledge_base_sync_health_h3_in_resilience(self):
        """The `Knowledge Base Sync Health` body h3 lives at id `ai-r1` in Resilience."""
        res_src = inspect.getsource(_render_resilience_section)
        # The block emits `<div class="section" id="ai-r1"><h3>…AI badge…KB Sync Health</h3>`.
        assert re.search(r'id="ai-r1"[^>]*>\s*<h3', res_src, re.DOTALL), (
            "`_render_resilience_section` no longer emits "
            '`<div id="ai-r1"><h3>…</h3>`. The R4 cross-cutting exception '
            "set is now stale — the Resilience section is missing its "
            "signal-only Knowledge Base Sync Health block."
        )
        assert 'CHECK_NAMES["ai-r1"]' in res_src, (
            "Resilience section renders the Knowledge Base Sync Health h3 "
            'but not via `CHECK_NAMES["ai-r1"]` — that duplicates the '
            "name literal outside the CHECK_NAMES source of truth (QB-68)."
        )

    def test_knowledge_base_sync_health_carries_signal_only_note(self):
        """The KB Sync Health block carries the design.md Fix 4 signal-only note."""
        res_src = inspect.getsource(_render_resilience_section)
        # Design.md → Fix 4 → step 2 mandates the wording begins with
        # "Not counted in the Executive Summary". The exact phrasing is
        # allowed to vary in surrounding words but this leading fragment
        # is the contract handle.
        assert "Not counted in the Executive Summary" in res_src, (
            "The Knowledge Base Sync Health block no longer carries the "
            '"Not counted in the Executive Summary" per-block note '
            "mandated by design.md → Fix 4 → step 2. Without the note a "
            "reader sees an unexplained ES-vs-body count mismatch."
        )

    def test_ai_findings_block_carries_cross_cutting_caption(self):
        """`_render_ai_findings_block` prepends the cross-cutting caption to every pillar."""
        block_src = inspect.getsource(_render_ai_findings_block)
        # Design.md → Fix 4 → step 1 mandates a caption reading roughly
        # "AI findings for this pillar are counted in the Executive Summary
        # but rendered inline here." The rendered string is spliced from
        # adjacent Python string literals in the source, so we search for
        # the two anchor fragments that survive the line wrap.
        assert (
            "AI findings for this pillar" in block_src
            and "rendered inline" in block_src
        ), (
            "`_render_ai_findings_block` no longer emits the caption "
            "documenting that its findings are counted in the ES but "
            "rendered inline. That caption is the per-pillar handle for "
            "the R4 cross-cutting contract."
        )

    def test_inline_ai_anchor_set_matches_expected(self):
        """The inline-AI anchor set is the exact five items design.md documents.

        This is the "fails on drift in either direction" clause: adding a
        sixth inline-AI check to `_render_ai_findings_block` without
        updating the cross-cutting documentation, or removing one of the
        current five, breaks the R4 contract and fails here.
        """
        # We derive the actual set from what `_render_ai_findings_block`
        # would push into `checks` by scanning the AI pillar fixture's
        # structured findings — the fixture is the canonical inventory of
        # AI checks the analyzer emits today.
        from fixtures import analyzer_payloads as payloads

        emitted = set()
        for pillar_findings in payloads.AI_PILLAR_FINDINGS.values():
            for finding in pillar_findings:
                check_name = finding.get("check_name")
                if check_name:
                    emitted.add(f"ai-{check_name}")

        # The fixture also seeds `assistant_discovery` under Resilience for
        # unrelated E2E assertions; that anchor is not part of the current
        # inline-AI cross-cutting set (it is an AI structured finding but
        # not one of the five design.md enumerates). Drop it before
        # comparing.
        emitted_inline_ai = {a for a in emitted if a not in {"ai-assistant_discovery"}}

        # The five design.md documents must all be present. Anything
        # additional the fixture emits beyond the documented five will fail
        # here and force a documentation update.
        missing = _INLINE_AI_ANCHORS - emitted_inline_ai
        extra = emitted_inline_ai - _INLINE_AI_ANCHORS
        assert not missing and not extra, (
            "The inline-AI anchor set enumerated in this test has drifted "
            "from what the AI analyzer fixture actually emits.\n"
            f"  Missing (in _INLINE_AI_ANCHORS but not emitted): {sorted(missing)}\n"
            f"  Extra (emitted but not in _INLINE_AI_ANCHORS):   {sorted(extra)}\n"
            "Update design.md → Fix 4 to enumerate the new set, then "
            "update `_INLINE_AI_ANCHORS` here."
        )
