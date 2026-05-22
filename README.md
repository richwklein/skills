# skills

Claude Code skills for [@richwklein](https://github.com/richwklein) repositories.

## Installation

```bash
npx skills add richwklein/skills
```

## Available skills

### repo-template-audit

Compares a local repo against a GitHub template repo to surface file drift and GitHub settings drift.

```
/repo-template-audit <owner/repo>
```

Pass the template repo to compare against (e.g., `richwklein/repo-template-base` or `richwklein/repo-template-astro`). The skill walks the template's directory tree and fetches live GitHub settings from both repos to produce a drift report.

## License

[MIT](LICENSE) © Richard Klein
