import {
  decodeAtlasBlob,
  drawNearestFrame,
} from './asset-runtime.mjs'
import {
  FIXED_FIXTURE_IDS,
  GUS_ACTION_FRAME_COUNTS,
  GUS_DIRECTIONS,
  TILE_METRICS,
  groundPointForPlacement,
  projectGridPoint,
  sortFixtureForOcclusion,
  validateAssetManifest,
} from './asset-manifest.mjs'

export const FIXTURE_IDS = Object.freeze({
  bare: FIXED_FIXTURE_IDS[0],
  growth: FIXED_FIXTURE_IDS[1],
  team: FIXED_FIXTURE_IDS[2],
  occlusion: FIXED_FIXTURE_IDS[3],
})

const FALLBACK_WORLD = Object.freeze([
  '#0d2228', '#17343a', '#374b4e', '#52686a', '#718487', '#94a29f', '#aeb7b4', '#d5d8cc',
  '#50453e', '#6f5d4e', '#8d765e', '#ad9679', '#c6b89d', '#d8ccb3', '#fff2cb', '#fff8df',
  '#3f7a68', '#75bd9f', '#8fc6a5', '#446f7f', '#78aabc', '#b9d9d5', '#754640', '#ba6c59',
  '#ed806c', '#a84e49', '#a87838', '#f1bf65', '#777f79', '#c4c5b9', '#88634a', '#7eb4bd',
])

const FALLBACK_PLAYERS = Object.freeze([
  '#ed806c', '#75bd9f', '#78aabc', '#f1bf65', '#a88bc2', '#dc8eb0', '#82ae68', '#dc9765',
])

export const ANIMATION_ACTIONS = Object.freeze([
  'walk',
  ...Object.keys(GUS_ACTION_FRAME_COUNTS).filter((action) => action !== 'walk'),
])

export const ANIMATION_DIRECTIONS = GUS_DIRECTIONS

const EMPTY_CANVAS = Object.freeze({ width: 480, height: 320 })

const DIRECTION_ALIASES = Object.freeze({
  southeast: Object.freeze(['southeast', 'south-east', 'se', 's', 'south', 'down']),
  southwest: Object.freeze(['southwest', 'south-west', 'sw', 'w', 'west', 'left']),
  northwest: Object.freeze(['northwest', 'north-west', 'nw', 'n', 'north', 'up']),
  northeast: Object.freeze(['northeast', 'north-east', 'ne', 'e', 'east', 'right']),
})

function finite(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function integer(value, fallback = 0) {
  return Math.round(finite(value, fallback))
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value))
}

function positiveInteger(value, fallback = 1) {
  const number = integer(value, fallback)
  return number > 0 ? number : fallback
}

function imageWidth(image, fallback = 1) {
  return positiveInteger(image?.naturalWidth ?? image?.videoWidth ?? image?.width, fallback)
}

function imageHeight(image, fallback = 1) {
  return positiveInteger(image?.naturalHeight ?? image?.videoHeight ?? image?.height, fallback)
}

function isPoint(value) {
  return Boolean(value) && Number.isFinite(Number(value.x)) && Number.isFinite(Number(value.y))
}

function asMetadata(version) {
  const metadata = version?.metadata
  // `_version_payload` always emits a decoded object.
  return metadata && typeof metadata === 'object' && !Array.isArray(metadata) ? metadata : {}
}

export function normalizeFrame(frame, image, fallbackWidth, fallbackHeight) {
  const width = positiveInteger(
    frame?.width ?? frame?.w,
    positiveInteger(fallbackWidth, imageWidth(image)),
  )
  const height = positiveInteger(
    frame?.height ?? frame?.h,
    positiveInteger(fallbackHeight, imageHeight(image)),
  )
  const x = Math.max(0, integer(frame?.x))
  const y = Math.max(0, integer(frame?.y))
  return {
    x,
    y,
    width: Math.min(width, Math.max(1, imageWidth(image) - x)),
    height: Math.min(height, Math.max(1, imageHeight(image) - y)),
    durationMs: positiveInteger(frame?.durationMs ?? frame?.duration, 125),
    anchor: isPoint(frame?.anchor) ? { x: integer(frame.anchor.x), y: integer(frame.anchor.y) } : null,
  }
}

function firstIndexList(value) {
  const queue = [value]
  while (queue.length) {
    const candidate = queue.shift()
    if (Array.isArray(candidate) && candidate.length) return candidate
    if (candidate && typeof candidate === 'object') queue.push(...Object.values(candidate))
  }
  return null
}

function canonicalDirection(value) {
  const normalized = String(value || '').trim().toLowerCase()
  return ANIMATION_DIRECTIONS.find((direction) => DIRECTION_ALIASES[direction].includes(normalized)) || normalized
}

function availableActionNames(animations) {
  const available = Object.keys(animations).filter((action) => firstIndexList(animations[action]))
  return [...new Set([...ANIMATION_ACTIONS, ...available])].filter((action) => available.includes(action))
}

function directionEntries(actionNode) {
  if (!actionNode || typeof actionNode !== 'object' || Array.isArray(actionNode)) return []
  const entries = []
  for (const [rawDirection, indexes] of Object.entries(actionNode)) {
    if (!Array.isArray(indexes) || !indexes.length) continue
    const direction = canonicalDirection(rawDirection)
    if (!entries.some((entry) => entry.direction === direction)) {
      entries.push({ direction, indexes })
    }
  }
  return entries
}

export function resolveAnimationSelection(version, {
  action = 'walk',
  direction = 'southeast',
} = {}) {
  const metadata = asMetadata(version)
  const animations = metadata.animations ?? version?.animations
  if (!animations || typeof animations !== 'object' || Array.isArray(animations)) {
    return { action: '', direction: '', actions: [], directions: [], indices: null }
  }

  const actions = availableActionNames(animations)
  const requestedAction = String(action || '').trim().toLowerCase()
  const selectedAction = actions.includes(requestedAction) ? requestedAction : actions[0] || ''
  if (!selectedAction) return { action: '', direction: '', actions: [], directions: [], indices: null }
  const actionNode = animations[selectedAction]
  if (Array.isArray(actionNode)) {
    return {
      action: selectedAction,
      direction: '',
      actions,
      directions: [],
      indices: actionNode,
    }
  }

  const entries = directionEntries(actionNode)
  if (!entries.length) {
    return {
      action: selectedAction,
      direction: '',
      actions,
      directions: [],
      indices: firstIndexList(actionNode),
    }
  }
  const availableDirections = entries.map((entry) => entry.direction)
  const requestedDirection = canonicalDirection(direction)
  const selectedDirection = [requestedDirection, ...ANIMATION_DIRECTIONS, ...availableDirections]
    .find((candidate) => availableDirections.includes(candidate))
  const indices = entries.find((entry) => entry.direction === selectedDirection)?.indexes ?? null
  return {
    action: selectedAction,
    direction: selectedDirection || '',
    actions,
    directions: availableDirections,
    indices,
  }
}

function rawVersionFrames(version, image, metadata) {
  if (Array.isArray(metadata.frames) && metadata.frames.length) return metadata.frames
  if (Array.isArray(version?.frames) && version.frames.length) return version.frames
  const frameWidth = positiveInteger(metadata.frameWidth, 0)
  const frameHeight = positiveInteger(metadata.frameHeight, 0)
  if (frameWidth && frameHeight) {
    const columns = positiveInteger(metadata.columns, Math.max(1, Math.floor(imageWidth(image) / frameWidth)))
    const availableRows = Math.max(1, Math.floor(imageHeight(image) / frameHeight))
    const frameCount = Math.min(
      positiveInteger(metadata.frameCount, columns * availableRows),
      columns * availableRows,
    )
    return Array.from({ length: frameCount }, (_, index) => ({
      x: (index % columns) * frameWidth,
      y: Math.floor(index / columns) * frameHeight,
      width: frameWidth,
      height: frameHeight,
    }))
  }
  return [version?.frame ?? metadata.frame ?? null]
}

export function versionFrames(version, image, selection = {}) {
  const metadata = asMetadata(version)
  const rawFrames = rawVersionFrames(version, image, metadata)
  const frames = rawFrames.map((frame) => normalizeFrame(
    frame,
    image,
    version?.width ?? metadata.width,
    version?.height ?? metadata.height,
  ))
  const indices = resolveAnimationSelection(version, selection).indices
  if (!indices) return frames
  const selected = indices
    .map((index) => frames[integer(index, -1)])
    .filter(Boolean)
  return selected.length ? selected : frames
}

export function versionAnchor(version, frame) {
  const metadata = asMetadata(version)
  const source = frame?.anchor ?? version?.anchor ?? metadata.anchor
  if (isPoint(source)) return { x: integer(source.x), y: integer(source.y) }
  return {
    x: Math.floor(positiveInteger(frame?.width, 1) / 2),
    y: Math.max(0, positiveInteger(frame?.height, 1) - 1),
  }
}

export function versionFootprint(version) {
  const metadata = asMetadata(version)
  const source = Array.isArray(version?.footprint)
    ? version.footprint
    : Array.isArray(metadata.footprint)
      ? metadata.footprint
      : [{ x: 0, y: 0, blocked: true }]
  const seen = new Set()
  return source
    .filter(isPoint)
    .map((cell) => ({ x: integer(cell.x), y: integer(cell.y), blocked: cell.blocked !== false }))
    .filter((cell) => {
      const key = `${cell.x},${cell.y}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}

export function validateVersionMetadata(version) {
  const metadata = asMetadata(version)
  if (metadata.manifest && typeof metadata.manifest === 'object') {
    return validateAssetManifest(metadata.manifest)
  }
  if (metadata.schemaVersion && Array.isArray(metadata.atlases) && Array.isArray(metadata.assets)) {
    return validateAssetManifest(metadata)
  }
  const errors = []
  if (metadata.anchor != null && !isPoint(metadata.anchor)) {
    errors.push({ code: 'ANCHOR_POINT', path: 'metadata.anchor', message: 'anchor 必须包含数字 x、y' })
  }
  if (metadata.footprint != null && !Array.isArray(metadata.footprint)) {
    errors.push({ code: 'FOOTPRINT_ARRAY', path: 'metadata.footprint', message: 'footprint 必须是数组' })
  }
  return { valid: errors.length === 0, errors }
}

export async function decodePngBlob(blob) {
  return decodeAtlasBlob(blob)
}

function paletteFor(style) {
  const world = Array.isArray(style?.worldPalette) && style.worldPalette.length
    ? style.worldPalette
    : Array.isArray(style?.palette?.world) && style.palette.world.length
      ? style.palette.world
      : FALLBACK_WORLD
  const players = Array.isArray(style?.playerAccents) && style.playerAccents.length
    ? style.playerAccents
    : Array.isArray(style?.palette?.players) && style.palette.players.length
      ? style.palette.players
      : FALLBACK_PLAYERS
  return { world, players }
}

function isoDiamond(ctx, center, width, height, fill, stroke = null, lineWidth = 1) {
  ctx.beginPath()
  ctx.moveTo(Math.round(center.x), Math.round(center.y - height / 2))
  ctx.lineTo(Math.round(center.x + width / 2), Math.round(center.y))
  ctx.lineTo(Math.round(center.x), Math.round(center.y + height / 2))
  ctx.lineTo(Math.round(center.x - width / 2), Math.round(center.y))
  ctx.closePath()
  if (fill) {
    ctx.fillStyle = fill
    ctx.fill()
  }
  if (stroke) {
    ctx.strokeStyle = stroke
    ctx.lineWidth = lineWidth
    ctx.stroke()
  }
}

function drawPixelBox(ctx, x, y, width, height, top, side) {
  ctx.fillStyle = side
  ctx.fillRect(Math.round(x), Math.round(y - height), Math.round(width), Math.round(height))
  ctx.fillStyle = top
  ctx.fillRect(Math.round(x + 2), Math.round(y - height - 4), Math.max(1, Math.round(width - 4)), 5)
}

function fixtureObjects(kind, palette) {
  const objects = []
  const add = (id, x, y, layer, draw) => objects.push({ id, x, y, depth: x + y, layer, draw })
  if (kind === 'bare') {
    add('box-left', 6, 5, 0, (ctx, point) => drawPixelBox(ctx, point.x - 10, point.y, 20, 16, palette.world[14], palette.world[11]))
    add('box-right', 13, 8, 0, (ctx, point) => drawPixelBox(ctx, point.x - 12, point.y, 24, 19, palette.world[14], palette.world[10]))
  }
  if (kind === 'growth' || kind === 'team' || kind === 'occlusion') {
    for (const [index, entry] of [[4, 3], [10, 3], [15, 7]].entries()) {
      const [x, y] = entry
      add(`desk-${index}`, x, y, 0, (ctx, point) => {
        isoDiamond(ctx, { x: point.x, y: point.y - 13 }, 70, 27, palette.world[13], palette.world[1], 2)
        drawPixelBox(ctx, point.x - 25, point.y + 1, 6, 18, palette.world[5], palette.world[2])
        drawPixelBox(ctx, point.x + 19, point.y + 1, 6, 18, palette.world[5], palette.world[2])
      })
    }
    add('plant', 3, 8, 1, (ctx, point) => {
      drawPixelBox(ctx, point.x - 7, point.y, 14, 14, palette.world[22], palette.world[23])
      ctx.fillStyle = palette.world[17]
      ctx.fillRect(point.x - 5, point.y - 29, 10, 18)
      ctx.fillRect(point.x - 11, point.y - 23, 8, 10)
      ctx.fillRect(point.x + 4, point.y - 26, 8, 11)
    })
  }
  if (kind === 'team') {
    const positions = [[2, 2], [6, 2], [10, 2], [14, 2], [4, 8], [8, 8], [12, 8], [16, 8]]
    positions.forEach(([x, y], index) => add(`actor-${index}`, x, y, 2, (ctx, point) => {
      ctx.fillStyle = palette.players[index % palette.players.length]
      ctx.fillRect(point.x - 6, point.y - 24, 12, 16)
      ctx.fillStyle = palette.world[1]
      ctx.fillRect(point.x - 6, point.y - 32, 12, 5)
      ctx.fillStyle = palette.world[15]
      ctx.fillRect(point.x - 5, point.y - 28, 10, 8)
      ctx.fillStyle = palette.world[0]
      ctx.fillRect(point.x - 5, point.y - 8, 4, 8)
      ctx.fillRect(point.x + 2, point.y - 8, 4, 8)
    }))
  }
  if (kind === 'occlusion') {
    add('storage-back', 8, 4, 1, (ctx, point) => drawPixelBox(ctx, point.x - 18, point.y, 36, 72, palette.world[6], palette.world[3]))
    add('partition-front', 11, 8, 4, (ctx, point) => drawPixelBox(ctx, point.x - 42, point.y, 84, 50, palette.world[6], palette.world[3]))
  }
  return objects
}

function fixtureLayout(fixture) {
  const fixtureId = FIXTURE_IDS[fixture] || fixture
  const index = FIXED_FIXTURE_IDS.indexOf(fixtureId)
  return {
    fixtureId,
    columns: 20,
    rows: 12,
    origin: { x: 320, y: 82 },
    selected: index === 0 ? { x: 10, y: 6 } : index === 3 ? { x: 10, y: 6 } : { x: 10, y: 5 },
  }
}

function drawFixtureGround(ctx, layout, palette) {
  ctx.fillStyle = palette.world[6]
  ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height)
  for (let diagonal = 0; diagonal < layout.columns + layout.rows - 1; diagonal += 1) {
    for (let x = 0; x < layout.columns; x += 1) {
      const y = diagonal - x
      if (y < 0 || y >= layout.rows) continue
      const point = projectGridPoint(x, y, { origin: layout.origin, tile: TILE_METRICS })
      const fill = (x + y) % 2 ? palette.world[12] : palette.world[13]
      isoDiamond(ctx, point, TILE_METRICS.width, TILE_METRICS.height, fill, palette.world[10])
    }
  }
}

export function selectedPlacement(version, frame, layout) {
  const anchor = versionAnchor(version, frame)
  const footprint = versionFootprint(version)
  const gridGround = groundPointForPlacement(
    { footprint },
    { x: layout.selected.x, y: layout.selected.y },
  )
  const ground = projectGridPoint(gridGround.x, gridGround.y, { origin: layout.origin, tile: TILE_METRICS })
  return {
    id: 'selected-asset',
    x: layout.selected.x,
    y: layout.selected.y,
    depth: Math.max(...footprint.map((cell) => layout.selected.x + cell.x + layout.selected.y + cell.y)),
    layer: finite(asMetadata(version).layer ?? version?.layer),
    ground,
    destination: {
      x: Math.round(ground.x - anchor.x),
      y: Math.round(ground.y - anchor.y),
      width: frame.width,
      height: frame.height,
    },
  }
}

function renderVersion(ctx, version, image, frameIndex, fixture, style, selection) {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height)
  if (!version || !image) return null
  const frames = versionFrames(version, image, selection)
  const frame = frames[Math.abs(integer(frameIndex)) % frames.length]
  if (fixture === 'asset') {
    const destination = {
      x: Math.floor((ctx.canvas.width - frame.width) / 2),
      y: Math.floor((ctx.canvas.height - frame.height) / 2),
      width: frame.width,
      height: frame.height,
    }
    drawNearestFrame(ctx, image, frame, destination)
    return {
      frame,
      anchor: versionAnchor(version, frame),
      footprint: versionFootprint(version),
      destination,
      ground: {
        x: destination.x + versionAnchor(version, frame).x,
        y: destination.y + versionAnchor(version, frame).y,
      },
      layout: null,
    }
  }

  const palette = paletteFor(style)
  const layout = fixtureLayout(fixture)
  drawFixtureGround(ctx, layout, palette)
  const selected = selectedPlacement(version, frame, layout)
  const decorations = fixtureObjects(fixture, palette).map((item) => ({
    ...item,
    point: projectGridPoint(item.x, item.y, { origin: layout.origin, tile: TILE_METRICS }),
  }))
  const objects = sortFixtureForOcclusion([...decorations, selected])
  for (const object of objects) {
    if (object.id === selected.id) drawNearestFrame(ctx, image, frame, selected.destination)
    else object.draw(ctx, object.point)
  }
  return {
    frame,
    anchor: versionAnchor(version, frame),
    footprint: versionFootprint(version),
    destination: selected.destination,
    ground: selected.ground,
    layout,
  }
}

function drawGridGuide(ctx, fixture, geometry) {
  ctx.save()
  ctx.strokeStyle = 'rgb(120 174 252 / 48%)'
  ctx.lineWidth = 1
  if (fixture === 'asset') {
    for (let x = 0.5; x < ctx.canvas.width; x += 8) {
      ctx.beginPath()
      ctx.moveTo(x, 0)
      ctx.lineTo(x, ctx.canvas.height)
      ctx.stroke()
    }
    for (let y = 0.5; y < ctx.canvas.height; y += 8) {
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(ctx.canvas.width, y)
      ctx.stroke()
    }
  } else if (geometry?.layout) {
    for (let x = 0; x < geometry.layout.columns; x += 1) {
      for (let y = 0; y < geometry.layout.rows; y += 1) {
        const point = projectGridPoint(x, y, { origin: geometry.layout.origin, tile: TILE_METRICS })
        isoDiamond(ctx, point, TILE_METRICS.width, TILE_METRICS.height, null, 'rgb(120 174 252 / 42%)')
      }
    }
  }
  ctx.restore()
}

function drawAnchorGuide(ctx, geometry, color) {
  if (!geometry?.ground) return
  const point = geometry.ground
  ctx.save()
  ctx.strokeStyle = color
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(point.x - 9, point.y)
  ctx.lineTo(point.x + 9, point.y)
  ctx.moveTo(point.x, point.y - 9)
  ctx.lineTo(point.x, point.y + 9)
  ctx.stroke()
  ctx.restore()
}

function drawFootprintGuide(ctx, geometry, color) {
  if (!geometry?.footprint?.length || !geometry.ground) return
  ctx.save()
  for (const cell of geometry.footprint) {
    const point = geometry.layout
      ? projectGridPoint(
        geometry.layout.selected.x + cell.x,
        geometry.layout.selected.y + cell.y,
        { origin: geometry.layout.origin, tile: TILE_METRICS },
      )
      : {
        x: geometry.ground.x + (cell.x - cell.y) * (TILE_METRICS.width / 2),
        y: geometry.ground.y + (cell.x + cell.y) * (TILE_METRICS.height / 2),
      }
    isoDiamond(ctx, point, TILE_METRICS.width, TILE_METRICS.height, `${color}28`, color, 2)
  }
  ctx.restore()
}

function drawLightGuide(ctx, style) {
  const rawDirection = String(style?.lightDirection ?? style?.light?.direction ?? 'top-left').toLowerCase()
  const fromRight = rawDirection.includes('right')
  const startX = fromRight ? ctx.canvas.width - 28 : 28
  const endX = fromRight ? ctx.canvas.width - 70 : 70
  const startY = 26
  const endY = 68
  ctx.save()
  ctx.strokeStyle = '#f2ba4b'
  ctx.fillStyle = '#f2ba4b'
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(startX, startY)
  ctx.lineTo(endX, endY)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(endX, endY)
  ctx.lineTo(endX + (fromRight ? 2 : -2), endY - 11)
  ctx.lineTo(endX + (fromRight ? 11 : -11), endY - 2)
  ctx.closePath()
  ctx.fill()
  ctx.font = 'bold 9px ui-monospace, monospace'
  ctx.fillText('LIGHT', fromRight ? endX - 38 : endX + 8, endY + 4)
  ctx.restore()
}

function outputSize(version, image, fixture, selection) {
  if (fixture !== 'asset') return { width: 640, height: 360 }
  const frames = versionFrames(version, image, selection)
  const width = Math.max(96, ...frames.map((frame) => frame.width + 48))
  const height = Math.max(96, ...frames.map((frame) => frame.height + 48))
  return { width: Math.min(2048, width), height: Math.min(2048, height) }
}

function sequenceDuration(frames) {
  return frames.reduce((sum, frame) => sum + positiveInteger(frame.durationMs, 125), 0)
}

export function frameIndexAtElapsed(frames, elapsedMs) {
  if (!frames.length) return 0
  const duration = sequenceDuration(frames)
  let cursor = duration ? Math.max(0, elapsedMs) % duration : 0
  for (let index = 0; index < frames.length; index += 1) {
    cursor -= positiveInteger(frames[index].durationMs, 125)
    if (cursor < 0) return index
  }
  return frames.length - 1
}

export function advanceAnimationElapsed(elapsedMs, deltaMs, {
  speed = 1,
  paused = false,
  maximumDeltaMs = 250,
} = {}) {
  const elapsed = Math.max(0, finite(elapsedMs))
  if (paused) return elapsed
  const delta = clamp(finite(deltaMs), 0, Math.max(0, finite(maximumDeltaMs, 250)))
  const rate = Math.max(0, finite(speed, 1))
  return elapsed + delta * rate
}

export function stepAnimationFrame(frames, elapsedMs, direction = 1) {
  if (!frames.length) return { index: 0, elapsedMs: 0 }
  if (frames.length === 1) return { index: 0, elapsedMs: 0 }
  const current = frameIndexAtElapsed(frames, elapsedMs)
  const next = (current + Math.sign(direction || 1) + frames.length) % frames.length
  return {
    index: next,
    elapsedMs: frames.slice(0, next).reduce(
      (sum, frame) => sum + positiveInteger(frame.durationMs, 125),
      0,
    ),
  }
}

export class AssetPreview {
  constructor(canvas, {
    viewport = canvas?.parentElement,
    onUpdate = () => {},
  } = {}) {
    if (!(canvas instanceof HTMLCanvasElement)) throw new TypeError('AssetPreview 需要 Canvas')
    this.canvas = canvas
    this.ctx = canvas.getContext('2d', { alpha: true, willReadFrequently: true })
    this.ctx.imageSmoothingEnabled = false
    this.viewport = viewport
    this.onUpdate = onUpdate
    this.versionA = null
    this.imageA = null
    this.style = null
    this.fixture = 'asset'
    this.scale = 1
    this.guides = { grid: false, anchor: false, footprint: false, light: false }
    this.reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.paused = this.reducedMotion
    this.speed = 1
    this.animationAction = 'walk'
    this.animationDirection = 'southeast'
    this.elapsedMs = 0
    this.lastTick = performance.now()
    this.lastFrameSignature = ''
    this.destroyed = false
    this.animationFrame = requestAnimationFrame((time) => this.tick(time))
  }

  setVersions(versionA, imageA) {
    this.versionA = versionA
    this.imageA = imageA
    const selection = resolveAnimationSelection(versionA, {
      action: this.animationAction,
      direction: this.animationDirection,
    })
    if (selection.action) this.animationAction = selection.action
    if (selection.direction) this.animationDirection = selection.direction
    this.elapsedMs = 0
    this.lastTick = performance.now()
    this.render(true)
  }

  setStyle(style) {
    this.style = style
    this.render(true)
  }

  setFixture(value) {
    this.fixture = ['asset', 'bare', 'growth', 'team', 'occlusion'].includes(value) ? value : 'asset'
    this.render(true)
  }

  setScale(value) {
    this.scale = [1, 2, 4].includes(Number(value)) ? Number(value) : 1
    this.render(true)
  }

  setGuides(value) {
    this.guides = { ...this.guides, ...(value || {}) }
    this.render(true)
  }

  setSpeed(value) {
    this.speed = [0.5, 1, 2].includes(Number(value)) ? Number(value) : 1
  }

  setAnimationSelection(action, direction) {
    const selection = resolveAnimationSelection(this.versionA, { action, direction })
    this.animationAction = selection.action || String(action || 'walk')
    this.animationDirection = selection.direction || String(direction || 'southeast')
    this.elapsedMs = 0
    this.lastTick = performance.now()
    this.render(true)
    return selection
  }

  setPaused(value) {
    this.paused = Boolean(value)
    this.lastTick = performance.now()
    this.render(true)
    return this.paused
  }

  togglePaused() {
    return this.setPaused(!this.paused)
  }

  step(direction) {
    this.paused = true
    const selection = { action: this.animationAction, direction: this.animationDirection }
    const frames = versionFrames(this.versionA, this.imageA, selection)
    if (frames.length <= 1) return 0
    const stepped = stepAnimationFrame(frames, this.elapsedMs, direction)
    this.elapsedMs = stepped.elapsedMs
    this.render(true)
    return stepped.index
  }

  tick(now) {
    if (this.destroyed) return
    const delta = now - this.lastTick
    this.lastTick = now
    this.elapsedMs = advanceAnimationElapsed(this.elapsedMs, delta, {
      speed: this.speed,
      paused: this.paused,
    })
    if (!this.paused) this.render(false)
    this.animationFrame = requestAnimationFrame((time) => this.tick(time))
  }

  render(force = false) {
    if (!this.versionA || !this.imageA) {
      this.canvas.width = EMPTY_CANVAS.width
      this.canvas.height = EMPTY_CANVAS.height
      this.canvas.style.removeProperty('width')
      this.canvas.style.removeProperty('height')
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
      this.lastFrameSignature = ''
      // Report the empty state too, so the frame counter and the animation controls do
      // not keep advertising the previously selected asset.
      this.onUpdate({
        width: 0,
        height: 0,
        frame: 0,
        frameCount: 0,
        animated: false,
        paused: this.paused,
        animationAction: this.animationAction,
        animationDirection: this.animationDirection,
        availableActions: [],
        availableDirections: [],
      })
      return
    }
    const selection = resolveAnimationSelection(this.versionA, {
      action: this.animationAction,
      direction: this.animationDirection,
    })
    const frames = versionFrames(this.versionA, this.imageA, selection)
    const frameIndex = frameIndexAtElapsed(frames, this.elapsedMs)
    const signature = [
      frameIndex, selection.action, selection.direction,
      this.fixture, this.scale,
      this.guides.grid, this.guides.anchor, this.guides.footprint, this.guides.light,
    ].join(':')
    if (!force && signature === this.lastFrameSignature) return
    this.lastFrameSignature = signature

    const size = outputSize(this.versionA, this.imageA, this.fixture, selection)
    this.canvas.width = size.width
    this.canvas.height = size.height
    this.canvas.style.width = `${size.width * this.scale}px`
    this.canvas.style.height = `${size.height * this.scale}px`
    // Resizing a canvas resets its 2D context, so restore nearest-neighbour before drawing.
    this.ctx.imageSmoothingEnabled = false

    const geometry = renderVersion(
      this.ctx,
      this.versionA,
      this.imageA,
      frameIndex,
      this.fixture,
      this.style,
      selection,
    )

    if (this.guides.grid) drawGridGuide(this.ctx, this.fixture, geometry)
    if (this.guides.anchor) drawAnchorGuide(this.ctx, geometry, '#f2ba4b')
    if (this.guides.footprint) drawFootprintGuide(this.ctx, geometry, '#65d5ab')
    if (this.guides.light) drawLightGuide(this.ctx, this.style)

    this.onUpdate({
      width: size.width,
      height: size.height,
      frame: frameIndex,
      frameCount: frames.length,
      animated: frames.length > 1,
      paused: this.paused,
      animationAction: selection.action,
      animationDirection: selection.direction,
      availableActions: selection.actions,
      availableDirections: selection.directions,
    })
  }

  destroy() {
    this.destroyed = true
    cancelAnimationFrame(this.animationFrame)
  }
}
