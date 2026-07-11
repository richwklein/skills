import { readdir, stat, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const QUALITY_STEPS = [82, 75, 68, 60, 52, 45]

let _sharp
function loadSharp() {
  if (!_sharp) {
    try {
      const hostRequire = createRequire(path.join(process.cwd(), '__sentinel__'))
      _sharp = hostRequire('sharp')
    } catch {
      throw new Error(
        'sharp not found in the host repository. Install it with: npm install sharp'
      )
    }
  }
  return _sharp
}

/**
 * Resizes a source image and writes it to the output directory.
 * For JPEG output, steps quality down (mozjpeg) until under maxBytes.
 */
export async function prepareImage(sourcePath, name, options = {}) {
  const {
    imageDir = 'public/images',
    width = 600,
    maxBytes = 100 * 1024,
    format = 'jpeg',
  } = options

  const sharp = loadSharp()

  const ext =
    format === 'jpeg'
      ? '.jpg'
      : path.extname(sourcePath).toLowerCase() || '.jpg'
  const baseName = /\.(jpe?g|png|gif|webp)$/i.test(name) ? name : `${name}${ext}`
  const outPath = path.join(imageDir, baseName)

  const pipeline = sharp(sourcePath)
    .rotate()
    .resize({ width, withoutEnlargement: true })

  if (format === 'jpeg') {
    for (const quality of QUALITY_STEPS) {
      const buffer = await pipeline.clone().jpeg({ quality, mozjpeg: true }).toBuffer()
      if (buffer.length <= maxBytes) {
        await writeFile(outPath, buffer)
        const { width: w, height: h } = await sharp(buffer).metadata()
        return { outPath, bytes: buffer.length, width: w, height: h, quality }
      }
    }
    throw new Error(
      `Could not compress ${sourcePath} under ${maxBytes} bytes at minimum quality ${QUALITY_STEPS.at(-1)}`
    )
  }

  const buffer = await pipeline.toBuffer()
  await writeFile(outPath, buffer)
  const { width: w, height: h } = await sharp(buffer).metadata()
  return { outPath, bytes: buffer.length, width: w, height: h }
}

/**
 * Validates every image in imageDir against the size and width rules.
 * Returns an array of finding objects; empty means all images comply.
 */
export async function checkImages(options = {}) {
  const {
    imageDir = 'public/images',
    width = 600,
    maxBytes = 100 * 1024,
    widthMode = 'exact',
  } = options

  const sharp = loadSharp()
  const findings = []

  for (const entry of await readdir(imageDir, { withFileTypes: true })) {
    if (!entry.isFile() || !/\.(jpe?g|png|gif|webp)$/i.test(entry.name)) {
      continue
    }
    const filePath = path.join(imageDir, entry.name)
    const { size } = await stat(filePath)
    const { width: imgWidth } = await sharp(filePath).metadata()

    const widthOk = widthMode === 'exact' ? imgWidth === width : imgWidth >= width
    if (!widthOk) {
      const expected = widthMode === 'exact' ? `exactly ${width}px` : `at least ${width}px`
      findings.push({
        type: 'Drifted',
        file: entry.name,
        issue: `width is ${imgWidth}px (expected ${expected})`,
      })
    }

    if (size > maxBytes) {
      findings.push({
        type: 'Drifted',
        file: entry.name,
        issue: `size is ${Math.round(size / 1024)} KB (limit: ${Math.round(maxBytes / 1024)} KB)`,
      })
    }
  }
  return findings
}
