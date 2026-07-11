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
4. Read the script's markdown output. **Do not present all findings at once.** Walk the user through the report one section at a time, following the [section-by-section flow](#section-by-section-flow) below. Wait for the user to respond before moving to the next section.

### Apply mode (`--apply`)

When invoked with `--apply`:

1. Resolve the target path. Default to the current working directory.
2. Run the **audit** script first to determine what will be changed:

   ```bash
   python3 <skill-dir>/audit [owner/repo] [target-path]
   ```

3. If stderr contains `DETECTED_TEMPLATE=`, ask the user to confirm that template before continuing.
4. **Show a preview and wait for confirmation.** From the audit output, summarize what the apply script will auto-apply in a table — never raw file content:

   | Action | Items |
   |---|---|
   | Settings to update | _list of drifted field names_ |
   | Missing files to pull in | _list of paths_ |
   | Missing rulesets to copy | _list of names_ |

   Note that **drifted files** are not auto-applied and will be presented separately after apply runs.

   **Stop. Wait for explicit confirmation before running the apply script.** If the user declines or requests changes, incorporate their feedback and re-present the preview before proceeding.

5. Run the apply script:

   ```bash
   python3 <skill-dir>/apply [owner/repo] [target-path]
   ```

6. Read the script's markdown output. The script **automatically applies**:
   - All settings drift (GitHub API calls)
   - Missing `exact_match` files (fetched from template and written locally)
   - Missing rulesets (copied from template)
7. The script reports **drifted files** that it did NOT auto-apply. Present those diffs to the user and ask which (if any) to reset to the template version.
8. If any files were synced locally, commit them:

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

2. **Workflow / CI files** (`.github/workflows/*.yaml`, `.github/actions/**`) — always classify as **Flagged**, even if the diff is additions-only. These files control security-sensitive pipelines (scanning, permissions, release).

3. **Deletion-containing diffs** — classify as **Flagged**. The template author included the removed content for a reason; the user must confirm each removal was deliberate.

4. **Additions-only diffs** in non-sensitive files — classify as **Informational**. These are likely intentional extensions (e.g., adding a language-specific section to `.gitignore`, adding an IDE extension).

5. **Cosmetic-only diffs** (e.g., quote style `'` vs `"`, trailing whitespace) — classify as **Informational** and note as low-risk.

### Section-by-section flow

The wall-of-text approach (dumping every section and every question in one response) is the failure mode this skill exists to prevent. Walk the user through the report **one section at a time**. Within a section, you MAY batch all items together and ask a single set of questions — but you MUST stop and wait for the user's response before presenting the next section.

Present sections in this order, skipping any that are empty. **The user needs enough context to decide without leaving the conversation** — show actual content (file contents, diffs, setting values), not summaries of content.

1. **Missing files** — for each path, give:
   - the path,
   - a one-line note on what the template uses it for,
   - a snippet (first ~20 lines, or the whole file if shorter) so the user can see what would land if they pull it in.

   Then ask: _"Should we pull these in from the template, or are any intentionally omitted?"_ **Stop. Wait.**

2. **Drifted files — Flagged group** — for each file, show in this order:
   - the path as a heading,
   - a one-line summary of what changed (e.g., _"removes the template's PATH FILTERS comment block; bumps `actions/github-script` v7 → v8"_),
   - **the actual unified diff in a fenced code block**, not a paraphrase. If the diff is genuinely huge (>80 lines), show the most decision-relevant hunks and say what was elided — never replace the diff with prose.

   After all files, ask one numbered question per file tied to the specific change (e.g., _"1. `release-please.yaml` — was removing the `No paths filter` guidance comment intentional?"_). **Stop. Wait.**

3. **Drifted files — Informational group** — for each file, give path + a one-line summary of what it adds (a diff isn't required here since these are additions-only and low-risk). If the user asks to see one, show the diff before moving on. Ask once: _"These look like expected project-specific additions — do any of them need a closer look?"_ **Stop. Wait.**

4. **Schema gaps** (if any) — for each gap, show the path, the missing field/script, and the template's value for it. Ask whether to address them. **Stop. Wait.**

5. **Settings drift** — present the table with field, template value, target value. For any row whose meaning isn't obvious from the field name (e.g., `default_workflow_permissions`, `allowed_actions`), add a one-line note on what that setting controls. Ask which rows to bring into alignment. **Stop. Wait.**

Rules:

- Never preview a later section while presenting an earlier one ("…and then we'll look at settings drift" is fine; showing the settings table early is not).
- Never combine the confirmation prompts for two sections into one response.
- If a section is empty, say so in one line and move on without a prompt.
- **Never** make a blanket statement that all diffs are intentional without applying the classification heuristics first.
- **Never** replace a diff or file snippet with a prose summary when asking the user to make a judgment call on it. Prose can accompany the diff; it cannot stand in for it.

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
