---
name: repo-template-audit
description: >
  Compare a local repo against a GitHub template repo to surface file drift and
  GitHub settings drift. Use when the user runs /repo-template-audit, asks to
  check template drift, or wants to verify repo settings match a template baseline.
---

# repo-template-audit

Audits a target repo against a template repo on GitHub.

1. **File drift**: walks the template repo's directory tree and compares each tracked file against the local copy. Files are classified as exact-match, presence-only, or schema-validated.
2. **Settings drift**: fetches live GitHub settings from both the template repo and the target repo, then compares fields defined in `reference/settings-checks.yaml`.

## Invocation

```
/repo-template-audit [--apply] [owner/repo]
```

The template repo argument specifies which template to compare against (e.g., `richwklein/repo-template-base` or `richwklein/repo-template-astro`).

If omitted, the script queries the GitHub API for the repo's `template_repository` metadata. When detected, the script prints `DETECTED_TEMPLATE=<owner/repo>` to stderr. **You MUST confirm the detected template with the user before presenting findings.** If no template is detected, prompt the user to provide one.

### Audit mode (default)

When invoked without `--apply`:

1. Resolve the target path. Default to the current working directory.
2. Run the audit script:

   ```bash
   python3 <skill-dir>/audit [owner/repo] [target-path]
   ```

   `<skill-dir>` is the directory containing this SKILL.md file.

3. If stderr contains `DETECTED_TEMPLATE=`, ask the user to confirm that template before continuing.
4. Read the script's markdown output. Present the findings to the user, grouped by section.

### Apply mode (`--apply`)

When invoked with `--apply`:

1. Resolve the target path. Default to the current working directory.
2. Run the apply script:

   ```bash
   python3 <skill-dir>/apply [owner/repo] [target-path]
   ```

3. If stderr contains `DETECTED_TEMPLATE=`, ask the user to confirm that template before continuing.
4. Read the script's markdown output. The script **automatically applies**:
   - All settings drift (GitHub API calls)
   - Missing `exact_match` files (fetched from template and written locally)
   - Missing rulesets (copied from template)
5. The script reports **drifted files** that it did NOT auto-apply. Present those diffs to the user and ask which (if any) to reset to the template version.
6. If any files were synced locally, commit them:

   ```bash
   git add <changed-files>
   git commit -m "chore(audit): sync files with template"
   ```

## Interpreting output

The script emits these finding types:

- **Missing files**: present in the template but absent locally. Almost always real drift.
- **Drifted files**: present locally but differ from the template. Inspect each diff individually — do NOT batch-dismiss them as intentional.
- **Schema gaps**: required fields or scripts missing (e.g., package.json scripts).
- **Settings drift**: GitHub repo settings differ between the template and the target. Shown as a table with field, template value, and target value.

### Per-file drift classification

For every drifted file, classify it before presenting. Apply these heuristics in order:

1. **CHANGELOG.md** — always skip. It's project-specific release history. Mention it once in passing, never flag it as drift.

2. **Workflow / CI files** (`.github/workflows/*.yaml`, `.github/actions/**`) — always flag, even if the diff is additions-only. These files control security-sensitive pipelines (scanning, permissions, release). Present the diff and ask the user to explicitly confirm the change is intentional.

3. **Deletion-containing diffs** — a diff that removes lines present in the template (`-` lines on the template side) must be flagged individually. The template author included that content for a reason; ask the user to confirm each removal was deliberate.

4. **Additions-only diffs** in non-sensitive files — the diff adds lines locally but preserves all template lines. These are likely intentional extensions (e.g., adding a language-specific section to `.gitignore`, adding an IDE extension). Group these together and ask for a single confirmation rather than flagging each one.

5. **Cosmetic-only diffs** (e.g., quote style `'` vs `"`, trailing whitespace) — note them as low-risk cosmetic differences, do not require explicit confirmation.

### Interaction model for drifted files

After classifying, present findings in two groups:

**Flagged (requires explicit confirmation):**
- Each CI/workflow file (additive or not)
- Each file with deletions from the template

For each flagged file, show the diff and ask: _"Was this change intentional?"_ Wait for the user's answer before moving on.

**Informational (group confirmation):**
- Additions-only diffs in non-sensitive files
- Cosmetic-only diffs

List these together and ask once: _"These look like expected project-specific additions — do any of them need a closer look?"_

**Never** make a blanket statement that all diffs are intentional without applying these heuristics first.

## Remediating

After presenting findings, offer to fix items one section at a time. The user confirms each batch.

### File fixes

For drifted or missing files, use the `gh` CLI to fetch the canonical content and overwrite locally:

```bash
gh api repos/<template-owner>/<template-repo>/contents/<path> \
  --jq '.content' | base64 -d > <path>
```

Then open a PR with the change. Commit message convention: `chore(audit): sync <files> with template`.

### Settings fixes

Each table row maps to a specific API endpoint. Common patches:

```bash
# Actions: workflow permissions
gh api --method PUT repos/<owner>/<repo>/actions/permissions/workflow \
  -F default_workflow_permissions=write -F can_approve_pull_request_reviews=true

# Actions: allowed_actions selection
gh api --method PUT repos/<owner>/<repo>/actions/permissions \
  -F enabled=true -F allowed_actions=selected

# Actions: selected-actions list
gh api --method PUT repos/<owner>/<repo>/actions/permissions/selected-actions \
  -F github_owned_allowed=true -F verified_allowed=true \
  -f 'patterns_allowed[]=googleapis/release-please-action@*' \
  -f 'patterns_allowed[]=github/codeql-action/*@*' \
  -f 'patterns_allowed[]=davelosert/vitest-coverage-report-action@*' \
  -f 'patterns_allowed[]=marocchino/sticky-pull-request-comment@*'

# Security toggles
gh api --method PATCH repos/<owner>/<repo> \
  --raw-field 'security_and_analysis[secret_scanning][status]=enabled' \
  --raw-field 'security_and_analysis[secret_scanning_push_protection][status]=enabled' \
  --raw-field 'security_and_analysis[dependabot_security_updates][status]=enabled'

# Dependabot alerts
gh api --method PUT repos/<owner>/<repo>/vulnerability-alerts

# Private vulnerability reporting
gh api --method PUT repos/<owner>/<repo>/private-vulnerability-reporting

# General merge / web-commit settings
gh api --method PATCH repos/<owner>/<repo> \
  -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F allow_auto_merge=false -F allow_update_branch=false \
  -F web_commit_signoff_required=true
```
