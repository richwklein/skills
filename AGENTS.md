# Repository Instructions

These instructions apply to any agent (Claude Code, Copilot, etc.) working in this repository.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.

Allowed types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `build`, `ci`, `perf`, `style`.

Breaking changes: append `!` after the type (e.g., `feat!: rename public API`) or add a `BREAKING CHANGE:` footer.

## Branching

- `main` is the default branch and is protected by a ruleset.
- All work happens in feature branches merged via pull request.
- Squash or rebase merges only — no merge commits.

## Skill authoring

See [CONVENTIONS.md](CONVENTIONS.md) for the full skill authoring specification (uses RFC 2119 language).

## Drift audit

Run `/repo-template-audit richwklein/repo-template-base` to check that this repo's template-tracked files and GitHub settings match the base template.
