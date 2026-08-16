import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  ATLAS_PADDING,
  CHARACTER_FRAME,
  CORE_FURNITURE_FOOTPRINTS,
  CORE_V1_DESK_SEATS,
  CORE_V1_NEW_REQUIRED_SLOTS,
  CORE_V1_REQUIRED_SLOTS,
  CORE_V2_FOCUS_DESK_SEATS,
  CORE_V2_NEW_REQUIRED_SLOTS,
  CORE_V2_PALETTE_LIMITS,
  CORE_V2_REQUIRED_SLOTS,
  CORE_V0_REQUIRED_SLOTS,
  DEPTH_RULE,
  FIXED_FIXTURE_IDS,
  GUS_ACTION_FRAME_COUNTS,
  GUS_ANCHOR,
  GUS_COLUMN_ORDER,
  GUS_DIRECTIONS,
  GUS_FRAME_IDS,
  GUS_SHEET,
  gusLayoutForSheet,
  PALETTE_LIMITS,
  RENDERABLE_KINDS,
  TILE_METRICS,
  depthForPlacement,
  fixtureCollisionCells,
  layoutAtlasFrames,
  projectFixture,
  resolveAnimationFrame,
  sortFixtureForOcclusion,
  stableManifestStringify,
  validateAssetManifest,
} from '../web/asset-manifest.mjs'
import {
  AssetIntegrityError,
  AssetRuntime,
  assertFrozenLayoutSha256,
  canonicalJsonStringify,
  drawNearestFrame,
  groundAxisAffineMatrix,
  loadActiveAssetPack,
  loadActiveManifest,
  loadPinnedAssetPack,
  sha256Hex,
} from '../web/asset-runtime.mjs'

const manifestUrl = new URL('../assets/core-pack.spec.json', import.meta.url)
const coreV1SpecUrl = new URL('../assets/core-v1-pack.spec.json', import.meta.url)
const coreV2SpecUrl = new URL('../assets/core-v2-pack.spec.json', import.meta.url)
const schemaUrl = new URL('../assets/manifest.schema.json', import.meta.url)
const manifest = JSON.parse(await readFile(fileURLToPath(manifestUrl), 'utf8'))
const coreV1Spec = JSON.parse(await readFile(fileURLToPath(coreV1SpecUrl), 'utf8'))
const coreV2Spec = JSON.parse(await readFile(fileURLToPath(coreV2SpecUrl), 'utf8'))
const schema = JSON.parse(await readFile(fileURLToPath(schemaUrl), 'utf8'))

function clone(value = manifest) {
  return JSON.parse(JSON.stringify(value))
}

function codes(value) {
  return validateAssetManifest(value).errors.map((error) => error.code)
}

function asset(value, id) {
  return value.assets.find((candidate) => candidate.id === id)
}

function coreV2WallContractManifest() {
  const value = clone()
  value.id = 'core-v2'
  value.geometryVersion = 2
  value.palette = clone(coreV2Spec.palette)
  const walls = coreV2Spec.assets.filter((candidate) => candidate.slot.startsWith('structure.wall-'))
  value.assets.push(...walls.map((candidate) => ({
    ...clone(candidate),
    atlas: 'core-v0',
    frame: { ...candidate.frame, x: 2, y: 2 },
  })))
  return value
}

function coreV2WallCodes(value) {
  return codes(value).filter((code) => code.startsWith('CORE_V2_WALL_'))
}

test('core-v0 fixes grid, Gus sheet, palette, eleven slots and four fixtures', () => {
  assert.deepEqual(TILE_METRICS, { width: 32, height: 16, elevation: 8 })
  assert.deepEqual(CHARACTER_FRAME, { width: 24, height: 48 })
  assert.equal(ATLAS_PADDING, 2)
  assert.equal(DEPTH_RULE, 'max-x-plus-y')
  assert.deepEqual(PALETTE_LIMITS, { world: 32, players: 8 })
  assert.equal(manifest.palette.world.length, 32)
  assert.equal(manifest.palette.players.length, 8)
  assert.deepEqual(GUS_ANCHOR, { x: 12, y: 46 })
  assert.deepEqual(GUS_SHEET, { width: 384, height: 192, columns: 16, rows: 4 })
  assert.deepEqual(GUS_DIRECTIONS, ['southeast', 'southwest', 'northwest', 'northeast'])
  assert.deepEqual(GUS_ACTION_FRAME_COUNTS, { idle: 4, walk: 8, work: 4 })
  assert.deepEqual(GUS_COLUMN_ORDER, [
    'idle', 'idle.1', 'idle.2', 'idle.3',
    'walk.0', 'walk.1', 'walk.2', 'walk.3', 'walk.4', 'walk.5', 'walk.6', 'walk.7',
    'work.0', 'work.1', 'work.2', 'work.3',
  ])
  assert.equal(GUS_FRAME_IDS.length, 64)
  assert.equal(manifest.assets.filter(({ kind }) => kind === 'character').length, 64)
  // The older 7-column sheet must still resolve, so packs derived before the
  // walk cycle was rebuilt keep loading instead of failing the contract.
  assert.equal(gusLayoutForSheet({ columns: 7, rows: 4 })?.id, 'v1')
  assert.equal(gusLayoutForSheet({ columns: 16, rows: 4 })?.id, 'v2')
  assert.equal(manifest.requiredSlots.length, 11)
  assert.deepEqual(manifest.fixtures.map(({ id }) => id), FIXED_FIXTURE_IDS)
  assert.deepEqual(manifest.requiredSlots, CORE_V0_REQUIRED_SLOTS)
  assert.equal(stableManifestStringify(manifest).includes('plant'), false)
  assert.deepEqual(validateAssetManifest(manifest), { valid: true, errors: [] })

  assert.equal(schema.properties.grid.properties.tileWidth.const, 32)
  assert.equal(schema.properties.grid.properties.tileHeight.const, 16)
  assert.equal(schema.properties.characterFrame.properties.width.const, 24)
  assert.equal(schema.properties.characterFrame.properties.height.const, 48)
  assert.equal(manifest.characterMotion.policy, 'canonical-idle-v1')
  assert.equal(manifest.characterMotion.identityLocked, true)
  assert.equal(manifest.characterMotion.trustedLegacyAcceptedMotion, undefined)
  assert.equal(manifest.characterMotion.fallback, 'canonical-idle-bob')
  assert.equal(schema.properties.characterMotion.properties.identityLocked.type, 'boolean')
  assert.equal(schema.properties.characterMotion.properties.trustedLegacyAcceptedMotion.type, 'boolean')
  assert.equal(schema.properties.palette.properties.world.minItems, 32)
  assert.equal(schema.properties.palette.properties.players.maxItems, 8)
  assert.equal(schema.properties.requiredSlots.minItems, CORE_V0_REQUIRED_SLOTS.length)
  assert.equal(schema.properties.requiredSlots.uniqueItems, true)
  assert.equal(schema.properties.sheets.minItems, 1)
  assert.equal(schema.properties.fixtures.minItems, 4)
  assert.equal(schema.properties.fixtures.maxItems, 4)
  assert.equal(schema.$defs.placement.additionalProperties, false)
})

test('core-v1 extends the inherited pack with approved scene kinds and fixed desk seats', () => {
  assert.equal(coreV1Spec.geometryVersion, 1)
  assert.equal(coreV1Spec.id, 'core-v1')
  assert.equal(coreV1Spec.basePackId, 'core-v0')
  assert.deepEqual(coreV1Spec.requiredNewSlots, CORE_V1_NEW_REQUIRED_SLOTS)
  assert.deepEqual(CORE_V1_REQUIRED_SLOTS, [...CORE_V0_REQUIRED_SLOTS, ...CORE_V1_NEW_REQUIRED_SLOTS])
  assert.deepEqual(coreV1Spec.baseAssetPatches['furniture.desk-island'].interactionPoints, CORE_V1_DESK_SEATS)
  assert.ok(['backdrop', 'structure', 'decor', 'furniture'].every((kind) => RENDERABLE_KINDS.includes(kind)))
  const schemaKinds = schema.$defs.asset.properties.kind.enum
  assert.ok(RENDERABLE_KINDS.every((kind) => schemaKinds.includes(kind)))
  for (const candidate of coreV1Spec.assets) {
    assert.ok(RENDERABLE_KINDS.includes(candidate.kind))
    assert.ok(candidate.footprint.length >= 1, `${candidate.id} must declare a footprint`)
  }
})

test('core-v2 declares 29 data-driven slots, 48 colors and native focus-desk seats', () => {
  assert.equal(coreV2Spec.id, 'core-v2')
  assert.equal(coreV2Spec.geometryVersion, 2)
  assert.equal(coreV2Spec.nativeFrameRequired, true)
  assert.equal(coreV2Spec.palette.world.length, CORE_V2_PALETTE_LIMITS.world)
  assert.equal(coreV2Spec.palette.players.length, CORE_V2_PALETTE_LIMITS.players)
  assert.equal(CORE_V2_REQUIRED_SLOTS.length, 29)
  assert.deepEqual(CORE_V2_REQUIRED_SLOTS.slice(-7), [...CORE_V2_NEW_REQUIRED_SLOTS])
  assert.equal(coreV2Spec.overrideSlots.length, 9)
  assert.equal(coreV2Spec.requiredNewSlots.length, 7)
  assert.equal(coreV2Spec.requiredEditableSlots.length, 16)
  assert.equal(new Set(coreV2Spec.requiredEditableSlots).size, 16)

  const assets = new Map(coreV2Spec.assets.map((asset) => [asset.slot, asset]))
  assert.deepEqual(assets.get('backdrop.beijing-cbd').frame, { x: 0, y: 0, width: 640, height: 360 })
  assert.deepEqual(assets.get('backdrop.beijing-cbd').anchor, { x: 320, y: 356 })
  assert.deepEqual(coreV2Spec.sceneShell, {
    version: 1,
    type: 'cutaway-office-tower',
    facadeDepth: 512,
    slabDepth: 8,
    windowBandPitch: 12,
    colors: {
      outline: '#0D2228',
      ambientOcclusion: '#0D2228',
      slab: '#566169',
      facadeLight: '#557F9C',
      facadeDark: '#3D6078',
      window: '#729FBE',
      mullion: '#3B454C',
    },
  })
  for (const [slot, expected] of Object.entries(CORE_V2_FOCUS_DESK_SEATS)) {
    const candidate = assets.get(slot)
    assert.ok(candidate, `${slot} missing`)
    assert.deepEqual(candidate.interactionPoints, expected)
    assert.deepEqual(
      candidate.collision.map(({ x, y }) => `${x},${y}`).sort(),
      ['0,0', '0,1', '1,0', '1,1'],
    )
    assert.equal(candidate.footprint.every((cell) => cell.blocked === true), true)
  }
})

test('core-v2 wall ground axes are native, anchored and directionally exact', () => {
  const value = coreV2WallContractManifest()
  assert.deepEqual(coreV2WallCodes(value), [])

  const templates = new Map(coreV2Spec.assets.map((candidate) => [candidate.slot, candidate]))
  for (const [slot, expectedDelta] of [
    ['structure.wall-solid-nw', [48, 24]],
    ['structure.wall-solid-ne', [48, 24]],
    ['structure.wall-window-nw', [64, 32]],
    ['structure.wall-window-ne', [64, 32]],
    ['structure.wall-door-ne', [48, 24]],
  ]) {
    const candidate = templates.get(slot)
    const { start, end } = candidate.groundAxis
    assert.deepEqual(
      { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 },
      candidate.anchor,
    )
    assert.deepEqual([Math.abs(end.x - start.x), Math.abs(end.y - start.y)], expectedDelta)
    assert.equal(Math.sign((end.x - start.x) * (end.y - start.y)), candidate.orientation === 'nw' ? 1 : -1)
    assert.equal(candidate.wallFaceHeight, 56)
    for (const point of [start, end]) {
      const topPoint = { x: point.x, y: point.y - candidate.wallFaceHeight }
      assert.ok(topPoint.x >= 0 && topPoint.x < candidate.frame.width)
      assert.ok(topPoint.y >= 0 && topPoint.y < candidate.frame.height)
    }
  }
})

test('core-v2 rejects missing, malformed and geometrically false wall geometry only for v2', () => {
  const mutateAndCodes = (mutate) => {
    const value = coreV2WallContractManifest()
    const wall = value.assets.find((candidate) => candidate.slot === 'structure.wall-solid-nw')
    mutate(wall)
    return coreV2WallCodes(value)
  }

  assert.ok(mutateAndCodes((wall) => { delete wall.groundAxis }).includes('CORE_V2_WALL_GROUND_AXIS_REQUIRED'))
  assert.ok(mutateAndCodes((wall) => { delete wall.wallFaceHeight }).includes('CORE_V2_WALL_FACE_HEIGHT_REQUIRED'))
  assert.ok(mutateAndCodes((wall) => { wall.wallFaceHeight = 55 }).includes('CORE_V2_WALL_FACE_HEIGHT_VALUE'))
  assert.ok(mutateAndCodes((wall) => { wall.wallFaceHeight = 56.5 }).includes('CORE_V2_WALL_FACE_HEIGHT_VALUE'))
  assert.ok(mutateAndCodes((wall) => { wall.groundAxis.start.x = 24.5 }).includes('CORE_V2_WALL_GROUND_AXIS_POINT'))
  assert.ok(mutateAndCodes((wall) => { wall.groundAxis.end.y = wall.frame.height }).includes('CORE_V2_WALL_GROUND_AXIS_BOUNDS'))
  assert.ok(mutateAndCodes((wall) => { wall.anchor.x += 1 }).includes('CORE_V2_WALL_GROUND_AXIS_MIDPOINT'))
  assert.ok(mutateAndCodes((wall) => {
    wall.groundAxis.start.x += 1
    wall.groundAxis.end.x -= 1
  }).includes('CORE_V2_WALL_GROUND_AXIS_DELTA'))
  assert.ok(mutateAndCodes((wall) => {
    const y = wall.groundAxis.start.y
    wall.groundAxis.start.y = wall.groundAxis.end.y
    wall.groundAxis.end.y = y
  }).includes('CORE_V2_WALL_GROUND_AXIS_SLOPE'))
  assert.ok(mutateAndCodes((wall) => { wall.orientation = 'ne' }).includes('CORE_V2_WALL_ORIENTATION'))
  assert.ok(mutateAndCodes((wall) => {
    wall.groundAxis.start.y -= 8
    wall.groundAxis.end.y -= 8
    wall.anchor.y -= 8
  }).includes('CORE_V2_WALL_TOP_AXIS_BOUNDS'))

  const legacy = coreV2WallContractManifest()
  legacy.id = 'core-v1'
  const legacyWall = legacy.assets.find((candidate) => candidate.slot === 'structure.wall-solid-nw')
  delete legacyWall.groundAxis
  delete legacyWall.orientation
  delete legacyWall.wallFaceHeight
  assert.deepEqual(coreV2WallCodes(legacy), [])
})

test('manifest required slots are self-describing instead of limited to named core generations', () => {
  const custom = clone()
  custom.id = 'office-experiment-v7'
  custom.requiredSlots = custom.requiredSlots.slice(0, 3)
  assert.deepEqual(validateAssetManifest(custom), { valid: true, errors: [] })
  custom.requiredSlots.push('furniture.missing-experiment')
  assert.ok(codes(custom).includes('REQUIRED_SLOT_MISSING'))
})

test('IDs and slots are unique across every addressable collection', () => {
  const duplicateAsset = clone()
  duplicateAsset.assets[1].id = duplicateAsset.assets[0].id
  assert.ok(codes(duplicateAsset).includes('DUPLICATE_ASSET_ID'))

  const duplicateRenderable = clone()
  duplicateRenderable.animations[0].id = duplicateRenderable.assets[0].id
  assert.ok(codes(duplicateRenderable).includes('DUPLICATE_RENDERABLE_ID'))

  const duplicateSlot = clone()
  duplicateSlot.animations[1].slot = duplicateSlot.animations[0].slot
  assert.ok(codes(duplicateSlot).includes('DUPLICATE_SLOT'))

  const duplicatePlacement = clone()
  duplicatePlacement.fixtures[0].placements[1].id = duplicatePlacement.fixtures[0].placements[0].id
  assert.ok(codes(duplicatePlacement).includes('DUPLICATE_PLACEMENT_ID'))
})

test('anchors, offsets, footprints and placements only accept integer coordinates', () => {
  const invalid = clone()
  invalid.assets[0].anchor.x = 15.5
  invalid.assets[1].offset.y = 0.25
  invalid.assets[2].footprint[0].x = 0.5
  invalid.fixtures[0].placements[0].y = 3.5
  const result = new Set(codes(invalid))
  assert.ok(result.has('ANCHOR_INTEGER'))
  assert.ok(result.has('OFFSET_INTEGER'))
  assert.ok(result.has('FOOTPRINT_INTEGER'))
  assert.ok(result.has('PLACEMENT_INTEGER'))
})

test('atlas validation rejects boundary, overlap and padding violations', () => {
  const outside = clone()
  outside.assets[0].frame.x = 0
  assert.ok(codes(outside).includes('FRAME_OUT_OF_ATLAS'))

  const overlap = clone()
  overlap.assets[1].frame = { ...overlap.assets[0].frame }
  assert.ok(codes(overlap).includes('ATLAS_FRAME_PADDING'))

  const narrowPadding = clone()
  narrowPadding.atlases[0].padding = 1
  assert.ok(codes(narrowPadding).includes('ATLAS_PADDING'))
})

test('anchors stay inside frames and character frames stay 24 x 48', () => {
  const outside = clone()
  outside.assets[0].anchor.x = outside.assets[0].frame.width
  assert.ok(codes(outside).includes('ANCHOR_OUT_OF_FRAME'))

  const wrongCharacter = clone()
  asset(wrongCharacter, 'character.gus.southeast.idle').frame.width = 25
  assert.ok(codes(wrongCharacter).includes('CHARACTER_FRAME_SIZE'))

  assert.deepEqual(asset(manifest, 'furniture.desk-island').anchor, { x: 48, y: 64 })
  for (const frame of manifest.assets.filter(({ kind }) => kind === 'character')) {
    assert.deepEqual(frame.anchor, { x: 12, y: 46 })
  }
})

test('every animation frame shares one foot anchor and offset', () => {
  const driftingAnchor = clone()
  asset(driftingAnchor, 'character.gus.southeast.walk.1').anchor.x += 1
  assert.ok(codes(driftingAnchor).includes('ANIMATION_FOOT_DRIFT'))

  const driftingOffset = clone()
  asset(driftingOffset, 'effect.good-card-heart.2').offset.y = -1
  assert.ok(codes(driftingOffset).includes('ANIMATION_FOOT_DRIFT'))
})

test('palette is exactly 32 world colors plus 8 disjoint player accents', () => {
  const shortWorld = clone()
  shortWorld.palette.world.pop()
  assert.ok(codes(shortWorld).includes('PALETTE_SIZE'))

  const duplicatePlayer = clone()
  duplicatePlayer.palette.players[7] = duplicatePlayer.palette.players[0]
  assert.ok(codes(duplicatePlayer).includes('PALETTE_DUPLICATE'))

  const crossDuplicate = clone()
  crossDuplicate.palette.players[0] = crossDuplicate.palette.world[0]
  assert.ok(codes(crossDuplicate).includes('PALETTE_CROSS_DUPLICATE'))
})

test('collision is exactly the blocked subset of each footprint', () => {
  const invalid = clone()
  asset(invalid, 'furniture.desk-island').collision.pop()
  assert.ok(codes(invalid).includes('FOOTPRINT_COLLISION_MISMATCH'))

  for (const [slot, expected] of Object.entries(CORE_FURNITURE_FOOTPRINTS)) {
    assert.equal(asset(manifest, slot).footprint.length, expected.length)
  }

  const midCells = fixtureCollisionCells(manifest, 'mid-growth')
  assert.equal(midCells.length, 18)
  assert.deepEqual(midCells[0], { x: 3, y: 2 })
  assert.ok(midCells.some(({ x, y }) => x === 11 && y === 5))
})

test('depth is max x+y and fixture occlusion is deterministic', () => {
  const desk = asset(manifest, 'furniture.desk-island')
  assert.equal(depthForPlacement(desk, { x: 3, y: 2 }), 8)

  const projected = projectFixture(manifest, 'occlusion-stress')
  const reversed = sortFixtureForOcclusion([...projected].reverse())
  assert.deepEqual(
    reversed.map(({ id }) => id),
    [
      'occlusion-desk',
      'occlusion-behind',
      'occlusion-storage',
      'occlusion-front',
      'occlusion-tea-bar',
    ],
  )
  const behind = reversed.find(({ id }) => id === 'occlusion-behind')
  const front = reversed.find(({ id }) => id === 'occlusion-front')
  assert.equal(front.ground.x, behind.ground.x - 48)
  assert.equal(front.ground.y, behind.ground.y + 24)

  const manualDepth = clone()
  manualDepth.fixtures[0].placements[0].depth = 999
  assert.ok(codes(manualDepth).includes('MANUAL_DEPTH_FORBIDDEN'))
})

test('animation resolution loops walking and clamps one-shot effects', () => {
  const walk = (ms) => resolveAnimationFrame(manifest, 'animation.gus.southeast.walk', ms).assetId
  assert.equal(walk(0), 'character.gus.southeast.walk.0')
  assert.equal(walk(51), 'character.gus.southeast.walk.1')
  assert.equal(walk(51 * 7), 'character.gus.southeast.walk.7')
  assert.equal(walk(51 * 8), 'character.gus.southeast.walk.0')
  // Idle declares per-frame durations, so the breath holds on its end poses.
  const idle = (ms) => resolveAnimationFrame(manifest, 'animation.gus.southeast.idle', ms).assetId
  assert.equal(idle(0), 'character.gus.southeast.idle')
  assert.equal(idle(699), 'character.gus.southeast.idle')
  assert.equal(idle(700), 'character.gus.southeast.idle.1')
  assert.equal(idle(1100), 'character.gus.southeast.idle.2')
  assert.equal(idle(1800), 'character.gus.southeast.idle.3')
  assert.equal(idle(2200), 'character.gus.southeast.idle')
  assert.equal(resolveAnimationFrame(manifest, 'animation.good-card-heart', 99_999).assetId, 'effect.good-card-heart.3')
})

test('legacy or unverified character releases use one canonical frame with deterministic bob', () => {
  const legacy = clone()
  delete legacy.characterMotion
  assert.deepEqual(validateAssetManifest(legacy), { valid: true, errors: [] })

  const first = resolveAnimationFrame(legacy, 'animation.gus.southeast.walk', 0)
  const second = resolveAnimationFrame(legacy, 'animation.gus.southeast.walk', 51)
  const work = resolveAnimationFrame(legacy, 'animation.gus.southwest.work', 110)
  assert.equal(first.assetId, 'character.gus.southeast.idle')
  assert.equal(second.assetId, 'character.gus.southeast.idle')
  assert.equal(first.motionFallback, true)
  assert.deepEqual(first.proceduralOffset, { x: 0, y: 0 })
  assert.deepEqual(second.proceduralOffset, { x: 0, y: -1 })
  assert.equal(work.assetId, 'character.gus.southwest.idle')
  assert.deepEqual(work.proceduralOffset, { x: 0, y: 0 })

  const runtime = new AssetRuntime(legacy, new Map([['core-v0', { width: 512, height: 512 }]]))
  const context = {
    imageSmoothingEnabled: true,
    globalAlpha: 1,
    drawImage() {},
  }
  const drawn = runtime.drawAnimation(context, 'animation.gus.southeast.walk', 51, 100, 80)
  assert.equal(drawn.assetId, 'character.gus.southeast.idle')
  assert.deepEqual(drawn.destination, { x: 88, y: 33, width: 24, height: 48 })

  const explicitUnverified = clone()
  explicitUnverified.characterMotion.identityLocked = false
  assert.equal(
    resolveAnimationFrame(explicitUnverified, 'animation.gus.northeast.walk', 240).assetId,
    'character.gus.northeast.idle',
  )
})

test('only frozen derived core packs with accepted legacy motion bypass idle fallback', () => {
  const trusted = clone()
  trusted.id = 'core-v1'
  trusted.baseReleaseId = 'release-frozen-core-v0'
  trusted.characterMotion.identityLocked = false
  trusted.characterMotion.trustedLegacyAcceptedMotion = true

  const walk = resolveAnimationFrame(trusted, 'animation.gus.southeast.walk', 51)
  const work = resolveAnimationFrame(trusted, 'animation.gus.northwest.work', 110)
  assert.equal(walk.assetId, 'character.gus.southeast.walk.1')
  assert.equal(work.assetId, 'character.gus.northwest.work.1')
  assert.equal(walk.motionFallback, false)
  assert.equal(work.motionFallback, false)

  const coreV2 = clone(trusted)
  coreV2.id = 'core-v2'
  coreV2.baseReleaseId = 'release-frozen-core-v1'
  assert.equal(
    resolveAnimationFrame(coreV2, 'animation.gus.southeast.walk', 51).assetId,
    'character.gus.southeast.walk.1',
  )

  for (const mutate of [
    (value) => { value.id = 'core-v0' },
    (value) => { delete value.baseReleaseId },
    (value) => { value.characterMotion.trustedLegacyAcceptedMotion = false },
  ]) {
    const untrusted = clone(trusted)
    mutate(untrusted)
    assert.equal(
      resolveAnimationFrame(untrusted, 'animation.gus.southwest.walk', 120).assetId,
      'character.gus.southwest.idle',
    )
  }

  const invalidCoreV0 = clone()
  invalidCoreV0.characterMotion.identityLocked = false
  invalidCoreV0.characterMotion.trustedLegacyAcceptedMotion = true
  assert.ok(codes(invalidCoreV0).includes('GUS_LEGACY_MOTION_TRUST'))

  const invalidType = clone()
  invalidType.characterMotion.trustedLegacyAcceptedMotion = 'yes'
  assert.ok(codes(invalidType).includes('GUS_LEGACY_MOTION_TRUST'))
})

test('atlas shelf layout and canonical serialization ignore input/key order', () => {
  const entries = [
    { id: 'z', width: 16, height: 16 },
    { id: 'a', width: 24, height: 48 },
    { id: 'm', width: 32, height: 16 },
  ]
  const forward = layoutAtlasFrames(entries, { width: 128, height: 128, padding: 2 })
  const reverse = layoutAtlasFrames([...entries].reverse(), { width: 128, height: 128, padding: 2 })
  assert.deepEqual(forward, reverse)
  assert.deepEqual(forward.map(({ id }) => id), ['a', 'm', 'z'])
  assert.equal(forward[0].x, 2)
  assert.ok(forward[1].x - (forward[0].x + forward[0].width) >= 2)
  assert.equal(
    stableManifestStringify({ z: 1, a: { y: 2, x: 1 } }, 0),
    stableManifestStringify({ a: { x: 1, y: 2 }, z: 1 }, 0),
  )
})

test('active manifest and atlas blobs load without a server', async () => {
  const manifestBlob = new Blob([JSON.stringify(manifest)], { type: 'application/json' })
  const loaded = await loadActiveManifest(manifestBlob, { baseUrl: 'https://assets.test/packs/core/' })
  assert.equal(loaded.manifest.id, 'core-v0')
  assert.equal(loaded.baseUrl.href, 'https://assets.test/packs/core/')

  let decodeCalls = 0
  const image = { width: 512, height: 512 }
  const runtime = await loadActiveAssetPack({
    manifestSource: manifestBlob,
    manifestUrl: 'https://assets.test/packs/core/manifest.json',
    atlasBlobs: new Map([['core-v0', new Blob(['not-a-real-png'])]]),
    fetchImpl: async () => { throw new Error('supplied blobs must avoid fetch') },
    decodeImage: async () => {
      decodeCalls += 1
      return image
    },
  })
  assert.ok(runtime instanceof AssetRuntime)
  assert.equal(decodeCalls, 1)
  assert.equal(runtime.asset('furniture.storage-cabinet').anchor.y, 64)
})

test('runtime drawing forces nearest-neighbor sampling and restores context state', () => {
  const image = { width: 512, height: 512 }
  const observations = []
  const ctx = {
    imageSmoothingEnabled: true,
    globalAlpha: 0.75,
    drawImage(...args) {
      observations.push({ args, smoothing: this.imageSmoothingEnabled, alpha: this.globalAlpha })
    },
  }
  drawNearestFrame(
    ctx,
    image,
    { x: 2, y: 2, width: 32, height: 16 },
    { x: 10.4, y: 20.6, width: 32, height: 16 },
    { alpha: 0.5 },
  )
  assert.equal(observations.length, 1)
  assert.equal(observations[0].smoothing, false)
  assert.equal(observations[0].alpha, 0.5)
  assert.deepEqual(observations[0].args.slice(-4), [10, 21, 32, 16])
  assert.equal(ctx.imageSmoothingEnabled, true)
  assert.equal(ctx.globalAlpha, 0.75)

  const runtime = new AssetRuntime(manifest, new Map([['core-v0', image]]))
  const result = runtime.drawAnimation(ctx, 'animation.gus.southeast.walk', 51, 100, 80)
  assert.equal(result.assetId, 'character.gus.southeast.walk.1')
  assert.deepEqual(result.destination, { x: 88, y: 34, width: 24, height: 48 })
})

test('runtime horizontal flip is centered on the authored ground anchor', () => {
  const transforms = []
  const ctx = {
    imageSmoothingEnabled: true,
    globalAlpha: 1,
    save() { transforms.push(['save']) },
    restore() { transforms.push(['restore']) },
    translate(x, y) { transforms.push(['translate', x, y]) },
    scale(x, y) { transforms.push(['scale', x, y]) },
    transform(...values) { transforms.push(['transform', ...values]) },
    drawImage(...args) { transforms.push(['drawImage', ...args]) },
  }
  drawNearestFrame(
    ctx,
    { width: 512, height: 512 },
    { x: 4, y: 6, width: 96, height: 88 },
    { x: 52, y: 20, width: 96, height: 88 },
    { flipX: true, flipOriginX: 100 },
  )
  assert.deepEqual(transforms.slice(0, 3), [
    ['save'],
    ['translate', 100, 108],
    ['scale', -1, 1],
  ])
  assert.deepEqual(transforms[3], ['translate', -100, -108])
  assert.equal(transforms.at(-1)[0], 'restore')
  assert.equal(ctx.imageSmoothingEnabled, true)
  assert.equal(ctx.globalAlpha, 1)
})

test('legacy wall affine fit closes module endpoints without scaling vertical height', () => {
  const transformVector = ({ x, y }, fit) => {
    const matrix = groundAxisAffineMatrix(fit)
    return {
      x: matrix.a * x + matrix.c * y,
      y: matrix.b * x + matrix.d * y,
    }
  }
  const cases = [
    [{ x: 94, y: 47 }, { k: 64 / 94, s: 0.5 }, { x: 64, y: 32 }],
    [{ x: 92, y: -46 }, { k: 64 / 92, s: -0.5 }, { x: 64, y: -32 }],
    [{ x: 85, y: 42.5 }, { k: 48 / 85, s: 0.5 }, { x: 48, y: 24 }],
    [{ x: 60, y: -30 }, { k: 48 / 60, s: -0.5 }, { x: 48, y: -24 }],
    [{ x: 85, y: -42.5 }, { k: 48 / 85, s: -0.5 }, { x: 48, y: -24 }],
  ]
  for (const [source, fit, expected] of cases) {
    assert.deepEqual(transformVector(source, fit), expected)
    assert.deepEqual(transformVector({ x: 0, y: -80 }, fit), { x: 0, y: -80 })
  }
})

test('ground-axis fit and door flip share one balanced Canvas state boundary', () => {
  const calls = []
  const ctx = {
    imageSmoothingEnabled: true,
    globalAlpha: 0.75,
    save() { calls.push(['save']) },
    restore() { calls.push(['restore']) },
    translate(x, y) { calls.push(['translate', x, y]) },
    scale(x, y) { calls.push(['scale', x, y]) },
    transform(...values) { calls.push(['transform', ...values]) },
    drawImage() { calls.push(['drawImage']) },
  }
  drawNearestFrame(
    ctx,
    { width: 512, height: 512 },
    { x: 0, y: 0, width: 96, height: 88 },
    { x: 52, y: 20, width: 96, height: 88 },
    {
      flipX: true,
      transformOrigin: { x: 100, y: 100 },
      groundTransform: { k: 48 / 85, s: -0.5 },
    },
  )
  const matrix = groundAxisAffineMatrix({ k: 48 / 85, s: -0.5 })
  assert.deepEqual(calls, [
    ['save'],
    ['translate', 100, 100],
    ['transform', matrix.a, matrix.b, 0, 1, 0, 0],
    ['scale', -1, 1],
    ['translate', -100, -100],
    ['drawImage'],
    ['restore'],
  ])
  assert.equal(ctx.imageSmoothingEnabled, true)
  assert.equal(ctx.globalAlpha, 0.75)
})

test('pinned runtime verifies canonical manifest and atlas bytes before decoding', async () => {
  const pinnedManifest = clone()
  const atlasBytes = new TextEncoder().encode('deterministic fake atlas bytes')
  const atlasSha256 = await sha256Hex(atlasBytes, webcrypto)
  pinnedManifest.atlases[0].source = `/api/assets/derived/${atlasSha256}.png`
  const manifestJson = stableManifestStringify(pinnedManifest, 0)
  const manifestSha256 = await sha256Hex(manifestJson, webcrypto)
  const manifestUrl = `https://game.test/api/assets/manifests/${manifestSha256}`
  const atlasUrl = `https://game.test/api/assets/derived/${atlasSha256}.png`
  const calls = []
  let decodeCalls = 0
  const fetchImpl = async (url) => {
    calls.push(String(url))
    if (String(url) === manifestUrl) return new Response(manifestJson, { status: 200 })
    if (String(url) === atlasUrl) return new Response(atlasBytes, { status: 200 })
    return new Response('not found', { status: 404 })
  }
  const runtime = await loadPinnedAssetPack({
    packId: 'core-v0',
    manifestUrl: `/api/assets/manifests/${manifestSha256}`,
    manifestSha256,
    atlasSha256,
    atlasUrl: `/api/assets/derived/${atlasSha256}.png`,
  }, {
    baseUrl: 'https://game.test/',
    allowedOrigin: 'https://game.test',
    fetchImpl,
    cryptoImpl: webcrypto,
    decodeImage: async () => {
      decodeCalls += 1
      return { width: 512, height: 512 }
    },
  })
  assert.ok(runtime instanceof AssetRuntime)
  assert.deepEqual(calls, [manifestUrl, atlasUrl])
  assert.equal(decodeCalls, 1)
  assert.equal(runtime.animationDuration('animation.good-card-heart'), 400)
  assert.equal(runtime.placementDepth('furniture.desk-island', { x: 3, y: 2 }), 8)
  runtime.dispose()

  await assert.rejects(
    loadPinnedAssetPack({
      packId: 'core-v0',
      manifestUrl,
      manifestSha256: '0'.repeat(64),
      atlasSha256,
      atlasUrl,
    }, {
      baseUrl: 'https://game.test/',
      allowedOrigin: 'https://game.test',
      fetchImpl,
      cryptoImpl: webcrypto,
      decodeImage: async () => { throw new Error('hash mismatch must fail before decode') },
    }),
    (error) => error instanceof AssetIntegrityError && error.code === 'asset_hash_mismatch',
  )
})

test('pinned runtime rejects a cross-origin manifest before fetching', async () => {
  let fetched = false
  await assert.rejects(
    loadPinnedAssetPack({
      manifestUrl: 'https://evil.test/manifest.json',
      manifestSha256: '1'.repeat(64),
      atlasSha256: '2'.repeat(64),
    }, {
      baseUrl: 'https://game.test/',
      allowedOrigin: 'https://game.test',
      fetchImpl: async () => {
        fetched = true
        throw new Error('must not fetch')
      },
      cryptoImpl: webcrypto,
    }),
    (error) => error instanceof AssetIntegrityError && error.code === 'asset_origin_mismatch',
  )
  assert.equal(fetched, false)
})

test('frozen layout hash uses Python-compatible canonical key ordering', async () => {
  const layout = {
    rows: 9,
    label: '光秃开局办公室',
    camera: { zoom: 1.25, y: 0, x: 0 },
    columns: 14,
  }
  assert.equal(
    canonicalJsonStringify(layout),
    '{"camera":{"x":0,"y":0,"zoom":1.25},"columns":14,"label":"光秃开局办公室","rows":9}',
  )
  const expected = await sha256Hex(canonicalJsonStringify(layout), webcrypto)
  assert.equal(
    await assertFrozenLayoutSha256({ ...layout, sha256: expected }, expected, webcrypto),
    expected,
  )
  await assert.rejects(
    () => assertFrozenLayoutSha256({ ...layout, rows: 10, sha256: expected }, expected, webcrypto),
    (error) => error instanceof AssetIntegrityError && error.code === 'asset_layout_hash_mismatch',
  )
})
