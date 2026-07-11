#!/usr/bin/env node
import { prepareImage, checkImages } from './lib/image.mjs'

function parseArgs(args) {
  const result = {
    check: false,
    positional: [],
    width: 600,
    maxKb: 100,
    dir: 'public/images',
    format: 'jpeg',
    widthMode: 'exact',
  }
  let i = 0
  while (i < args.length) {
    const a = args[i]
    if (a === '--check') result.check = true
    else if (a === '--width') result.width = parseInt(args[++i], 10)
    else if (a === '--max-kb') result.maxKb = parseInt(args[++i], 10)
    else if (a === '--dir') result.dir = args[++i]
    else if (a === '--format') result.format = args[++i]
    else if (a === '--width-mode') result.widthMode = args[++i]
    else if (!a.startsWith('--')) result.positional.push(a)
    i++
  }
  return result
}

function printUsage() {
  process.stderr.write(
    'Usage: node prepare-image.mjs <source> <output-name> [--width N] [--max-kb N] [--dir path] [--format jpeg|keep]\n' +
    '       node prepare-image.mjs --check [--dir path] [--width N] [--max-kb N] [--width-mode exact|at-least]\n'
  )
}

const opts = parseArgs(process.argv.slice(2))

if (opts.check) {
  const findings = await checkImages({
    imageDir: opts.dir,
    width: opts.width,
    maxBytes: opts.maxKb * 1024,
    widthMode: opts.widthMode,
  })
  if (findings.length === 0) {
    const widthDesc = opts.widthMode === 'exact' ? `exactly ${opts.width}px` : `at least ${opts.width}px`
    console.log(`prepare-image check: all images comply (${widthDesc} wide, under ${opts.maxKb} KB)`)
  } else {
    for (const f of findings) {
      process.stderr.write(`prepare-image check: [${f.type}] ${f.file}: ${f.issue}\n`)
    }
    process.exit(1)
  }
} else if (opts.positional.length === 2) {
  const [source, outputName] = opts.positional
  const result = await prepareImage(source, outputName, {
    imageDir: opts.dir,
    width: opts.width,
    maxBytes: opts.maxKb * 1024,
    format: opts.format,
  })
  const qualityNote = result.quality != null ? `, quality ${result.quality}` : ''
  console.log(
    `prepare-image: wrote ${result.outPath} ` +
    `(${result.width}x${result.height}, ${Math.round(result.bytes / 1024)} KB${qualityNote})`
  )
} else {
  printUsage()
  process.exit(1)
}
