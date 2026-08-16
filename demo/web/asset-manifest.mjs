export const ASSET_CONTRACT_VERSION = 1
export const DEPTH_RULE = 'max-x-plus-y'
export const ATLAS_PADDING = 2

export const TILE_METRICS = Object.freeze({
  width: 32,
  height: 16,
  elevation: 8,
})

export const CHARACTER_FRAME = Object.freeze({
  width: 24,
  height: 48,
})

export const GUS_ANCHOR = Object.freeze({ x: 12, y: 46 })

export const PALETTE_LIMITS = Object.freeze({
  world: 32,
  players: 8,
})

export const CORE_V2_PALETTE_LIMITS = Object.freeze({
  world: 48,
  players: 8,
})

export const SCENE_SHELL_COLOR_KEYS = Object.freeze([
  'outline',
  'ambientOcclusion',
  'slab',
  'facadeLight',
  'facadeDark',
  'window',
  'mullion',
])

export const FIXED_FIXTURE_IDS = Object.freeze([
  'opening-empty',
  'mid-growth',
  'eight-player',
  'occlusion-stress',
])

export const CORE_V0_REQUIRED_SLOTS = Object.freeze([
  'floor.raw-concrete',
  'floor.patched-concrete',
  'floor.light-wood',
  'floor.utility-border',
  'furniture.moving-box',
  'furniture.desk-island',
  'furniture.storage-cabinet',
  'furniture.tea-coffee-bar',
  'furniture.meeting-table',
  'character.gus',
  'effect.good-card-heart',
])

export const CORE_V1_NEW_REQUIRED_SLOTS = Object.freeze([
  'backdrop.beijing-cbd',
  'structure.wall-solid-nw',
  'structure.wall-solid-ne',
  'structure.wall-window-nw',
  'structure.wall-window-ne',
  'structure.wall-door-ne',
  'structure.corner-column',
  'decor.whiteboard-stand',
  'decor.floor-plant',
  'furniture.printer-station',
  'furniture.lounge-set',
])

export const CORE_V1_REQUIRED_SLOTS = Object.freeze([
  ...CORE_V0_REQUIRED_SLOTS,
  ...CORE_V1_NEW_REQUIRED_SLOTS,
])

export const CORE_V2_NEW_REQUIRED_SLOTS = Object.freeze([
  'furniture.focus-desk-nw',
  'furniture.focus-desk-ne',
  'furniture.media-console',
  'furniture.prototype-bench',
  'furniture.low-bookcase',
  'furniture.entry-bench',
  'decor.pinboard-stand',
])

export const CORE_V2_REQUIRED_SLOTS = Object.freeze([
  ...CORE_V1_REQUIRED_SLOTS,
  ...CORE_V2_NEW_REQUIRED_SLOTS,
])

export const REQUIRED_SLOTS_BY_PACK = Object.freeze({
  'core-v0': CORE_V0_REQUIRED_SLOTS,
  'core-v1': CORE_V1_REQUIRED_SLOTS,
  'core-v2': CORE_V2_REQUIRED_SLOTS,
})

export const RENDERABLE_KINDS = Object.freeze([
  'floor',
  'prop',
  'backdrop',
  'structure',
  'decor',
  'furniture',
  'character',
  'effect',
])

export const WORK_SEAT_FACINGS = Object.freeze([
  'southeast',
  'southwest',
  'northwest',
  'northeast',
])

export const CORE_V1_DESK_SEATS = Object.freeze([
  Object.freeze({ id: 'seat-se', kind: 'work-seat', x: 1, y: 2, facing: 'northwest' }),
  Object.freeze({ id: 'seat-sw', kind: 'work-seat', x: -1, y: 1, facing: 'northeast' }),
  Object.freeze({ id: 'seat-nw', kind: 'work-seat', x: 1, y: -1, facing: 'southeast' }),
  Object.freeze({ id: 'seat-ne', kind: 'work-seat', x: 3, y: 0, facing: 'southwest' }),
])

export const CORE_V2_FOCUS_DESK_SEATS = Object.freeze({
  'furniture.focus-desk-nw': Object.freeze([
    Object.freeze({ id: 'seat-work', kind: 'work-seat', x: 1, y: 2, facing: 'northwest' }),
  ]),
  'furniture.focus-desk-ne': Object.freeze([
    Object.freeze({ id: 'seat-work', kind: 'work-seat', x: -1, y: 1, facing: 'northeast' }),
  ]),
})

const CORE_V2_WALL_GROUND_AXES = Object.freeze({
  'structure.wall-solid-nw': Object.freeze({ orientation: 'nw', dx: 48, dy: 24 }),
  'structure.wall-solid-ne': Object.freeze({ orientation: 'ne', dx: 48, dy: 24 }),
  'structure.wall-window-nw': Object.freeze({ orientation: 'nw', dx: 64, dy: 32 }),
  'structure.wall-window-ne': Object.freeze({ orientation: 'ne', dx: 64, dy: 32 }),
  'structure.wall-door-ne': Object.freeze({ orientation: 'ne', dx: 48, dy: 24 }),
})

const CORE_V2_WALL_FACE_HEIGHT = 56

export const GUS_DIRECTIONS = Object.freeze([
  'southeast',
  'southwest',
  'northwest',
  'northeast',
])

export const GUS_ACTIONS = Object.freeze(['idle', 'walk', 'work'])

function buildGusLayout(id, frameCounts) {
  const columns = GUS_ACTIONS.reduce((total, action) => total + frameCounts[action], 0)
  // Idle frame 0 keeps its bare id: the schema's canonicalFrames const and the
  // untrusted-motion fallback both address it by name.
  const columnOrder = GUS_ACTIONS.flatMap((action) => Array.from(
    { length: frameCounts[action] },
    (_, index) => (action === 'idle' && index === 0 ? 'idle' : `${action}.${index}`),
  ))
  return Object.freeze({
    id,
    frameCounts: Object.freeze({ ...frameCounts }),
    columnOrder: Object.freeze(columnOrder),
    sheet: Object.freeze({
      width: CHARACTER_FRAME.width * columns,
      height: CHARACTER_FRAME.height * GUS_DIRECTIONS.length,
      columns,
      rows: GUS_DIRECTIONS.length,
    }),
    frameIds: Object.freeze(GUS_DIRECTIONS.flatMap((direction) => (
      columnOrder.map((column) => `character.gus.${direction}.${column}`)
    ))),
  })
}

// Known Gus sheet layouts, current first.  A manifest is matched to one of
// these by its declared sheet geometry instead of being checked against a
// single frozen shape, so packs derived before the walk cycle was rebuilt stay
// loadable rather than being rejected by a contract that moved under them.
export const GUS_LAYOUTS = Object.freeze([
  buildGusLayout('v2', { idle: 4, walk: 8, work: 4 }),
  buildGusLayout('v1', { idle: 1, walk: 4, work: 2 }),
])

export const GUS_LAYOUT = GUS_LAYOUTS[0]

export function gusLayoutForSheet(sheet) {
  if (!sheet) return null
  return GUS_LAYOUTS.find((layout) => (
    layout.sheet.columns === sheet.columns && layout.sheet.rows === sheet.rows
  )) ?? null
}

export const GUS_SHEET = GUS_LAYOUT.sheet
export const GUS_ACTION_FRAME_COUNTS = GUS_LAYOUT.frameCounts
export const GUS_COLUMN_ORDER = GUS_LAYOUT.columnOrder
export const GUS_FRAME_IDS = GUS_LAYOUT.frameIds

export const GUS_MOTION_POLICY = Object.freeze({
  policy: 'canonical-idle-v1',
  fallback: 'canonical-idle-bob',
})

export const CORE_FURNITURE_FOOTPRINTS = Object.freeze({
  'furniture.moving-box': Object.freeze([[0, 0]]),
  'furniture.desk-island': Object.freeze([
    [0, 0], [1, 0], [2, 0],
    [0, 1], [1, 1], [2, 1],
  ]),
  'furniture.storage-cabinet': Object.freeze([[0, 0], [1, 0]]),
  'furniture.tea-coffee-bar': Object.freeze([[0, 0], [1, 0]]),
  'furniture.meeting-table': Object.freeze([
    [0, 0], [1, 0], [2, 0], [3, 0],
    [0, 1], [1, 1], [2, 1], [3, 1],
  ]),
})

const HEX_COLOR = /^#[0-9a-f]{6}$/i

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isInteger(value) {
  return Number.isInteger(value)
}

function cellKey(cell) {
  return `${cell.x},${cell.y}`
}

function compareStrings(left, right) {
  return String(left).localeCompare(String(right), 'en')
}

function sortedUnique(values) {
  return [...new Set(values)].sort(compareStrings)
}

function sameStringSet(left, right) {
  const a = sortedUnique(left)
  const b = sortedUnique(right)
  return a.length === b.length && a.every((value, index) => value === b[index])
}

function addError(errors, code, path, message) {
  errors.push({ code, path, message })
}

function indexById(items) {
  return new Map((Array.isArray(items) ? items : []).map((item) => [item?.id, item]))
}

function sheetForAsset(asset, sheets) {
  if (!asset) return null
  if (asset.sheet) return sheets.get(asset.sheet) || null
  for (const sheet of sheets.values()) {
    if (String(asset.id || '').startsWith(`${sheet.id}.`)) return sheet
  }
  return null
}

function validateUniqueField(items, field, path, errors, code) {
  const seen = new Map()
  for (const [index, item] of (Array.isArray(items) ? items : []).entries()) {
    const value = item?.[field]
    if (typeof value !== 'string' || !value) continue
    if (seen.has(value)) {
      addError(errors, code, `${path}[${index}].${field}`, `${field} “${value}” 必须唯一`)
    } else {
      seen.set(value, index)
    }
  }
}

function validateIntegerPoint(point, path, errors, code = 'INTEGER_POINT_REQUIRED') {
  if (!isRecord(point) || !isInteger(point.x) || !isInteger(point.y)) {
    addError(errors, code, path, '坐标必须包含整数 x、y')
    return false
  }
  return true
}

function validatePalette(manifest, errors) {
  const palette = manifest.palette
  if (!isRecord(palette)) {
    addError(errors, 'PALETTE_REQUIRED', 'palette', '必须声明 world 与 players 调色板')
    return
  }
  const limits = Number(manifest.geometryVersion) >= 2 ? CORE_V2_PALETTE_LIMITS : PALETTE_LIMITS
  for (const [name, exactLength] of Object.entries(limits)) {
    const colors = palette[name]
    if (!Array.isArray(colors) || colors.length !== exactLength) {
      addError(
        errors,
        'PALETTE_SIZE',
        `palette.${name}`,
        `${name} 调色板必须恰好包含 ${exactLength} 色`,
      )
      continue
    }
    for (const [index, color] of colors.entries()) {
      if (!HEX_COLOR.test(String(color))) {
        addError(errors, 'PALETTE_COLOR', `palette.${name}[${index}]`, '颜色必须是 #RRGGBB')
      }
    }
    if (new Set(colors.map((color) => String(color).toLowerCase())).size !== colors.length) {
      addError(errors, 'PALETTE_DUPLICATE', `palette.${name}`, `${name} 调色板不能包含重复颜色`)
    }
  }
  if (Array.isArray(palette.world) && Array.isArray(palette.players)) {
    const world = new Set(palette.world.map((color) => String(color).toLowerCase()))
    const overlap = palette.players.filter((color) => world.has(String(color).toLowerCase()))
    if (overlap.length) {
      addError(errors, 'PALETTE_CROSS_DUPLICATE', 'palette', '玩家强调色不得占用世界调色板颜色')
    }
  }
}

function validateSceneShell(manifest, errors) {
  const shell = manifest.sceneShell
  if (shell === undefined) return
  if (!isRecord(shell)) {
    addError(errors, 'SCENE_SHELL_OBJECT', 'sceneShell', 'sceneShell 必须是对象')
    return
  }

  const allowed = new Set([
    'version',
    'type',
    'facadeDepth',
    'slabDepth',
    'windowBandPitch',
    'colors',
  ])
  for (const key of Object.keys(shell)) {
    if (!allowed.has(key)) {
      addError(errors, 'SCENE_SHELL_FIELD', `sceneShell.${key}`, `sceneShell 不允许字段 “${key}”`)
    }
  }
  for (const key of allowed) {
    if (!(key in shell)) {
      addError(errors, 'SCENE_SHELL_FIELD_REQUIRED', `sceneShell.${key}`, `sceneShell 必须声明 ${key}`)
    }
  }
  if (Number(manifest.geometryVersion) < 2) {
    addError(errors, 'SCENE_SHELL_GEOMETRY_VERSION', 'sceneShell', 'sceneShell 仅适用于 geometryVersion >= 2')
  }
  if (shell.version !== 1) {
    addError(errors, 'SCENE_SHELL_VERSION', 'sceneShell.version', 'sceneShell.version 必须是 1')
  }
  if (shell.type !== 'cutaway-office-tower') {
    addError(
      errors,
      'SCENE_SHELL_TYPE',
      'sceneShell.type',
      'sceneShell.type 必须是 cutaway-office-tower',
    )
  }
  for (const field of ['facadeDepth', 'slabDepth', 'windowBandPitch']) {
    if (!isInteger(shell[field]) || shell[field] <= 0) {
      addError(errors, 'SCENE_SHELL_POSITIVE_INTEGER', `sceneShell.${field}`, `${field} 必须是正整数`)
    }
  }
  if (isInteger(shell.facadeDepth)
    && isInteger(shell.slabDepth)
    && shell.facadeDepth <= shell.slabDepth) {
    addError(
      errors,
      'SCENE_SHELL_DEPTH_ORDER',
      'sceneShell.facadeDepth',
      'facadeDepth 必须大于 slabDepth',
    )
  }

  if (!isRecord(shell.colors)) {
    addError(errors, 'SCENE_SHELL_COLORS', 'sceneShell.colors', 'sceneShell.colors 必须是对象')
    return
  }
  const expectedColors = new Set(SCENE_SHELL_COLOR_KEYS)
  for (const key of Object.keys(shell.colors)) {
    if (!expectedColors.has(key)) {
      addError(errors, 'SCENE_SHELL_COLOR_FIELD', `sceneShell.colors.${key}`, `不允许颜色字段 “${key}”`)
    }
  }
  for (const key of SCENE_SHELL_COLOR_KEYS) {
    if (!HEX_COLOR.test(String(shell.colors[key] || ''))) {
      addError(errors, 'SCENE_SHELL_COLOR', `sceneShell.colors.${key}`, `${key} 必须是 #RRGGBB`)
    }
  }
}

function validateAtlasDefinitions(manifest, errors) {
  const atlases = Array.isArray(manifest.atlases) ? manifest.atlases : []
  validateUniqueField(atlases, 'id', 'atlases', errors, 'DUPLICATE_ATLAS_ID')
  if (!atlases.length) addError(errors, 'ATLAS_REQUIRED', 'atlases', '至少需要一个 atlas')
  for (const [index, atlas] of atlases.entries()) {
    const path = `atlases[${index}]`
    if (!isRecord(atlas)) {
      addError(errors, 'ATLAS_INVALID', path, 'atlas 必须是对象')
      continue
    }
    if (!isInteger(atlas.width) || !isInteger(atlas.height) || atlas.width <= 0 || atlas.height <= 0) {
      addError(errors, 'ATLAS_SIZE', path, 'atlas 宽高必须是正整数')
    }
    if (!isInteger(atlas.padding) || atlas.padding < ATLAS_PADDING) {
      addError(errors, 'ATLAS_PADDING', `${path}.padding`, `atlas padding 不得小于 ${ATLAS_PADDING}`)
    }
  }
}

function validateSheetDefinitions(manifest, errors) {
  const sheets = Array.isArray(manifest.sheets) ? manifest.sheets : []
  const atlases = indexById(manifest.atlases)
  validateUniqueField(sheets, 'id', 'sheets', errors, 'DUPLICATE_SHEET_ID')
  if (!sheets.length) addError(errors, 'SHEET_REQUIRED', 'sheets', 'core-v0 必须声明 Gus sheet')
  for (const [index, sheet] of sheets.entries()) {
    const path = `sheets[${index}]`
    const atlas = atlases.get(sheet?.atlas)
    if (!atlas) {
      addError(errors, 'SHEET_ATLAS_REFERENCE', `${path}.atlas`, `未知 atlas “${sheet?.atlas ?? ''}”`)
      continue
    }
    const frame = sheet.frame
    if (!isRecord(frame)
      || !isInteger(frame.x)
      || !isInteger(frame.y)
      || !isInteger(frame.width)
      || !isInteger(frame.height)
      || frame.x < atlas.padding
      || frame.y < atlas.padding
      || frame.x + frame.width > atlas.width - atlas.padding
      || frame.y + frame.height > atlas.height - atlas.padding) {
      addError(errors, 'SHEET_FRAME_BOUNDS', `${path}.frame`, 'sheet frame 必须位于 atlas padding 边界内')
    }
    for (const field of ['columns', 'rows', 'cellWidth', 'cellHeight']) {
      if (!isInteger(sheet?.[field]) || sheet[field] <= 0) {
        addError(errors, 'SHEET_GRID', `${path}.${field}`, `${field} 必须是正整数`)
      }
    }
    if (isRecord(frame)
      && isInteger(sheet?.columns) && isInteger(sheet?.rows)
      && isInteger(sheet?.cellWidth) && isInteger(sheet?.cellHeight)
      && (frame.width !== sheet.columns * sheet.cellWidth
        || frame.height !== sheet.rows * sheet.cellHeight)) {
      addError(errors, 'SHEET_GRID_SIZE', path, 'sheet 尺寸必须等于 columns×cellWidth、rows×cellHeight')
    }
  }
}

function framesHaveRequiredGap(left, right, padding) {
  return left.x + left.width + padding <= right.x
    || right.x + right.width + padding <= left.x
    || left.y + left.height + padding <= right.y
    || right.y + right.height + padding <= left.y
}

function validateAssetGeometry(asset, index, atlas, errors) {
  const path = `assets[${index}]`
  if (!RENDERABLE_KINDS.includes(asset.kind)) {
    addError(
      errors,
      'ASSET_KIND',
      `${path}.kind`,
      `kind 必须是 ${RENDERABLE_KINDS.join(', ')} 之一`,
    )
  }
  const frame = asset.frame
  if (!isRecord(frame)
    || !isInteger(frame.x)
    || !isInteger(frame.y)
    || !isInteger(frame.width)
    || !isInteger(frame.height)
    || frame.width <= 0
    || frame.height <= 0) {
    addError(errors, 'FRAME_INTEGER_BOUNDS', `${path}.frame`, 'frame 坐标与尺寸必须是正整数')
  } else if (atlas) {
    const padding = atlas.padding
    if (frame.x < padding
      || frame.y < padding
      || frame.x + frame.width > atlas.width - padding
      || frame.y + frame.height > atlas.height - padding) {
      addError(errors, 'FRAME_OUT_OF_ATLAS', `${path}.frame`, 'frame 必须位于 atlas padding 边界内')
    }
  }

  const anchorValid = validateIntegerPoint(asset.anchor, `${path}.anchor`, errors, 'ANCHOR_INTEGER')
  if (anchorValid && isRecord(frame)
    && isInteger(frame.width) && isInteger(frame.height)
    && (asset.anchor.x < 0
      || asset.anchor.y < 0
      || asset.anchor.x >= frame.width
      || asset.anchor.y >= frame.height)) {
    addError(errors, 'ANCHOR_OUT_OF_FRAME', `${path}.anchor`, 'anchor 必须位于 frame 内')
  }
  validateIntegerPoint(asset.offset, `${path}.offset`, errors, 'OFFSET_INTEGER')

  const footprint = Array.isArray(asset.footprint) ? asset.footprint : []
  if (!footprint.length) {
    addError(errors, 'FOOTPRINT_REQUIRED', `${path}.footprint`, '资产必须声明至少一个 footprint cell')
  }
  const footprintKeys = new Set()
  for (const [cellIndex, cell] of footprint.entries()) {
    const cellPath = `${path}.footprint[${cellIndex}]`
    if (!validateIntegerPoint(cell, cellPath, errors, 'FOOTPRINT_INTEGER')) continue
    const key = cellKey(cell)
    if (footprintKeys.has(key)) {
      addError(errors, 'FOOTPRINT_DUPLICATE', cellPath, `重复 footprint cell ${key}`)
    }
    footprintKeys.add(key)
    if (typeof cell.blocked !== 'boolean') {
      addError(errors, 'FOOTPRINT_BLOCKED_BOOLEAN', `${cellPath}.blocked`, 'blocked 必须是布尔值')
    }
  }

  const collision = Array.isArray(asset.collision) ? asset.collision : []
  const collisionKeys = []
  for (const [cellIndex, cell] of collision.entries()) {
    const cellPath = `${path}.collision[${cellIndex}]`
    if (validateIntegerPoint(cell, cellPath, errors, 'COLLISION_INTEGER')) collisionKeys.push(cellKey(cell))
  }
  if (new Set(collisionKeys).size !== collisionKeys.length) {
    addError(errors, 'COLLISION_DUPLICATE', `${path}.collision`, 'collision 不能包含重复 cell')
  }
  const blockedKeys = footprint
    .filter((cell) => cell?.blocked === true && isInteger(cell.x) && isInteger(cell.y))
    .map(cellKey)
  if (!sameStringSet(blockedKeys, collisionKeys)) {
    addError(
      errors,
      'FOOTPRINT_COLLISION_MISMATCH',
      path,
      'collision 必须与 footprint 中 blocked=true 的 cell 完全一致',
    )
  }

  if (asset.kind === 'character' && isRecord(frame)
    && (frame.width !== CHARACTER_FRAME.width || frame.height !== CHARACTER_FRAME.height)) {
    addError(
      errors,
      'CHARACTER_FRAME_SIZE',
      `${path}.frame`,
      `角色帧必须是 ${CHARACTER_FRAME.width}×${CHARACTER_FRAME.height}`,
    )
  }

  const interactionPoints = asset.interactionPoints
  if (interactionPoints != null && !Array.isArray(interactionPoints)) {
    addError(errors, 'INTERACTION_POINTS_ARRAY', `${path}.interactionPoints`, '交互点必须是数组')
  }
  const seenInteractionIds = new Set()
  for (const [pointIndex, point] of (Array.isArray(interactionPoints) ? interactionPoints : []).entries()) {
    const pointPath = `${path}.interactionPoints[${pointIndex}]`
    if (!isRecord(point)) {
      addError(errors, 'INTERACTION_POINT_OBJECT', pointPath, '交互点必须是对象')
      continue
    }
    if (typeof point.id !== 'string' || !point.id) {
      addError(errors, 'INTERACTION_POINT_ID', `${pointPath}.id`, '交互点必须有 id')
    } else if (seenInteractionIds.has(point.id)) {
      addError(errors, 'INTERACTION_POINT_DUPLICATE', `${pointPath}.id`, `交互点 “${point.id}” 重复`)
    } else {
      seenInteractionIds.add(point.id)
    }
    validateIntegerPoint(point, pointPath, errors, 'INTERACTION_POINT_INTEGER')
    if (point.kind !== 'work-seat') {
      addError(errors, 'INTERACTION_POINT_KIND', `${pointPath}.kind`, '当前只支持 work-seat 交互点')
    }
    if (!WORK_SEAT_FACINGS.includes(point.facing)) {
      addError(
        errors,
        'INTERACTION_POINT_FACING',
        `${pointPath}.facing`,
        '工作座位必须声明四向 facing',
      )
    }
  }
}

function validateAssets(manifest, errors) {
  const assets = Array.isArray(manifest.assets) ? manifest.assets : []
  const atlases = indexById(manifest.atlases)
  const sheets = indexById(manifest.sheets)
  validateUniqueField(assets, 'id', 'assets', errors, 'DUPLICATE_ASSET_ID')
  if (!assets.length) addError(errors, 'ASSET_REQUIRED', 'assets', '至少需要一个资产')

  for (const [index, asset] of assets.entries()) {
    const atlas = atlases.get(asset?.atlas)
    if (!atlas) {
      addError(errors, 'ATLAS_REFERENCE', `assets[${index}].atlas`, `未知 atlas “${asset?.atlas ?? ''}”`)
    }
    validateAssetGeometry(asset || {}, index, atlas, errors)
    const sheet = sheetForAsset(asset, sheets)
    if (asset?.sheet || sheet) {
      if (!sheet) {
        addError(errors, 'SHEET_REFERENCE', `assets[${index}].sheet`, `未知 sheet “${asset.sheet}”`)
      } else if (sheet.atlas !== asset.atlas) {
        addError(errors, 'SHEET_ATLAS_MISMATCH', `assets[${index}].sheet`, '资产与 sheet 必须使用同一 atlas')
      } else if (isRecord(asset.frame)) {
        const relativeX = asset.frame.x - sheet.frame.x
        const relativeY = asset.frame.y - sheet.frame.y
        if (asset.frame.width !== sheet.cellWidth
          || asset.frame.height !== sheet.cellHeight
          || relativeX < 0
          || relativeY < 0
          || relativeX % sheet.cellWidth !== 0
          || relativeY % sheet.cellHeight !== 0
          || relativeX / sheet.cellWidth >= sheet.columns
          || relativeY / sheet.cellHeight >= sheet.rows) {
          addError(errors, 'SHEET_CELL', `assets[${index}].frame`, 'sheet 资产必须精确落在一个 cell 内')
        }
      }
    }
  }

  for (const atlas of (Array.isArray(manifest.atlases) ? manifest.atlases : [])) {
    const regions = [
      ...(Array.isArray(manifest.sheets) ? manifest.sheets : [])
        .filter((sheet) => sheet?.atlas === atlas.id && isRecord(sheet.frame))
        .map((sheet) => ({ id: `sheet:${sheet.id}`, frame: sheet.frame })),
      ...assets
        .filter((asset) => asset?.atlas === atlas.id && !sheetForAsset(asset, sheets) && isRecord(asset.frame))
        .map((asset) => ({ id: asset.id, frame: asset.frame })),
    ]
    for (let leftIndex = 0; leftIndex < regions.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < regions.length; rightIndex += 1) {
        const left = regions[leftIndex]
        const right = regions[rightIndex]
        if (!framesHaveRequiredGap(left.frame, right.frame, atlas.padding)) {
          addError(
            errors,
            'ATLAS_FRAME_PADDING',
            `atlases.${atlas.id}`,
            `frame “${left.id}” 与 “${right.id}” 重叠或不足 ${atlas.padding}px 间距`,
          )
        }
      }
    }
  }

  for (const sheet of (Array.isArray(manifest.sheets) ? manifest.sheets : [])) {
    const occupiedCells = new Set()
    for (const [index, asset] of assets.entries()) {
      if (sheetForAsset(asset, sheets)?.id !== sheet.id || !isRecord(asset.frame)) continue
      const key = `${asset.frame.x},${asset.frame.y}`
      if (occupiedCells.has(key)) {
        addError(errors, 'SHEET_CELL_DUPLICATE', `assets[${index}].frame`, `sheet cell ${key} 被重复占用`)
      }
      occupiedCells.add(key)
    }
  }
}

function validateAnimations(manifest, errors) {
  const animations = Array.isArray(manifest.animations) ? manifest.animations : []
  const assets = indexById(manifest.assets)
  validateUniqueField(animations, 'id', 'animations', errors, 'DUPLICATE_ANIMATION_ID')

  const renderableIds = new Set()
  for (const [kind, items] of [['asset', manifest.assets], ['animation', animations]]) {
    for (const item of (Array.isArray(items) ? items : [])) {
      if (renderableIds.has(item?.id)) {
        addError(errors, 'DUPLICATE_RENDERABLE_ID', `${kind}s`, `renderable id “${item?.id}” 必须全局唯一`)
      }
      renderableIds.add(item?.id)
    }
  }

  for (const [index, animation] of animations.entries()) {
    const path = `animations[${index}]`
    const anchorValid = validateIntegerPoint(animation?.anchor, `${path}.anchor`, errors, 'ANIMATION_ANCHOR_INTEGER')
    if (!isInteger(animation?.frameDurationMs) || animation.frameDurationMs <= 0) {
      addError(errors, 'ANIMATION_DURATION', `${path}.frameDurationMs`, 'frameDurationMs 必须是正整数')
    }
    if (animation?.frameDurationsMs !== undefined) {
      const perFrame = animation.frameDurationsMs
      const frameCount = Array.isArray(animation?.frames) ? animation.frames.length : 0
      if (!Array.isArray(perFrame)
        || perFrame.length !== frameCount
        || perFrame.some((value) => !isInteger(value) || value <= 0)) {
        addError(
          errors,
          'ANIMATION_FRAME_DURATIONS',
          `${path}.frameDurationsMs`,
          'frameDurationsMs 必须是正整数数组，且长度等于 frames',
        )
      }
    }
    if (animation?.motion !== undefined) {
      const motion = animation.motion
      if (!isRecord(motion)
        || !isInteger(motion.strideScreenPx) || motion.strideScreenPx <= 0
        || !isInteger(motion.framesPerStep) || motion.framesPerStep <= 0) {
        addError(
          errors,
          'ANIMATION_MOTION',
          `${path}.motion`,
          'motion 必须声明正整数 strideScreenPx 与 framesPerStep',
        )
      }
    }
    if (!Array.isArray(animation?.frames) || !animation.frames.length) {
      addError(errors, 'ANIMATION_FRAMES', `${path}.frames`, '动画必须至少引用一帧')
      continue
    }
    let sharedOffset = null
    for (const [frameIndex, assetId] of animation.frames.entries()) {
      const asset = assets.get(assetId)
      if (!asset) {
        addError(errors, 'ANIMATION_FRAME_REFERENCE', `${path}.frames[${frameIndex}]`, `未知资产 “${assetId}”`)
        continue
      }
      if (anchorValid && (asset.anchor?.x !== animation.anchor.x || asset.anchor?.y !== animation.anchor.y)) {
        addError(
          errors,
          'ANIMATION_FOOT_DRIFT',
          `${path}.frames[${frameIndex}]`,
          `动画帧 “${assetId}” 未共享 anchor`,
        )
      }
      const offset = `${asset.offset?.x},${asset.offset?.y}`
      if (sharedOffset == null) sharedOffset = offset
      else if (offset !== sharedOffset) {
        addError(
          errors,
          'ANIMATION_FOOT_DRIFT',
          `${path}.frames[${frameIndex}]`,
          `动画帧 “${assetId}” 的 offset 导致脚底漂移`,
        )
      }
    }
  }
}

function gusFramesForAction(direction, action, layout = GUS_LAYOUT) {
  return Array.from(
    { length: layout.frameCounts[action] },
    (_, index) => (action === 'idle' && index === 0
      ? `character.gus.${direction}.idle`
      : `character.gus.${direction}.${action}.${index}`),
  )
}

function validateCoreGeometry(manifest, errors) {
  const assets = Array.isArray(manifest.assets) ? manifest.assets : []
  if (manifest.id === 'core-v0') {
    for (const candidate of assets) {
      if (/plant/i.test(`${candidate?.id || ''} ${candidate?.slot || ''}`)) {
        addError(errors, 'CORE_PLANT_FORBIDDEN', 'assets', 'core-v0 不得包含 plant 资产')
      }
    }
  }

  for (const [slot, expectedCells] of Object.entries(CORE_FURNITURE_FOOTPRINTS)) {
    const candidate = assets.find((asset) => asset?.slot === slot)
    if (!candidate) continue
    const actual = (candidate.footprint || [])
      .filter((cell) => cell?.blocked === true)
      .map(cellKey)
    const expected = expectedCells.map(([x, y]) => `${x},${y}`)
    if (candidate.footprint?.length !== expectedCells.length || !sameStringSet(actual, expected)) {
      addError(
        errors,
        'CORE_FOOTPRINT',
        `assets.${candidate.id}.footprint`,
        `槽位 “${slot}” footprint 不符合 core-v0 固定尺寸`,
      )
    }
  }

  if (manifest.id === 'core-v1' || manifest.id === 'core-v2') {
    const desk = assets.find((asset) => asset?.slot === 'furniture.desk-island')
    const points = Array.isArray(desk?.interactionPoints) ? desk.interactionPoints : []
    const expected = CORE_V1_DESK_SEATS
    const matches = points.length === expected.length && expected.every((seat, index) => (
      points[index]?.id === seat.id
      && points[index]?.kind === seat.kind
      && points[index]?.x === seat.x
      && points[index]?.y === seat.y
      && points[index]?.facing === seat.facing
    ))
    if (!matches) {
      addError(
        errors,
        'CORE_V1_DESK_SEATS',
        'assets.furniture.desk-island.interactionPoints',
        `${manifest.id} 桌岛必须按 seat-se/seat-sw/seat-nw/seat-ne 声明四个固定工作座位`,
      )
    }
  }

  if (manifest.id === 'core-v2') {
    for (const [slot, expected] of Object.entries(CORE_V2_WALL_GROUND_AXES)) {
      const candidate = assets.find((asset) => asset?.slot === slot)
      if (!candidate) continue
      const path = `assets.${candidate.id}`
      if (candidate.orientation !== expected.orientation) {
        addError(
          errors,
          'CORE_V2_WALL_ORIENTATION',
          `${path}.orientation`,
          `槽位 “${slot}” orientation 必须是 ${expected.orientation}`,
        )
      }
      const wallFaceHeight = candidate.wallFaceHeight
      if (wallFaceHeight === undefined || wallFaceHeight === null) {
        addError(
          errors,
          'CORE_V2_WALL_FACE_HEIGHT_REQUIRED',
          `${path}.wallFaceHeight`,
          `槽位 “${slot}” 必须声明 wallFaceHeight`,
        )
      } else if (!Number.isInteger(wallFaceHeight) || wallFaceHeight !== CORE_V2_WALL_FACE_HEIGHT) {
        addError(
          errors,
          'CORE_V2_WALL_FACE_HEIGHT_VALUE',
          `${path}.wallFaceHeight`,
          `wallFaceHeight 必须是整数 ${CORE_V2_WALL_FACE_HEIGHT}`,
        )
      }
      const axis = candidate.groundAxis
      if (!isRecord(axis)) {
        addError(
          errors,
          'CORE_V2_WALL_GROUND_AXIS_REQUIRED',
          `${path}.groundAxis`,
          `槽位 “${slot}” 必须声明原生 groundAxis`,
        )
        continue
      }
      const startValid = validateIntegerPoint(
        axis.start,
        `${path}.groundAxis.start`,
        errors,
        'CORE_V2_WALL_GROUND_AXIS_POINT',
      )
      const endValid = validateIntegerPoint(
        axis.end,
        `${path}.groundAxis.end`,
        errors,
        'CORE_V2_WALL_GROUND_AXIS_POINT',
      )
      if (!startValid || !endValid) continue
      const frameWidth = Number(candidate.frame?.width)
      const frameHeight = Number(candidate.frame?.height)
      for (const [name, point] of [['start', axis.start], ['end', axis.end]]) {
        if (!Number.isInteger(frameWidth)
          || !Number.isInteger(frameHeight)
          || point.x < 0
          || point.y < 0
          || point.x >= frameWidth
          || point.y >= frameHeight) {
          addError(
            errors,
            'CORE_V2_WALL_GROUND_AXIS_BOUNDS',
            `${path}.groundAxis.${name}`,
            `groundAxis ${name} 必须位于原生 frame 内`,
          )
        }
      }
      if (wallFaceHeight === CORE_V2_WALL_FACE_HEIGHT) {
        for (const [name, point] of [['start', axis.start], ['end', axis.end]]) {
          const topPoint = {
            x: point.x,
            y: point.y - wallFaceHeight,
          }
          if (!Number.isInteger(frameWidth)
            || !Number.isInteger(frameHeight)
            || topPoint.x < 0
            || topPoint.y < 0
            || topPoint.x >= frameWidth
            || topPoint.y >= frameHeight) {
            addError(
              errors,
              'CORE_V2_WALL_TOP_AXIS_BOUNDS',
              `${path}.wallFaceHeight`,
              `groundAxis ${name} 上移 ${CORE_V2_WALL_FACE_HEIGHT}px 后的 topAxis 必须位于原生 frame 内`,
            )
          }
        }
      }
      if ((axis.start.x + axis.end.x) !== Number(candidate.anchor?.x) * 2
        || (axis.start.y + axis.end.y) !== Number(candidate.anchor?.y) * 2) {
        addError(
          errors,
          'CORE_V2_WALL_GROUND_AXIS_MIDPOINT',
          `${path}.groundAxis`,
          'groundAxis 中点必须与 anchor 完全一致',
        )
      }
      const deltaX = axis.end.x - axis.start.x
      const deltaY = axis.end.y - axis.start.y
      if (Math.abs(deltaX) !== expected.dx || Math.abs(deltaY) !== expected.dy) {
        addError(
          errors,
          'CORE_V2_WALL_GROUND_AXIS_DELTA',
          `${path}.groundAxis`,
          `groundAxis 绝对跨度必须是 ${expected.dx}×${expected.dy}`,
        )
      }
      const slopeSign = Math.sign(deltaX * deltaY)
      const expectedSign = expected.orientation === 'nw' ? 1 : -1
      if (slopeSign !== expectedSign) {
        addError(
          errors,
          'CORE_V2_WALL_GROUND_AXIS_SLOPE',
          `${path}.groundAxis`,
          `groundAxis 斜率方向必须匹配 ${expected.orientation}`,
        )
      }
    }

    const expectedFootprint = ['0,0', '0,1', '1,0', '1,1']
    for (const [slot, expectedSeats] of Object.entries(CORE_V2_FOCUS_DESK_SEATS)) {
      const candidate = assets.find((asset) => asset?.slot === slot)
      if (!candidate) continue
      const actualFootprint = (candidate.footprint || []).map(cellKey)
      const actualCollision = (candidate.collision || []).map(cellKey)
      if (!sameStringSet(actualFootprint, expectedFootprint)
        || !sameStringSet(actualCollision, expectedFootprint)
        || candidate.footprint?.some((cell) => cell.blocked !== true)) {
        addError(
          errors,
          'CORE_V2_FOCUS_DESK_FOOTPRINT',
          `assets.${candidate.id}.footprint`,
          `槽位 “${slot}” 必须声明 2×2 全阻挡 footprint/collision`,
        )
      }
      const points = Array.isArray(candidate.interactionPoints) ? candidate.interactionPoints : []
      const expected = expectedSeats[0]
      const actual = points[0]
      if (points.length !== 1
        || actual?.id !== expected.id
        || actual?.kind !== expected.kind
        || actual?.x !== expected.x
        || actual?.y !== expected.y
        || actual?.facing !== expected.facing) {
        addError(
          errors,
          'CORE_V2_FOCUS_DESK_SEAT',
          `assets.${candidate.id}.interactionPoints`,
          `槽位 “${slot}” 必须声明固定工作座位 ${expected.id}`,
        )
      }
    }
  }
}

function validateGusContract(manifest, errors) {
  const sheets = Array.isArray(manifest.sheets) ? manifest.sheets : []
  const gusSheet = sheets.find((sheet) => sheet?.id === 'character.gus')
  // Resolve which known layout this manifest claims before checking anything
  // else, so an older pack is measured against the shape it was built for.
  const matchedLayout = gusLayoutForSheet(gusSheet)
  if (gusSheet && !matchedLayout) {
    // The manifest is internally consistent but describes a sheet shape this
    // build has never heard of — almost always a page running older code than
    // the server. Say that once and stop: measuring 64 frames against a
    // 28-frame layout produces a page of misleading per-frame errors that send
    // people looking at the art instead of at the reload button.
    const known = GUS_LAYOUTS.map((entry) => `${entry.sheet.columns}×${entry.sheet.rows}`).join('、')
    addError(
      errors,
      'GUS_SHEET_UNKNOWN',
      'sheets',
      `Gus sheet 是 ${gusSheet.columns}×${gusSheet.rows}，本页面只认识 ${known}；`
      + '页面代码比资产包旧，请硬刷新（Cmd/Ctrl+Shift+R）后重试',
    )
    return
  }
  const layout = matchedLayout ?? GUS_LAYOUT
  if (sheets.length !== 1
    || !gusSheet
    || gusSheet.frame?.width !== layout.sheet.width
    || gusSheet.frame?.height !== layout.sheet.height
    || gusSheet.columns !== layout.sheet.columns
    || gusSheet.rows !== layout.sheet.rows
    || gusSheet.cellWidth !== CHARACTER_FRAME.width
    || gusSheet.cellHeight !== CHARACTER_FRAME.height) {
    const shapes = GUS_LAYOUTS.map((entry) => `${entry.sheet.width}×${entry.sheet.height}`).join(' 或 ')
    addError(errors, 'GUS_SHEET', 'sheets', `Gus sheet 必须是 ${shapes}、cell 24×48`)
  }
  const characterAssets = (Array.isArray(manifest.assets) ? manifest.assets : [])
    .filter((asset) => asset?.kind === 'character')
  const characterIds = characterAssets.map((asset) => asset.id)
  if (characterIds.length !== layout.frameIds.length
    || characterIds.some((id, index) => id !== layout.frameIds[index])) {
    const counts = GUS_ACTIONS.map((action) => `${layout.frameCounts[action]} ${action}`).join('、')
    addError(
      errors,
      'GUS_FRAME_SEQUENCE',
      'assets',
      `Gus 必须按 southeast/southwest/northwest/northeast 顺序提供每方向 ${counts}，共 ${layout.frameIds.length} 帧`,
    )
  }
  for (const [index, asset] of characterAssets.entries()) {
    if (asset.slot !== `frame.${asset.id}`) {
      addError(errors, 'GUS_FRAME_SLOT', `assets.${asset.id}.slot`, 'Gus 内部帧 slot 必须使用 frame.character.gus.*')
    }
    if (sheetForAsset(asset, indexById(manifest.sheets))?.id !== 'character.gus') {
      addError(errors, 'GUS_FRAME_SHEET', `assets.${asset.id}.sheet`, `Gus 的 ${layout.frameIds.length} 帧必须来自 character.gus sheet`)
    }
    if (asset.anchor?.x !== GUS_ANCHOR.x || asset.anchor?.y !== GUS_ANCHOR.y) {
      addError(errors, 'GUS_ANCHOR', `assets.${asset.id}.anchor`, 'Gus 每帧共享 anchor 必须是 {x:12,y:46}')
    }
    if (gusSheet) {
      const expectedX = gusSheet.frame.x + (index % layout.sheet.columns) * CHARACTER_FRAME.width
      const expectedY = gusSheet.frame.y + Math.floor(index / layout.sheet.columns) * CHARACTER_FRAME.height
      if (asset.frame?.x !== expectedX || asset.frame?.y !== expectedY) {
        addError(
          errors,
          'GUS_SHEET_ORDER',
          `assets.${asset.id}.frame`,
          `Gus sheet 必须每行一个方向，列顺序为 ${layout.columnOrder.join('、')}`,
        )
      }
    }
  }

  const expectedAnimations = []
  for (const direction of GUS_DIRECTIONS) {
    for (const action of GUS_ACTIONS) {
      expectedAnimations.push({
        id: `animation.gus.${direction}.${action}`,
        frames: gusFramesForAction(direction, action, layout),
        slot: direction === GUS_DIRECTIONS[0] && action === 'idle'
          ? 'character.gus'
          : `animation.gus.${direction}.${action}`,
      })
    }
  }
  const gusAnimations = (Array.isArray(manifest.animations) ? manifest.animations : [])
    .filter((animation) => animation?.id?.startsWith('animation.gus.'))
  if (gusAnimations.length !== expectedAnimations.length
    || gusAnimations.some((animation, index) => animation.id !== expectedAnimations[index].id)) {
    addError(
      errors,
      'GUS_ANIMATION_SEQUENCE',
      'animations',
      'Gus 动画必须按四方向与 idle/walk/work 固定顺序声明',
    )
  }
  const animationIndex = indexById(gusAnimations)
  for (const expected of expectedAnimations) {
    const animation = animationIndex.get(expected.id)
    if (!animation) continue
    if (animation.slot !== expected.slot) {
      addError(
        errors,
        'GUS_ANIMATION_SLOT',
        `animations.${animation.id}.slot`,
        `Gus 动画 slot 必须是 “${expected.slot}”`,
      )
    }
    if (animation.anchor?.x !== GUS_ANCHOR.x || animation.anchor?.y !== GUS_ANCHOR.y) {
      addError(errors, 'GUS_ANCHOR', `animations.${animation.id}.anchor`, 'Gus 动画 anchor 必须是 {x:12,y:46}')
    }
    if (animation.frames?.length !== expected.frames.length
      || animation.frames.some((frame, index) => frame !== expected.frames[index])) {
      addError(
        errors,
        'GUS_ANIMATION_FRAMES',
        `animations.${animation.id}.frames`,
        `Gus ${animation.id} 帧组成不符合批准计划`,
      )
    }
  }
}

function validateCharacterMotion(manifest, errors) {
  const motion = manifest.characterMotion
  // Releases made before the identity-lock contract remain readable.  The
  // runtime treats a missing policy as untrusted and uses the safe idle frame.
  if (motion == null) return
  if (!isRecord(motion)
    || motion.policy !== GUS_MOTION_POLICY.policy
    || motion.fallback !== GUS_MOTION_POLICY.fallback
    || typeof motion.identityLocked !== 'boolean'
    || !isRecord(motion.canonicalFrames)) {
    addError(
      errors,
      'GUS_MOTION_POLICY',
      'characterMotion',
      '人物动画必须声明 canonical-idle-v1 身份锁与 canonical-idle-bob 安全回退',
    )
    return
  }
  if (motion.trustedLegacyAcceptedMotion != null
    && typeof motion.trustedLegacyAcceptedMotion !== 'boolean') {
    addError(
      errors,
      'GUS_LEGACY_MOTION_TRUST',
      'characterMotion.trustedLegacyAcceptedMotion',
      '旧动作信任标记必须是 boolean',
    )
  }
  if (motion.trustedLegacyAcceptedMotion === true
    && (!['core-v1', 'core-v2'].includes(manifest.id)
      || typeof manifest.baseReleaseId !== 'string'
      || manifest.baseReleaseId.length === 0
      || motion.identityLocked === true)) {
    addError(
      errors,
      'GUS_LEGACY_MOTION_TRUST',
      'characterMotion.trustedLegacyAcceptedMotion',
      '旧动作信任只允许用于绑定冻结 base release 且尚无新版身份锁的派生 core pack',
    )
  }
  for (const direction of GUS_DIRECTIONS) {
    const expected = `character.gus.${direction}.idle`
    if (motion.canonicalFrames[direction] !== expected) {
      addError(
        errors,
        'GUS_CANONICAL_FRAME',
        `characterMotion.canonicalFrames.${direction}`,
        `方向 ${direction} 的身份母版必须是 “${expected}”`,
      )
    }
  }
}

function renderableForPlacement(manifest, placement) {
  const assets = indexById(manifest.assets)
  if (placement?.assetId) return assets.get(placement.assetId) || null
  const animation = indexById(manifest.animations).get(placement?.animationId)
  return animation ? assets.get(animation.frames?.[0]) || null : null
}

function validateFixtures(manifest, errors) {
  const fixtures = Array.isArray(manifest.fixtures) ? manifest.fixtures : []
  validateUniqueField(fixtures, 'id', 'fixtures', errors, 'DUPLICATE_FIXTURE_ID')
  const fixtureIds = fixtures.map((fixture) => fixture?.id).filter(Boolean)
  if (fixtures.length !== FIXED_FIXTURE_IDS.length || !sameStringSet(fixtureIds, FIXED_FIXTURE_IDS)) {
    addError(
      errors,
      'FIXED_FIXTURES',
      'fixtures',
      `fixtures 必须恰好是 ${FIXED_FIXTURE_IDS.join(', ')}`,
    )
  }

  const assetIds = new Set((manifest.assets || []).map((asset) => asset.id))
  const animationIds = new Set((manifest.animations || []).map((animation) => animation.id))
  for (const [fixtureIndex, fixture] of fixtures.entries()) {
    const path = `fixtures[${fixtureIndex}]`
    if (!isInteger(fixture?.columns) || !isInteger(fixture?.rows) || fixture.columns <= 0 || fixture.rows <= 0) {
      addError(errors, 'FIXTURE_SIZE', path, 'fixture columns/rows 必须是正整数')
    }
    validateIntegerPoint(fixture?.origin, `${path}.origin`, errors, 'FIXTURE_ORIGIN_INTEGER')
    const camera = fixture?.camera
    if (!isRecord(camera)
      || !isInteger(camera.x)
      || !isInteger(camera.y)
      || !Number.isFinite(camera.zoom)
      || camera.zoom <= 0) {
      addError(errors, 'FIXTURE_CAMERA', `${path}.camera`, 'fixture camera 必须含整数 x/y 与正数 zoom')
    }
    const placements = Array.isArray(fixture?.placements) ? fixture.placements : []
    validateUniqueField(placements, 'id', `${path}.placements`, errors, 'DUPLICATE_PLACEMENT_ID')
    for (const [placementIndex, placement] of placements.entries()) {
      const placementPath = `${path}.placements[${placementIndex}]`
      if (!validateIntegerPoint(placement, placementPath, errors, 'PLACEMENT_INTEGER')) continue
      const references = Number(Boolean(placement.assetId)) + Number(Boolean(placement.animationId))
      if (references !== 1) {
        addError(errors, 'PLACEMENT_REFERENCE', placementPath, 'placement 必须且只能引用 assetId 或 animationId')
        continue
      }
      if (placement.assetId && !assetIds.has(placement.assetId)) {
        addError(errors, 'PLACEMENT_ASSET_REFERENCE', `${placementPath}.assetId`, `未知资产 “${placement.assetId}”`)
      }
      if (placement.animationId && !animationIds.has(placement.animationId)) {
        addError(
          errors,
          'PLACEMENT_ANIMATION_REFERENCE',
          `${placementPath}.animationId`,
          `未知动画 “${placement.animationId}”`,
        )
      }
      if ('depth' in placement) {
        addError(errors, 'MANUAL_DEPTH_FORBIDDEN', `${placementPath}.depth`, `深度必须由 ${DEPTH_RULE} 派生`)
      }
      const asset = renderableForPlacement(manifest, placement)
      for (const cell of (asset?.footprint || [])) {
        const x = placement.x + cell.x
        const y = placement.y + cell.y
        if (x < 0 || y < 0 || x >= fixture.columns || y >= fixture.rows) {
          addError(errors, 'PLACEMENT_OUT_OF_GRID', placementPath, `footprint cell ${x},${y} 超出 fixture`)
        }
      }
    }
  }
}

function validateSlots(manifest, errors) {
  const required = Array.isArray(manifest.requiredSlots) ? manifest.requiredSlots : []
  if (new Set(required).size !== required.length) {
    addError(errors, 'DUPLICATE_REQUIRED_SLOT', 'requiredSlots', 'requiredSlots 不能重复')
  }
  const provided = [...(manifest.assets || []), ...(manifest.animations || [])]
    .map((item) => item?.slot)
    .filter(Boolean)
  if (new Set(provided).size !== provided.length) {
    addError(errors, 'DUPLICATE_SLOT', 'assets/animations', '每个 slot 只能由一个资产或动画提供')
  }
  for (const slot of required) {
    if (!provided.includes(slot)) {
      addError(errors, 'REQUIRED_SLOT_MISSING', 'requiredSlots', `必需槽位 “${slot}” 没有实现`)
    }
  }
}

export function validateAssetManifest(manifest) {
  const errors = []
  if (!isRecord(manifest)) {
    return {
      valid: false,
      errors: [{ code: 'MANIFEST_OBJECT', path: '', message: 'manifest 必须是对象' }],
    }
  }
  if (manifest.schemaVersion !== ASSET_CONTRACT_VERSION) {
    addError(
      errors,
      'SCHEMA_VERSION',
      'schemaVersion',
      `schemaVersion 必须是 ${ASSET_CONTRACT_VERSION}`,
    )
  }
  if (manifest.grid?.tileWidth !== TILE_METRICS.width
    || manifest.grid?.tileHeight !== TILE_METRICS.height
    || manifest.grid?.elevation !== TILE_METRICS.elevation) {
    addError(errors, 'GRID_METRICS', 'grid', '网格必须是 32×16，elevation=8')
  }
  if (manifest.grid?.depthRule !== DEPTH_RULE) {
    addError(errors, 'DEPTH_RULE', 'grid.depthRule', `深度规则必须是 ${DEPTH_RULE}`)
  }
  if (manifest.characterFrame?.width !== CHARACTER_FRAME.width
    || manifest.characterFrame?.height !== CHARACTER_FRAME.height) {
    addError(errors, 'CHARACTER_METRICS', 'characterFrame', '角色帧必须是 24×48')
  }

  validatePalette(manifest, errors)
  validateSceneShell(manifest, errors)
  validateAtlasDefinitions(manifest, errors)
  validateSheetDefinitions(manifest, errors)
  validateAssets(manifest, errors)
  validateAnimations(manifest, errors)
  validateCoreGeometry(manifest, errors)
  validateGusContract(manifest, errors)
  validateCharacterMotion(manifest, errors)
  validateSlots(manifest, errors)
  validateFixtures(manifest, errors)

  errors.sort((left, right) => compareStrings(left.path, right.path) || compareStrings(left.code, right.code))
  return { valid: errors.length === 0, errors }
}

export class AssetManifestError extends Error {
  constructor(errors) {
    super(errors.map((error) => `${error.path || '<root>'}: ${error.message}`).join('\n'))
    this.name = 'AssetManifestError'
    this.errors = errors
  }
}

export function assertAssetManifest(manifest) {
  const result = validateAssetManifest(manifest)
  if (!result.valid) throw new AssetManifestError(result.errors)
  return manifest
}

export function depthForPlacement(asset, placement) {
  const footprint = Array.isArray(asset?.footprint) && asset.footprint.length
    ? asset.footprint
    : [{ x: 0, y: 0 }]
  return Math.max(...footprint.map((cell) => (
    Number(placement?.x) + Number(cell.x) + Number(placement?.y) + Number(cell.y)
  )))
}

/**
 * Return the ground anchor for a placed asset.
 *
 * Layout placement coordinates identify the first/top-left footprint cell so
 * collision and interaction points can remain integer grid coordinates.  A
 * sprite anchor, however, is authored at the centre of the complete footprint.
 * Multi-cell sprites must therefore be projected from the footprint bounds'
 * centre instead of from the first cell.
 */
export function groundPointForPlacement(asset, placement) {
  const footprint = Array.isArray(asset?.footprint) && asset.footprint.length
    ? asset.footprint
    : [{ x: 0, y: 0 }]
  const xs = footprint.map((cell) => Number(cell.x))
  const ys = footprint.map((cell) => Number(cell.y))
  return {
    x: Number(placement?.x) + (Math.min(...xs) + Math.max(...xs)) / 2,
    y: Number(placement?.y) + (Math.min(...ys) + Math.max(...ys)) / 2,
  }
}

export function projectGridPoint(x, y, {
  origin = { x: 0, y: 0 },
  camera = { x: 0, y: 0, zoom: 1 },
  tile = TILE_METRICS,
} = {}) {
  const zoom = Number(camera.zoom) || 1
  return {
    x: Number(origin.x) + Number(camera.x || 0) + (Number(x) - Number(y)) * (tile.width / 2) * zoom,
    y: Number(origin.y) + Number(camera.y || 0) + (Number(x) + Number(y)) * (tile.height / 2) * zoom,
  }
}

export function animationFrameDurations(animation) {
  const count = Array.isArray(animation?.frames) ? animation.frames.length : 0
  const perFrame = animation?.frameDurationsMs
  if (Array.isArray(perFrame) && perFrame.length === count) return perFrame.map(Number)
  return Array.from({ length: count }, () => Number(animation?.frameDurationMs))
}

export function animationTotalDuration(animation) {
  return animationFrameDurations(animation).reduce((total, value) => total + value, 0)
}

export function resolveAnimationFrame(manifest, animationId, elapsedMs = 0) {
  const animation = indexById(manifest?.animations).get(animationId)
  if (!animation) throw new RangeError(`未知动画 “${animationId}”`)
  // Per-frame durations let a cycle hold on the poses that carry weight — the
  // top of an idle breath, a walk contact — instead of marching every frame at
  // one rate.  Animations without them behave exactly as before.
  const durations = animationFrameDurations(animation)
  const total = animationTotalDuration(animation)
  const elapsed = Math.max(0, Number(elapsedMs) || 0)
  const local = animation.loop === false
    ? Math.min(elapsed, Math.max(0, total - 1))
    : elapsed % total
  let frameIndex = durations.length - 1
  let frameStart = 0
  let cursor = 0
  for (const [index, value] of durations.entries()) {
    if (local < cursor + value) {
      frameIndex = index
      frameStart = cursor
      break
    }
    cursor += value
    frameStart = cursor
  }
  let assetId = animation.frames[frameIndex]
  let motionFallback = false
  let proceduralOffset = { x: 0, y: 0 }
  const characterMatch = String(animationId).match(
    /^animation\.gus\.(southeast|southwest|northwest|northeast)\.(idle|walk|work)$/,
  )
  const legacyAcceptedMotion = manifest?.characterMotion?.trustedLegacyAcceptedMotion === true
    && manifest?.characterMotion?.identityLocked === false
    && ['core-v1', 'core-v2'].includes(manifest?.id)
    && typeof manifest?.baseReleaseId === 'string'
    && manifest.baseReleaseId.length > 0
  const motionTrusted = manifest?.characterMotion?.identityLocked === true || legacyAcceptedMotion
  if (characterMatch && characterMatch[2] !== 'idle' && !motionTrusted) {
    const [, direction, action] = characterMatch
    assetId = manifest?.characterMotion?.canonicalFrames?.[direction]
      || `character.gus.${direction}.idle`
    motionFallback = true
    if (action === 'walk') {
      proceduralOffset = { x: 0, y: frameIndex % 2 === 1 ? -1 : 0 }
    }
  }
  const asset = indexById(manifest.assets).get(assetId)
  if (!asset) throw new RangeError(`动画 “${animationId}” 引用了未知资产 “${assetId}”`)
  return {
    animation,
    frameIndex,
    elapsedInFrame: local - frameStart,
    assetId,
    asset,
    motionFallback,
    proceduralOffset,
  }
}

export function projectFixture(manifest, fixtureOrId, { elapsedMs = 0, camera, origin } = {}) {
  const fixture = typeof fixtureOrId === 'string'
    ? indexById(manifest?.fixtures).get(fixtureOrId)
    : fixtureOrId
  if (!fixture) throw new RangeError(`未知 fixture “${fixtureOrId}”`)
  const assets = indexById(manifest.assets)
  const activeCamera = camera || fixture.camera
  const activeOrigin = origin || fixture.origin
  return fixture.placements.map((placement) => {
    const resolved = placement.animationId
      ? resolveAnimationFrame(manifest, placement.animationId, placement.elapsedMs ?? elapsedMs)
      : { assetId: placement.assetId, asset: assets.get(placement.assetId), frameIndex: 0 }
    if (!resolved.asset) throw new RangeError(`placement “${placement.id}” 引用了未知资产`)
    const renderGround = groundPointForPlacement(resolved.asset, placement)
    const ground = projectGridPoint(renderGround.x, renderGround.y, {
      origin: activeOrigin,
      camera: activeCamera,
    })
    const zoom = Number(activeCamera?.zoom) || 1
    const destination = {
      x: Math.round(ground.x + (resolved.asset.offset.x - resolved.asset.anchor.x) * zoom),
      y: Math.round(ground.y + (resolved.asset.offset.y - resolved.asset.anchor.y) * zoom),
      width: Math.round(resolved.asset.frame.width * zoom),
      height: Math.round(resolved.asset.frame.height * zoom),
    }
    return {
      ...placement,
      assetId: resolved.assetId,
      asset: resolved.asset,
      frameIndex: resolved.frameIndex,
      ground,
      destination,
      depth: depthForPlacement(resolved.asset, placement),
      layer: Number(resolved.asset.layer) || 0,
    }
  })
}

export function sortFixtureForOcclusion(projectedPlacements) {
  return [...projectedPlacements].sort((left, right) => (
    left.depth - right.depth
    || left.layer - right.layer
    || compareStrings(left.id, right.id)
  ))
}

export function fixtureCollisionCells(manifest, fixtureOrId) {
  const projected = projectFixture(manifest, fixtureOrId)
  const cells = new Map()
  for (const placement of projected) {
    for (const cell of placement.asset.collision || []) {
      const worldCell = { x: placement.x + cell.x, y: placement.y + cell.y }
      cells.set(cellKey(worldCell), worldCell)
    }
  }
  return [...cells.values()].sort((left, right) => left.y - right.y || left.x - right.x)
}

export function layoutAtlasFrames(entries, {
  width = 512,
  height = 512,
  padding = ATLAS_PADDING,
} = {}) {
  if (!isInteger(width) || !isInteger(height) || width <= 0 || height <= 0) {
    throw new TypeError('atlas width/height 必须是正整数')
  }
  if (!isInteger(padding) || padding < ATLAS_PADDING) {
    throw new TypeError(`atlas padding 不得小于 ${ATLAS_PADDING}`)
  }
  const items = (Array.isArray(entries) ? entries : [])
    .map((entry) => ({ id: entry.id, width: entry.width, height: entry.height }))
    .sort((left, right) => compareStrings(left.id, right.id))
  if (new Set(items.map((item) => item.id)).size !== items.length) {
    throw new TypeError('atlas layout id 必须唯一')
  }

  let x = padding
  let y = padding
  let rowHeight = 0
  const frames = []
  for (const item of items) {
    if (typeof item.id !== 'string' || !item.id || !isInteger(item.width) || !isInteger(item.height)
      || item.width <= 0 || item.height <= 0) {
      throw new TypeError('atlas layout entry 必须包含 id 与正整数 width/height')
    }
    if (item.width > width - padding * 2 || item.height > height - padding * 2) {
      throw new RangeError(`资产 “${item.id}” 无法放入 atlas`)
    }
    if (x + item.width > width - padding) {
      x = padding
      y += rowHeight + padding
      rowHeight = 0
    }
    if (y + item.height > height - padding) {
      throw new RangeError(`atlas 空间不足，无法放置 “${item.id}”`)
    }
    frames.push({ id: item.id, x, y, width: item.width, height: item.height })
    x += item.width + padding
    rowHeight = Math.max(rowHeight, item.height)
  }
  return frames
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue)
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.keys(value)
      .sort(compareStrings)
      .map((key) => [key, canonicalValue(value[key])]),
  )
}

export function stableManifestStringify(manifest, space = 2) {
  return JSON.stringify(canonicalValue(manifest), null, space)
}
