#!/usr/bin/env sh
# =============================================================================
# generate-release-notes.sh
# Generates rich release notes for a git tag by combining:
#   1. CHANGELOG.md section for the tag (curated highlights, if present)
#   2. Categorized commit history since the previous tag (with SHAs)
#   3. Contributor list + diff stats
#   4. Deployment artifacts info
#
# Usage: ./scripts/generate-release-notes.sh [TAG]
# Falls back to CI_COMMIT_TAG env var when TAG is omitted.
# Writes output to release-notes.md in the current working directory.
# =============================================================================
set -eu

TAG="${1:-${CI_COMMIT_TAG:-}}"
if [ -z "$TAG" ]; then
  echo "ERROR: No tag specified. Pass as argument or set CI_COMMIT_TAG." >&2
  exit 1
fi

# Resolve the git ref for the tag. If the tag doesn't exist yet (preview mode
# before the tag is created), fall back to HEAD so authors can iterate on
# release notes locally without needing to tag first. The tag NAME still
# appears in the output — only the git-ref used to compute ranges changes.
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
  TAG_REF="$TAG"
else
  echo "WARNING: Tag '$TAG' does not exist yet — using HEAD as the release endpoint (preview mode)." >&2
  TAG_REF="HEAD"
fi

# --- Previous tag (highest version-sorted tag that isn't this one) -----------
PREV_TAG=$(git tag --sort=-version:refname | grep -v "^${TAG}$" | head -1 || true)
if [ -z "$PREV_TAG" ]; then
  RANGE="$TAG_REF"
  PREV_DISPLAY="the initial commit"
  DIFF_BASE=$(git rev-list --max-parents=0 HEAD | tail -1)
else
  RANGE="${PREV_TAG}..${TAG_REF}"
  PREV_DISPLAY="$PREV_TAG"
  DIFF_BASE="$PREV_TAG"
fi

# --- Data collection ---------------------------------------------------------
TOTAL=$(git log "$RANGE" --no-merges --oneline | wc -l | tr -d ' ')
CONTRIBUTORS=$(git log "$RANGE" --no-merges --format='%an' | sort -u)
CONTRIBUTOR_COUNT=$(printf '%s\n' "$CONTRIBUTORS" | grep -c . || true)
STATS=$(git diff --shortstat "${DIFF_BASE}..${TAG_REF}" 2>/dev/null | sed 's/^ *//' || true)

# --- Helpers -----------------------------------------------------------------

# Emit a markdown bullet for a commit, with a link when CI_PROJECT_URL is set.
commit_link() {
  short="$1"
  subject="$2"
  if [ -n "${CI_PROJECT_URL:-}" ]; then
    printf -- '- %s ([`%s`](%s/-/commit/%s))\n' "$subject" "$short" "$CI_PROJECT_URL" "$short"
  else
    printf -- '- %s (`%s`)\n' "$subject" "$short"
  fi
}

# List commits whose subject matches the given case-insensitive ERE pattern.
# The pattern is applied against the subject (post-SHA portion of each log line).
list_by_pattern() {
  pat="$1"
  git log "$RANGE" --no-merges --pretty=tformat:'%h %s' \
    | while IFS=' ' read -r sha rest; do
        [ -z "$sha" ] && continue
        printf '%s' "$rest" | grep -iqE "$pat" && commit_link "$sha" "$rest"
      done
}

# List commits whose subject does NOT match any known conventional prefix.
list_uncategorized() {
  known='^(feat|fix|bugfix|perf|refactor|test|docs?|ci|chore|build|style)([(:!/]|$| )'
  git log "$RANGE" --no-merges --pretty=tformat:'%h %s' \
    | while IFS=' ' read -r sha rest; do
        [ -z "$sha" ] && continue
        printf '%s' "$rest" | grep -iqE "$known" || commit_link "$sha" "$rest"
      done
}

# Emit a "#### Label" section only if the body is non-empty.
emit_section() {
  label="$1"
  body="$2"
  if [ -n "$body" ]; then
    printf '#### %s\n%s\n\n' "$label" "$body"
  fi
}

# Extract the CHANGELOG.md section for $TAG, or emit nothing if not present.
# Matches headers of the form "## [<tag>]" (optionally followed by " - date" etc.).
extract_changelog_section() {
  [ -f CHANGELOG.md ] || return 0
  awk -v tag="$TAG" '
    BEGIN {
      hdr = "## [" tag "]"
      hlen = length(hdr)
    }
    {
      if (substr($0, 1, hlen) == hdr && (length($0) == hlen || substr($0, hlen+1, 1) == " ")) {
        in_section = 1
        next
      }
      if (in_section && substr($0, 1, 3) == "## ") exit
      if (in_section) print
    }
  ' CHANGELOG.md \
    | sed '/^---[[:space:]]*$/d' \
    | awk 'NF { seen=1 } seen'
}

# --- Assemble release-notes.md -----------------------------------------------

{
  printf '## Amazon Connect Operational Review %s\n\n' "$TAG"
  printf '**%s commit(s) since %s**' "$TOTAL" "$PREV_DISPLAY"
  if [ "$CONTRIBUTOR_COUNT" -gt 0 ]; then
    printf ' by %s contributor(s)' "$CONTRIBUTOR_COUNT"
  fi
  printf '\n'
  if [ -n "$STATS" ]; then
    printf '\n> %s\n' "$STATS"
  fi
  printf '\n'

  # 1. Curated highlights from CHANGELOG.md (if a matching entry exists)
  CHANGELOG_SECTION=$(extract_changelog_section)
  if [ -n "$CHANGELOG_SECTION" ]; then
    printf '### Highlights\n\n'
    printf '%s\n\n' "$CHANGELOG_SECTION"
  fi

  # 2. Categorized commit list
  printf '### Commits\n\n'
  emit_section "Features"      "$(list_by_pattern '^(feat)([(:!/]|$| )')"
  emit_section "Bug Fixes"     "$(list_by_pattern '^(fix|bugfix)([(:!/]|$| )')"
  emit_section "Performance"   "$(list_by_pattern '^(perf)([(:!/]|$| )')"
  emit_section "Refactoring"   "$(list_by_pattern '^(refactor)([(:!/]|$| )')"
  emit_section "Tests"         "$(list_by_pattern '^(test)([(:!/]|$| )')"
  emit_section "Documentation" "$(list_by_pattern '^(docs?)([(:!/]|$| )')"
  emit_section "CI & Chores"   "$(list_by_pattern '^(ci|chore|build|style)([(:!/]|$| )')"
  emit_section "Other Changes" "$(list_uncategorized)"

  # 3. Contributors
  if [ -n "$CONTRIBUTORS" ]; then
    printf '### Contributors\n\n'
    printf '%s\n' "$CONTRIBUTORS" | while IFS= read -r name; do
      [ -n "$name" ] && printf -- '- %s\n' "$name"
    done
    printf '\n'
  fi

  # 4. Compare link (GitLab renders it as a clickable diff view)
  if [ -n "$PREV_TAG" ] && [ -n "${CI_PROJECT_URL:-}" ]; then
    printf '### Full Changelog\n\n'
    printf '[`%s...%s`](%s/-/compare/%s...%s)\n\n' \
      "$PREV_TAG" "$TAG" "$CI_PROJECT_URL" "$PREV_TAG" "$TAG"
  fi

  # 5. Deployment artifacts (unchanged content)
  cat <<'EOF'
---

### Deployment Artifacts

| Artifact | Description |
|----------|-------------|
| **CloudFormation Template** | Single-file deployment — launch directly in any AWS account |
| **Terraform Module** | Full module with variables, state machine definition, and Lambda packages |

### Quick Start

- **CFT**: Download `cft-release.zip`, extract, and deploy `CFT-AmazonConnectOperationsReview.yml` via the AWS Console or CLI
- **Terraform**: Download `tf-module.tar.gz`, extract, configure `terraform.tfvars`, and run `terraform apply`

See the included README in each package for full deployment instructions.
EOF
} > release-notes.md

echo "Release notes written to release-notes.md (${TOTAL} commits, range: ${RANGE})"
