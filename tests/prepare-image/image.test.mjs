import { randomFillSync } from 'node:crypto'
import { mkdtemp, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { before, describe, it } from 'node:test'
import assert from 'node:assert/strict'

import sharp from 'sharp'

import { checkImages, prepareImage } from '../../skills/prepare-image/lib/image.mjs'

// Large noise image: resists compression, forces quality stepping.
async function makeNoisySource(dir) {
  const width = 2400
  const height = 1600
  const raw = Buffer.alloc(width * height * 3)
  randomFillSync(raw)
  const sourcePath = path.join(dir, 'source.png')
  await sharp(raw, { raw: { width, height, channels: 3 } }).png().toFile(sourcePath)
  return sourcePath
}

describe('prepareImage', () => {
  let dir
  let result

  before(async () => {
    dir = await mkdtemp(path.join(tmpdir(), 'prepare-image-'))
    const sourcePath = await makeNoisySource(dir)
    result = await prepareImage(sourcePath, 'event-test', { imageDir: dir })
  }, { timeout: 30000 })

  it('outputs exactly the target width', () => {
    assert.equal(result.width, 600)
  })

  it('outputs under 100 KB', async () => {
    const { size } = await stat(result.outPath)
    assert.ok(size <= 100 * 1024, `expected size <= 102400, got ${size}`)
    assert.equal(size, result.bytes)
  })

  it('appends .jpg when no extension given', () => {
    assert.equal(result.outPath, path.join(dir, 'event-test.jpg'))
  })

  it('passes its own output through checkImages', async () => {
    const findings = await checkImages({ imageDir: dir })
    const ours = findings.filter((f) => f.file === 'event-test.jpg')
    assert.deepEqual(ours, [])
  })
})

describe('checkImages', () => {
  it('reports wrong-width and oversized images as Drifted', async () => {
    const dir = await mkdtemp(path.join(tmpdir(), 'prepare-image-check-'))

    await sharp({ create: { width: 800, height: 400, channels: 3, background: '#336666' } })
      .jpeg()
      .toFile(path.join(dir, 'wrong-width.jpg'))

    const raw = Buffer.alloc(300 * 300 * 3)
    randomFillSync(raw)
    const big = await sharp(raw, { raw: { width: 300, height: 300, channels: 3 } })
      .png({ compressionLevel: 0 })
      .toBuffer()
    await writeFile(path.join(dir, 'too-big.png'), big)

    const findings = await checkImages({ imageDir: dir })

    const widthFinding = findings.find((f) => f.file === 'wrong-width.jpg' && f.issue.includes('800px'))
    assert.ok(widthFinding, 'expected a Drifted finding for wrong-width.jpg')
    assert.equal(widthFinding.type, 'Drifted')

    const sizeFinding = findings.find((f) => f.file === 'too-big.png' && f.issue.includes('KB'))
    assert.ok(sizeFinding, 'expected a Drifted finding for too-big.png')
    assert.equal(sizeFinding.type, 'Drifted')
  })

  it('uses at-least width mode correctly', async () => {
    const dir = await mkdtemp(path.join(tmpdir(), 'prepare-image-atLeast-'))

    await sharp({ create: { width: 1920, height: 1080, channels: 3, background: '#000000' } })
      .jpeg({ quality: 80 })
      .toFile(path.join(dir, 'cover.jpg'))

    const exactFindings = await checkImages({ imageDir: dir, width: 1920, widthMode: 'exact' })
    const atLeastFindings = await checkImages({ imageDir: dir, width: 1920, widthMode: 'at-least', maxBytes: 10 * 1024 * 1024 })

    assert.ok(
      !exactFindings.find((f) => f.file === 'cover.jpg' && f.issue.includes('width')),
      'exact mode should not flag a 1920px image when target is 1920'
    )
    assert.ok(
      !atLeastFindings.find((f) => f.file === 'cover.jpg' && f.issue.includes('width')),
      'at-least mode should not flag a 1920px image when minimum is 1920'
    )

    const narrowFindings = await checkImages({ imageDir: dir, width: 2000, widthMode: 'at-least', maxBytes: 10 * 1024 * 1024 })
    assert.ok(
      narrowFindings.find((f) => f.file === 'cover.jpg' && f.issue.includes('1920px')),
      'at-least mode should flag a 1920px image when minimum is 2000'
    )
  })
})
