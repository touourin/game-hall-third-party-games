import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  SCENE_SHELL_COLOR_KEYS,
  validateAssetManifest,
} from '../web/asset-manifest.mjs'
import {
  CONTRACT_PACK_ID,
  buildFromRepository,
  buildTowerShellContract,
  contractLayouts,
  readFixture,
} from './tower-shell-contract.mjs'
import {
  cameraTransformPoint,
  floorFrontEdges,
  sceneVisualBounds,
  towerShellGeometry,
  windowBandPolygon,
} from '../web/scene.mjs'

const coreSpecUrl = new URL('../assets/core-pack.spec.json', import.meta.url)
const coreV2SpecUrl = new URL('../assets/core-v2-pack.spec.json', import.meta.url)
const schemaUrl = new URL('../assets/manifest.schema.json', import.meta.url)
const coreSpec = JSON.parse(await readFile(fileURLToPath(coreSpecUrl), 'utf8'))
const coreV2Spec = JSON.parse(await readFile(fileURLToPath(coreV2SpecUrl), 'utf8'))
const schema = JSON.parse(await readFile(fileURLToPath(schemaUrl), 'utf8'))

const SHELL = Object.freeze({
  version: 1,
  type: 'cutaway-office-tower',
  facadeDepth: 512,
  slabDepth: 8,
  windowBandPitch: 12,
  colors: Object.freeze({
    outline: '#0D2228',
    ambientOcclusion: '#0D2228',
    slab: '#566169',
    facadeLight: '#557F9C',
    facadeDark: '#3D6078',
    window: '#729FBE',
    mullion: '#3B454C',
  }),
})

function manifestWithShell() {
  const manifest = structuredClone(coreSpec)
  manifest.geometryVersion = 2
  manifest.palette = structuredClone(coreV2Spec.palette)
  manifest.sceneShell = structuredClone(SHELL)
  return manifest
}

function sceneShellCodes(manifest) {
  return validateAssetManifest(manifest).errors
    .filter((error) => error.code.startsWith('SCENE_SHELL_'))
    .map((error) => error.code)
}

test('sceneShell is optional, exact, versioned and schema-backed', () => {
  assert.deepEqual(validateAssetManifest(coreSpec), { valid: true, errors: [] })
  assert.deepEqual(validateAssetManifest(manifestWithShell()), { valid: true, errors: [] })
  assert.deepEqual(Object.keys(SHELL.colors), SCENE_SHELL_COLOR_KEYS)
  assert.equal(schema.properties.sceneShell.$ref, '#/$defs/sceneShell')
  assert.equal(schema.$defs.sceneShell.additionalProperties, false)
  assert.deepEqual(schema.$defs.sceneShell.required, [
    'version', 'type', 'facadeDepth', 'slabDepth', 'windowBandPitch', 'colors',
  ])
  assert.equal(schema.$defs.sceneShell.properties.colors.additionalProperties, false)

  const wrongGeometry = manifestWithShell()
  wrongGeometry.geometryVersion = 1
  wrongGeometry.palette = structuredClone(coreSpec.palette)
  assert.ok(sceneShellCodes(wrongGeometry).includes('SCENE_SHELL_GEOMETRY_VERSION'))

  const malformed = manifestWithShell()
  malformed.sceneShell.version = 2
  malformed.sceneShell.type = 'floating-island'
  malformed.sceneShell.facadeDepth = 8
  malformed.sceneShell.slabDepth = 8
  malformed.sceneShell.windowBandPitch = 0
  malformed.sceneShell.colors.window = 'blue'
  malformed.sceneShell.colors.extra = '#ffffff'
  malformed.sceneShell.extra = true
  const malformedCodes = sceneShellCodes(malformed)
  for (const code of [
    'SCENE_SHELL_VERSION',
    'SCENE_SHELL_TYPE',
    'SCENE_SHELL_POSITIVE_INTEGER',
    'SCENE_SHELL_DEPTH_ORDER',
    'SCENE_SHELL_COLOR',
    'SCENE_SHELL_COLOR_FIELD',
    'SCENE_SHELL_FIELD',
  ]) assert.ok(malformedCodes.includes(code), code)
})

test('opening and mid maps expose only x=max and y=max with one exact front corner', () => {
  const cases = [
    {
      layout: { columns: 14, rows: 9, origin: { x: 280, y: 136 } },
      right: { x: 504, y: 240 },
      front: { x: 360, y: 312 },
      left: { x: 136, y: 200 },
    },
    {
      layout: { columns: 20, rows: 12, origin: { x: 256, y: 100 } },
      right: { x: 576, y: 252 },
      front: { x: 384, y: 348 },
      left: { x: 64, y: 188 },
    },
  ]

  for (const { layout, right, front, left } of cases) {
    const edges = floorFrontEdges(layout)
    assert.deepEqual(edges.rightCorner, right)
    assert.deepEqual(edges.frontCorner, front)
    assert.deepEqual(edges.leftCorner, left)
    assert.deepEqual(edges.xMax[0], right)
    assert.deepEqual(edges.xMax.at(-1), front)
    assert.deepEqual(edges.yMax[0], front)
    assert.deepEqual(edges.yMax.at(-1), left)
    assert.equal(edges.xMax.length, layout.rows + 1)
    assert.equal(edges.yMax.length, layout.columns + 1)
    assert.equal(edges.xMax.every((point, index) => (
      index === 0
      || (point.x - edges.xMax[index - 1].x === -16
        && point.y - edges.xMax[index - 1].y === 8)
    )), true)
    assert.equal(edges.yMax.every((point, index) => (
      index === 0
      || (point.x - edges.yMax[index - 1].x === -16
        && point.y - edges.yMax[index - 1].y === -8)
    )), true)
  }
})

test('slab, AO, mullions and facade remain gapless through all camera steps and pans', () => {
  const layouts = [
    { columns: 14, rows: 9, origin: { x: 280, y: 136 } },
    { columns: 20, rows: 12, origin: { x: 256, y: 100 } },
  ]
  const cameras = [
    { x: 0, y: 0, zoom: 1 },
    { x: 73, y: -55, zoom: 1.25 },
    { x: -118, y: 96, zoom: 1.5 },
    { x: 160, y: -300, zoom: 2 },
  ]

  for (const layout of layouts) {
    const geometry = towerShellGeometry(layout, SHELL)
    assert.equal(geometry.xMax.mullions.length, layout.rows + 1)
    assert.equal(geometry.yMax.mullions.length, layout.columns + 1)
    assert.deepEqual(geometry.xMax.facade[1], geometry.yMax.facade[0])
    assert.deepEqual(geometry.xMax.facade[2], geometry.yMax.facade[3])
    assert.deepEqual(geometry.xMax.slab[1], geometry.yMax.slab[0])
    assert.deepEqual(geometry.xMax.ambientOcclusion[1], geometry.yMax.ambientOcclusion[0])
    for (const face of [geometry.xMax, geometry.yMax]) {
      const bands = face.windowBands
      assert.equal(bands.every((d, index) => index === 0 || d - bands[index - 1] === 12), true)
      assert.ok(bands[0] >= SHELL.slabDepth, 'first band must clear the roof slab')
      assert.ok(bands.at(-1) + 12 <= SHELL.facadeDepth, 'last band must stay on the facade')
    }

    for (const camera of cameras) {
      const xTop = cameraTransformPoint(geometry.xMax.facade[1], camera)
      const yTop = cameraTransformPoint(geometry.yMax.facade[0], camera)
      const xBottom = cameraTransformPoint(geometry.xMax.facade[2], camera)
      const yBottom = cameraTransformPoint(geometry.yMax.facade[3], camera)
      assert.deepEqual(xTop, yTop)
      assert.deepEqual(xBottom, yBottom)
      assert.ok(xBottom.y > 360, '512px facade must be clipped below the logical canvas')
    }
  }
})

test('window bands follow the eave instead of the screen horizontal', () => {
  const layouts = [
    { columns: 14, rows: 9, origin: { x: 280, y: 136 } },
    { columns: 20, rows: 12, origin: { x: 256, y: 100 } },
  ]
  const height = SHELL.windowBandPitch - 3

  for (const layout of layouts) {
    const geometry = towerShellGeometry(layout, SHELL)
    const depth = geometry.xMax.windowBands[3]

    for (const face of [geometry.xMax, geometry.yMax]) {
      const band = windowBandPolygon(face, depth, height)
      const run = band[1].x - band[0].x
      const rise = band[1].y - band[0].y
      // A curtain wall's floor line is horizontal in world space, so on the 2:1
      // grid it falls one pixel for every two it travels sideways.  Drawing it
      // at rise 0 flattens the whole tower into a billboard.
      assert.notEqual(rise, 0, `${face.id} band must not be screen-horizontal`)
      assert.equal(Math.abs(rise / run), 0.5)
      // Parallel to the eave it hangs from, not merely slanted.
      const eave = face.topEdge.at(-1)
      assert.equal(rise / run, (eave.y - face.topEdge[0].y) / (eave.x - face.topEdge[0].x))
      assert.equal(band[3].y - band[0].y, height)
    }

    // Both faces belong to one tower, so band k meets its twin exactly at the
    // shared front corner — the same contract the facade quads hold.
    const xBand = windowBandPolygon(geometry.xMax, depth, height)
    const yBand = windowBandPolygon(geometry.yMax, depth, height)
    assert.deepEqual(xBand[1], yBand[0])
    assert.deepEqual(xBand[2], yBand[3])
  }
})

test('browser geometry matches the committed parity fixture', async () => {
  // The offline QA renderer asserts against this same file from `tests/`.
  // Neither suite runs the other's runtime, so a one-sided geometry edit fails
  // in the opposite language rather than passing quietly.  Regenerate with
  // `.venv/bin/python -m codex_v0.tower_shell_contract regenerate`, which
  // builds both halves and refuses to write when they disagree.
  const [built, fixture] = await Promise.all([buildFromRepository(), readFixture()])
  assert.deepEqual(built, fixture)

  assert.equal(fixture.packId, CONTRACT_PACK_ID)
  assert.deepEqual(
    fixture.layouts.map((layout) => [layout.layout.columns, layout.layout.rows]).sort(),
    [[14, 9], [20, 12]],
  )
})

test('the parity contract refuses fractional local coordinates', async () => {
  // Local scene space must stay an integer lattice: the browser snaps vertices
  // before the camera transform while Pillow rounds after it, and that is only
  // bounded to half a device pixel while every local coordinate is whole.
  const packSpec = JSON.parse(await readFile(fileURLToPath(coreV2SpecUrl), 'utf8'))
  const layouts = {
    layouts: [{
      id: 'world.fractional-probe',
      requiredPackId: CONTRACT_PACK_ID,
      columns: 14,
      rows: 9,
      origin: { x: 280.5, y: 136 },
    }],
  }
  assert.equal(contractLayouts(layouts).length, 1)
  assert.throws(
    () => buildTowerShellContract(layouts, packSpec),
    /局部场景空间中的整数/,
  )
})

test('scene shell never expands automatic full-scene camera bounds', () => {
  const manifest = manifestWithShell()
  const withoutShell = structuredClone(manifest)
  delete withoutShell.sceneShell
  const layout = {
    columns: 14,
    rows: 9,
    origin: { x: 280, y: 136 },
    floors: [
      { assetId: 'floor.raw-concrete', x: 0, y: 0 },
      { assetId: 'floor.raw-concrete', x: 13, y: 8 },
    ],
    objects: [],
  }
  assert.deepEqual(sceneVisualBounds(manifest, layout, []), sceneVisualBounds(withoutShell, layout, []))
})
