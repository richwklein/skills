---
name: prepare-image
description: >
  Resize and optimize a source image for a target repository's image rules (width,
  size limit, format). Also validates an existing image directory against those rules.
  Use when the user runs /prepare-image, wants to resize or compress an image, or
  asks to check whether images in a directory meet documented constraints.
---

# prepare-image

Prepares a source image for use in the host repository — EXIF-rotate, resize, and
compress to JPEG (mozjpeg quality stepping) — or validates an existing image directory
against documented constraints.

## Runtime dependency

This skill requires `sharp` to be installed in the **host repository** (the repo where
the image work is happening). Resolve it from that repo's `node_modules`; do not install
it globally. If `sharp` is missing, the script will emit an error with the install command.

## Invocation

```text
/prepare-image <source> <output-name> [--width N] [--max-kb N] [--dir path] [--format jpeg|keep]
/prepare-image --check [--dir path] [--width N] [--max-kb N] [--width-mode exact|at-least]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `<source>` | — | Path to the source image file |
| `<output-name>` | — | Output filename (`.jpg` appended if no extension) |
| `--width N` | `600` | Target pixel width |
| `--max-kb N` | `100` | Maximum output size in KB (JPEG only) |
| `--dir path` | `public/images` | Output directory |
| `--format jpeg\|keep` | `jpeg` | Output format; `keep` preserves the source format |
| `--check` | — | Validate images in `--dir` instead of writing |
| `--width-mode exact\|at-least` | `exact` | Width check semantics for `--check` mode |

## Prepare flow

1. **Read the host repo's image rules.** Before doing anything else, check `README.md` and
   `AGENTS.md` (and any linked docs) in the current working directory for documented image
   constraints: required width, size limits, output directory, format requirements.

2. **Confirm parameters.** Resolve the effective values for `--width`, `--max-kb`, `--dir`,
   and `--format` from the user's arguments and any rules found in step 1. If required
   parameters are missing or ambiguous, ask the user before proceeding.

3. **Show a preview and wait for confirmation.** Per the [preview and confirm convention](../../CONVENTIONS.md),
   present a parameter table — never raw file content — and pause for explicit approval:

   | Parameter | Value |
   |---|---|
   | Source | `<source-path>` |
   | Output | `<dir>/<output-name>.jpg` |
   | Width | `<N>px` |
   | Format | `JPEG (mozjpeg, quality 82→45)` or `keep (<ext>)` |
   | Size limit | `<N> KB` |

   **Stop. Wait for the user to confirm before running the script.** If the user declines or requests changes, incorporate their feedback, update the preview table, and re-present it before proceeding.

4. **Run the script** from the host repository's root directory:

   ```bash
   node <skill-dir>/prepare-image.mjs <source> <output-name> \
     --width <N> --max-kb <N> --dir <path> --format <jpeg|keep>
   ```

   `<skill-dir>` is the directory containing this SKILL.md file.

5. **Report results.** Present the output path, dimensions, size, and quality (if JPEG).
   If compression failed (could not meet the size limit), show the error and ask the user
   whether to relax the `--max-kb` constraint.

## Check flow

1. **Read the host repo's image rules** (same as step 1 in the prepare flow). Pay particular
   attention to whether the width constraint means *exactly* that width (a strict upload rule)
   or *at least* that width (a social-preview target). Use `--width-mode exact` for strict
   rules and `--width-mode at-least` for minimum/target rules.

2. **Run the check script:**

   ```bash
   node <skill-dir>/prepare-image.mjs --check \
     --dir <path> --width <N> --max-kb <N> --width-mode <exact|at-least>
   ```

3. **Interpret the output.** The script emits `[Drifted]` lines for every non-compliant image.
   Present findings using this classification:

   - **Drifted** — image exists but violates a width or size constraint.
   - **Gap** — the images directory is absent or contains no recognizable image files.

   If all images comply, report that clearly with the rules that were checked.

4. **Offer remediation.** For each Drifted image, offer to run `/prepare-image <file>
   <name>` with the repo's documented parameters to bring it into compliance.
