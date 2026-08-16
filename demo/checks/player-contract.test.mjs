import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  AssetBindingError,
  GameNetwork,
  isTrustedReviewCommand,
  normaliseBootstrapAssetBinding,
  parseLaunchContext,
  rememberToken,
} from '../web/network.mjs'
import { fixtureCollisionCells, groundPointForPlacement } from '../web/asset-manifest.mjs'
import {
  ACTOR_COUNT,
  BACKDROP_GROUND,
  GRID_COLUMNS,
  GRID_ROWS,
  ZOOM_STEPS,
  advanceMotionPoint,
  actorDepthForOcclusion,
  actorAnimationId,
  backdropGroundForManifest,
  backdropScreenGround,
  cameraForAssetLayout,
  cameraForVisualBounds,
  directionForMotion,
  layoutActorLabels,
  normaliseActorRoster,
  projectIsometric,
  reduceMoveFeedback,
  reduceSelfMovementMarkers,
  resolveAssetWorldLayout,
  renderSpecForAsset,
  resolveWorldSnapshotDimensions,
  sceneVisualBounds,
  sortByIsometricDepth,
  unprojectIsometric,
} from '../web/scene.mjs'
import { moneyFromCents } from '../web/ui-format.mjs'

const playerHtmlUrl = new URL('../web/index.html', import.meta.url)
const playerStylesUrl = new URL('../web/styles.css', import.meta.url)
const pixelSourceUrl = new URL('../web/pixel.mjs', import.meta.url)
const networkSourceUrl = new URL('../web/network.mjs', import.meta.url)
const clientSourceUrl = new URL('../web/client.mjs', import.meta.url)
const assetManifestUrl = new URL('../assets/core-pack.spec.json', import.meta.url)
const coreV1SpecUrl = new URL('../assets/core-v1-pack.spec.json', import.meta.url)
const coreV2SpecUrl = new URL('../assets/core-v2-pack.spec.json', import.meta.url)
const worldLayoutsUrl = new URL('../assets/world-layouts.json', import.meta.url)
const assetManifest = JSON.parse(await readFile(fileURLToPath(assetManifestUrl), 'utf8'))
const coreV1Spec = JSON.parse(await readFile(fileURLToPath(coreV1SpecUrl), 'utf8'))
const coreV2Spec = JSON.parse(await readFile(fileURLToPath(coreV2SpecUrl), 'utf8'))
const worldLayouts = JSON.parse(await readFile(fileURLToPath(worldLayoutsUrl), 'utf8'))

function coreV1RuntimeManifest() {
  const manifest = structuredClone(assetManifest)
  manifest.id = 'core-v1'
  const patches = coreV1Spec.baseAssetPatches
  for (const asset of manifest.assets) Object.assign(asset, structuredClone(patches[asset.id] ?? {}))
  manifest.assets.push(...structuredClone(coreV1Spec.assets))
  return manifest
}

function coreV2RuntimeManifest() {
  const manifest = coreV1RuntimeManifest()
  manifest.id = 'core-v2'
  manifest.geometryVersion = 2
  manifest.sceneShell = structuredClone(coreV2Spec.sceneShell)
  manifest.palette = structuredClone(coreV2Spec.palette)
  const overrides = new Set(coreV2Spec.assets.map((asset) => asset.id))
  manifest.assets = manifest.assets.filter((asset) => !overrides.has(asset.id))
  manifest.assets.push(...structuredClone(coreV2Spec.assets))
  return manifest
}

function frozenLayoutForRuntime(rawLayout, manifest) {
  const assets = new Map(manifest.assets.map((asset) => [asset.id, asset]))
  const blockedCells = rawLayout.placements.flatMap((placement) => (
    assets.get(placement.assetId).collision.map((cell) => ({
      x: placement.x + cell.x,
      y: placement.y + cell.y,
    }))
  ))
  return { ...structuredClone(rawLayout), sha256: 'd'.repeat(64), blockedCells }
}

test('eight-player roster is stable for every self identity', () => {
  const names = ['Ava', 'Ben', 'Cleo', 'Drew', 'Eli', 'Faye', 'Gus', 'Hana']
  const players = names.map((name, index) => ({
    id: `player-${index + 1}`,
    name,
    color: `#${String(index + 1).repeat(6)}`,
    x: index < 4 ? index : index + 7,
    y: index < 4 ? 1 : 10,
  }))

  for (const self of players) {
    const roster = normaliseActorRoster({
      player: self,
      players,
      columns: GRID_COLUMNS,
      rows: GRID_ROWS,
    })
    assert.equal(roster.length, ACTOR_COUNT)
    assert.deepEqual(roster.map(({ id }) => id), players.map(({ id }) => id))
    assert.deepEqual(roster.map(({ name }) => name), names)
  }

  assert.deepEqual(
    normaliseActorRoster({ players: [{ id: 'player-7' }, { id: 'hana' }] })
      .slice(0, 2)
      .map(({ name }) => name),
    ['Gus', 'Hana'],
  )
})

test('launch credentials come from the fragment and remain memory-only', async () => {
  rememberToken('')
  assert.equal(parseLaunchContext({ href: 'https://game.test/?run=r1&token=query-leak' }).token, '')
  assert.deepEqual(
    parseLaunchContext({ href: 'https://game.test/#reviewRun=r2&playerToken=secret&review=1' }),
    { run: 'r2', token: 'secret', review: true },
  )
  const source = await readFile(fileURLToPath(networkSourceUrl), 'utf8')
  assert.equal(source.includes('sessionStorage'), false)
  assert.equal(source.includes('localStorage'), false)
  assert.equal(source.includes('searchParams.get("token")'), false)
  assert.ok(source.includes('replaceState'))
})

test('review commands require the exact parent, origin and non-empty run id', () => {
  const parentWindow = {}
  const accepted = {
    source: parentWindow,
    origin: 'https://game.test',
    data: { channel: 'codex-review', type: 'camera', runId: 'run-1' },
  }
  const options = { parentWindow, parentOrigin: 'https://game.test', runId: 'run-1' }
  assert.equal(isTrustedReviewCommand(accepted, options), true)
  assert.equal(isTrustedReviewCommand({ ...accepted, source: {} }, options), false)
  assert.equal(isTrustedReviewCommand({ ...accepted, origin: 'https://evil.test' }, options), false)
  assert.equal(isTrustedReviewCommand({ ...accepted, data: { ...accepted.data, runId: '' } }, options), false)
  assert.equal(isTrustedReviewCommand({ ...accepted, data: { ...accepted.data, runId: 'run-2' } }, options), false)
  assert.equal(isTrustedReviewCommand(accepted, { ...options, parentOrigin: 'null' }), false)
})

test('websocket authenticates first and queues the formal move payload', async (t) => {
  const originalWebSocket = globalThis.WebSocket
  const originalCustomEvent = globalThis.CustomEvent
  if (!globalThis.CustomEvent) {
    globalThis.CustomEvent = class CustomEvent extends Event {
      constructor(type, init = {}) {
        super(type)
        this.detail = init.detail
      }
    }
  }

  class MockSocket {
    static OPEN = 1
    static CONNECTING = 0

    constructor(url) {
      this.url = url
      this.readyState = 0
      this.listeners = new Map()
      this.sent = []
      MockSocket.last = this
    }

    addEventListener(type, handler) {
      const handlers = this.listeners.get(type) || []
      handlers.push(handler)
      this.listeners.set(type, handlers)
    }

    send(value) {
      this.sent.push(JSON.parse(value))
    }

    fire(type, payload = {}) {
      if (type === 'open') this.readyState = MockSocket.OPEN
      for (const handler of this.listeners.get(type) || []) handler(payload)
    }

    close() {
      this.readyState = 3
    }
  }

  globalThis.WebSocket = MockSocket
  t.after(() => {
    globalThis.WebSocket = originalWebSocket
    globalThis.CustomEvent = originalCustomEvent
  })

  const network = new GameNetwork({ run: 'run 1', token: 'secret', baseUrl: 'https://game.test' })
  network.connect()
  const socket = MockSocket.last
  socket.fire('open')
  assert.deepEqual(socket.sent, [{ type: 'auth', token: 'secret' }])

  network.sendMove({ x: 3, y: 4 })
  assert.equal(socket.sent.length, 1, 'move must wait for auth.ok')
  socket.fire('message', { data: JSON.stringify({ type: 'auth.ok', lastClientSeq: 40 }) })
  assert.deepEqual(socket.sent[1], {
    type: 'move.target',
    tileX: 3,
    tileY: 4,
    clientSeq: 41,
  })
  network.sendWorkStart({ placementId: 'growth-desk', seatId: 'seat-ne' })
  network.sendWorkStop()
  assert.deepEqual(socket.sent[2], {
    type: 'work.start',
    placementId: 'growth-desk',
    seatId: 'seat-ne',
    clientSeq: 42,
  })
  assert.deepEqual(socket.sent[3], { type: 'work.stop', clientSeq: 43 })
  socket.fire('message', { data: JSON.stringify({ type: 'world.snapshot', lastClientSeq: 80 }) })
  network.sendMove({ x: 5, y: 6 })
  assert.deepEqual(socket.sent[4], {
    type: 'move.target', tileX: 5, tileY: 6, clientSeq: 81,
  })
  network.close()
})

test('REST uses run, Bearer and stable in-flight idempotency keys', async (t) => {
  const originalFetch = globalThis.fetch
  const calls = []
  let releaseGoodCard
  globalThis.fetch = (url, options) => {
    calls.push({ url: String(url), options })
    if (new URL(url).pathname.endsWith('/good-cards')) {
      return new Promise((resolve) => {
        releaseGoodCard = () => resolve(new Response(
          JSON.stringify({ ok: true, available: false, revision: 2 }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ))
      })
    }
    return Promise.resolve(new Response(
      JSON.stringify({ ok: true, rewardCents: 100, revision: 1 }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const network = new GameNetwork({ run: 'run 1', token: 'secret', baseUrl: 'https://game.test' })
  await network.spin('spin-key')
  const first = network.sendGoodCard('player-2', 'card-key')
  const repeated = network.sendGoodCard('player-2', 'must-not-replace-in-flight-key')
  assert.strictEqual(first, repeated)
  assert.equal(calls.length, 2)

  for (const call of calls) {
    assert.equal(new URL(call.url).searchParams.get('run'), 'run 1')
    assert.equal(call.options.headers.Authorization, 'Bearer secret')
  }
  assert.equal(calls[0].options.headers['Idempotency-Key'], 'spin-key')
  assert.equal(calls[1].options.headers['Idempotency-Key'], 'card-key')
  releaseGoodCard()
  await first
})

test('move rejection rolls pending target back without erasing accepted path', () => {
  const current = {
    acceptedTarget: { x: 4, y: 4 },
    pendingTarget: { x: 8, y: 8 },
    acceptedPath: [{ x: 3, y: 3 }, { x: 4, y: 4 }],
  }
  for (const message of [
    { type: 'error', code: 'rate_limited' },
    { type: 'error', code: 'target_occupied' },
    { type: 'error', code: 'target_blocked' },
    { type: 'move.ignored', clientSeq: 2 },
  ]) {
    const next = reduceMoveFeedback(current, message)
    assert.deepEqual(next.pendingTarget, current.acceptedTarget)
    assert.deepEqual(next.acceptedPath, current.acceptedPath)
  }
  assert.equal(
    reduceMoveFeedback(
      { acceptedTarget: null, pendingTarget: { x: 8, y: 8 }, acceptedPath: [] },
      { type: 'error', code: 'path_unavailable' },
    ).pendingTarget,
    null,
  )
})

test('self work stop clears an accepted route while still walking to a reserved seat', () => {
  const current = {
    selfId: 'player-1',
    acceptedTarget: { x: 8, y: 3 },
    pendingTarget: { x: 8, y: 3 },
    acceptedPath: [{ x: 3, y: 2 }, { x: 8, y: 3 }],
  }
  assert.deepEqual(
    reduceSelfMovementMarkers(current, { type: 'work.stopped', stopped: true }),
    { acceptedPath: [], acceptedTarget: null, pendingTarget: null },
  )
})

test('authoritative reconnect snapshot clears stale self movement markers', () => {
  const current = {
    selfId: 'player-1',
    acceptedTarget: { x: 8, y: 3 },
    pendingTarget: { x: 8, y: 3 },
    acceptedPath: [{ x: 3, y: 2 }, { x: 8, y: 3 }],
  }
  assert.deepEqual(
    reduceSelfMovementMarkers(current, {
      type: 'world.snapshot',
      players: [{ id: 'player-1', moving: false, targetX: null, targetY: null }],
    }),
    { acceptedPath: [], acceptedTarget: null, pendingTarget: null },
  )
  assert.deepEqual(
    reduceSelfMovementMarkers(current, {
      type: 'world.snapshot',
      players: [{ id: 'player-1', moving: true, targetX: 8, targetY: 3 }],
    }),
    {
      acceptedPath: current.acceptedPath,
      acceptedTarget: current.acceptedTarget,
      pendingTarget: current.pendingTarget,
    },
  )
})

test('asset snapshot dimensions reject drift without changing renderer boundaries', () => {
  const current = { columns: 14, rows: 9 }
  const frozenLayout = { columns: 14, rows: 9 }
  assert.deepEqual(
    resolveWorldSnapshotDimensions(current, { columns: 14, rows: 9 }, frozenLayout),
    current,
  )
  assert.throws(
    () => resolveWorldSnapshotDimensions(current, { columns: 20, rows: 12 }, frozenLayout),
    (error) => error.code === 'asset_world_dimensions_changed' && error.assetFailure === true,
  )
  assert.deepEqual(current, { columns: 14, rows: 9 })
})

test('isometric projection and inverse agree throughout the 20 x 12 grid', () => {
  const cameras = [
    { x: 0, y: 10, zoom: 1 },
    { x: -37, y: 22, zoom: 0.72 },
    { x: 81, y: -45, zoom: 1.8 },
  ]
  for (const camera of cameras) {
    for (const point of [{ x: 0, y: 0 }, { x: 9, y: 5 }, { x: 19, y: 11 }]) {
      const screen = projectIsometric(point.x, point.y, camera)
      const restored = unprojectIsometric(screen.x, screen.y, camera)
      assert.ok(Math.abs(restored.x - point.x) < 1e-9)
      assert.ok(Math.abs(restored.y - point.y) < 1e-9)
    }
  }
  const origin = { x: 320, y: 72 }
  const camera = { x: 24, y: -11, zoom: 1.25 }
  for (const point of [{ x: 0, y: 0 }, { x: 6, y: 4 }, { x: 13, y: 8 }]) {
    const screen = projectIsometric(point.x, point.y, camera, { origin })
    const restored = unprojectIsometric(screen.x, screen.y, camera, { origin })
    assert.ok(Math.abs(restored.x - point.x) < 1e-9)
    assert.ok(Math.abs(restored.y - point.y) < 1e-9)
  }
})

test('multi-cell sprite anchors project from the footprint centre, not its first cell', () => {
  const manifest = coreV1RuntimeManifest()
  const assets = new Map(manifest.assets.map((candidate) => [candidate.id, candidate]))
  const cases = [
    ['structure.corner-column', { x: 7, y: 8 }, { x: 7, y: 8 }],
    ['furniture.desk-island', { x: 5, y: 3 }, { x: 6, y: 3.5 }],
    ['structure.wall-window-nw', { x: 1, y: 0 }, { x: 2.5, y: 0 }],
    ['structure.wall-window-ne', { x: 0, y: 1 }, { x: 0, y: 2.5 }],
  ]
  for (const [assetId, placement, expected] of cases) {
    const candidate = assets.get(assetId)
    const ground = groundPointForPlacement(candidate, placement)
    assert.deepEqual(ground, expected, `${assetId} must use its complete footprint centre`)

    const projected = projectIsometric(ground.x, ground.y, { x: 0, y: 0, zoom: 1 }, {
      origin: { x: 320, y: 72 },
    })
    const first = candidate.footprint[0]
    const last = candidate.footprint.at(-1)
    const firstPoint = projectIsometric(placement.x + first.x, placement.y + first.y, { x: 0, y: 0, zoom: 1 }, {
      origin: { x: 320, y: 72 },
    })
    const lastPoint = projectIsometric(placement.x + last.x, placement.y + last.y, { x: 0, y: 0, zoom: 1 }, {
      origin: { x: 320, y: 72 },
    })
    assert.deepEqual(projected, {
      x: (firstPoint.x + lastPoint.x) / 2,
      y: (firstPoint.y + lastPoint.y) / 2,
    })
  }
})

test('adjacent four-cell wall sections preserve exact grid continuity', () => {
  const manifest = coreV1RuntimeManifest()
  const assets = new Map(manifest.assets.map((candidate) => [candidate.id, candidate]))
  const origin = { x: 320, y: 64 }
  const camera = { x: 0, y: 0, zoom: 1 }
  const projectGround = (assetId, placement) => {
    const ground = groundPointForPlacement(assets.get(assetId), placement)
    return projectIsometric(ground.x, ground.y, camera, { origin })
  }

  const nwA = projectGround('structure.wall-window-nw', { x: 1, y: 0 })
  const nwB = projectGround('structure.wall-window-nw', { x: 5, y: 0 })
  assert.deepEqual({ x: nwB.x - nwA.x, y: nwB.y - nwA.y }, { x: 64, y: 32 })

  const neA = projectGround('structure.wall-window-ne', { x: 0, y: 1 })
  const neB = projectGround('structure.wall-window-ne', { x: 0, y: 5 })
  assert.deepEqual({ x: neB.x - neA.x, y: neB.y - neA.y }, { x: -64, y: 32 })
})

test('core-v1 legacy geometry swaps mislabeled wall frames and mirrors only the door', () => {
  const legacy = { id: 'core-v1' }
  assert.deepEqual(
    renderSpecForAsset(legacy, 'structure.wall-solid-nw'),
    { assetId: 'structure.wall-solid-ne', flipX: false, groundFit: { k: 48 / 85, s: 0.5 } },
  )
  assert.deepEqual(
    renderSpecForAsset({ id: 'core-v1', geometryVersion: 1 }, 'structure.wall-window-ne'),
    { assetId: 'structure.wall-window-nw', flipX: false, groundFit: { k: 64 / 92, s: -0.5 } },
  )
  assert.deepEqual(
    renderSpecForAsset(legacy, 'structure.wall-door-ne'),
    { assetId: 'structure.wall-door-ne', flipX: true, groundFit: { k: 48 / 85, s: -0.5 } },
  )
  assert.deepEqual(
    renderSpecForAsset({ id: 'core-v1', geometryVersion: 2 }, 'structure.wall-solid-nw'),
    { assetId: 'structure.wall-solid-nw', flipX: false, groundFit: null },
  )
  assert.deepEqual(
    renderSpecForAsset({ id: 'core-v0' }, 'structure.wall-solid-nw'),
    { assetId: 'structure.wall-solid-nw', flipX: false, groundFit: null },
  )
  assert.deepEqual(BACKDROP_GROUND, { x: 320, y: 108 })
})

test('core-v2 uses native geometry and pins the backdrop frame to screen-space origin', () => {
  const manifest = coreV2RuntimeManifest()
  const backdrop = manifest.assets.find((asset) => asset.id === 'backdrop.beijing-cbd')
  assert.deepEqual(backdropScreenGround(backdrop), { x: 320, y: 356 })
  assert.deepEqual(backdropGroundForManifest(manifest, backdrop), { x: 320, y: 356 })
  assert.deepEqual(backdropGroundForManifest(coreV1RuntimeManifest(), backdrop), BACKDROP_GROUND)
  assert.deepEqual(
    renderSpecForAsset(manifest, 'structure.wall-window-ne'),
    { assetId: 'structure.wall-window-ne', flipX: false, groundFit: null },
  )
  assert.deepEqual(ZOOM_STEPS, [1, 1.25, 1.5, 2])
})

test('core-v2 formal maps resolve initial work intent without synthesizing player activity', () => {
  const manifest = coreV2RuntimeManifest()
  for (const layoutId of ['world.opening-empty-v2', 'world.mid-growth-v3']) {
    const raw = worldLayouts.layouts.find((layout) => layout.id === layoutId)
    const layout = resolveAssetWorldLayout(manifest, frozenLayoutForRuntime(raw, manifest))
    assert.equal(layout.id, layoutId)
    assert.equal(layout.initialActivities.length, raw.initialActivities.length)
    assert.equal(layout.initialActivities.every((activity) => activity.type === 'work'), true)
    assert.equal(layout.seats.length >= layout.initialActivities.length, true)
  }
  const actors = normaliseActorRoster({
    players: [{ id: 'ava', x: 6, y: 2 }],
    columns: 14,
    rows: 9,
  })
  assert.equal(actors[0].activity, null)
})

test('full-scene camera fits floor, shell, actors and labels while excluding backdrop', () => {
  const manifest = coreV2RuntimeManifest()
  for (const layoutId of ['world.opening-empty-v2', 'world.mid-growth-v3']) {
    const raw = worldLayouts.layouts.find((layout) => layout.id === layoutId)
    const layout = resolveAssetWorldLayout(manifest, frozenLayoutForRuntime(raw, manifest))
    const players = raw.spawnPoints.map((spawn) => ({ ...spawn, id: spawn.playerId }))
    const actors = normaliseActorRoster({ players, columns: raw.columns, rows: raw.rows })
    const bounds = sceneVisualBounds(manifest, layout, actors)
    const camera = cameraForVisualBounds(bounds, { padding: 16 })
    assert.deepEqual(cameraForAssetLayout(manifest, layout, actors), camera)
    assert.ok(bounds.left > -640, 'screen-space backdrop must not expand scene bounds')
    assert.ok(ZOOM_STEPS.includes(camera.zoom))
    const screenLeft = 320 + camera.x + (bounds.left - 320) * camera.zoom
    const screenRight = 320 + camera.x + (bounds.right - 320) * camera.zoom
    const screenTop = 180 + camera.y + (bounds.top - 180) * camera.zoom
    const screenBottom = 180 + camera.y + (bounds.bottom - 180) * camera.zoom
    assert.ok(screenLeft >= 15 && screenRight <= 625, `${layoutId} horizontal bounds`)
    assert.ok(screenTop >= 15 && screenBottom <= 345, `${layoutId} vertical bounds`)
  }

  const legacyManifest = coreV1RuntimeManifest()
  const legacyRaw = worldLayouts.layouts.find((layout) => layout.id === 'world.opening-empty-v1')
  const legacyLayout = resolveAssetWorldLayout(
    legacyManifest,
    frozenLayoutForRuntime(legacyRaw, legacyManifest),
  )
  assert.deepEqual(cameraForAssetLayout(legacyManifest, legacyLayout, []), legacyLayout.camera)
})

test('money formatting hides zero cents and preserves real cents', () => {
  assert.equal(moneyFromCents(0), '$0')
  assert.equal(moneyFromCents(128_000), '$1,280')
  assert.equal(moneyFromCents(128_050), '$1,280.50')
})

test('actor labels clamp to the viewport and avoid each other deterministically', () => {
  const items = [
    { actor: { id: 'ava', name: 'Ava' }, point: { x: 4, y: 10 } },
    { actor: { id: 'ben', name: 'Ben' }, point: { x: 4, y: 10 } },
    { actor: { id: 'cleo', name: 'Cleo' }, point: { x: 638, y: 350 } },
  ]
  const labels = layoutActorLabels(items, {
    visibleBounds: { left: 5, top: 5, right: 635, bottom: 355 },
    measureText: (text) => text.length * 6,
  })
  assert.equal(labels[0].left >= 5, true)
  assert.notEqual(labels[0].y, labels[1].y)
  assert.equal(labels[2].left + labels[2].width <= 635, true)
  assert.deepEqual(layoutActorLabels(items, {
    visibleBounds: { left: 5, top: 5, right: 635, bottom: 355 },
    measureText: (text) => text.length * 6,
  }), labels)
})

test('occlusion order follows x + y depth, then layer, without mutating input', () => {
  const source = [
    { id: 'far', x: 8, y: 7, layer: 0 },
    { id: 'near-top', x: 2, y: 2, layer: 2 },
    { id: 'near-floor', x: 3, y: 1, layer: 0 },
    { id: 'middle', x: 5, y: 3, layer: 1 },
  ]
  assert.deepEqual(
    sortByIsometricDepth(source).map(({ id }) => id),
    ['near-floor', 'near-top', 'middle', 'far'],
  )
  assert.equal(source[0].id, 'far')
})

test('active rear desk workers remain visible without becoming globally foreground', () => {
  const manifest = coreV1RuntimeManifest()
  const rawLayout = worldLayouts.layouts.find(({ id }) => id === 'world.mid-growth-v2')
  const layout = resolveAssetWorldLayout(
    manifest,
    frozenLayoutForRuntime(rawLayout, manifest),
  )
  const desk = layout.placements.find(({ id }) => id === 'growth-v2-desk')
  assert.equal(desk.depth, 8)

  const workActor = (seatId, renderX, renderY) => ({
    id: 'gus',
    renderX,
    renderY,
    activity: { type: 'work', placementId: desk.id, seatId, facing: 'northeast' },
  })
  const rearWorkActor = workActor('seat-sw', 2, 3)
  const rearIdleActor = { ...rearWorkActor, activity: null }
  const unknownSeatActor = {
    ...rearWorkActor,
    activity: { ...rearWorkActor.activity, seatId: 'seat-unknown' },
  }
  const unknownPlacementActor = {
    ...rearWorkActor,
    activity: { ...rearWorkActor.activity, placementId: 'desk-unknown' },
  }
  const originalActor = structuredClone(rearWorkActor)
  const originalLayout = structuredClone(layout)

  assert.equal(actorDepthForOcclusion(rearIdleActor, layout), 5.7)
  assert.equal(actorDepthForOcclusion(unknownSeatActor, layout), 5.7)
  assert.equal(actorDepthForOcclusion(unknownPlacementActor, layout), 5.7)
  assert.equal(actorDepthForOcclusion(workActor('seat-sw', 2, 3), layout), desk.depth)
  assert.equal(actorDepthForOcclusion(workActor('seat-nw', 4, 1), layout), desk.depth)
  assert.equal(actorDepthForOcclusion(workActor('seat-se', 4, 4), layout), 8.7)
  assert.equal(actorDepthForOcclusion(workActor('seat-ne', 6, 2), layout), 8.7)
  assert.deepEqual(rearWorkActor, originalActor)
  assert.deepEqual(layout, originalLayout)

  const sorted = sortByIsometricDepth([
    { id: desk.id, depth: desk.depth, layer: desk.layer },
    { id: 'gus', depth: actorDepthForOcclusion(rearWorkActor, layout), layer: 2 },
    { id: 'nearer-furniture', depth: 9, layer: 0 },
  ])
  assert.deepEqual(sorted.map(({ id }) => id), [desk.id, 'gus', 'nearer-furniture'])
})

test('core-v2 focus-desk workers lift only to their matching desk depth', () => {
  const manifest = coreV2RuntimeManifest()
  const rawLayout = worldLayouts.layouts.find(({ id }) => id === 'world.mid-growth-v3')
  const layout = resolveAssetWorldLayout(
    manifest,
    frozenLayoutForRuntime(rawLayout, manifest),
  )
  const focusDesks = layout.placements.filter(({ assetId }) => assetId === 'furniture.focus-desk-ne')
  assert.equal(focusDesks.length, 2)

  for (const desk of focusDesks) {
    const seat = layout.seats.find(({ placementId }) => placementId === desk.id)
    const worker = {
      id: `worker-${desk.id}`,
      renderX: seat.x,
      renderY: seat.y,
      activity: {
        type: 'work', placementId: desk.id, seatId: seat.id, facing: seat.facing,
      },
    }
    assert.equal(actorDepthForOcclusion(worker, layout), desk.depth)
    assert.equal(
      actorDepthForOcclusion({ ...worker, activity: { ...worker.activity, seatId: 'wrong-seat' } }, layout),
      seat.x + seat.y + 0.7,
    )
    const sorted = sortByIsometricDepth([
      { id: desk.id, depth: desk.depth, layer: desk.layer },
      { id: worker.id, depth: actorDepthForOcclusion(worker, layout), layer: 2 },
      { id: 'foreground-furniture', depth: desk.depth + 1, layer: 0 },
    ])
    assert.deepEqual(sorted.map(({ id }) => id), [desk.id, worker.id, 'foreground-furniture'])
  }
})

test('animation speed changes distance while reduced motion lands immediately', () => {
  const start = { x: 0, y: 0 }
  const target = { x: 10, y: 6 }
  const normal = advanceMotionPoint(start, target, 16, 1, false)
  const fast = advanceMotionPoint(start, target, 16, 2, false)
  assert.ok(fast.x > normal.x)
  assert.ok(fast.y > normal.y)
  assert.deepEqual(advanceMotionPoint(start, target, 16, 1, true), target)
})

test('player page keeps the compact HUD and review bridge contract', async () => {
  const [html, client, css, pixel] = await Promise.all([
    readFile(fileURLToPath(playerHtmlUrl), 'utf8'),
    readFile(fileURLToPath(clientSourceUrl), 'utf8'),
    readFile(fileURLToPath(playerStylesUrl), 'utf8'),
    readFile(fileURLToPath(pixelSourceUrl), 'utf8'),
  ])
  assert.ok(html.includes('width="640"'))
  assert.ok(html.includes('height="360"'))
  assert.equal((html.match(/class="hud-button/g) || []).length, 2)
  assert.ok(html.includes('id="balance"'))
  assert.ok(html.includes('id="zoom-out-button"'))
  assert.ok(html.includes('id="zoom-in-button"'))
  assert.ok(client.includes('channel: "codex-game"'))
  assert.ok(client.includes('isTrustedReviewCommand(event'))
  assert.match(css, /\.hud\s*\{[^}]*width:\s*100%/s)
  assert.match(css, /\.canvas-stage\s*\{[^}]*width:\s*min\(100%,\s*calc\(\(100dvh - 70px\) \* 16 \/ 9\)\)/s)
  assert.equal(css.includes('1440px'), false)
  assert.match(css, /#game-canvas\s*\{[^}]*object-fit:\s*contain/s)
  assert.equal(css.includes('backdrop-filter'), false)
  assert.equal(pixel.includes('actor.online === false ?'), false)
  assert.match(pixel, /ctx\.globalAlpha\s*=\s*1;/)
})

test('bootstrap asset binding is additive for legacy runs and strict for bound runs', () => {
  assert.deepEqual(
    normaliseBootstrapAssetBinding({ world: { layout: null }, assetPack: null }),
    { mode: 'legacy', legacy: true, layoutId: 'legacy' },
  )
  const manifestSha256 = 'a'.repeat(64)
  const atlasSha256 = 'b'.repeat(64)
  const layout = {
    id: 'world.mid-growth-v1',
    sha256: 'c'.repeat(64),
    columns: 20,
    rows: 12,
    floor: { defaultAssetId: 'floor.raw-concrete', regions: [], border: null },
    placements: [],
    blockedCells: [],
  }
  const binding = normaliseBootstrapAssetBinding({
    assetPack: {
      releaseId: 'release-1',
      packId: 'core-v0',
      manifestSha256,
      manifestUrl: `/api/assets/manifests/${manifestSha256}`,
      atlasSha256,
      atlasUrl: `/api/assets/derived/${atlasSha256}.png`,
      catalogRevision: 7,
    },
    world: { layout },
  })
  assert.equal(binding.mode, 'asset')
  assert.equal(binding.packId, 'core-v0')
  assert.equal(binding.manifestSha256, manifestSha256)
  assert.equal(binding.layoutSha256, 'c'.repeat(64))
  assert.strictEqual(binding.layout, layout)

  assert.throws(
    () => normaliseBootstrapAssetBinding({ assetPack: {}, world: { layout } }),
    AssetBindingError,
  )
  assert.throws(
    () => normaliseBootstrapAssetBinding({
      assetPack: {
        packId: 'core-v0', manifestSha256, manifestUrl: '/manifest',
        atlasSha256, atlasUrl: '/atlas',
      },
      world: { layout: { ...layout, sha256: undefined } },
    }),
    (error) => error.code === 'asset_binding_hash_invalid',
  )
  assert.throws(
    () => normaliseBootstrapAssetBinding({
      assetPack: {
        packId: 'core-v0',
        manifestSha256,
        manifestUrl: '/manifest',
        atlasSha256,
        atlasUrl: '/atlas',
      },
      world: { layout: null },
    }),
    (error) => error.code === 'asset_binding_layout_missing',
  )
  assert.throws(
    () => normaliseBootstrapAssetBinding({
      assetPack: {
        packId: 'core-v0', manifestSha256, manifestUrl: '/manifest',
        atlasSha256, atlasUrl: '/atlas',
      },
      run: {
        assetPack: {
          packId: 'core-v0', manifestSha256: 'c'.repeat(64), manifestUrl: '/other',
          atlasSha256, atlasUrl: '/atlas',
        },
        worldLayout: layout,
      },
      world: { layout },
    }),
    (error) => error.code === 'asset_binding_inconsistent',
  )
})

test('asset scene resolves frozen floor regions, border, furniture and collision', () => {
  const fixture = assetManifest.fixtures.find(({ id }) => id === 'mid-growth')
  const blockedCells = fixtureCollisionCells(assetManifest, fixture.id)
  const layout = resolveAssetWorldLayout(assetManifest, {
    id: 'world.mid-growth-v1',
    sourceFixtureId: fixture.id,
    columns: 20,
    rows: 12,
    floor: {
      defaultAssetId: 'floor.raw-concrete',
      regions: [
        { assetId: 'floor.patched-concrete', x: 2, y: 2, width: 5, height: 4 },
        { assetId: 'floor.light-wood', x: 7, y: 3, width: 6, height: 4 },
      ],
      border: { assetId: 'floor.utility-border', edges: ['north', 'south'] },
    },
    placements: fixture.placements.filter(({ assetId }) => assetId),
    blockedCells,
  }, {
    columns: 20,
    rows: 12,
    blockedCells,
  })
  assert.equal(layout.floors.length, 240)
  assert.equal(layout.furniture.length, 4)
  assert.equal(layout.blockedCells.length, 18)
  assert.equal(layout.floors.find(({ x, y }) => x === 3 && y === 3).assetId, 'floor.patched-concrete')
  assert.equal(layout.floors.find(({ x, y }) => x === 8 && y === 4).assetId, 'floor.light-wood')
  assert.equal(layout.floors.find(({ x, y }) => x === 8 && y === 0).assetId, 'floor.utility-border')
  assert.equal(layout.furniture.find(({ id }) => id === 'growth-desk').depth, 8)

  assert.throws(
    () => resolveAssetWorldLayout(assetManifest, {
      ...layout,
      floor: {
        defaultAssetId: 'floor.raw-concrete',
        regions: [],
        border: null,
      },
      placements: fixture.placements.filter(({ assetId }) => assetId),
      blockedCells: blockedCells.slice(1),
    }),
    /collision/,
  )
})

test('core-v1 layout resolves asset shell kinds, origin, spawns and desk seats', () => {
  const manifest = structuredClone(assetManifest)
  const movingBox = manifest.assets.find(({ slot }) => slot === 'furniture.moving-box')
  const desk = manifest.assets.find(({ slot }) => slot === 'furniture.desk-island')
  desk.interactionPoints = [
    { id: 'seat-se', kind: 'work-seat', x: 1, y: 2, facing: 'northwest' },
    { id: 'seat-sw', kind: 'work-seat', x: -1, y: 1, facing: 'northeast' },
    { id: 'seat-nw', kind: 'work-seat', x: 1, y: -1, facing: 'southeast' },
    { id: 'seat-ne', kind: 'work-seat', x: 3, y: 0, facing: 'southwest' },
  ]
  manifest.assets.push(
    {
      ...structuredClone(movingBox),
      id: 'backdrop.beijing-cbd',
      slot: 'backdrop.beijing-cbd',
      kind: 'backdrop',
      footprint: [{ x: 0, y: 0, blocked: false }],
      collision: [],
    },
    {
      ...structuredClone(movingBox),
      id: 'structure.corner-column',
      slot: 'structure.corner-column',
      kind: 'structure',
    },
    {
      ...structuredClone(movingBox),
      id: 'decor.floor-plant',
      slot: 'decor.floor-plant',
      kind: 'decor',
    },
  )
  const blockedCells = [
    { x: 0, y: 1 },
    { x: 2, y: 2 },
    ...desk.collision.map((cell) => ({ x: 5 + cell.x, y: 3 + cell.y })),
  ]
  const layout = resolveAssetWorldLayout(manifest, {
    id: 'world.opening-empty-v1',
    stage: 'opening',
    requiredPackId: 'core-v1',
    sha256: 'c'.repeat(64),
    columns: 14,
    rows: 9,
    origin: { x: 320, y: 72 },
    camera: { x: 0, y: 0, zoom: 1.25 },
    floor: { defaultAssetId: 'floor.raw-concrete', regions: [], border: null },
    placements: [
      { id: 'view', assetId: 'backdrop.beijing-cbd', x: 0, y: 0 },
      { id: 'corner', assetId: 'structure.corner-column', x: 0, y: 1 },
      { id: 'plant', assetId: 'decor.floor-plant', x: 2, y: 2 },
      { id: 'opening-desk', assetId: 'furniture.desk-island', x: 5, y: 3 },
    ],
    spawnPoints: [{ playerId: 'gus', name: 'Gus', x: 9, y: 7 }],
    blockedCells,
  }, { columns: 14, rows: 9, blockedCells })

  assert.deepEqual(layout.origin, { x: 320, y: 72 })
  assert.equal(layout.backdrops.length, 1)
  assert.equal(layout.structures.length, 1)
  assert.equal(layout.decor.length, 1)
  assert.equal(layout.furniture.length, 1)
  assert.equal(layout.seats.length, 4)
  assert.equal(layout.structures[0].renderAssetId, 'structure.corner-column')
  assert.deepEqual(
    layout.seats.map(({ placementId, id, x, y, facing }) => ({ placementId, id, x, y, facing })),
    [
      { placementId: 'opening-desk', id: 'seat-se', x: 6, y: 5, facing: 'northwest' },
      { placementId: 'opening-desk', id: 'seat-sw', x: 4, y: 4, facing: 'northeast' },
      { placementId: 'opening-desk', id: 'seat-nw', x: 6, y: 2, facing: 'southeast' },
      { placementId: 'opening-desk', id: 'seat-ne', x: 8, y: 3, facing: 'southwest' },
    ],
  )
})

test('both formal core-v1 maps resolve through the real player runtime contract', () => {
  const manifest = coreV1RuntimeManifest()
  const expectations = new Map([
    ['world.opening-empty-v1', {
      columns: 14, rows: 9, origin: { x: 320, y: 72 }, objects: 12, furniture: 6, structures: 6, decor: 0,
    }],
    ['world.mid-growth-v2', {
      columns: 20, rows: 12, origin: { x: 320, y: 64 }, objects: 18, furniture: 7, structures: 9, decor: 2,
    }],
  ])
  for (const [layoutId, expected] of expectations) {
    const raw = worldLayouts.layouts.find(({ id }) => id === layoutId)
    const frozen = frozenLayoutForRuntime(raw, manifest)
    const layout = resolveAssetWorldLayout(manifest, frozen, {
      columns: expected.columns,
      rows: expected.rows,
      blockedCells: frozen.blockedCells,
    })
    assert.equal(layout.id, layoutId)
    assert.equal(layout.columns, expected.columns)
    assert.equal(layout.rows, expected.rows)
    assert.deepEqual(layout.origin, expected.origin)
    assert.equal(layout.backdrops.length, 1)
    assert.equal(layout.objects.length, expected.objects)
    assert.equal(layout.furniture.length, expected.furniture)
    assert.equal(layout.structures.length, expected.structures)
    assert.equal(layout.decor.length, expected.decor)
    assert.equal(layout.spawnPoints.length, 8)
    assert.equal(layout.seats.length, 4)
    const nwWindow = layout.structures.find(({ assetId }) => assetId === 'structure.wall-window-nw')
    const neWindow = layout.structures.find(({ assetId }) => assetId === 'structure.wall-window-ne')
    assert.equal(nwWindow?.renderAssetId, 'structure.wall-window-ne')
    if (neWindow) assert.equal(neWindow.renderAssetId, 'structure.wall-window-nw')
    assert.equal(
      layout.structures.find(({ assetId }) => assetId === 'structure.wall-door-ne')?.renderFlipX,
      true,
    )
  }
})

test('four-direction Gus animation follows grid motion and preserves facing while idle', () => {
  assert.equal(directionForMotion(1, 0), 'southeast')
  assert.equal(directionForMotion(0, 1), 'southwest')
  assert.equal(directionForMotion(-1, 0), 'northwest')
  assert.equal(directionForMotion(0, -1), 'northeast')
  assert.equal(directionForMotion(0, 0, 'northwest'), 'northwest')
  assert.equal(actorAnimationId({ facing: 'southwest', animationAction: 'walk' }), 'animation.gus.southwest.walk')
  assert.equal(
    actorAnimationId({ facing: 'southwest', animationAction: 'walk' }, true),
    'animation.gus.southwest.idle',
  )
  assert.equal(
    actorAnimationId({
      facing: 'northwest',
      animationAction: 'idle',
      activity: { type: 'work', facing: 'northwest' },
    }),
    'animation.gus.northwest.work',
  )
  assert.equal(
    actorAnimationId({
      facing: 'northeast',
      activity: { type: 'work', facing: 'northeast' },
    }, true),
    'animation.gus.northeast.work',
  )
})

test('bound player loads pinned assets, blocks failures and reports hash to review', async () => {
  const client = await readFile(fileURLToPath(clientSourceUrl), 'utf8')
  assert.ok(client.includes('loadPinnedAssetPack(binding'))
  assert.ok(client.includes('scene.setAssetRuntime(runtime, binding.layout'))
  assert.ok(client.includes('blockAssetRun(error'))
  assert.ok(client.includes('postGameMessage("assetReady"'))
  assert.ok(client.includes('manifestSha256: asset.manifestSha256'))
  assert.ok(client.includes('layoutSha256: asset.layoutSha256'))
  assert.ok(client.includes('releaseId: state.asset.releaseId'))
  assert.ok(client.includes('case "camera"'))
  assert.ok(client.includes('activity: director.activity'))
  assert.ok(client.includes('target: director.target'))
  assert.ok(client.includes('seatOccupancy: director.seatOccupancy'))
  assert.ok(client.includes('network.sendWorkStart(target)'))
  assert.equal(client.includes('"/api/assets/active/manifest"'), false)
})
