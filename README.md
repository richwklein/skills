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

### prepare-image

Resizes and optimizes a source image to meet a repository's documented image rules (width, size limit, format), or validates an existing image directory against those rules.

```
/prepare-image <source> <output-name> [--width N] [--max-kb N] [--dir path] [--format jpeg|keep]
/prepare-image --check [--dir path] [--width N] [--max-kb N] [--width-mode exact|at-least]
```

The skill reads the host repository's `README.md` and `AGENTS.md` to discover image constraints, confirms parameters with you before writing, and uses `sharp` (from the host repo's `node_modules`) to EXIF-rotate, resize, and compress images.

## License

[MIT](LICENSE) © Richard Klein
