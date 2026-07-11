# Skill Authoring Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in skill documents are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Structure

Every skill MUST live in its own directory under `skills/`, containing a `SKILL.md` file.

```
skills/
└── skill-name/
    ├── SKILL.md          # REQUIRED — skill metadata and instructions
    ├── lib/              # OPTIONAL — implementation scripts
    └── reference/        # OPTIONAL — config files read at runtime
```

## SKILL.md

The `SKILL.md` file MUST include YAML frontmatter with at minimum:

- `name` — a unique identifier for the skill.
- `description` — when and why to invoke the skill. SHOULD include trigger conditions.

The body MUST describe the invocation syntax and expected behavior. It SHOULD describe how to interpret output and how to remediate findings.

## Arguments

Skills that operate on external resources SHOULD accept those resources as explicit arguments rather than relying on implicit detection or hardcoded values.

## Dependencies

Skills MUST NOT depend on files existing in target repositories beyond what the skill itself provides. Runtime dependencies (e.g., `python3`, `gh`) SHOULD be documented in the SKILL.md.

## Output

Skills that produce reports MUST output well-formed Markdown. Reports SHOULD be structured with headers so Claude can present findings grouped by section.

## Classification language

When a skill classifies findings by severity or action, it SHOULD use these terms consistently:

- **Missing** — an expected artifact is absent.
- **Drifted** — an artifact exists but differs from the reference.
- **Gap** — a structural or schema requirement is unmet.

## Preview and confirm

Skills that write files MUST show the user a semantic preview before writing anything.

- The preview MUST be a summary of the intended action — a table or structured list of "parameter → value" or "file → action" pairs. It MUST NOT contain raw file markup (Markdown, MDX, HTML, YAML, JSON) as if those files had already been written.
- The skill MUST pause after presenting the preview and wait for explicit user confirmation before performing any write.
- If the user declines or requests changes, the skill MUST incorporate the updated intent and re-present the preview before proceeding.
