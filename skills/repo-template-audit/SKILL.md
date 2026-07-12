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
   repo-template-audit [owner/repo] [target-path]
   ```

   If `repo-template-audit` is not on PATH, fall back to `python3 <skill-dir>/audit`, where `<skill-dir>` is the directory containing this SKILL.md file.

3. If stderr contains `DETECTED_TEMPLATE=`, ask the user to confirm that template before continuing.
4. Read the script's markdown output. **Do not present all findings at once.** Walk the user through the report one section at a time, following the [section-by-section flow](#section-by-section-flow) below. Wait for the user to respond before moving to the next section.

### Apply mode (`--apply`)

When invoked with `--apply`:

1. Resolve the target path. Default to the current working directory.
2. Run the **audit** script first to determine what will be changed:

   ```bash
   repo-template-audit [owner/repo] [target-path]
   ```

3. If stderr contains `DETECTED_TEMPLATE=`, ask the user to confirm that template before continuing.
4. **Show a preview and wait for confirmation.** From the audit output, summarize what the apply script will auto-apply in a table — never raw file content:

   | Action | Items |
   |---|---|
   | Settings to update | _list of drifted field names_ |
   | Missing files to pull in | _paths marked **new in template**_ |
   | Missing rulesets to copy | _list of names_ |

   Note that **drifted files** are not auto-applied, and missing files marked **deleted locally** are skipped — both are presented separately after apply runs.

   **Stop. Wait for explicit confirmation before running the apply script.** If the user declines or requests changes, incorporate their feedback and re-present the preview before proceeding.

5. Run the apply script:

   ```bash
   repo-template-audit-apply [owner/repo] [target-path]
   ```

   If `repo-template-audit-apply` is not on PATH, fall back to `python3 <skill-dir>/apply`.

6. Read the script's markdown output. The script **automatically applies**:
   - All settings drift (GitHub API calls)
   - Missing `exact_match` files that are **new in the template** (fetched from template and written locally). Files a local commit deleted are **not** restored; they appear under "Skipped — deleted locally" — confirm with the user whether each removal still stands before restoring any.
   - Missing rulesets (copied from template)
7. The script reports **drifted files** that it did NOT auto-apply. Files marked **behind template** are unmodified older template versions — recommend resetting those. For the rest, present the diffs to the user and ask which (if any) to reset to the template version.
8. If any files were synced locally, commit them:

   ```bash
   git add <changed-files>
   git commit -m "chore(audit): sync files with template"
   ```

## Interpreting output

The script emits these finding types:

- **Missing files**: present in the template but absent locally. The script classifies each by the target's git history:
  - **New in template** — no local commit ever deleted it; the file landed in the template after this repo was generated. This is template evolution the repo hasn't picked up. Default action: adopt it.
  - **Deleted locally** — a local commit removed it; the report cites the deleting commit. Confirm the removal still stands before restoring.
- **Drifted files**: present locally but differ from the template. Diffs read **local → template** — the patch a sync would apply: `+` lines are template content the repo hasn't picked up; `-` lines are local content a sync would remove. Files marked **behind template** are unmodified older template versions — the template simply moved forward, and syncing is a safe fast-forward. Inspect each diff individually — do NOT batch-dismiss them as intentional.
- **Schema gaps**: required fields or scripts missing (e.g., package.json scripts).
- **Settings drift**: GitHub repo settings differ between the template and the target. Shown as a table with field, template value, and target value.

### Per-file drift classification

For every drifted file, classify it into one of three groups — **Sync recommended**, **Flagged**, or **Informational** — before presenting. Remember the diff direction (local → template) when reading these. Apply the heuristics in order:

1. **CHANGELOG.md** — always skip. It's project-specific release history. Mention it once in passing, never flag it as drift.

2. **Workflow / CI files** (`.github/workflows/*.yaml`, `.github/actions/**`) — always classify as **Flagged**, even when marked behind-template or additions-only. These files control security-sensitive pipelines (scanning, permissions, release). Mention the behind-template evidence when presenting, but still show the diff and get confirmation.

3. **Behind template** (marked by the script) — classify as **Sync recommended**. The local file is an unmodified older template version; syncing is a fast-forward that loses nothing local.

4. **Diffs with only `+` lines** — classify as **Sync recommended**. The template gained content this repo hasn't picked up, and there is no local-only content a sync would remove.

5. **Diffs with only `-` lines** in non-sensitive files — classify as **Informational**. These are local extensions the template doesn't have (e.g., a language-specific section in `.gitignore`, an extra IDE extension); the default is to keep them.

6. **Mixed diffs** (both `+` and `-` lines) — classify as **Flagged**. Both sides changed: syncing wholesale would drop local content, keeping local misses template updates. The user must decide per file; a manual merge may be needed.

7. **Cosmetic-only diffs** (e.g., quote style `'` vs `"`, trailing whitespace) — classify as **Informational** and note as low-risk.

### Section-by-section flow

The wall-of-text approach (dumping every section and every question in one response) is the failure mode this skill exists to prevent. Walk the user through the report **one section at a time**. Within a section, you MAY batch all items together and ask a single set of questions — but you MUST stop and wait for the user's response before presenting the next section.

Present sections in this order, skipping any that are empty. **The user needs enough context to decide without leaving the conversation** — show actual content (file contents, diffs, setting values), not summaries of content.

1. **Missing files** — present the two provenance groups from the report separately:

   **New in template** (never existed in this repo — template evolution to pick up). For each path, give:
   - the path,
   - a one-line note on what the template uses it for,
   - a snippet (first ~20 lines, or the whole file if shorter) so the user can see what would land.

   Then ask: _"These are new in the template and never existed here — I recommend pulling them all in. Any you'd rather skip?"_

   **Deleted locally** (a local commit removed them). Show each path, the deleting commit from the report, and a snippet of the template version. Ask one numbered question per file: _"1. `foo.yaml` was deleted here in `abc1234 remove foo` — keep it removed, or restore the template version?"_

   **Stop. Wait.**

2. **Drifted files — Sync recommended group** — for each file, show the path, why it's safe (behind-template with the matching commit, or additions-only), and **the actual unified diff in a fenced code block** so the user sees what lands. Then ask once: _"These only pick up template changes — nothing local is lost. Sync them all, or skip any?"_ **Stop. Wait.**

3. **Drifted files — Flagged group** — for each file, show in this order:
   - the path as a heading,
   - a one-line summary of what changed (e.g., _"template bumps `actions/github-script` v7 → v8; local adds a deploy step the template lacks"_),
   - **the actual unified diff in a fenced code block**, not a paraphrase. If the diff is genuinely huge (>80 lines), show the most decision-relevant hunks and say what was elided — never replace the diff with prose.

   After all files, ask one numbered question per file tied to the specific change (e.g., _"1. `release-please.yaml` — sync the template's new comment block, keep your local edit, or merge both?"_). **Stop. Wait.**

4. **Drifted files — Informational group** — for each file, give path + a one-line summary of the local-only content it keeps (a diff isn't required here since these are local extensions or cosmetic). If the user asks to see one, show the diff before moving on. Ask once: _"These look like expected project-specific extensions — do any of them need a closer look?"_ **Stop. Wait.**

5. **Schema gaps** (if any) — for each gap, show the path, the missing field/script, and the template's value for it. Ask whether to address them. **Stop. Wait.**

6. **Settings drift** — present the table with field, template value, target value. For any row whose meaning isn't obvious from the field name (e.g., `default_workflow_permissions`, `allowed_actions`), add a one-line note on what that setting controls. Ask which rows to bring into alignment. **Stop. Wait.**

Rules:

- Never preview a later section while presenting an earlier one ("…and then we'll look at settings drift" is fine; showing the settings table early is not).
- Never combine the confirmation prompts for two sections into one response.
- If a section is empty, say so in one line and move on without a prompt.
- **Never** make a blanket statement that all diffs are intentional without applying the classification heuristics first.
- **Never** suggest a missing file was intentionally omitted unless the report marks it **deleted locally** (or the user has said so). A file marked **new in template** is template evolution — the default is to adopt it, not to rationalize its absence.
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
