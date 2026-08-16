import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  VIEWPORT_PRESETS,
  ReviewApi,
  buildPlayerFrameUrl,
  comparePlayerAssetReport,
  comparePlayerAssetReports,
  isTrustedPlayerMessage,
  normalizePlayerDirectorState,
  normalizePlayerAssetReport,
  normalizeReviewLayout,
  playerKey,
  projectViewport,
  reducePlayback,
  reviewLayoutOptionLabel,
  sanitizeStoredReviewRun,
  sweepStoredReviewRuns,
  worldLayoutIdentity,
} from '../web/review.mjs'

const reviewHtmlUrl = new URL('../web/review.html', import.meta.url)
const reviewCssUrl = new URL('../web/review.css', import.meta.url)
const reviewClientUrl = new URL('../web/review.mjs', import.meta.url)

test('desktop projection preserves a real 1440 x 900 iframe viewport', () => {
  const projection = projectViewport(VIEWPORT_PRESETS.desktop, 720, 900)
  assert.deepEqual(projection, {
    width: 1440,
    height: 900,
    scale: 0.5,
    projectedWidth: 720,
    projectedHeight: 450,
  })
})

test('phone projection never upscales its real CSS viewport', () => {
  assert.deepEqual(projectViewport('mobile', 800, 1000), {
    width: 390,
    height: 844,
    scale: 1,
    projectedWidth: 390,
    projectedHeight: 844,
  })
  assert.equal(projectViewport('compact', 160, 1000).scale, 0.5)
})

test('playback reducer supports speed, pause, resume and replay signals', () => {
  let state = reducePlayback(null, null)
  state = reducePlayback(state, { type: 'set-speed', value: 2 })
  state = reducePlayback(state, { type: 'pause' })
  state = reducePlayback(state, { type: 'replay-last' })
  assert.deepEqual(state, { speed: 2, paused: true, replayNonce: 1 })
  assert.equal(reducePlayback(state, { type: 'resume' }).paused, false)
  assert.throws(() => reducePlayback(state, { type: 'set-speed', value: 3 }), RangeError)
})

test('review API preserves the browser receiver for native fetch', async () => {
  const originalFetch = globalThis.fetch
  let receiver = null
  globalThis.fetch = function () {
    receiver = this
    return Promise.resolve({
      headers: { get: () => 'application/json' },
      status: 200,
      ok: true,
      json: async () => ({ ok: true }),
    })
  }
  try {
    const payload = await new ReviewApi('/api/review').request('runs')
    assert.deepEqual(payload, { ok: true })
    assert.equal(receiver, globalThis)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('review API lists maps and requires an explicit layout when creating a run', async () => {
  const calls = []
  const api = new ReviewApi('/api/review', async (url, options) => {
    calls.push({ url, options })
    return {
      headers: { get: () => 'application/json' },
      status: 200,
      ok: true,
      json: async () => ({ ok: true }),
    }
  })
  await api.getLayouts('bootstrap-token')
  await api.createRun('bootstrap-token', 'world.opening-empty-v1')
  assert.equal(calls[0].url, '/api/review/layouts')
  assert.equal(calls[1].url, '/api/review/runs')
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    label: 'Gus personal acceptance',
    layoutId: 'world.opening-empty-v1',
  })
})

test('player iframe reloads with a nonce while credentials stay in the fragment', () => {
  const url = buildPlayerFrameUrl(
    { id: 'gus', token: 'secret-player-token', url: 'http://127.0.0.1:10700/?token=leaked' },
    'run-test',
    'http://127.0.0.1:10700/review',
    'frame-reload-test',
  )
  assert.equal(url.searchParams.get('reviewLoad'), 'frame-reload-test')
  assert.equal(url.searchParams.has('token'), false)
  assert.equal(url.hash.includes('secret-player-token'), true)
  assert.equal(url.hash.includes('run-test'), true)
})

test('eight-player roster remains identifiable while the director fallback is Gus-only', () => {
  const roster = ['Ava', 'Ben', 'Cleo', 'Drew', 'Eli', 'Faye', 'Gus', 'Hana']
  assert.deepEqual(
    roster.map((name, index) => playerKey({ id: name.toLowerCase(), name }, index)),
    ['ava', 'ben', 'cleo', 'drew', 'eli', 'faye', 'gus', 'hana'],
  )
  assert.equal(playerKey({}, 0), 'gus')
  assert.equal(playerKey({}, 1), 'player-2')
})

test('single Gus asset report compares every immutable run identity', () => {
  const gus = normalizePlayerAssetReport({
    type: 'assetReady',
    asset: {
      ready: true,
      packId: 'core-v1',
      releaseId: 'release-1',
      manifestSha256: 'a'.repeat(64),
      atlasSha256: 'b'.repeat(64),
      layoutId: 'layout-1',
      layoutSha256: 'c'.repeat(64),
    },
  })
  const expected = {
    bound: true,
    packId: 'core-v1',
    releaseId: 'release-1',
    manifestSha256: 'a'.repeat(64),
    atlasSha256: 'b'.repeat(64),
    layoutId: 'layout-1',
    layoutSha256: 'c'.repeat(64),
  }
  assert.equal(comparePlayerAssetReport(gus, expected).state, 'match')
  assert.equal(comparePlayerAssetReports({ gus }, expected).state, 'match')
  assert.equal(comparePlayerAssetReport({ ...gus, atlasSha256: 'd'.repeat(64) }, expected).state, 'mismatch')
  assert.equal(comparePlayerAssetReport(null, expected).state, 'waiting')
  const legacy = {
    ready: true, packId: null, releaseId: null, manifestSha256: null, atlasSha256: null,
    layoutId: 'legacy', layoutSha256: null, legacy: true, error: '',
  }
  assert.equal(comparePlayerAssetReport(legacy, { bound: false }).state, 'legacy')
  assert.equal(comparePlayerAssetReport(legacy, expected).state, 'mismatch')
  assert.equal(comparePlayerAssetReport({ ready: false, error: 'atlas failed' }, expected).state, 'error')
})

test('layout catalog entries normalize availability without selecting a default', () => {
  assert.deepEqual(normalizeReviewLayout({
    layoutId: 'world.opening-empty-v1',
    label: '光秃开局',
    stage: 'opening',
    columns: 14,
    rows: 9,
    requiredPackId: 'core-v1',
    available: true,
  }), {
    id: 'world.opening-empty-v1', label: '光秃开局', stage: 'opening', columns: 14, rows: 9,
    requiredPackId: 'core-v1', available: true, reason: '',
  })
  // displayLabel wins over label so the picker never shows a generation suffix.
  assert.equal(normalizeReviewLayout({
    id: 'world.mid-growth-v3', displayLabel: '丰富中期办公室', label: '丰富中期办公室 v3',
    stage: 'mid-growth', columns: 20, rows: 12, requiredPackId: 'core-v2', available: true,
  }).label, '丰富中期办公室')
})

test('player bridge requires the exact frame, origin and non-empty matching run id', () => {
  const frameWindow = {}
  const message = { channel: 'codex-game', runId: 'run-1', type: 'state' }
  const trusted = { source: frameWindow, origin: 'https://game.test', data: message }
  assert.equal(isTrustedPlayerMessage(trusted, frameWindow, 'https://game.test', 'run-1'), true)
  assert.equal(isTrustedPlayerMessage({ ...trusted, source: {} }, frameWindow, 'https://game.test', 'run-1'), false)
  assert.equal(isTrustedPlayerMessage({ ...trusted, origin: 'https://other.test' }, frameWindow, 'https://game.test', 'run-1'), false)
  assert.equal(isTrustedPlayerMessage(trusted, frameWindow, '', 'run-1'), false)
  assert.equal(isTrustedPlayerMessage({ ...trusted, data: { ...message, runId: '' } }, frameWindow, 'https://game.test', 'run-1'), false)
  assert.equal(isTrustedPlayerMessage({ ...trusted, data: { ...message, runId: 'run-2' } }, frameWindow, 'https://game.test', 'run-1'), false)
})

test('director preserves reported camera, target and sanitized seat occupancy', () => {
  const first = normalizePlayerDirectorState({
    state: {
      camera: { x: 12, y: -8, zoom: 1.4 },
      target: { x: 6, y: 5 },
      seatOccupancy: [
        { placementId: 'opening-desk', seatId: 'seat-se', playerId: 'gus', state: 'active' },
        { placementId: '', seatId: 'invalid', playerId: 'gus' },
      ],
    },
  })
  assert.deepEqual(first, {
    camera: { x: 12, y: -8, zoom: 1.4 },
    target: { x: 6, y: 5 },
    seatOccupancy: [{ placementId: 'opening-desk', seatId: 'seat-se', playerId: 'gus', state: 'active' }],
  })
  assert.deepEqual(normalizePlayerDirectorState({ type: 'event' }, first), first)
  assert.deepEqual(normalizePlayerDirectorState({ state: { target: null, seatOccupancy: [] } }, first), {
    camera: first.camera,
    target: null,
    seatOccupancy: [],
  })
})

test('run diagnostics accept the backend workSeats snapshot field', () => {
  const layout = worldLayoutIdentity({
    worldLayout: {
      id: 'world.opening-empty-v1',
      workSeats: [
        { placementId: 'opening-desk', seatId: 'seat-se', x: 6, y: 5 },
        { placementId: 'opening-desk', seatId: 'seat-sw', x: 4, y: 4 },
      ],
    },
  })
  assert.equal(layout.interactionPoints.length, 2)
  assert.equal(layout.interactionPoints[0].seatId, 'seat-se')
})

test('map options name the map and its grid without naming an asset pack', () => {
  assert.equal(reviewLayoutOptionLabel({
    id: 'world.mid-growth-v3', displayLabel: '丰富中期办公室', stage: 'mid-growth',
    columns: 20, rows: 12, requiredPackId: 'core-v2', available: true,
  }), '丰富中期办公室 · 20×12')
  // The label is identical whichever generation backs it; the client filters
  // unavailable maps out of the picker rather than annotating them.
  assert.equal(reviewLayoutOptionLabel({
    id: 'world.mid-growth-v2', displayLabel: '丰富中期办公室', columns: 20, rows: 12,
    requiredPackId: 'core-v1', available: false, reason: '请先激活 core-v1',
  }), '丰富中期办公室 · 20×12')
})

test('session sweep rewrites every stored run to controller plus Gus only', () => {
  class MemoryStorage {
    constructor(entries) { this.values = new Map(entries) }
    get length() { return this.values.size }
    key(index) { return [...this.values.keys()][index] ?? null }
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  const legacyRun = (id) => JSON.stringify({
    id,
    controllerToken: `controller-${id}`,
    players: [
      { id: 'gus', name: 'Gus', token: `gus-${id}` },
      { id: 'hana', name: 'Hana', token: `hana-${id}` },
      { id: 'ava', name: 'Ava', token: `ava-${id}` },
    ],
    playerTokens: { hana: `hidden-hana-${id}` },
  })
  const storage = new MemoryStorage([
    ['codex-review:run:one', legacyRun('one')],
    ['codex-review:run:two', legacyRun('two')],
    ['codex-review:run:broken', '{'],
    ['unrelated', 'leave-me'],
  ])
  assert.deepEqual(sweepStoredReviewRuns(storage), { migrated: 2, removed: 1 })
  for (const id of ['one', 'two']) {
    const stored = JSON.parse(storage.getItem(`codex-review:run:${id}`))
    assert.equal(stored.controllerToken, `controller-${id}`)
    assert.deepEqual(stored.players.map(({ id: playerId, token }) => [playerId, token]), [['gus', `gus-${id}`]])
    assert.equal(JSON.stringify(stored).includes(`hana-${id}`), false)
    assert.equal(Object.hasOwn(stored, 'playerTokens'), false)
    assert.deepEqual(sanitizeStoredReviewRun(stored), stored)
  }
  assert.equal(storage.getItem('codex-review:run:broken'), null)
  assert.equal(storage.getItem('unrelated'), 'leave-me')
})

test('review page contains one Gus client, explicit map selection and every director control', async () => {
  const [html, css, client] = await Promise.all([
    readFile(fileURLToPath(reviewHtmlUrl), 'utf8'),
    readFile(fileURLToPath(reviewCssUrl), 'utf8'),
    readFile(fileURLToPath(reviewClientUrl), 'utf8'),
  ])
  for (const marker of [
    'id="gusFrame"',
    'id="layoutSelect"',
    'id="layoutAvailability"',
    'id="mapDiagnosticsHeading"',
    'id="mapIdLabel"',
    'id="mapStageIdLabel"',
    'id="mapSizeLabel"',
    'id="layoutShaLabel"',
    'id="assetReleaseLabel"',
    'id="spawnSummary"',
    'id="playerActivityLabel"',
    'data-viewport="desktop"',
    'data-viewport="mobile"',
    'data-viewport="compact"',
    'id="pauseButton"',
    'id="replayButton"',
    'id="wheelForm"',
    'id="advanceDayButton"',
    'id="resetDailyButton"',
    'data-overlay="grid"',
    'data-overlay="blocked"',
    'data-overlay="path"',
    'data-overlay="target"',
    'data-overlay="spawn"',
    'data-overlay="footprint"',
    'data-overlay="depth"',
    'data-camera="full"',
    'data-camera="gus"',
    'data-camera="desk"',
    'id="delayInput"',
    'id="copyFeedbackButton"',
    'id="liveSection"',
    'id="advancedControls"',
    'id="assetHashBadge"',
    'id="assetHashDetails"',
    'id="resetConfirmDialog"',
    'id="confirmResetButton"',
    'id="cancelResetButton"',
  ]) {
    assert.ok(html.includes(marker), `missing review contract marker: ${marker}`)
  }
  assert.ok(html.indexOf('id="liveSection"') < html.indexOf('id="advancedControls"'))
  assert.equal(/\bhana\b/i.test(html), false)
  assert.equal(/\bhana\b/i.test(client), false)
  assert.equal(/\bhana\b/i.test(css), false)
  assert.equal(html.includes('data-mobile-player'), false)
  assert.match(css, /\.delay-control input\s*\{[^}]*min-height:\s*44px/s)
  // The on-screen event log was removed; the trace is still collected in memory so
  // 复制反馈 can attach recentEvents and backend events keep driving the revision label.
  for (const removed of ['id="eventLog"', 'id="autoScrollInput"', 'id="clearLogButton"', 'event-section', 'id="unavailableLayoutList"']) {
    assert.equal(html.includes(removed), false, `removed review marker remains: ${removed}`)
  }
  assert.equal(/\.event-row|\.event-log|\.event-actions|\.text-button|\.unavailable-layout-list/.test(css), false)
  assert.ok(client.includes('recentEvents: this.events.slice(-50)'))
  assert.ok(css.includes('@media (max-width: 430px)'))
  assert.equal(client.includes('option.disabled = !layout.available'), false)
  // The picker offers only what the active pack can build, so no user-facing
  // string on this page may name an asset pack or an unavailable map.
  assert.equal(/不可用/.test(client), false)
  assert.equal(/core-v\d/.test(client), false)
  // Explicit selection stays required: the placeholder is always the fallback.
  assert.ok(client.includes("placeholder.value = ''"))
  assert.ok(client.includes('filter((layout) => layout.available)'))
  assert.ok(client.includes('reportedCamera: this.playerDirectorState.camera'))
  assert.ok(client.includes('seatOccupancy: this.playerDirectorState.seatOccupancy'))
  assert.ok(client.includes("openResetConfirmation('run')"))
  assert.ok(client.includes("openResetConfirmation('daily')"))
})
