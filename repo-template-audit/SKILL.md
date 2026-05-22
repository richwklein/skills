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
/repo-template-audit [owner/repo]
```

The template repo argument specifies which template to compare against (e.g., `richwklein/repo-template-base` or `richwklein/repo-template-astro`).

If omitted, the script queries the GitHub API for the repo's `template_repository` metadata. When detected, the script prints `DETECTED_TEMPLATE=<owner/repo>` to stderr. **You MUST confirm the detected template with the user before presenting findings.** If no template is detected, prompt the user to provide one.

When invoked:

1. Resolve the target path. Default to the current working directory.
2. Run the audit script:

   ```bash
   python3 <skill-dir>/lib/audit.py [owner/repo] [target-path]
   ```

   `<skill-dir>` is the directory containing this SKILL.md file.

3. If stderr contains `DETECTED_TEMPLATE=`, ask the user to confirm that template before continuing.
4. Read the script's markdown output. Present the findings to the user, grouped by section.

## Interpreting output

The script emits these finding types:

- **Missing files**: present in the template but absent locally. Almost always real drift.
- **Drifted files**: present locally but differ from the template. Inspect each diff before proposing a fix — some drift is intentional.
- **Schema gaps**: required fields or scripts missing (e.g., package.json scripts).
- **Settings drift**: GitHub repo settings differ between the template and the target. Shown as a table with field, template value, and target value.

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
