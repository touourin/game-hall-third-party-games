export const VIEWPORT_PRESETS = Object.freeze({
  desktop: Object.freeze({ key: 'desktop', label: '桌面', width: 1440, height: 900 }),
  mobile: Object.freeze({ key: 'mobile', label: '手机', width: 390, height: 844 }),
  compact: Object.freeze({ key: 'compact', label: '窄屏', width: 320, height: 568 }),
})

const PLAYBACK_SPEEDS = Object.freeze([0.5, 1, 2])
const PLAYER_KEYS = Object.freeze(['gus'])
const CAMERA_PRESETS = Object.freeze(['full', 'gus', 'desk'])
const OVERLAY_KEYS = Object.freeze(['grid', 'blocked', 'path', 'target', 'spawn', 'footprint', 'depth'])
const SESSION_ACTIVE_RUN = 'codex-review:active-run'
const SESSION_BOOTSTRAP_TOKEN = 'codex-review:bootstrap-token'
const SESSION_RUN_PREFIX = 'codex-review:run:'
const MAX_EVENT_TRACE = 300

export function projectViewport(preset, availableWidth, availableHeight = Number.POSITIVE_INFINITY) {
  const selected = typeof preset === 'string' ? VIEWPORT_PRESETS[preset] : preset
  if (!selected || !Number.isFinite(selected.width) || !Number.isFinite(selected.height)) {
    throw new TypeError('无效的视口预设')
  }
  const safeWidth = Number.isFinite(availableWidth) ? Math.max(1, availableWidth) : selected.width
  const safeHeight = Number.isFinite(availableHeight) ? Math.max(1, availableHeight) : selected.height
  const scale = Math.min(1, safeWidth / selected.width, safeHeight / selected.height)
  return {
    width: selected.width,
    height: selected.height,
    scale,
    projectedWidth: Math.max(1, Math.round(selected.width * scale)),
    projectedHeight: Math.max(1, Math.round(selected.height * scale)),
  }
}

export function reducePlayback(current, action) {
  const state = {
    speed: PLAYBACK_SPEEDS.includes(Number(current?.speed)) ? Number(current.speed) : 1,
    paused: Boolean(current?.paused),
    replayNonce: Number.isSafeInteger(current?.replayNonce) ? current.replayNonce : 0,
  }
  switch (action?.type) {
    case 'set-speed': {
      const speed = Number(action.value)
      if (!PLAYBACK_SPEEDS.includes(speed)) throw new RangeError('播放速度必须为 0.5、1 或 2')
      return { ...state, speed }
    }
    case 'pause':
      return { ...state, paused: true }
    case 'resume':
      return { ...state, paused: false }
    case 'toggle-pause':
      return { ...state, paused: !state.paused }
    case 'replay-last':
      return { ...state, replayNonce: state.replayNonce + 1 }
    default:
      return state
  }
}

function safeSessionGet(key) {
  try { return window.sessionStorage.getItem(key) } catch { return null }
}

function safeSessionSet(key, value) {
  try { window.sessionStorage.setItem(key, value) } catch { /* session-only best effort */ }
}

function safeSessionRemove(key) {
  try { window.sessionStorage.removeItem(key) } catch { /* session-only best effort */ }
}

export function isTrustedPlayerMessage(event, frameWindow, expectedOrigin, expectedRunId) {
  if (!event || !frameWindow || event.source !== frameWindow) return false
  const origin = String(expectedOrigin || '')
  const runId = String(expectedRunId || '')
  if (!origin || !runId || event.origin !== origin) return false
  const message = event.data
  return Boolean(
    message
    && typeof message === 'object'
    && message.channel === 'codex-game'
    && String(message.runId || '') === runId,
  )
}

function commandId(prefix = 'review') {
  if (globalThis.crypto?.randomUUID) return `${prefix}:${globalThis.crypto.randomUUID()}`
  return `${prefix}:${Date.now()}:${Math.random().toString(36).slice(2)}`
}

function cleanBaseUrl(value) {
  return String(value || '/api/review').replace(/\/+$/, '')
}

export function buildPlayerFrameUrl(player, runId, baseHref, reloadId = commandId('frame')) {
  const url = new URL(player.url || './index.html', baseHref)
  for (const name of ['run', 'reviewRun', 'token', 'playerToken', 'adminToken']) url.searchParams.delete(name)
  url.searchParams.set('reviewLoad', reloadId)
  const fragment = new URLSearchParams()
  fragment.set('review', '1')
  fragment.set('reviewRun', runId)
  fragment.set('run', runId)
  fragment.set('player', player.id)
  fragment.set('playerToken', player.token)
  fragment.set('token', player.token)
  url.hash = fragment.toString()
  return url
}

function responseMessage(payload, fallback) {
  if (typeof payload?.detail === 'string') return payload.detail
  if (typeof payload?.error === 'string') return payload.error
  if (typeof payload?.message === 'string') return payload.message
  return fallback
}

export class ReviewApi {
  constructor(baseUrl, fetchImpl = (...args) => globalThis.fetch(...args)) {
    this.baseUrl = cleanBaseUrl(baseUrl)
    this.fetchImpl = fetchImpl
  }

  resolve(path) {
    if (/^https?:\/\//i.test(path)) return path
    if (path.startsWith('/')) return path
    return `${this.baseUrl}/${path.replace(/^\/+/, '')}`
  }

  async request(path, { method = 'GET', token = '', body, idempotencyKey } = {}) {
    const headers = { Accept: 'application/json' }
    if (token) {
      headers.Authorization = `Bearer ${token}`
      headers['X-Admin-Token'] = token
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
    const response = await this.fetchImpl(this.resolve(path), {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const contentType = response.headers.get('content-type') || ''
    let payload = null
    if (response.status !== 204) {
      payload = contentType.includes('json')
        ? await response.json().catch(() => null)
        : await response.text().catch(() => '')
    }
    if (!response.ok) {
      const error = new Error(responseMessage(payload, `验收接口返回 ${response.status}`))
      error.status = response.status
      error.payload = payload
      throw error
    }
    return payload || { ok: true }
  }

  getLayouts(bootstrapToken = '') {
    return this.request('layouts', { token: bootstrapToken })
  }

  createRun(bootstrapToken, layoutId) {
    return this.request('runs', {
      method: 'POST',
      token: bootstrapToken,
      body: { label: 'Gus personal acceptance', layoutId },
      idempotencyKey: commandId('create-run'),
    })
  }

  getRun(run) {
    return this.request(`runs/${encodeURIComponent(run.id)}`, { token: run.controllerToken })
  }

  resetRun(run) {
    return this.request(`runs/${encodeURIComponent(run.id)}/reset`, {
      method: 'POST',
      token: run.controllerToken,
      body: {},
      idempotencyKey: commandId('reset-run'),
    })
  }

  advanceDay(run) {
    return this.request(`runs/${encodeURIComponent(run.id)}/advance-day`, {
      method: 'POST',
      token: run.controllerToken,
      body: { days: 1 },
      idempotencyKey: commandId('advance-day'),
    })
  }

  resetDaily(run) {
    return this.request(`runs/${encodeURIComponent(run.id)}/reset-daily`, {
      method: 'POST',
      token: run.controllerToken,
      body: {},
      idempotencyKey: commandId('reset-daily'),
    })
  }

  forceWheel(run, reward) {
    return this.request(`runs/${encodeURIComponent(run.id)}/force-wheel`, {
      method: 'POST',
      token: run.controllerToken,
      body: { reward },
      idempotencyKey: commandId('force-wheel'),
    })
  }

  setPaused(run, paused) {
    return this.request(`runs/${encodeURIComponent(run.id)}/pause`, {
      method: 'POST',
      token: run.controllerToken,
      body: { paused },
      idempotencyKey: commandId('pause'),
    })
  }

  setSpeed(run, speed) {
    return this.request(`runs/${encodeURIComponent(run.id)}/speed`, {
      method: 'POST',
      token: run.controllerToken,
      body: { speed },
      idempotencyKey: commandId('speed'),
    })
  }
}

function objectValue(source, keys) {
  for (const key of keys) {
    if (source && source[key] !== undefined && source[key] !== null) return source[key]
  }
  return undefined
}

export function playerKey(player, index) {
  const candidate = String(objectValue(player, ['key', 'slug', 'name', 'displayName', 'id']) || '').toLowerCase()
  if (candidate) {
    if (candidate === 'gus') return 'gus'
    return candidate
  }
  return PLAYER_KEYS[index] || `player-${index + 1}`
}

export function normalizePlayerAssetReport(message) {
  if (!message || typeof message !== 'object') return null
  const nested = message.asset && typeof message.asset === 'object' ? message.asset : null
  const hasTopLevel = message.assetReady !== undefined
    || message.releaseId !== undefined
    || message.manifestSha256 !== undefined
    || message.atlasSha256 !== undefined
    || message.layoutSha256 !== undefined
  if (!nested && !hasTopLevel) return null
  const source = nested || message
  const ready = Boolean(source.ready ?? message.assetReady)
  return {
    ready,
    packId: source.packId == null ? null : String(source.packId),
    releaseId: source.releaseId == null
      ? message.releaseId == null ? null : String(message.releaseId)
      : String(source.releaseId),
    manifestSha256: source.manifestSha256 == null
      ? message.manifestSha256 == null ? null : String(message.manifestSha256)
      : String(source.manifestSha256),
    atlasSha256: source.atlasSha256 == null
      ? message.atlasSha256 == null ? null : String(message.atlasSha256)
      : String(source.atlasSha256),
    layoutId: source.layoutId == null
      ? message.layoutId == null ? null : String(message.layoutId)
      : String(source.layoutId),
    layoutSha256: source.layoutSha256 == null
      ? message.layoutSha256 == null ? null : String(message.layoutSha256)
      : String(source.layoutSha256),
    legacy: Boolean(source.legacy),
    error: source.error == null ? '' : String(source.error),
  }
}

export function comparePlayerAssetReport(report, expected = null) {
  if (!report) return { state: 'waiting', label: '等待 Gus 回报' }
  if (!report.ready) return { state: 'error', label: report.error || 'Gus 资产加载失败' }
  const authoritative = expected && typeof expected === 'object' ? expected : null
  const bound = Boolean(authoritative?.bound)
  if (!bound) {
    if (report.legacy) return { state: 'legacy', label: 'Legacy · Run 无资产绑定' }
    return { state: 'mismatch', label: '异常 · 未绑定 Run 加载了资产', mismatches: ['legacy'] }
  }
  if (report.legacy) {
    return { state: 'mismatch', label: '资产不一致 · 绑定 Run 不得回报 Legacy', mismatches: ['legacy'] }
  }
  const fields = ['packId', 'releaseId', 'manifestSha256', 'atlasSha256', 'layoutId', 'layoutSha256']
  const authoritativeIncomplete = fields.filter((field) => !authoritative[field])
  if (authoritativeIncomplete.length) {
    return {
      state: 'error',
      label: `Run 绑定不完整 · ${authoritativeIncomplete.join(', ')}`,
      mismatches: authoritativeIncomplete,
    }
  }
  const mismatches = fields.filter((field) => !report[field] || String(report[field]) !== String(authoritative[field]))
  if (mismatches.length) {
    return { state: 'mismatch', label: `资产不一致 · ${mismatches.join(', ')}`, mismatches }
  }
  return { state: 'match', label: `已核对 · ${report.manifestSha256.slice(0, 10)}…` }
}

// Kept as a narrow compatibility export for older imports; comparison is now Gus-only.
export function comparePlayerAssetReports(reports, expected = null) {
  return comparePlayerAssetReport(reports?.gus || null, expected)
}

export function normalizeReviewLayout(raw) {
  const layout = raw && typeof raw === 'object' ? raw : {}
  const id = String(objectValue(layout, ['id', 'layoutId', 'layout_id']) || '')
  const columns = Number(objectValue(layout, ['columns', 'width']))
  const rows = Number(objectValue(layout, ['rows', 'height']))
  return {
    id,
    label: String(objectValue(layout, ['displayLabel', 'display_label', 'label', 'name']) || id),
    stage: String(objectValue(layout, ['stage', 'phase']) || '—'),
    columns: Number.isInteger(columns) ? columns : null,
    rows: Number.isInteger(rows) ? rows : null,
    requiredPackId: String(objectValue(layout, ['requiredPackId', 'required_pack_id', 'packId']) || ''),
    available: Boolean(objectValue(layout, ['available', 'isAvailable'])),
    reason: String(objectValue(layout, ['reason', 'unavailableReason', 'unavailable_reason']) || ''),
  }
}

export function formatGridSize(columns, rows) {
  return Number.isInteger(columns) && Number.isInteger(rows) ? `${columns}×${rows}` : ''
}

// The picker only ever lists maps the active pack can build, so an option
// names the map and its grid — never the asset pack behind it.
export function reviewLayoutOptionLabel(layout) {
  const normalized = normalizeReviewLayout(layout)
  const name = normalized.label || normalized.id
  const size = formatGridSize(normalized.columns, normalized.rows)
  return size ? `${name} · ${size}` : name
}

// Newest-pack-wins, matching the convention in assets.mjs. Ranked numerically
// rather than lexically, so a two-digit generation beats a one-digit one.
export function newestPackId(packIds) {
  let newest = ''
  let newestRank = -1
  for (const packId of packIds) {
    const match = /^core-v(\d+)$/.exec(packId)
    const rank = match ? Number(match[1]) : -1
    if (rank > newestRank || (rank === newestRank && packId > newest)) {
      newest = packId
      newestRank = rank
    }
  }
  return newest
}

function normalizePlayer(raw, index, previous) {
  const player = raw && typeof raw === 'object' ? raw : {}
  return {
    key: playerKey(player, index),
    id: String(objectValue(player, ['id', 'playerId', 'player_id']) || previous?.id || playerKey(player, index)),
    name: String(objectValue(player, ['name', 'displayName', 'playerName']) || previous?.name || playerKey(player, index)),
    color: String(objectValue(player, ['color', 'colour']) || previous?.color || ''),
    token: String(objectValue(player, ['token', 'playerToken', 'player_token', 'launchToken']) || previous?.token || ''),
    url: String(objectValue(player, ['url', 'playerUrl', 'launchUrl']) || previous?.url || './index.html'),
  }
}

function normalizePlayers(payload, previousPlayers = []) {
  const root = payload?.run && typeof payload.run === 'object' ? payload.run : payload
  let rawPlayers = payload?.players ?? root?.players ?? []
  if (!Array.isArray(rawPlayers) && rawPlayers && typeof rawPlayers === 'object') {
    rawPlayers = Object.entries(rawPlayers).map(([key, value]) => ({ key, ...(value || {}) }))
  }
  const normalized = (Array.isArray(rawPlayers) ? rawPlayers : []).map((player, index) => {
    const key = playerKey(player, index)
    return normalizePlayer(player, index, previousPlayers.find((item) => item.key === key))
  })
  const gus = normalized.find((player) => player.key === 'gus' || player.name.toLowerCase() === 'gus')
  const previousGus = previousPlayers.find((player) => player.key === 'gus' || player.name.toLowerCase() === 'gus')
  if (!gus && !previousGus) return []
  return [{ ...(previousGus || {}), ...(gus || {}), key: 'gus' }]
}

function normalizeRun(payload, previous = null, fallbackControllerToken = '') {
  const root = payload?.run && typeof payload.run === 'object' ? payload.run : (payload || {})
  const id = String(objectValue(root, ['id', 'runId', 'run_id']) || previous?.id || '')
  const controllerToken = String(
    objectValue(payload, ['controllerToken', 'adminToken', 'controller_token'])
    || objectValue(root, ['controllerToken', 'adminToken', 'controller_token'])
    || previous?.controllerToken
    || fallbackControllerToken
    || '',
  )
  const revisionValue = objectValue(root, ['revision', 'version', 'rev'])
  const speedValue = Number(objectValue(root, ['speed', 'playbackSpeed']) ?? previous?.speed ?? 1)
  return {
    id,
    controllerToken,
    day: objectValue(root, ['day', 'businessDate', 'business_date']) ?? previous?.day ?? null,
    revision: Number.isFinite(Number(revisionValue)) ? Number(revisionValue) : Number(previous?.revision || 0),
    paused: Boolean(objectValue(root, ['paused', 'isPaused']) ?? previous?.paused ?? false),
    speed: Number.isFinite(speedValue) ? speedValue : 1,
    forcedWheel: objectValue(root, ['forcedWheel', 'forced_wheel', 'wheelReward']) ?? previous?.forcedWheel ?? null,
    assetPack: objectValue(root, ['assetPack', 'asset_pack']) ?? previous?.assetPack ?? null,
    worldLayout: objectValue(root, ['worldLayout', 'world_layout', 'layout']) ?? previous?.worldLayout ?? null,
    activity: objectValue(payload, ['activity', 'playerActivity', 'player_activity'])
      ?? objectValue(root, ['activity', 'playerActivity', 'player_activity'])
      ?? previous?.activity
      ?? null,
    players: normalizePlayers(payload, previous?.players || []),
    reviewUrl: String(objectValue(payload, ['reviewUrl', 'review_url']) || previous?.reviewUrl || ''),
  }
}

export function sanitizeStoredReviewRun(raw) {
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    const normalized = normalizeRun(parsed)
    return normalized.id ? normalized : null
  } catch {
    return null
  }
}

export function sweepStoredReviewRuns(storage = null) {
  const result = { migrated: 0, removed: 0 }
  let activeStorage = storage
  if (!activeStorage) {
    try { activeStorage = globalThis.sessionStorage } catch { return result }
  }
  if (!activeStorage) return result
  try {
    const keys = []
    for (let index = 0; index < activeStorage.length; index += 1) {
      const key = activeStorage.key(index)
      if (key?.startsWith(SESSION_RUN_PREFIX)) keys.push(key)
    }
    for (const key of keys) {
      const normalized = sanitizeStoredReviewRun(activeStorage.getItem(key))
      if (!normalized) {
        activeStorage.removeItem(key)
        result.removed += 1
        continue
      }
      activeStorage.setItem(key, JSON.stringify(normalized))
      result.migrated += 1
    }
  } catch {
    // Session storage can be unavailable in hardened/private browser contexts.
  }
  return result
}

function arrayValue(source, keys) {
  const value = objectValue(source, keys)
  return Array.isArray(value) ? value : []
}

export function worldLayoutIdentity(run) {
  const layout = run?.worldLayout && typeof run.worldLayout === 'object' ? run.worldLayout : null
  return {
    id: String(objectValue(layout, ['id', 'layoutId', 'layout_id']) || ''),
    sha256: String(objectValue(layout, ['sha256', 'layoutSha256', 'layout_sha256']) || ''),
    label: String(objectValue(layout, ['label', 'name']) || ''),
    stage: String(objectValue(layout, ['stage', 'phase']) || '—'),
    columns: Number(objectValue(layout, ['columns', 'width'])),
    rows: Number(objectValue(layout, ['rows', 'height'])),
    placements: arrayValue(layout, ['placements', 'furniture', 'objects']),
    blockedCells: arrayValue(layout, ['blockedCells', 'blocked_cells', 'blocked']),
    spawnPoints: arrayValue(layout, ['spawnPoints', 'spawn_points', 'spawns']),
    interactionPoints: arrayValue(layout, ['workSeats', 'work_seats', 'interactionPoints', 'interaction_points', 'interactions']),
    origin: objectValue(layout, ['origin']) || null,
  }
}

function authoritativeAssetBinding(run) {
  const asset = run?.assetPack && typeof run.assetPack === 'object' ? run.assetPack : null
  const layout = worldLayoutIdentity(run)
  if (!asset && !run?.worldLayout) return { bound: false, layoutId: 'legacy' }
  return {
    bound: Boolean(asset || run?.worldLayout),
    packId: String(objectValue(asset, ['packId', 'pack_id', 'id']) || ''),
    releaseId: String(objectValue(asset, ['releaseId', 'release_id']) || ''),
    manifestSha256: String(objectValue(asset, ['manifestSha256', 'manifest_sha256', 'manifestHash']) || ''),
    atlasSha256: String(objectValue(asset, ['atlasSha256', 'atlas_sha256', 'atlasHash']) || ''),
    layoutId: layout.id,
    layoutSha256: layout.sha256,
  }
}

function normalizeActivity(value) {
  if (!value || typeof value !== 'object') return null
  const type = String(objectValue(value, ['type', 'activityType', 'activity_type']) || '')
  if (!type || type === 'idle') return null
  return {
    type,
    placementId: String(objectValue(value, ['placementId', 'placement_id']) || ''),
    seatId: String(objectValue(value, ['seatId', 'seat_id']) || ''),
    facing: String(objectValue(value, ['facing', 'direction']) || ''),
    phase: String(objectValue(value, ['phase', 'state']) || ''),
  }
}

function normalizedCamera(value) {
  if (!value || typeof value !== 'object') return null
  const x = Number(value.x)
  const y = Number(value.y)
  const zoom = Number(value.zoom)
  if (![x, y, zoom].every(Number.isFinite) || zoom <= 0) return null
  return { x, y, zoom }
}

function normalizedTarget(value) {
  if (!value || typeof value !== 'object') return null
  const x = Number(value.x ?? value.tileX)
  const y = Number(value.y ?? value.tileY)
  if (![x, y].every(Number.isFinite)) return null
  return { x, y }
}

function normalizedSeatOccupancy(value) {
  if (!Array.isArray(value)) return []
  return value.slice(0, 64).map((entry) => ({
    placementId: String(objectValue(entry, ['placementId', 'placement_id']) || ''),
    seatId: String(objectValue(entry, ['seatId', 'seat_id']) || ''),
    playerId: String(objectValue(entry, ['playerId', 'player_id']) || ''),
    state: String(objectValue(entry, ['state', 'phase']) || ''),
  })).filter((entry) => entry.placementId && entry.seatId && entry.playerId)
}

function suppliedValue(sources, keys) {
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(source, key)) return { supplied: true, value: source[key] }
    }
  }
  return { supplied: false, value: undefined }
}

export function normalizePlayerDirectorState(message, previous = null) {
  const fallback = previous && typeof previous === 'object' ? previous : {}
  const nested = message?.state && typeof message.state === 'object' ? message.state : null
  const sources = [nested, message]
  const camera = suppliedValue(sources, ['camera'])
  const target = suppliedValue(sources, ['target'])
  const seats = suppliedValue(sources, ['seatOccupancy', 'seat_occupancy'])
  return {
    camera: camera.supplied ? normalizedCamera(camera.value) : normalizedCamera(fallback.camera),
    target: target.supplied ? normalizedTarget(target.value) : normalizedTarget(fallback.target),
    seatOccupancy: seats.supplied
      ? normalizedSeatOccupancy(seats.value)
      : normalizedSeatOccupancy(fallback.seatOccupancy),
  }
}

function redactSensitive(value, seen = new WeakSet()) {
  if (value === null || value === undefined) return value
  if (typeof value !== 'object') return value
  if (seen.has(value)) return '[circular]'
  seen.add(value)
  if (Array.isArray(value)) return value.map((item) => redactSensitive(item, seen))
  const output = {}
  for (const [key, item] of Object.entries(value)) {
    if (/token|secret|authorization|cookie|password/i.test(key)) output[key] = '[redacted]'
    else output[key] = redactSensitive(item, seen)
  }
  return output
}

function safeJson(value, maximum = 1800) {
  let text
  try { text = JSON.stringify(redactSensitive(value)) } catch { text = String(value) }
  if (text.length > maximum) return `${text.slice(0, maximum)}…`
  return text
}

function extractResponseEvents(payload) {
  const root = payload?.run && typeof payload.run === 'object' ? payload.run : payload
  const candidates = payload?.events ?? root?.events
  return Array.isArray(candidates) ? candidates : []
}

class ReviewDirector {
  constructor(documentRef) {
    this.document = documentRef
    this.api = new ReviewApi(
      documentRef.querySelector('meta[name="review-api-base"]')?.content
      || documentRef.documentElement.dataset.reviewApiBase
      || '/api/review',
    )
    this.run = null
    this.bootstrapToken = safeSessionGet(SESSION_BOOTSTRAP_TOKEN) || ''
    this.layouts = []
    this.layoutIndex = new Map()
    this.missingPackIds = []
    this.layoutError = ''
    this.selectedLayoutId = ''
    this.viewportKey = 'desktop'
    this.playback = reducePlayback(null, null)
    this.overlays = Object.fromEntries(OVERLAY_KEYS.map((key) => [key, false]))
    this.cameraPreset = 'full'
    this.activity = null
    this.delayMs = 0
    this.events = []
    this.seenEventIds = new Set()
    this.positionLogAt = new Map()
    this.busy = false
    this.pollTimer = null
    this.pollFailures = 0
    this.toastTimer = null
    this.resizeObserver = null
    this.frameOrigins = new Map()
    this.playerAssets = { gus: null }
    this.playerDirectorState = normalizePlayerDirectorState(null)
    this.pendingResetAction = ''
    this.elements = this.collectElements()
  }

  collectElements() {
    const id = (value) => this.document.getElementById(value)
    return {
      connectionBadge: id('connectionBadge'),
      runIdLabel: id('runIdLabel'),
      revisionLabel: id('revisionLabel'),
      createRunButton: id('createRunButton'),
      layoutSelect: id('layoutSelect'),
      layoutAvailability: id('layoutAvailability'),
      resetRunButton: id('resetRunButton'),
      copyFeedbackButton: id('copyFeedbackButton'),
      viewportPresets: id('viewportPresets'),
      viewportDescription: id('viewportDescription'),
      speedControls: id('speedControls'),
      pauseButton: id('pauseButton'),
      replayButton: id('replayButton'),
      wheelForm: id('wheelForm'),
      wheelValue: id('wheelValue'),
      advanceDayButton: id('advanceDayButton'),
      resetDailyButton: id('resetDailyButton'),
      delayInput: id('delayInput'),
      delayOutput: id('delayOutput'),
      advancedControls: id('advancedControls'),
      assetHashBadge: id('assetHashBadge'),
      assetHashDetails: id('assetHashDetails'),
      cameraPresets: id('cameraPresets'),
      playerGrid: id('playerGrid'),
      mapStageBadge: id('mapStageBadge'),
      mapIdLabel: id('mapIdLabel'),
      mapStageIdLabel: id('mapStageIdLabel'),
      mapSizeLabel: id('mapSizeLabel'),
      layoutShaLabel: id('layoutShaLabel'),
      assetReleaseLabel: id('assetReleaseLabel'),
      placementCountLabel: id('placementCountLabel'),
      blockedCountLabel: id('blockedCountLabel'),
      spawnSummary: id('spawnSummary'),
      playerActivityLabel: id('playerActivityLabel'),
      resetConfirmDialog: id('resetConfirmDialog'),
      resetConfirmTitle: id('resetConfirmTitle'),
      resetConfirmCopy: id('resetConfirmCopy'),
      confirmResetButton: id('confirmResetButton'),
      cancelResetButton: id('cancelResetButton'),
      toast: id('toast'),
      frames: {
        gus: id('gusFrame'),
      },
    }
  }

  async start() {
    this.consumeFragmentCredentials()
    sweepStoredReviewRuns()
    this.bindEvents()
    this.observeViewportHosts()
    this.applyViewport('desktop')
    this.applyCameraPreset('full', { post: false, log: false })
    this.renderAssetIntegrity()
    this.renderMapDiagnostics()
    this.updateControls()
    await this.loadLayouts()
    const restored = this.restoreRun()
    if (!restored) {
      this.setStatus('尚未创建验收局', 'idle')
      return
    }
    this.setRun(restored, { reloadFrames: true, persist: false })
    this.setStatus('正在恢复验收局', 'working')
    try {
      const payload = await this.api.getRun(this.run)
      this.acceptResponse(payload, 'director.restore', { log: false, reloadFrames: true })
      this.appendEvent({ source: 'director', name: 'run.restored', revision: this.run.revision, data: { runId: this.run.id } })
      this.setStatus('验收局已连接', 'ready')
      this.startPolling()
    } catch (error) {
      this.setStatus('验收局恢复失败', 'error')
      this.appendError('run.restore_failed', error)
    }
  }

  consumeFragmentCredentials() {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const query = new URLSearchParams(window.location.search)
    const runId = query.get('run') || query.get('reviewRun') || hash.get('run') || hash.get('reviewRun') || ''
    const suppliedToken = hash.get('adminToken') || hash.get('controllerToken') || hash.get('token') || ''
    if (suppliedToken) {
      if (runId) {
        const prior = this.readStoredRun(runId) || { id: runId, players: [] }
        prior.controllerToken = suppliedToken
        this.writeStoredRun(prior)
      } else {
        this.bootstrapToken = suppliedToken
        safeSessionSet(SESSION_BOOTSTRAP_TOKEN, suppliedToken)
      }
    }
    if (suppliedToken || hash.has('adminToken') || hash.has('controllerToken') || hash.has('token')) {
      hash.delete('adminToken')
      hash.delete('controllerToken')
      hash.delete('token')
      hash.delete('run')
      const cleanHash = hash.toString()
      const cleanUrl = `${window.location.pathname}${window.location.search}${cleanHash ? `#${cleanHash}` : ''}`
      window.history.replaceState(null, '', cleanUrl)
    }
    if (runId) safeSessionSet(SESSION_ACTIVE_RUN, runId)
  }

  bindEvents() {
    this.elements.createRunButton.addEventListener('click', () => this.createRun())
    this.elements.layoutSelect.addEventListener('change', () => {
      this.selectedLayoutId = this.elements.layoutSelect.value
      this.renderLayoutAvailability()
      this.updateControls()
    })
    this.elements.resetRunButton.addEventListener('click', () => this.openResetConfirmation('run'))
    this.elements.copyFeedbackButton.addEventListener('click', () => this.copyFeedback())
    this.elements.viewportPresets.addEventListener('click', (event) => {
      const button = event.target.closest('[data-viewport]')
      if (button) this.applyViewport(button.dataset.viewport)
    })
    this.elements.speedControls.addEventListener('click', (event) => {
      const button = event.target.closest('[data-speed]')
      if (button) this.setSpeed(Number(button.dataset.speed))
    })
    this.elements.pauseButton.addEventListener('click', () => this.setPaused(!this.playback.paused))
    this.elements.replayButton.addEventListener('click', () => this.replayLast())
    this.elements.wheelForm.addEventListener('submit', (event) => {
      event.preventDefault()
      const raw = this.elements.wheelValue.value
      this.forceWheel(raw === '' ? null : Number(raw))
    })
    this.elements.advanceDayButton.addEventListener('click', () => this.advanceDay())
    this.elements.resetDailyButton.addEventListener('click', () => this.openResetConfirmation('daily'))
    this.elements.cameraPresets.addEventListener('click', (event) => {
      const button = event.target.closest('[data-camera]')
      if (button) this.applyCameraPreset(button.dataset.camera)
    })
    this.elements.confirmResetButton.addEventListener('click', () => this.confirmReset())
    this.elements.cancelResetButton.addEventListener('click', () => this.closeResetConfirmation())
    this.elements.resetConfirmDialog.addEventListener('click', (event) => {
      if (event.target === this.elements.resetConfirmDialog) this.closeResetConfirmation()
    })
    this.elements.resetConfirmDialog.addEventListener('cancel', () => {
      this.pendingResetAction = ''
    })
    this.document.querySelectorAll('[data-overlay]').forEach((button) => {
      button.addEventListener('click', () => this.toggleOverlay(button.dataset.overlay))
    })
    this.elements.delayInput.addEventListener('input', () => {
      this.elements.delayOutput.value = `${this.elements.delayInput.value} ms`
    })
    this.elements.delayInput.addEventListener('change', () => this.setDelay(Number(this.elements.delayInput.value)))
    window.addEventListener('message', (event) => this.receivePlayerMessage(event))
    window.addEventListener('resize', () => this.resizeFrames())
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return
      // The active pack can change in the 资产验收台 tab while this one sits
      // in the background; refetch so the picker never advertises a stale map.
      this.loadLayouts()
      if (this.run) this.pollRun()
    })
    for (const key of PLAYER_KEYS) {
      this.elements.frames[key].addEventListener('load', () => this.onFrameLoad(key))
    }
  }

  observeViewportHosts() {
    if (!globalThis.ResizeObserver) return
    this.resizeObserver = new ResizeObserver(() => this.resizeFrames())
    this.document.querySelectorAll('[data-viewport-host]').forEach((host) => this.resizeObserver.observe(host))
  }

  async loadLayouts() {
    this.elements.layoutSelect.disabled = true
    try {
      const payload = await this.api.getLayouts(this.bootstrapToken)
      const source = Array.isArray(payload) ? payload : arrayValue(payload, ['layouts', 'items', 'data'])
      const all = source.map(normalizeReviewLayout).filter((layout) => layout.id)
      // Every generation stays in the index so a restored run can still resolve
      // its own map name, but only the active pack's maps reach the picker.
      this.layoutIndex = new Map(all.map((layout) => [layout.id, layout]))
      this.layouts = all.filter((layout) => layout.available)
      this.missingPackIds = [...new Set(
        all.filter((layout) => !layout.available && layout.requiredPackId)
          .map((layout) => layout.requiredPackId),
      )].sort()
      this.layoutError = ''
    } catch (error) {
      this.layoutIndex = new Map()
      this.layouts = []
      this.missingPackIds = []
      this.layoutError = error?.message || '未知错误'
      this.appendError('layouts.load_failed', error)
    } finally {
      this.renderLayoutOptions()
      this.renderMapDiagnostics()
      this.updateControls()
    }
  }

  renderLayoutOptions() {
    const priorSelection = this.selectedLayoutId
    const placeholder = this.document.createElement('option')
    placeholder.value = ''
    placeholder.textContent = this.layoutError
      ? '无法读取地图'
      : (this.layouts.length ? '请选择验收地图' : '暂无可用地图')
    const options = this.layouts.map((layout) => {
      const option = this.document.createElement('option')
      option.value = layout.id
      option.textContent = reviewLayoutOptionLabel(layout)
      return option
    })
    this.elements.layoutSelect.replaceChildren(placeholder, ...options)
    // No auto-select: the placeholder is always option[0] and always the
    // fallback, so nothing ever picks a map on the user's behalf.
    this.selectedLayoutId = priorSelection && this.layouts.some((layout) => layout.id === priorSelection)
      ? priorSelection
      : ''
    this.elements.layoutSelect.value = this.selectedLayoutId
    this.renderLayoutAvailability()
  }

  selectedLayout() {
    return this.layouts.find((layout) => layout.id === this.selectedLayoutId) || null
  }

  renderLayoutAvailability() {
    const target = this.elements.layoutAvailability
    target.classList.remove('is-error', 'is-notice')
    if (this.layoutError) {
      target.classList.add('is-error')
      target.textContent = `地图列表读取失败：${this.layoutError}。请刷新页面或稍后重试。`
      return
    }
    // The happy path says nothing at all. Every map on offer is buildable, so
    // there is no availability to explain.
    if (this.layouts.length) {
      target.replaceChildren()
      return
    }
    target.classList.add('is-notice')
    const newest = newestPackId(this.missingPackIds)
    const sentence = this.document.createElement('span')
    // Worded to hold whether a pack is active but map-less, or none is active
    // at all — the client cannot tell the two apart and does not need to.
    sentence.textContent = newest
      ? `当前没有可用的验收地图。请到资产验收台激活 ${newest}，然后回到本页重新读取地图。`
      : '当前没有可创建的验收地图，请到资产验收台检查资产包配置。'
    const actions = this.document.createElement('span')
    actions.className = 'layout-notice-actions'
    const link = this.document.createElement('a')
    link.className = 'workspace-link'
    link.href = '/assets'
    link.textContent = '打开资产验收台'
    const retry = this.document.createElement('button')
    retry.type = 'button'
    retry.className = 'button'
    retry.textContent = '重新读取地图'
    retry.addEventListener('click', () => this.loadLayouts())
    actions.replaceChildren(link, retry)
    target.replaceChildren(sentence, actions)
  }

  restoreRun() {
    const query = new URLSearchParams(window.location.search)
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const runId = query.get('run') || query.get('reviewRun') || hash.get('reviewRun') || safeSessionGet(SESSION_ACTIVE_RUN)
    return runId ? this.readStoredRun(runId) : null
  }

  readStoredRun(runId) {
    const raw = safeSessionGet(`${SESSION_RUN_PREFIX}${runId}`)
    if (!raw) return null
    const normalized = sanitizeStoredReviewRun(raw)
    if (!normalized) {
      safeSessionRemove(`${SESSION_RUN_PREFIX}${runId}`)
      return null
    }
    safeSessionSet(`${SESSION_RUN_PREFIX}${runId}`, JSON.stringify(normalized))
    return normalized
  }

  writeStoredRun(run) {
    const normalized = sanitizeStoredReviewRun(run)
    if (!normalized) return
    safeSessionSet(SESSION_ACTIVE_RUN, normalized.id)
    safeSessionSet(`${SESSION_RUN_PREFIX}${normalized.id}`, JSON.stringify(normalized))
  }

  updateLocation(runId) {
    const url = new URL(window.location.href)
    url.searchParams.set('run', runId)
    url.hash = ''
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }

  setRun(run, { reloadFrames = false, persist = true } = {}) {
    this.run = run
    this.activity = normalizeActivity(run.activity) || this.activity
    this.playback = {
      ...this.playback,
      speed: PLAYBACK_SPEEDS.includes(Number(run.speed)) ? Number(run.speed) : this.playback.speed,
      paused: Boolean(run.paused),
    }
    if (persist) this.writeStoredRun(run)
    this.elements.runIdLabel.textContent = run.id || '—'
    this.setRevision(run.revision)
    this.renderPlaybackControls()
    this.renderMapDiagnostics()
    this.renderAssetIntegrity()
    this.renderActivity()
    this.updateControls()
    if (reloadFrames) {
      this.playerAssets = { gus: null }
      this.playerDirectorState = normalizePlayerDirectorState(null)
      this.activity = null
      this.renderAssetIntegrity()
      this.renderActivity()
      this.loadPlayerFrames()
    }
  }

  acceptResponse(payload, source, { log = true, reloadFrames = false } = {}) {
    const priorRevision = this.run?.revision || 0
    const run = normalizeRun(payload, this.run)
    if (!run.id || !run.controllerToken) throw new Error('验收接口未返回 run 或当前 run 的 controllerToken')
    this.setRun(run, { reloadFrames, persist: true })
    if (log) {
      this.appendEvent({
        source: 'backend',
        name: source,
        revision: run.revision,
        data: {
          runId: run.id,
          day: run.day,
          paused: run.paused,
          speed: run.speed,
          forcedWheel: run.forcedWheel,
        },
      })
    }
    for (const event of extractResponseEvents(payload)) this.appendBackendEvent(event)
    if (run.revision > priorRevision && source === 'run.poll') {
      this.appendEvent({ source: 'backend', name: 'revision.changed', revision: run.revision, data: { from: priorRevision, to: run.revision } })
    }
    return run
  }

  async createRun() {
    if (this.busy) return
    const layout = this.selectedLayout()
    if (!layout) {
      this.toast('请先选择一张验收地图', true)
      return
    }
    this.setBusy(true)
    this.setStatus('正在创建隔离验收局', 'working')
    try {
      const priorRunId = this.run?.id || safeSessionGet(SESSION_ACTIVE_RUN)
      const payload = await this.api.createRun(this.bootstrapToken, layout.id)
      const run = normalizeRun(payload)
      if (!run.id || !run.controllerToken) throw new Error('创建响应缺少 run 或每局随机 controllerToken')
      if (!run.players.some((player) => player.key === 'gus' && player.token)) {
        throw new Error('创建响应未包含 Gus 玩家令牌')
      }
      this.stopPolling()
      this.resetEventTrace()
      if (priorRunId && priorRunId !== run.id) safeSessionRemove(`${SESSION_RUN_PREFIX}${priorRunId}`)
      this.bootstrapToken = ''
      safeSessionRemove(SESSION_BOOTSTRAP_TOKEN)
      this.updateLocation(run.id)
      this.setRun(run, { reloadFrames: true, persist: true })
      this.appendEvent({ source: 'director', name: 'run.created', revision: run.revision, data: { runId: run.id, player: 'Gus', layoutId: layout.id } })
      this.setStatus('验收局已连接', 'ready')
      this.startPolling()
    } catch (error) {
      this.setStatus('创建验收局失败', 'error')
      this.appendError('run.create_failed', error)
      this.toast(error.message, true)
      // 409 means the active pack moved under us; refetch so the picker
      // stops advertising maps the server will no longer build.
      if (error?.status === 409) await this.loadLayouts()
    } finally {
      this.setBusy(false)
    }
  }

  async resetRun() {
    if (!this.run || this.busy) return
    this.setBusy(true)
    this.setStatus('正在重置当前验收局', 'working')
    try {
      const payload = await this.api.resetRun(this.run)
      this.acceptResponse(payload, 'run.reset', { reloadFrames: true })
      this.setStatus('当前验收局已重置', 'ready')
      this.toast('隔离验收局已归零')
    } catch (error) {
      this.handleCommandError('run.reset_failed', error)
    } finally {
      this.setBusy(false)
    }
  }

  openResetConfirmation(action) {
    if (!this.run || this.busy || !['run', 'daily'].includes(action)) return
    this.pendingResetAction = action
    const isRun = action === 'run'
    this.elements.resetConfirmTitle.textContent = isRun ? '重置当前验收局？' : '重置今日状态？'
    this.elements.resetConfirmCopy.textContent = isRun
      ? '将隔离局的玩家位置、余额、工作座位和每日操作归零，并重新载入 Gus 页面。已绑定的地图与资产版本不变。'
      : '只重开当天的签到与好人卡操作，不改变局、玩家位置或资产绑定。'
    this.elements.confirmResetButton.textContent = isRun ? '确认重置当前局' : '确认重置今日'
    if (typeof this.elements.resetConfirmDialog.showModal === 'function') this.elements.resetConfirmDialog.showModal()
    else this.elements.resetConfirmDialog.setAttribute('open', '')
  }

  closeResetConfirmation() {
    this.pendingResetAction = ''
    if (typeof this.elements.resetConfirmDialog.close === 'function') this.elements.resetConfirmDialog.close()
    else this.elements.resetConfirmDialog.removeAttribute('open')
  }

  async confirmReset() {
    const action = this.pendingResetAction
    if (!action || this.busy) return
    this.closeResetConfirmation()
    if (action === 'run') await this.resetRun()
    else await this.resetDaily()
  }

  async setSpeed(speed) {
    if (!this.run || this.busy) return
    const next = reducePlayback(this.playback, { type: 'set-speed', value: speed })
    this.setBusy(true)
    try {
      const payload = await this.api.setSpeed(this.run, speed)
      this.acceptResponse(payload, 'playback.speed')
      this.playback = next
      this.renderPlaybackControls()
      this.postToPlayers('speed', speed)
    } catch (error) {
      this.handleCommandError('playback.speed_failed', error)
    } finally {
      this.setBusy(false)
    }
  }

  async setPaused(paused) {
    if (!this.run || this.busy) return
    this.setBusy(true)
    try {
      const payload = await this.api.setPaused(this.run, paused)
      this.acceptResponse(payload, paused ? 'playback.paused' : 'playback.resumed')
      this.playback = reducePlayback(this.playback, { type: paused ? 'pause' : 'resume' })
      this.renderPlaybackControls()
      this.postToPlayers('pause', paused, { action: paused ? 'pause' : 'resume' })
    } catch (error) {
      this.handleCommandError('playback.pause_failed', error)
    } finally {
      this.setBusy(false)
    }
  }

  replayLast() {
    if (!this.run) return
    this.playback = reducePlayback(this.playback, { type: 'replay-last' })
    this.postToPlayers('replay', 'last', { action: 'last', replayNonce: this.playback.replayNonce })
    this.appendEvent({ source: 'director', name: 'playback.replay_last', revision: this.run.revision, data: { replayNonce: this.playback.replayNonce } })
  }

  async forceWheel(reward) {
    if (!this.run || this.busy) return
    const allowed = [null, 1, 2, 3, 5, 10, 20]
    if (!allowed.includes(reward)) {
      this.toast('转盘值必须为 1、2、3、5、10、20 或正常随机', true)
      return
    }
    await this.executeDurable('wheel.force', () => this.api.forceWheel(this.run, reward), {
      reward,
      mode: reward === null ? 'random' : 'forced',
    })
  }

  async advanceDay() {
    await this.executeDurable('clock.advance_day', () => this.api.advanceDay(this.run), { days: 1 })
  }

  async resetDaily() {
    await this.executeDurable('daily.reset', () => this.api.resetDaily(this.run), {})
  }

  async executeDurable(name, operation, requestData) {
    if (!this.run || this.busy) return
    this.setBusy(true)
    this.setStatus('正在执行验收指令', 'working')
    try {
      const payload = await operation()
      this.acceptResponse(payload, name)
      this.setStatus('验收局已连接', 'ready')
      this.appendEvent({ source: 'director', name: `${name}.requested`, revision: this.run.revision, data: requestData })
    } catch (error) {
      this.handleCommandError(`${name}_failed`, error)
    } finally {
      this.setBusy(false)
    }
  }

  toggleOverlay(name) {
    if (!(name in this.overlays)) return
    this.overlays[name] = !this.overlays[name]
    const button = this.document.querySelector(`[data-overlay="${name}"]`)
    button?.setAttribute('aria-pressed', String(this.overlays[name]))
    this.postToPlayers('overlays', { ...this.overlays })
    this.appendEvent({ source: 'director', name: 'overlay.changed', revision: this.run?.revision || 0, data: { ...this.overlays } })
  }

  setDelay(delayMs) {
    this.delayMs = Math.max(0, Math.min(2000, Math.round(Number(delayMs) || 0)))
    this.elements.delayInput.value = String(this.delayMs)
    this.elements.delayOutput.value = `${this.delayMs} ms`
    this.postToPlayers('delay', this.delayMs)
    this.appendEvent({ source: 'director', name: 'network.delay', revision: this.run?.revision || 0, data: { delayMs: this.delayMs } })
  }

  postToPlayers(type, value, extra = {}, { log = false } = {}) {
    if (!this.run) return
    const message = {
      channel: 'codex-review',
      type,
      value,
      ...extra,
      runId: this.run.id,
      revision: this.run.revision,
      commandId: commandId(type),
    }
    for (const key of PLAYER_KEYS) {
      const frame = this.elements.frames[key]
      if (!frame?.contentWindow || frame.src === 'about:blank') continue
      frame.contentWindow.postMessage(message, this.frameOrigins.get(key) || window.location.origin)
    }
    if (log) this.appendEvent({ source: 'director', name: `postmessage.${type}`, revision: this.run.revision, data: { value, ...extra } })
  }

  sendCurrentPlayerSettings(key) {
    const frame = this.elements.frames[key]
    if (!this.run || !frame?.contentWindow || frame.src === 'about:blank') return
    const targetOrigin = this.frameOrigins.get(key) || window.location.origin
    const common = { channel: 'codex-review', runId: this.run.id, revision: this.run.revision }
    frame.contentWindow.postMessage({ ...common, type: 'speed', value: this.playback.speed }, targetOrigin)
    frame.contentWindow.postMessage({ ...common, type: 'pause', value: this.playback.paused, action: this.playback.paused ? 'pause' : 'resume' }, targetOrigin)
    frame.contentWindow.postMessage({ ...common, type: 'overlays', value: { ...this.overlays } }, targetOrigin)
    frame.contentWindow.postMessage({ ...common, type: 'camera', value: { preset: this.cameraPreset } }, targetOrigin)
    frame.contentWindow.postMessage({ ...common, type: 'delay', value: this.delayMs }, targetOrigin)
  }

  applyCameraPreset(preset, { post = true, log = true } = {}) {
    if (!CAMERA_PRESETS.includes(preset)) return
    this.cameraPreset = preset
    for (const button of this.elements.cameraPresets.querySelectorAll('[data-camera]')) {
      button.setAttribute('aria-pressed', String(button.dataset.camera === preset))
    }
    if (post) this.postToPlayers('camera', { preset })
    if (log && this.run) {
      this.appendEvent({ source: 'director', name: 'camera.changed', revision: this.run.revision, data: { preset } })
    }
  }

  renderAssetIntegrity() {
    const expected = authoritativeAssetBinding(this.run)
    const comparison = comparePlayerAssetReport(this.playerAssets.gus, expected)
    this.elements.assetHashBadge.className = `asset-hash-badge is-${comparison.state}`
    this.elements.assetHashBadge.textContent = comparison.label
    const label = (report) => {
      if (!report) return 'Gus · 等待资产状态'
      if (!report.ready) return `Gus · 失败 · ${report.error || '未返回错误详情'}`
      if (report.legacy) return 'Gus · Legacy · 无资产绑定'
      const manifest = report.manifestSha256 ? `${report.manifestSha256.slice(0, 10)}…` : '无 manifest hash'
      const atlas = report.atlasSha256 ? `${report.atlasSha256.slice(0, 10)}…` : '无 atlas hash'
      const layout = report.layoutSha256 ? `${report.layoutSha256.slice(0, 10)}…` : '无 layout hash'
      return `Gus · ${report.packId || '无 pack'} · ${report.releaseId || '无 release'} · M ${manifest} · A ${atlas} · L ${layout} · ${report.layoutId || '无 layout'}`
    }
    const clientRow = this.document.createElement('span')
    clientRow.textContent = label(this.playerAssets.gus)
    const bindingRow = this.document.createElement('span')
    bindingRow.textContent = expected.bound
      ? `Run · ${expected.packId || '无 pack'} · ${expected.releaseId || '无 release'} · ${expected.layoutId || '无 layout'}`
      : 'Run · Legacy · 无冻结资产绑定'
    this.elements.assetHashDetails.replaceChildren(clientRow, bindingRow)
  }

  renderMapDiagnostics() {
    const layout = worldLayoutIdentity(this.run)
    const asset = authoritativeAssetBinding(this.run)
    const hasLayout = Boolean(layout.id)
    // Prefer the catalog's generation-free name; the frozen snapshot's own
    // label still carries the 'v3' suffix and is only a degraded fallback.
    const catalogName = this.layoutIndex.get(layout.id)?.label || ''
    this.elements.mapStageBadge.textContent = hasLayout
      ? (catalogName || layout.label || layout.id)
      : '未绑定'
    this.elements.mapStageBadge.classList.toggle('is-bound', hasLayout)
    this.elements.mapIdLabel.textContent = layout.id || '—'
    this.elements.mapStageIdLabel.textContent = hasLayout ? layout.stage : '—'
    this.elements.mapSizeLabel.textContent = formatGridSize(layout.columns, layout.rows) || '—'
    this.elements.layoutShaLabel.textContent = layout.sha256 || '—'
    this.elements.layoutShaLabel.title = layout.sha256 || ''
    this.elements.assetReleaseLabel.textContent = asset.releaseId || (asset.bound ? '绑定不完整' : '—')
    this.elements.placementCountLabel.textContent = hasLayout ? String(layout.placements.length) : '—'
    this.elements.blockedCountLabel.textContent = hasLayout ? String(layout.blockedCells.length) : '—'
    this.elements.spawnSummary.textContent = hasLayout
      ? layout.spawnPoints.map((spawn) => {
          const name = objectValue(spawn, ['name', 'playerName', 'playerId', 'player_id', 'id']) || '?'
          return `${name} (${Number(spawn.x)},${Number(spawn.y)})`
        }).join(' · ') || '未返回出生点'
      : '—'
  }

  renderActivity() {
    const activity = this.activity
    if (!activity) {
      this.elements.playerActivityLabel.textContent = '活动：空闲'
      return
    }
    const phase = activity.phase ? ` · ${activity.phase}` : ''
    const seat = activity.seatId ? ` · ${activity.seatId}` : ''
    const facing = activity.facing ? ` · ${activity.facing}` : ''
    this.elements.playerActivityLabel.textContent = `活动：${activity.type}${phase}${seat}${facing}`
  }

  receivePlayerMessage(event) {
    const key = PLAYER_KEYS.find((candidate) => this.elements.frames[candidate]?.contentWindow === event.source)
    if (!key) return
    const expectedOrigin = this.frameOrigins.get(key)
    const frameWindow = this.elements.frames[key]?.contentWindow
    if (!isTrustedPlayerMessage(event, frameWindow, expectedOrigin, this.run?.id)) return
    const message = event.data
    const revision = Number(message.revision)
    if (Number.isFinite(revision)) this.setRevision(Math.max(this.run?.revision || 0, revision))
    const assetReport = normalizePlayerAssetReport(message)
    if (assetReport) {
      this.playerAssets[key] = assetReport
      this.renderAssetIntegrity()
    }
    this.playerDirectorState = normalizePlayerDirectorState(message, this.playerDirectorState)
    const activitySources = [message, message.state, message.player, message.data]
    const activitySource = activitySources.find((source) => source && Object.prototype.hasOwnProperty.call(source, 'activity'))
    if (activitySource) {
      this.activity = normalizeActivity(activitySource.activity)
      this.renderActivity()
    }
    const status = this.document.querySelector(`[data-player-status="${key}"]`)
    if (message.type === 'ready' || message.type === 'connection') {
      status.textContent = message.connected === false ? '正在重连' : '已连接'
      status.classList.toggle('is-ready', message.connected !== false)
      status.classList.toggle('is-error', message.connected === false)
      if (message.type === 'ready') this.sendCurrentPlayerSettings(key)
    }
    if (message.type === 'event' && message.eventType === 'world.positions') {
      const now = performance.now()
      const lastLoggedAt = this.positionLogAt.get(key) || 0
      if (now - lastLoggedAt < 1_000) return
      this.positionLogAt.set(key, now)
    }
    this.appendEvent({
      source: key,
      name: `player.${message.type || 'message'}`,
      revision: Number.isFinite(revision) ? revision : this.run?.revision || 0,
      data: message,
      eventId: message.eventId || message.id,
    })
  }

  playerFor(key) {
    return this.run?.players.find((player) => player.key === key || player.name.toLowerCase() === key) || null
  }

  playerFrameUrl(player) {
    return buildPlayerFrameUrl(player, this.run.id, window.location.href)
  }

  loadPlayerFrames() {
    this.frameOrigins.clear()
    this.positionLogAt.clear()
    for (const key of PLAYER_KEYS) {
      const frame = this.elements.frames[key]
      const player = this.playerFor(key)
      const host = this.document.querySelector(`[data-viewport-host="${key}"]`)
      const status = this.document.querySelector(`[data-player-status="${key}"]`)
      if (!player?.token) {
        frame.src = 'about:blank'
        host.classList.remove('has-player')
        status.textContent = '缺少玩家令牌'
        status.classList.add('is-error')
        continue
      }
      const url = this.playerFrameUrl(player)
      this.frameOrigins.set(key, url.origin)
      status.textContent = '正在载入'
      status.classList.remove('is-ready', 'is-error')
      host.classList.add('has-player')
      frame.src = url.href
    }
    this.resizeFrames()
  }

  onFrameLoad(key) {
    const frame = this.elements.frames[key]
    if (!this.run || frame.src === 'about:blank') return
    const status = this.document.querySelector(`[data-player-status="${key}"]`)
    status.textContent = '页面已载入'
    status.classList.add('is-ready')
    this.sendCurrentPlayerSettings(key)
    this.appendEvent({ source: key, name: 'frame.loaded', revision: this.run.revision, data: { viewport: this.viewportKey } })
  }

  applyViewport(key) {
    if (!VIEWPORT_PRESETS[key]) return
    this.viewportKey = key
    for (const button of this.elements.viewportPresets.querySelectorAll('[data-viewport]')) {
      button.setAttribute('aria-pressed', String(button.dataset.viewport === key))
    }
    const preset = VIEWPORT_PRESETS[key]
    this.elements.viewportDescription.textContent = `实际视口 ${preset.width} × ${preset.height}，缩放后预览`
    this.resizeFrames()
    if (this.run) this.appendEvent({ source: 'director', name: 'viewport.changed', revision: this.run.revision, data: preset })
  }

  resizeFrames() {
    const preset = VIEWPORT_PRESETS[this.viewportKey]
    if (!preset) return
    for (const key of PLAYER_KEYS) {
      const host = this.document.querySelector(`[data-viewport-host="${key}"]`)
      const canvas = this.document.querySelector(`[data-viewport-canvas="${key}"]`)
      const frame = this.elements.frames[key]
      if (!host || !canvas || !frame) continue
      const projection = projectViewport(preset, Math.max(1, host.clientWidth - 24), 900)
      frame.style.width = `${projection.width}px`
      frame.style.height = `${projection.height}px`
      frame.style.transform = `scale(${projection.scale})`
      canvas.style.width = `${projection.projectedWidth}px`
      canvas.style.height = `${projection.projectedHeight}px`
      host.style.height = `${Math.max(320, projection.projectedHeight + 24)}px`
    }
  }

  renderPlaybackControls() {
    for (const button of this.elements.speedControls.querySelectorAll('[data-speed]')) {
      button.setAttribute('aria-pressed', String(Number(button.dataset.speed) === this.playback.speed))
    }
    this.elements.pauseButton.textContent = this.playback.paused ? '继续' : '暂停'
  }

  setBusy(busy) {
    this.busy = busy
    this.updateControls()
  }

  updateControls() {
    const hasRun = Boolean(this.run?.id && this.run?.controllerToken)
    const canCreate = Boolean(this.selectedLayout())
    this.elements.createRunButton.disabled = this.busy || !canCreate
    this.elements.layoutSelect.disabled = this.busy || !this.layouts.length
    this.elements.resetRunButton.disabled = !hasRun || this.busy
    this.elements.pauseButton.disabled = !hasRun || this.busy
    this.elements.replayButton.disabled = !hasRun || this.busy
    this.elements.advanceDayButton.disabled = !hasRun || this.busy
    this.elements.resetDailyButton.disabled = !hasRun || this.busy
    this.elements.confirmResetButton.disabled = this.busy
    this.elements.wheelForm.querySelector('button[type="submit"]').disabled = !hasRun || this.busy
    this.elements.speedControls.querySelectorAll('button').forEach((button) => { button.disabled = !hasRun || this.busy })
  }

  setStatus(text, kind) {
    const badge = this.elements.connectionBadge
    badge.className = `status-badge is-${kind}`
    badge.lastChild.textContent = text
  }

  setRevision(revision) {
    const normalized = Number.isFinite(Number(revision)) ? Number(revision) : 0
    if (this.run) this.run.revision = Math.max(this.run.revision || 0, normalized)
    this.elements.revisionLabel.textContent = String(this.run?.revision || normalized)
  }

  appendBackendEvent(event) {
    this.appendEvent({
      source: event.source || 'backend',
      name: event.type || event.name || event.event || 'backend.event',
      revision: event.revision ?? event.version ?? this.run?.revision ?? 0,
      data: event.payload ?? event.data ?? event,
      at: event.createdAt || event.created_at || event.at,
      eventId: event.id || event.eventId,
    })
  }

  appendEvent(event) {
    const safeEvent = redactSensitive({
      at: event.at || new Date().toISOString(),
      source: String(event.source || 'director'),
      name: String(event.name || 'event'),
      revision: Number.isFinite(Number(event.revision)) ? Number(event.revision) : 0,
      data: event.data ?? {},
      level: event.level || 'info',
    })
    const eventId = String(event.eventId || `${safeEvent.source}:${safeEvent.revision}:${safeEvent.name}:${safeJson(safeEvent.data, 240)}`)
    if (this.seenEventIds.has(eventId)) return
    this.seenEventIds.add(eventId)
    this.events.push(safeEvent)
    if (this.events.length > MAX_EVENT_TRACE) this.events.splice(0, this.events.length - MAX_EVENT_TRACE)
    if (safeEvent.revision) this.setRevision(safeEvent.revision)
  }

  appendError(name, error) {
    this.appendEvent({
      source: 'director',
      name,
      revision: this.run?.revision || 0,
      level: 'error',
      data: { message: error?.message || String(error), status: error?.status },
    })
  }

  /**
   * The on-screen event log was removed; the trace itself is still collected in memory so
   * that 复制反馈 can attach `recentEvents` and so backend events keep driving the revision
   * label. Reset it when a new run starts.
   */
  resetEventTrace() {
    this.events = []
  }

  startPolling() {
    this.stopPolling()
    this.pollFailures = 0
    this.pollTimer = window.setTimeout(() => this.pollRun(), 700)
  }

  stopPolling() {
    if (this.pollTimer !== null) window.clearTimeout(this.pollTimer)
    this.pollTimer = null
  }

  async pollRun() {
    if (!this.run || this.busy) {
      this.schedulePoll(1200)
      return
    }
    try {
      const before = this.run.revision
      const payload = await this.api.getRun(this.run)
      this.acceptResponse(payload, 'run.poll', { log: false })
      if (this.pollFailures) this.appendEvent({ source: 'backend', name: 'poll.recovered', revision: this.run.revision, data: {} })
      this.pollFailures = 0
      if (this.run.revision !== before) {
        this.appendEvent({ source: 'backend', name: 'run.state', revision: this.run.revision, data: { day: this.run.day, paused: this.run.paused, speed: this.run.speed, forcedWheel: this.run.forcedWheel } })
      }
      this.setStatus('验收局已连接', 'ready')
    } catch (error) {
      this.pollFailures += 1
      if (this.pollFailures === 1) this.appendError('poll.failed', error)
      this.setStatus('验收局连接中断', 'error')
    } finally {
      this.schedulePoll(document.hidden ? 3000 : Math.min(5000, 1200 * Math.max(1, this.pollFailures)))
    }
  }

  schedulePoll(delay) {
    this.stopPolling()
    if (this.run) this.pollTimer = window.setTimeout(() => this.pollRun(), delay)
  }

  handleCommandError(name, error) {
    this.setStatus('验收指令失败', 'error')
    this.appendError(name, error)
    this.toast(error.message || '指令失败', true)
  }

  feedbackBundle() {
    const preset = VIEWPORT_PRESETS[this.viewportKey]
    const frame = this.elements.frames.gus
    const frameUrl = frame && frame.src !== 'about:blank' ? new URL(frame.src) : null
    const layout = worldLayoutIdentity(this.run)
    const binding = authoritativeAssetBinding(this.run)
    return redactSensitive({
      formatVersion: 2,
      generatedAt: new Date().toISOString(),
      pageVersion: 'review-director-v2-single-gus',
      runId: this.run?.id || null,
      revision: this.run?.revision || 0,
      day: this.run?.day || null,
      map: {
        id: layout.id || null,
        label: layout.label || null,
        stage: layout.stage,
        columns: Number.isInteger(layout.columns) ? layout.columns : null,
        rows: Number.isInteger(layout.rows) ? layout.rows : null,
        layoutSha256: layout.sha256 || null,
        origin: layout.origin,
        placementCount: layout.placements.length,
        blockedCellCount: layout.blockedCells.length,
        spawnPoints: layout.spawnPoints,
        interactionPointCount: layout.interactionPoints.length,
        workSeatCount: layout.interactionPoints.length,
      },
      // The picker no longer shows pack ids, so a bug report has to carry
      // enough to diagnose a pack/map mismatch on its own.
      mapCatalog: {
        offeredLayoutIds: this.layouts.map((entry) => entry.id),
        selectedLayoutId: this.selectedLayoutId,
        packsWithoutOfferedMaps: this.missingPackIds,
        layoutError: this.layoutError || null,
      },
      assetBinding: binding,
      viewport: { key: preset.key, width: preset.width, height: preset.height },
      cameraPreset: this.cameraPreset,
      reportedCamera: this.playerDirectorState.camera,
      target: this.playerDirectorState.target,
      seatOccupancy: this.playerDirectorState.seatOccupancy,
      playback: this.playback,
      overlays: this.overlays,
      activity: this.activity,
      simulatedDelayMs: this.delayMs,
      gusPage: { player: 'gus', url: frameUrl ? `${frameUrl.origin}${frameUrl.pathname}` : null },
      gusAssetReport: this.playerAssets.gus,
      browser: navigator.userAgent,
      recentEvents: this.events.slice(-50),
    })
  }

  async copyFeedback() {
    const text = JSON.stringify(this.feedbackBundle(), null, 2)
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
      else this.fallbackCopy(text)
      this.toast('已复制反馈信息（不含任何令牌）')
    } catch (error) {
      this.appendError('feedback.copy_failed', error)
      this.toast('复制失败，请检查浏览器权限', true)
    }
  }

  fallbackCopy(text) {
    const textarea = this.document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    this.document.body.append(textarea)
    textarea.select()
    const copied = this.document.execCommand('copy')
    textarea.remove()
    if (!copied) throw new Error('浏览器拒绝复制')
  }

  toast(message, isError = false) {
    window.clearTimeout(this.toastTimer)
    this.elements.toast.textContent = message
    this.elements.toast.classList.toggle('is-error', isError)
    this.elements.toast.classList.add('is-visible')
    this.toastTimer = window.setTimeout(() => this.elements.toast.classList.remove('is-visible'), 2600)
  }
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const director = new ReviewDirector(document)
  director.start().catch((error) => {
    // This is the only last-resort console output; tokens are never included.
    console.error('Review director failed to start:', error?.message || String(error))
  })
}
