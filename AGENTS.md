# Repository Instructions

These instructions apply to any agent (Claude Code, Copilot, etc.) working in this repository.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages. The release workflow (release-please) parses these to generate changelogs and version bumps.

Allowed types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `build`, `ci`, `perf`, `style`.

Breaking changes: append `!` after the type (e.g., `feat!: rename public API`) or add a `BREAKING CHANGE:` footer.

## Branching

- `main` is the default branch and is protected by a ruleset.
- All work happens in feature branches merged via pull request.
- Squash or rebase merges only — no merge commits.
- Branches must be up to date with `main` before merging (`strict_required_status_checks_policy`).

## Required local checks

Before pushing, run `make check` to verify formatting, lint, and tests locally. The workflows that gate merge are `analyze` (CodeQL), `lint`, and `test`.

## Drift audit

Install the audit skill: `npx skills add richwklein/skills`

Run `/repo-template-audit richwklein/repo-template-base` to check that template-tracked files and GitHub repo settings still match the template.

## Skill authoring

See [CONVENTIONS.md](CONVENTIONS.md) for the full skill authoring specification (uses RFC 2119 language).
