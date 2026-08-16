import {
  AssetPreview,
  decodePngBlob,
  validateVersionMetadata,
  versionAnchor,
  versionFrames,
  versionFootprint,
} from './assets-preview.mjs'
import {
  CORE_V0_REQUIRED_SLOTS,
  CORE_V1_REQUIRED_SLOTS,
  CORE_V2_REQUIRED_SLOTS,
  validateAssetManifest,
} from './asset-manifest.mjs'

const DEFAULT_API_BASE = '/api/assets'
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const REVIEW_DECISIONS = new Set(['accepted', 'rejected'])
const MAX_NOTE_LENGTH = 2_000
const MAX_REVIEW_BATCH_ITEMS = 200
const ANIMATION_ACTION_LABELS = Object.freeze({ walk: '行走', idle: '待机', work: '工作' })
const ANIMATION_DIRECTION_LABELS = Object.freeze({
  southeast: '右下',
  southwest: '左下',
  northwest: '左上',
  northeast: '右上',
})

function firstValue(source, keys, fallback = undefined) {
  for (const key of keys) {
    if (source?.[key] !== undefined && source[key] !== null) return source[key]
  }
  return fallback
}

function asArray(value) {
  return Array.isArray(value) ? value : []
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function finite(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function integer(value, fallback = 0) {
  return Math.round(finite(value, fallback))
}

function formatBytes(value) {
  const bytes = Math.max(0, finite(value))
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

function dateLabel(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return String(value)
  return date.toLocaleString([], { hour12: false })
}

function shortHash(value) {
  const text = String(value || '')
  return text ? `${text.slice(0, 10)}${text.length > 10 ? '…' : ''}` : '—'
}

function optionLabel(value) {
  const labels = {
    accepted: '已接受',
    rejected: '已拒绝',
    draft: '待验收',
    superseded: '已替代',
    pending: '待验收',
    active: '已激活',
    furniture: '家具',
    character: '角色',
    floor: '地面',
    effect: '特效',
    prop: '道具',
    backdrop: '远景',
    structure: '建筑结构',
    decor: '场景标志物',
    inherited: '继承·只读',
  }
  return labels[String(value).toLowerCase()] || String(value)
}

export function plainText(value, maximum = MAX_NOTE_LENGTH) {
  return String(value ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .slice(0, Math.max(0, integer(maximum, MAX_NOTE_LENGTH)))
}

export function unwrapPayload(payload) {
  if (!payload || typeof payload !== 'object') return payload
  const data = payload.data
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    const keys = Object.keys(payload).filter((key) => !['ok', 'data'].includes(key))
    return keys.length ? { ...data, ...Object.fromEntries(keys.map((key) => [key, payload[key]])) } : data
  }
  return payload
}

function normalizeFilterOptions(value) {
  const source = Array.isArray(value)
    ? value
    : value && typeof value === 'object'
      ? Object.entries(value).map(([key, label]) => ({ value: key, label }))
      : []
  const seen = new Set()
  return source
    .map((item) => {
      if (item && typeof item === 'object') {
        const value = String(firstValue(item, ['value', 'id', 'key', 'name'], ''))
        const label = String(firstValue(item, ['label', 'displayName', 'name'], optionLabel(value)))
        return { value, label }
      }
      return { value: String(item), label: optionLabel(item) }
    })
    .filter((item) => item.value && !seen.has(item.value) && seen.add(item.value))
}

function normalizeFilters(filters) {
  const source = asObject(filters)
  return {
    // `_filter_payload` emits only the plural forms.
    kind: normalizeFilterOptions(firstValue(source, ['kind', 'kinds'], [])),
    status: normalizeFilterOptions(firstValue(source, ['status', 'statuses'], [])),
  }
}

function normalizePackSlot(raw, index) {
  const source = asObject(raw)
  return {
    ...source,
    slot: String(firstValue(source, ['slot', 'slotId', 'id'], `slot-${index}`)),
    assetId: String(firstValue(source, ['assetId', 'asset_id'], '')),
    kind: String(firstValue(source, ['kind', 'type'], 'unknown')),
    displayName: String(firstValue(source, ['displayName', 'name', 'slot'], '')),
    required: firstValue(source, ['required'], true) !== false,
    selectedVersionId: String(firstValue(source, ['selectedVersionId', 'versionId'], '')),
    selectedStatus: String(firstValue(source, ['selectedStatus', 'status'], '')).toLowerCase(),
    inherited: Boolean(firstValue(source, ['inherited', 'readOnly'], false)),
    overridable: Boolean(firstValue(source, ['overridable', 'canOverride'], false)),
    overrideRequired: Boolean(firstValue(source, ['overrideRequired', 'requiresOverride'], false)),
    sourceReleaseId: String(firstValue(source, ['sourceReleaseId', 'baseReleaseId'], '')),
    ownerPackId: String(firstValue(source, ['ownerPackId', 'owner_pack_id'], '')),
  }
}

function normalizePreviewScene(raw, index) {
  const source = asObject(raw)
  return {
    id: String(firstValue(source, ['id', 'previewId'], `preview-${index}`)),
    label: String(firstValue(source, ['label', 'name', 'layoutId'], `场景 ${index + 1}`)),
    layoutId: String(firstValue(source, ['layoutId', 'layout_id'], '')),
    status: String(firstValue(source, ['status', 'state'], 'pending')).toLowerCase(),
    blobUrl: String(firstValue(source, ['blobUrl', 'url', 'pngUrl'], '')),
    width: integer(firstValue(source, ['width', 'pixelWidth'], 0)),
    height: integer(firstValue(source, ['height', 'pixelHeight'], 0)),
    sha256: String(firstValue(source, ['sha256', 'sha'], '')),
  }
}

function normalizePack(raw, fallbackRevision = 0) {
  const source = asObject(raw)
  const activation = asObject(source.activation)
  const slots = asArray(firstValue(source, ['slots', 'members'], [])).map(normalizePackSlot)
  const status = String(firstValue(source, ['status', 'state'], 'draft'))
  // The server emits no gate list; every gate shown is synthesized from the slot state.
  const gates = []
  const hasMissingSlots = Object.prototype.hasOwnProperty.call(source, 'missingSlots')
    || Object.prototype.hasOwnProperty.call(activation, 'missingSlots')
  const missingSlots = asArray(firstValue(source, ['missingSlots'], activation.missingSlots)).map(String)
  const hasInvalidSlots = Object.prototype.hasOwnProperty.call(source, 'invalidSlots')
    || Object.prototype.hasOwnProperty.call(activation, 'invalidSlots')
  const invalidSlots = asArray(firstValue(source, ['invalidSlots'], activation.invalidSlots)).map(String)
  if (hasMissingSlots) {
    gates.unshift({
      id: 'required-slots',
      label: missingSlots.length ? `缺少 ${missingSlots.length} 个必需槽位` : '必需槽位完整',
      passed: missingSlots.length === 0,
      detail: missingSlots.join(', '),
    })
  }
  if (hasInvalidSlots) {
    gates.push({
      id: 'accepted-slots',
      label: invalidSlots.length ? `${invalidSlots.length} 个槽位尚未接受` : '所选槽位均已接受',
      passed: invalidSlots.length === 0,
      detail: invalidSlots.join(', '),
    })
  }
  return {
    ...source,
    id: String(firstValue(source, ['id', 'packId', 'slug'], '')),
    name: String(firstValue(source, ['displayName', 'name', 'id', 'packId'], '未命名资产包')),
    revision: integer(firstValue(source, ['revision', 'rev'], fallbackRevision)),
    status,
    active: Boolean(firstValue(source, ['active', 'isActive'], activation.active ?? status === 'active')),
    baseReleaseId: String(firstValue(source, ['baseReleaseId', 'base_release_id'], '')),
    activeRelease: asObject(firstValue(source, ['activeRelease', 'release'], {})),
    previewScenes: asArray(firstValue(source, ['previewScenes', 'previews'], [])).map(normalizePreviewScene),
    slots,
    requiredSlots: asArray(firstValue(source, ['requiredSlots', 'required_slots'], [])).map(String),
    requiredSlotCount: integer(firstValue(source, ['requiredSlotCount'], slots.filter((slot) => slot.required).length)),
    hasPendingChanges: Boolean(firstValue(source, ['hasPendingChanges'], activation.hasPendingChanges ?? false)),
    canActivate: firstValue(source, ['canActivate', 'activationAllowed'], activation.enabled),
    missingSlots,
    invalidSlots,
    gates,
  }
}

export function normalizeBootstrap(rawPayload) {
  const payload = asObject(unwrapPayload(rawPayload))
  const revision = integer(firstValue(payload, ['revision', 'catalogRevision'], 0))
  const style = asObject(firstValue(payload, ['styleProfile', 'style'], {}))
  const singularPack = normalizePack(payload.pack, revision)
  let packs = asArray(payload.packs).map((pack) => normalizePack(pack, revision)).filter((pack) => pack.id)
  if (!packs.length && singularPack.id) packs = [singularPack]
  const pack = packs.find((candidate) => candidate.id === singularPack.id) || packs[0] || singularPack
  return {
    schemaVersion: integer(firstValue(payload, ['schemaVersion', 'schema'], 1), 1),
    revision,
    csrfToken: String(firstValue(payload, ['csrfToken', 'csrf_token'], '')),
    style: {
      ...style,
      id: String(firstValue(style, ['id', 'styleId', 'slug'], '')),
      name: String(firstValue(style, ['displayName', 'name', 'id', 'styleId'], '未命名风格')),
    },
    pack,
    packs,
    filters: normalizeFilters(payload.filters),
    limits: asObject(payload.limits),
  }
}

export function selectBootstrapPack(packs, singularPack, requestedId = '') {
  const available = asArray(packs)
  const newestCorePack = [...available]
    .map((pack, index) => {
      const match = String(pack?.id || '').match(/^core-v(\d+)$/)
      return { pack, index, version: match ? Number(match[1]) : -1 }
    })
    .sort((left, right) => right.version - left.version || right.index - left.index)
    .find((entry) => entry.version >= 0)?.pack
  return available.find((pack) => pack?.id === String(requestedId || ''))
    || newestCorePack
    || available.find((pack) => pack?.id === singularPack?.id)
    || singularPack
    || available[0]
    || normalizePack(null)
}

export function normalizeVersion(raw, asset = {}) {
  const source = asObject(raw)
  const metadata = asObject(firstValue(source, ['metadata', 'meta'], {}))
  const number = integer(firstValue(source, ['number', 'versionNumber', 'sequence'], 0))
  const id = String(firstValue(source, ['id', 'versionId'], number ? `v${number}` : ''))
  const sha256 = String(firstValue(source, ['sha256', 'sha', 'blobSha'], ''))
  return {
    ...source,
    id,
    number,
    status: String(firstValue(source, ['status', 'reviewStatus', 'decision'], 'pending')).toLowerCase(),
    sha256,
    blobUrl: String(firstValue(source, ['blobUrl', 'url', 'pngUrl'], sha256 ? `/api/assets/blobs/${encodeURIComponent(sha256)}` : '')),
    width: integer(firstValue(source, ['width', 'pixelWidth'], metadata.width), 0),
    height: integer(firstValue(source, ['height', 'pixelHeight'], metadata.height), 0),
    sizeBytes: integer(firstValue(source, ['sizeBytes', 'bytes'], 0), 0),
    metadata,
    createdAt: firstValue(source, ['createdAt', 'created_at'], null),
    reviewedAt: firstValue(source, ['reviewedAt', 'reviewed_at'], null),
    revision: integer(firstValue(source, ['revision'], asset.revision), 0),
    reviewNote: plainText(firstValue(source, ['reviewNote', 'note'], '')),
  }
}

export function normalizeAsset(raw) {
  const source = asObject(raw)
  const base = {
    ...source,
    id: String(firstValue(source, ['id', 'assetId'], '')),
    packId: String(firstValue(source, ['packId', 'pack_id'], '')),
    ownerPackId: String(firstValue(source, ['ownerPackId', 'owner_pack_id'], '')),
    inherited: Boolean(firstValue(source, ['inherited', 'readOnly'], false)),
    overridable: Boolean(firstValue(source, ['overridable', 'canOverride'], false)),
    overrideRequired: Boolean(firstValue(source, ['overrideRequired', 'requiresOverride'], false)),
    sourceReleaseId: String(firstValue(source, ['sourceReleaseId', 'baseReleaseId'], '')),
    slot: String(firstValue(source, ['slot', 'slotId'], '')),
    kind: String(firstValue(source, ['kind', 'type'], 'unknown')),
    displayName: String(firstValue(source, ['displayName', 'name', 'id', 'assetId'], '未命名资产')),
    revision: integer(firstValue(source, ['revision', 'rev'], 0)),
    selectedVersionId: String(firstValue(source, ['selectedVersionId', 'currentVersionId', 'activeVersionId'], '')),
    job: String(firstValue(source, ['job', 'jobId', 'generationJob'], '')),
    status: String(firstValue(source, ['status', 'reviewStatus'], '')),
  }
  const versionsSource = firstValue(source, ['versions', 'history'], source.version ? [source.version] : [])
  const versions = asArray(versionsSource).map((version) => normalizeVersion(version, base))
  versions.sort((left, right) => right.number - left.number || String(right.createdAt).localeCompare(String(left.createdAt)))
  const namingVersion = versions.find((version) => version.id === base.selectedVersionId) || versions[0]
  const metadataDisplayName = namingVersion?.metadata?.displayName
  if (typeof metadataDisplayName === 'string' && metadataDisplayName.trim()) {
    base.displayName = metadataDisplayName.trim()
  }
  if (!base.selectedVersionId && versions.length) {
    base.selectedVersionId = versions.find((version) => version.status === 'accepted')?.id || versions[0].id
  }
  if (!base.status && versions.length) base.status = versions.find((version) => version.id === base.selectedVersionId)?.status || versions[0].status
  if (!base.job && versions.length) {
    base.job = String(
      versions
        .map((version) => firstValue(version.metadata, ['jobId', 'job', 'generationJob'], ''))
        .find(Boolean)
      || '',
    )
  }
  return { ...base, versions }
}

export function characterConsistencyState(asset, version) {
  const required = String(asset?.slot || '') === 'character.gus'
    || String(asset?.kind || '').toLowerCase() === 'character'
  if (!required) return {
    required: false,
    state: 'not-applicable',
    acceptanceBlocked: false,
    report: null,
    motionBuild: null,
  }
  const report = asObject(version?.metadata?.characterConsistency)
  const motionBuild = asObject(version?.metadata?.motionBuild)
  if (report.ok === true && motionBuild.verified === true) {
    return { required: true, state: 'passed', acceptanceBlocked: false, report, motionBuild }
  }
  if (report.ok === false || motionBuild.verified === false) {
    return { required: true, state: 'blocked', acceptanceBlocked: true, report, motionBuild }
  }
  return {
    required: true,
    state: 'legacy-unverified',
    acceptanceBlocked: true,
    report: report.ok === true ? report : null,
    motionBuild: motionBuild.verified === true ? motionBuild : null,
  }
}

export function isFrozenInheritedAsset(asset) {
  return Boolean(asset?.inherited) && !Boolean(asset?.overridable)
}

export function normalizeCatalog(rawPayload) {
  const payload = asObject(unwrapPayload(rawPayload))
  const assets = asArray(firstValue(payload, ['assets', 'items', 'catalog'], [])).map(normalizeAsset)
  assets.sort((left, right) => (
    left.slot.localeCompare(right.slot, 'en')
    || left.displayName.localeCompare(right.displayName, 'zh-Hans')
    || left.id.localeCompare(right.id, 'en')
  ))
  return {
    revision: integer(firstValue(payload, ['revision', 'catalogRevision'], 0)),
    assets,
  }
}

export function buildReviewPayload(decision, note, expectedRevision) {
  const normalizedDecision = String(decision || '').toLowerCase()
  if (!REVIEW_DECISIONS.has(normalizedDecision)) throw new RangeError('decision 必须是 accepted 或 rejected')
  const normalizedNote = plainText(note)
  if (normalizedDecision === 'rejected' && !normalizedNote.trim()) throw new RangeError('拒绝版本时必须填写修改说明')
  const revision = integer(expectedRevision, -1)
  if (revision < 0) throw new RangeError('expectedRevision 必须是非负整数')
  return { decision: normalizedDecision, note: normalizedNote, expectedRevision: revision }
}

export function buildGenerationRequest(asset, version, style = {}) {
  if (!asset || !version) return ''
  const anchor = versionAnchor(version, { width: version.width || 1, height: version.height || 1 })
  const footprint = versionFootprint(version)
  const common = [
    `资产：${asset.displayName}（${asset.id}）`,
    `槽位：${asset.slot || '未指定'}；类型：${asset.kind}`,
    `风格基线：${style.name || style.id || '当前资产包风格'}`,
    `参考版本：v${version.number || version.id}，SHA-256 ${version.sha256 || '未知'}`,
  ]
  if (String(asset.slot || '') === 'character.gus' || String(asset.kind || '').toLowerCase() === 'character') {
    return [
      `请只生成 Gus 的四方向中立站姿转面参考图，不要生成任何正式动画帧。`,
      ...common,
      `身份要保持白发、深绿服装和当前角色气质；四个视图为 southeast、southwest、northwest、northeast，统一比例、脚底基线和中立站姿。`,
      `参考图不得包含动作序列、家具、键盘、椅子、阴影、特效、文字或背景；它只供人工按像素重绘身份母版。`,
      `正式 idle / walk / work 禁止逐帧 AI 生成。动作必须由 assets/gus-rig/rig.json 和 deterministic-pixel-rig-v1 编译器在整数像素网格上确定性生成。`,
      `生产输出固定为 168×192 RGBA PNG（7×4，每格 24×48），共享脚底 anchor (${anchor.x}, ${anchor.y})；任何未通过服务端 motionBuild 复编译逐像素比对的图片都不能被接受。`,
    ].join('\n')
  }
  return [
    `请为等距 2D 粗像素办公室资产生成一个新的 PNG 版本。`,
    ...common,
    `保持 32×16 等距网格、整数像素边缘和透明背景；不要抗锯齿或模糊缩放。`,
    `输出必须直接按原生 frame ${version.width || version.metadata?.width || '未知'}×${version.height || version.metadata?.height || '未知'} px 创作，不允许先生成 16:9 场景图再 cover 裁切。`,
    `地面锚点：(${anchor.x}, ${anchor.y})；占地：${footprint.map((cell) => `(${cell.x},${cell.y})`).join('、')}。`,
    `光线从屏幕左上方进入，顶面最亮、右侧最暗；避免未登记的高饱和颜色。`,
    `输出仅包含 PNG 与对应 metadata JSON，不要改变资产 ID 和槽位。`,
  ].join('\n')
}

export function buildDraftReviewQueue(assets, currentAssetId = '', currentVersionId = '') {
  const queue = asArray(assets).filter((asset) => !asset?.inherited || asset?.overridable).flatMap((asset) => asArray(asset?.versions)
    .filter((version) => String(version?.status).toLowerCase() === 'draft')
    .map((version) => ({ assetId: String(asset.id), versionId: String(version.id) })))
  if (!queue.length) return []
  const currentIndex = queue.findIndex((item) => (
    item.assetId === String(currentAssetId) && item.versionId === String(currentVersionId)
  ))
  if (currentIndex < 0) return queue
  return [...queue.slice(currentIndex + 1), ...queue.slice(0, currentIndex)]
}

export function eligibleDraftTargets(assets) {
  return asArray(assets).filter((asset) => !asset?.inherited || asset?.overridable).flatMap((asset) => {
    // normalizeAsset sorts versions newest-first, so the first draft is the newest one.
    const version = asArray(asset?.versions).find((item) => String(item?.status).toLowerCase() === 'draft')
    if (!version) return []
    return [{
      assetId: String(asset.id),
      versionId: String(version.id),
      versionNumber: integer(version.number, 0),
      displayName: String(asset.displayName || asset.id),
      slot: String(asset.slot || ''),
      kind: String(asset.kind || ''),
    }]
  })
}

/**
 * Narrow the catalog to the rows the current filter shows. Membership only: a surviving
 * asset keeps its complete `versions` array, because the inspector, the row thumbnail and
 * the "N 版" row count all read the full history. A status filter asks "does this asset
 * have such a version", never "hide the other versions".
 */
export function filterCatalogAssets(assets, filters = {}) {
  const kind = String(filters?.kind || '')
  const status = String(filters?.status || '').toLowerCase()
  if (!kind && !status) return asArray(assets)
  return asArray(assets).filter((asset) => {
    if (kind && String(asset?.kind || '') !== kind) return false
    if (!status) return true
    // A slot with no versions drops out of a status filter, as the server used to drop it.
    return asArray(asset?.versions).some((version) => String(version?.status).toLowerCase() === status)
  })
}

export function buildBatchSelection(assets, selectedIds) {
  const selected = new Set(asArray([...(selectedIds || [])]).map(String))
  const eligible = eligibleDraftTargets(assets)
  const eligibleIds = new Set(eligible.map((item) => item.assetId))
  const items = eligible.filter((item) => selected.has(item.assetId))
  const staleIds = [...selected].filter((id) => !eligibleIds.has(id))
  return {
    items,
    eligible,
    selectedCount: items.length,
    eligibleCount: eligible.length,
    allSelected: eligible.length > 0 && items.length === eligible.length,
    partial: items.length > 0 && items.length < eligible.length,
    staleIds,
  }
}

export function batchAcceptBlockers(items, assets) {
  const byId = new Map(asArray(assets).map((asset) => [String(asset?.id), asset]))
  return asArray(items).flatMap((item) => {
    const asset = byId.get(String(item?.assetId))
    const version = asArray(asset?.versions).find((entry) => String(entry?.id) === String(item?.versionId))
    if (!asset || !version) return []
    const consistency = characterConsistencyState(asset, version)
    if (!consistency.acceptanceBlocked) return []
    return [{
      ...item,
      reason: consistency.state === 'blocked'
        ? '未通过角色身份一致性门禁'
        : '缺少已验证的确定性 Rig 编译结果',
    }]
  })
}

export function buildBatchReviewPayload(decision, note, expectedRevision, items) {
  const base = buildReviewPayload(decision, note, expectedRevision)
  const seen = new Set()
  const acceptedAssets = new Set()
  const normalizedItems = []
  for (const entry of asArray(items)) {
    const assetId = String(entry?.assetId ?? '')
    const versionId = String(entry?.versionId ?? '')
    if (!assetId || !versionId) throw new RangeError('批量条目必须同时包含 assetId 与 versionId')
    const key = `${assetId}\u0000${versionId}`
    // Select-all followed by an individual click is a gesture, not an error: dedupe it.
    if (seen.has(key)) continue
    seen.add(key)
    if (base.decision === 'accepted') {
      if (acceptedAssets.has(assetId)) throw new RangeError('同一资产不能在一次批量中接受两个版本')
      acceptedAssets.add(assetId)
    }
    normalizedItems.push({ assetId, versionId, decision: base.decision })
  }
  if (!normalizedItems.length) throw new RangeError('批量验收至少需要选择一个待验收版本')
  if (normalizedItems.length > MAX_REVIEW_BATCH_ITEMS) {
    throw new RangeError(`一次最多批量验收 ${MAX_REVIEW_BATCH_ITEMS} 个版本`)
  }
  return { items: normalizedItems, note: base.note, expectedRevision: base.expectedRevision }
}

const BATCH_FAILURE_REASONS = Object.freeze({
  'version.not_found': '版本已不存在',
  'version.not_draft': '已不是待验收草稿',
})

export function partitionBatchReviewFailures(items, failures) {
  const submitted = asArray(items)
  const blockedKeys = new Set()
  const blocked = asArray(failures).map((failure) => {
    const assetId = String(failure?.assetId ?? '')
    const versionId = String(failure?.versionId ?? '')
    blockedKeys.add(`${assetId}\u0000${versionId}`)
    const source = submitted.find((item) => (
      String(item?.assetId) === assetId && String(item?.versionId) === versionId
    ))
    const code = String(failure?.code || '')
    return {
      assetId,
      versionId,
      displayName: String(source?.displayName || assetId),
      // The gate failures carry Chinese messages, but the generic ones are English server
      // strings; translate those rather than leaking them into the console.
      reason: BATCH_FAILURE_REASONS[code] || String(
        firstValue(asObject(failure), ['message', 'detail', 'reason'], '')
        || code
        || '不可验收',
      ),
    }
  })
  const remaining = submitted.filter((item) => !blockedKeys.has(
    `${String(item?.assetId ?? '')}\u0000${String(item?.versionId ?? '')}`,
  ))
  return { blocked, remaining }
}

export function buildBatchConfirmationSummary(items, decision, {
  revision = 0,
  packName = '',
  packId = '',
  blocked = [],
} = {}) {
  const normalizedDecision = String(decision || '').toLowerCase()
  return [
    `资产包：${packName || packId || '当前资产包'}`,
    `资产包 ID：${packId || '未知'}`,
    `批量结论：${REVIEW_DECISIONS.has(normalizedDecision) ? optionLabel(normalizedDecision) : '未选择'}`,
    `提交项数：${asArray(items).length}`,
    `跳过项数：${asArray(blocked).length}`,
    `预期修订：r${integer(revision, 0)}`,
    `原子提交：整批要么全部写入，要么全部不写入`,
  ].join('\n')
}

export function requiredSlotsForPack(pack) {
  const declared = asArray(pack?.slots).filter((slot) => slot?.required !== false).map((slot) => String(slot.slot || '')).filter(Boolean)
  if (declared.length) return declared
  const required = asArray(pack?.requiredSlots).map(String).filter(Boolean)
  if (required.length) return required
  if (String(pack?.id) === 'core-v2') return [...CORE_V2_REQUIRED_SLOTS]
  return String(pack?.id) === 'core-v1' ? [...CORE_V1_REQUIRED_SLOTS] : [...CORE_V0_REQUIRED_SLOTS]
}

export function buildActivationSlotCoverage(assets, requiredSlots = CORE_V0_REQUIRED_SLOTS, packSlots = []) {
  const slotIndex = new Map(asArray(packSlots).map((slot, index) => {
    const normalized = normalizePackSlot(slot, index)
    return [normalized.slot, normalized]
  }))
  return asArray(requiredSlots).map((slot) => {
    const asset = asArray(assets).find((candidate) => String(candidate?.slot) === String(slot)) || null
    const packSlot = slotIndex.get(String(slot)) || null
    const versions = asArray(asset?.versions)
    const selectedVersionId = asset?.selectedVersionId || packSlot?.selectedVersionId || ''
    const selected = versions.find((version) => version.id === selectedVersionId)
    const accepted = selected?.status === 'accepted'
      ? selected
      : versions.find((version) => String(version?.status).toLowerCase() === 'accepted') || null
    const inherited = Boolean(asset?.inherited || packSlot?.inherited)
    const overridable = Boolean(asset?.overridable || packSlot?.overridable)
    const overrideRequired = Boolean(packSlot?.overrideRequired)
    const serverAccepted = packSlot?.selectedStatus === 'accepted' && Boolean(packSlot?.selectedVersionId)
    const ready = Boolean((asset || packSlot?.assetId) && (overrideRequired
      ? ((selected?.status === 'accepted' && selected.id === selectedVersionId) || serverAccepted)
      : (accepted || serverAccepted)))
    return {
      slot: String(slot),
      assetId: asset?.id ? String(asset.id) : String(packSlot?.assetId || ''),
      versionId: accepted?.id ? String(accepted.id) : String(packSlot?.selectedVersionId || ''),
      sha256: accepted?.sha256 ? String(accepted.sha256) : '',
      ready,
      inherited,
      overridable,
      overrideRequired,
      sourceReleaseId: String(asset?.sourceReleaseId || packSlot?.sourceReleaseId || ''),
      state: !(asset || packSlot?.assetId) ? 'missing' : ready ? 'accepted' : 'unaccepted',
    }
  })
}

function responseMessage(payload, fallback) {
  const source = asObject(payload)
  const envelope = asObject(firstValue(source, ['details', 'detail', 'error'], {}))
  const details = { ...envelope, ...asObject(envelope.details) }
  const missingSlots = asArray(details.missingSlots)
  if (missingSlots.length) return `资产包仍缺少必需槽位：${missingSlots.join(', ')}`
  for (const value of [source.detail, source.error, source.message, details.message]) {
    if (typeof value === 'string' && value) return value
  }
  return fallback
}

export class AssetsApi {
  constructor(baseUrl = DEFAULT_API_BASE, fetchImpl = (...args) => globalThis.fetch(...args)) {
    this.baseUrl = String(baseUrl || DEFAULT_API_BASE).replace(/\/+$/, '')
    this.fetchImpl = fetchImpl
    this.csrfToken = ''
  }

  setCsrfToken(value) {
    this.csrfToken = String(value || '')
  }

  resolve(path) {
    const value = String(path || '')
    if (/^https?:\/\//i.test(value)) return value
    if (value.startsWith('/')) return value
    return `${this.baseUrl}/${value.replace(/^\/+/, '')}`
  }

  async request(path, { method = 'GET', body, multipart = false } = {}) {
    const normalizedMethod = String(method).toUpperCase()
    const headers = { Accept: 'application/json' }
    if (WRITE_METHODS.has(normalizedMethod)) headers['X-CSRF-Token'] = this.csrfToken
    if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json'
    const response = await this.fetchImpl(this.resolve(path), {
      method: normalizedMethod,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    })
    const contentType = response.headers?.get?.('content-type') || ''
    let payload = null
    if (response.status !== 204) {
      payload = contentType.includes('json')
        ? await response.json().catch(() => null)
        : await response.text().catch(() => '')
    }
    if (!response.ok) {
      const error = new Error(responseMessage(payload, `资产接口返回 ${response.status}`))
      error.status = response.status
      error.payload = payload
      throw error
    }
    return unwrapPayload(payload ?? {})
  }

  bootstrap() {
    return this.request('bootstrap')
  }

  catalog(filters = {}) {
    const query = new URLSearchParams()
    for (const key of ['packId']) {
      const value = String(filters[key] || '')
      if (value) query.set(key, value)
    }
    return this.request(`catalog${query.size ? `?${query}` : ''}`)
  }

  importPng(file, metadata, packId = '') {
    const form = new FormData()
    form.append('png', file, file?.name || 'asset.png')
    const scopedMetadata = { ...asObject(metadata) }
    if (packId) scopedMetadata.packId = String(packId)
    form.append('metadata', JSON.stringify(scopedMetadata))
    return this.request('import', { method: 'POST', body: form, multipart: true })
  }

  scanInbox(packId = '') {
    const query = new URLSearchParams()
    if (packId) query.set('packId', String(packId))
    return this.request(`inbox/scan${query.size ? `?${query}` : ''}`, { method: 'POST' })
  }

  reviewBatch(payload) {
    return this.request('reviews/batch', { method: 'POST', body: payload })
  }

  review(assetId, versionId, payload) {
    return this.request(
      `${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionId)}/review`,
      { method: 'POST', body: payload },
    )
  }

  activate(packId, expectedRevision) {
    return this.request(`packs/${encodeURIComponent(packId)}/activate`, {
      method: 'POST',
      body: { expectedRevision: integer(expectedRevision) },
    })
  }

  async blob(sha256, blobUrl = '') {
    const path = blobUrl || `blobs/${encodeURIComponent(sha256)}`
    const response = await this.fetchImpl(this.resolve(path), {
      method: 'GET',
      headers: { Accept: 'image/png' },
      credentials: 'same-origin',
    })
    if (!response.ok) throw new Error(`PNG 加载失败（HTTP ${response.status}）`)
    return response.blob()
  }
}

function createElement(documentRef, tag, className, text) {
  const element = documentRef.createElement(tag)
  if (className) element.className = className
  if (text !== undefined) element.textContent = String(text)
  return element
}

function setOptions(select, options, { emptyLabel = '', value = '' } = {}) {
  const documentRef = select.ownerDocument
  const fragment = documentRef.createDocumentFragment()
  if (emptyLabel) {
    const option = createElement(documentRef, 'option', '', emptyLabel)
    option.value = ''
    fragment.append(option)
  }
  for (const entry of options) {
    const option = createElement(documentRef, 'option', '', entry.label)
    option.value = entry.value
    fragment.append(option)
  }
  select.replaceChildren(fragment)
  if ([...select.options].some((option) => option.value === String(value))) select.value = String(value)
}

function safeStatusClass(value) {
  return String(value || 'pending').toLowerCase().replace(/[^a-z0-9_-]/g, '-')
}

class AssetsWorkbench {
  constructor(documentRef) {
    this.document = documentRef
    const apiBase = documentRef.querySelector('meta[name="assets-api-base"]')?.content
      || documentRef.documentElement.dataset.assetsApiBase
      || DEFAULT_API_BASE
    this.api = new AssetsApi(apiBase)
    this.bootstrapState = null
    this.style = {}
    this.pack = normalizePack(null)
    this.packs = []
    this.selectedPackId = ''
    this.revision = 0
    this.assets = []
    this.selectedAssetId = ''
    this.versionAId = ''
    this.filters = { kind: '', status: '' }
    this.decision = ''
    this.busy = false
    this.previewLoadNonce = 0
    this.catalogRenderNonce = 0
    this.imageCache = new Map()
    // Batch selection is in-memory only; the page never touches web storage.
    this.selectedIds = new Set()
    this.pendingBatch = null
    this.renderedVersionKey = ''
    this.animationControlsKey = ''
    this.mobilePane = 'catalog'
    this.toastTimer = null
    this.elements = this.collectElements()
    this.preview = new AssetPreview(this.elements.assetCanvas, {
      viewport: this.elements.canvasViewport,
      onUpdate: (details) => this.updatePreviewDetails(details),
    })
  }

  collectElements() {
    const id = (value) => this.document.getElementById(value)
    return {
      connectionBadge: id('connectionBadge'),
      styleLabel: id('styleLabel'),
      baseReleaseSummary: id('baseReleaseSummary'),
      baseReleaseLabel: id('baseReleaseLabel'),
      packRevisionLabel: id('packRevisionLabel'),
      packSelect: id('packSelect'),
      packContext: id('packContext'),
      mobileAssetFlow: id('mobileAssetFlow'),
      assetTabButton: id('assetTabButton'),
      previewTabButton: id('previewTabButton'),
      reviewTabButton: id('reviewTabButton'),
      mobileSelectionLabel: id('mobileSelectionLabel'),
      mobileNextButton: id('mobileNextButton'),
      workbenchGrid: id('workbenchGrid'),
      refreshButton: id('refreshButton'),
      scanInboxButton: id('scanInboxButton'),
      openImportButton: id('openImportButton'),
      filterForm: id('filterForm'),
      kindFilter: id('kindFilter'),
      statusFilter: id('statusFilter'),
      clearFiltersButton: id('clearFiltersButton'),
      catalogState: id('catalogState'),
      catalogCount: id('catalogCount'),
      assetList: id('assetList'),
      selectAllControl: id('selectAllControl'),
      selectAllCheckbox: id('selectAllCheckbox'),
      batchBar: id('batchBar'),
      batchSelectionLabel: id('batchSelectionLabel'),
      batchClearButton: id('batchClearButton'),
      batchNote: id('batchNote'),
      batchAcceptButton: id('batchAcceptButton'),
      batchRejectButton: id('batchRejectButton'),
      batchDialog: id('batchDialog'),
      batchForm: id('batchForm'),
      batchDialogTitle: id('batchDialogTitle'),
      batchSummary: id('batchSummary'),
      batchItemSummary: id('batchItemSummary'),
      batchItemCount: id('batchItemCount'),
      confirmBatchButton: id('confirmBatchButton'),
      previewHeading: id('previewHeading'),
      previewStatus: id('previewStatus'),
      packScenePreviewSection: id('packScenePreviewSection'),
      packScenePreviewCount: id('packScenePreviewCount'),
      packScenePreviewList: id('packScenePreviewList'),
      versionASelect: id('versionASelect'),
      scaleControls: id('scaleControls'),
      fixtureSelect: id('fixtureSelect'),
      canvasViewport: id('canvasViewport'),
      assetCanvas: id('assetCanvas'),
      previousFrameButton: id('previousFrameButton'),
      playPauseButton: id('playPauseButton'),
      nextFrameButton: id('nextFrameButton'),
      animationActionSelect: id('animationActionSelect'),
      animationDirectionSelect: id('animationDirectionSelect'),
      animationSpeedSelect: id('animationSpeedSelect'),
      frameLabel: id('frameLabel'),
      metadataSection: id('metadataSection'),
      historyCount: id('historyCount'),
      versionHistory: id('versionHistory'),
      decisionControls: id('decisionControls'),
      reviewForm: id('reviewForm'),
      reviewNote: id('reviewNote'),
      inheritedNotice: id('inheritedNotice'),
      reviewRevisionHint: id('reviewRevisionHint'),
      submitReviewButton: id('submitReviewButton'),
      copyGenerationButton: id('copyGenerationButton'),
      copyFeedbackButton: id('copyFeedbackButton'),
      activationHeading: id('activationHeading'),
      activationState: id('activationState'),
      gateList: id('gateList'),
      activatePackButton: id('activatePackButton'),
      importDialog: id('importDialog'),
      importForm: id('importForm'),
      pngInput: id('pngInput'),
      metadataInput: id('metadataInput'),
      importError: id('importError'),
      submitImportButton: id('submitImportButton'),
      activationDialog: id('activationDialog'),
      activationForm: id('activationForm'),
      activationSummary: id('activationSummary'),
      activationSlotSummary: id('activationSlotSummary'),
      activationSlotCount: id('activationSlotCount'),
      confirmActivationButton: id('confirmActivationButton'),
      toast: id('toast'),
    }
  }

  async start() {
    this.bindEvents()
    this.setMobilePane('catalog')
    this.restoreFilterQuery()
    // Also the session's first updateControls(): the write buttons must not be clickable
    // before applyBootstrap installs the CSRF token, or an import posts an empty one.
    this.setBusy(true, '正在读取资产工作区')
    try {
      const payload = await this.api.bootstrap()
      this.applyBootstrap(payload)
      await this.loadCatalog({ announce: false })
      this.setStatus('资产工作区已连接', 'ready')
    } catch (error) {
      this.showCatalogState(`资产目录暂时不可用。\n${error.message}`)
      this.setStatus('资产接口连接失败', 'error')
      this.toast(error.message || '资产接口连接失败', true)
    } finally {
      this.setBusy(false)
    }
  }

  bindEvents() {
    this.elements.refreshButton.addEventListener('click', () => this.refreshWorkspace())
    this.elements.scanInboxButton.addEventListener('click', () => this.scanInbox())
    this.elements.openImportButton.addEventListener('click', () => this.prepareImportDialog())
    this.elements.packSelect.addEventListener('change', () => this.switchPack(this.elements.packSelect.value))
    this.elements.filterForm.addEventListener('change', () => this.applyFilters())
    this.elements.clearFiltersButton.addEventListener('click', () => this.clearFilters())
    this.elements.mobileAssetFlow.addEventListener('click', (event) => {
      const button = event.target.closest('[data-mobile-pane-target]')
      if (button && !button.disabled) this.setMobilePane(button.dataset.mobilePaneTarget)
    })
    this.elements.mobileNextButton.addEventListener('click', () => this.advanceMobileFlow())
    this.elements.assetList.addEventListener('click', (event) => {
      if (event.target.closest('input')) return
      const button = event.target.closest('[data-asset-id]')
      if (!button) return
      this.selectAsset(button.dataset.assetId)
      // Tapping a row is the step-1 → step-2 gesture; keyboard navigation is not, and
      // switching panes there would hide the list this listener is bound to.
      this.setMobilePane('preview')
    })
    this.elements.assetList.addEventListener('change', (event) => {
      const checkbox = event.target.closest('[data-select-asset]')
      if (checkbox) this.toggleAssetSelection(checkbox.dataset.selectAsset, checkbox.checked)
    })
    this.elements.assetList.addEventListener('keydown', (event) => this.navigateCatalog(event))
    this.elements.selectAllCheckbox.addEventListener('change', () => {
      this.setSelectAll(this.elements.selectAllCheckbox.checked)
    })
    this.elements.batchClearButton.addEventListener('click', () => this.clearBatchSelection({ notify: true }))
    for (const button of [this.elements.batchAcceptButton, this.elements.batchRejectButton]) {
      button.addEventListener('click', () => this.openBatchConfirmation(button.dataset.batchDecision))
    }
    this.elements.batchForm.addEventListener('submit', (event) => {
      event.preventDefault()
      this.submitBatchReview()
    })
    this.elements.versionHistory.addEventListener('click', (event) => {
      const button = event.target.closest('[data-history-version]')
      if (!button) return
      this.versionAId = button.dataset.historyVersion
      this.renderSelection()
      this.setMobilePane('preview')
    })
    this.elements.versionASelect.addEventListener('change', () => {
      this.versionAId = this.elements.versionASelect.value
      this.renderSelection()
    })
    this.elements.scaleControls.addEventListener('click', (event) => {
      const button = event.target.closest('[data-scale]')
      if (!button) return
      const scale = Number(button.dataset.scale)
      this.preview.setScale(scale)
      for (const candidate of this.elements.scaleControls.querySelectorAll('[data-scale]')) {
        candidate.setAttribute('aria-pressed', String(candidate === button))
      }
    })
    this.elements.fixtureSelect.addEventListener('change', () => this.preview.setFixture(this.elements.fixtureSelect.value))
    this.document.querySelectorAll('[data-guide]').forEach((button) => {
      button.addEventListener('click', () => this.toggleGuide(button))
    })
    this.elements.previousFrameButton.addEventListener('click', () => this.stepFrame(-1))
    this.elements.nextFrameButton.addEventListener('click', () => this.stepFrame(1))
    const updateAnimationSelection = () => this.preview.setAnimationSelection(
      this.elements.animationActionSelect.value,
      this.elements.animationDirectionSelect.value,
    )
    this.elements.animationActionSelect.addEventListener('change', updateAnimationSelection)
    this.elements.animationDirectionSelect.addEventListener('change', updateAnimationSelection)
    this.elements.playPauseButton.addEventListener('click', () => this.preview.togglePaused())
    this.elements.animationSpeedSelect.addEventListener('change', () => this.preview.setSpeed(this.elements.animationSpeedSelect.value))
    this.elements.canvasViewport.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') {
        event.preventDefault()
        this.stepFrame(-1)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        this.stepFrame(1)
      } else if (event.key === ' ') {
        event.preventDefault()
        this.elements.playPauseButton.click()
      }
    })
    this.elements.decisionControls.addEventListener('click', (event) => {
      const button = event.target.closest('[data-decision]')
      if (button) this.setDecision(button.dataset.decision)
    })
    this.elements.reviewForm.addEventListener('submit', (event) => {
      event.preventDefault()
      this.submitReview()
    })
    this.elements.copyGenerationButton.addEventListener('click', () => this.copyGenerationRequest())
    this.elements.copyFeedbackButton.addEventListener('click', () => this.copyModificationFeedback())
    this.elements.importForm.addEventListener('submit', (event) => {
      event.preventDefault()
      this.importPng()
    })
    this.elements.activatePackButton.addEventListener('click', () => this.openActivationConfirmation())
    this.elements.activationForm.addEventListener('submit', (event) => {
      event.preventDefault()
      this.activatePack()
    })
    this.document.querySelectorAll('[data-close-dialog]').forEach((button) => {
      button.addEventListener('click', () => this.closeDialog(this.document.getElementById(button.dataset.closeDialog)))
    })
    for (const dialog of [this.elements.importDialog, this.elements.activationDialog, this.elements.batchDialog]) {
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog) this.closeDialog(dialog)
      })
    }
  }

  applyBootstrap(rawPayload) {
    const bootstrap = normalizeBootstrap(rawPayload)
    this.bootstrapState = bootstrap
    this.style = bootstrap.style
    this.packs = bootstrap.packs.length ? bootstrap.packs : bootstrap.pack.id ? [bootstrap.pack] : []
    this.pack = selectBootstrapPack(this.packs, bootstrap.pack, this.selectedPackId)
    this.selectedPackId = this.pack.id
    this.revision = bootstrap.revision
    this.api.setCsrfToken(bootstrap.csrfToken)
    this.preview.setStyle(this.style)
    this.elements.styleLabel.textContent = this.style.name || this.style.id || '—'
    this.elements.packRevisionLabel.textContent = String(this.revision)
    this.renderPackSelector()
    this.populateFilterOptions(bootstrap.filters)
    this.updateFilterQuery()
    this.renderActivation()
  }

  renderPackSelector() {
    const options = this.packs.map((pack) => {
      const state = pack.active ? '已激活' : pack.status === 'active' ? '已激活' : '未激活'
      return { value: pack.id, label: `${pack.name || pack.id} · ${state}` }
    })
    setOptions(this.elements.packSelect, options, { value: this.pack.id })
    this.elements.packSelect.disabled = this.busy || options.length < 2
    this.elements.baseReleaseSummary.hidden = !this.pack.baseReleaseId
    this.elements.baseReleaseLabel.textContent = this.pack.baseReleaseId || '—'
    const requiredSlots = requiredSlotsForPack(this.pack)
    const inheritedCount = this.pack.slots.filter((slot) => slot.inherited).length
    const overridableCount = this.pack.slots.filter((slot) => slot.inherited && slot.overridable).length
    const readOnlyCount = inheritedCount - overridableCount
    const localCount = Math.max(0, requiredSlots.length - inheritedCount)
    this.elements.packContext.textContent = this.pack.baseReleaseId
      ? `${requiredSlots.length} 个必需槽位：${readOnlyCount} 个继承只读，${overridableCount} 个可覆盖，${localCount} 个本地可验收。`
      : `${requiredSlots.length} 个必需槽位；此资产包的版本可独立验收。`
    // The hint stays static: #packContext above already reports the slot composition, and
    // overwriting it here hid the only explanation of the import/scan buttons.
    this.renderPackScenePreviews()
  }

  renderPackScenePreviews() {
    const scenes = asArray(this.pack.previewScenes)
    this.elements.packScenePreviewSection.hidden = scenes.length === 0
    this.elements.packScenePreviewCount.textContent = `${scenes.filter((scene) => scene.status === 'ready' && scene.blobUrl).length}/${scenes.length} 张`
    this.elements.packScenePreviewList.replaceChildren()
    const fragment = this.document.createDocumentFragment()
    for (const scene of scenes) {
      // Only `invalid` carries a style; `ready`/`pending` are the default look.
      const invalid = scene.status === 'invalid'
      const figure = createElement(this.document, 'figure', `pack-scene-preview${invalid ? ' is-invalid' : ''}`)
      let previewUrl = ''
      try {
        const resolved = new URL(scene.blobUrl, window.location.href)
        if (resolved.origin === window.location.origin) previewUrl = resolved.href
      } catch { /* pending/invalid preview remains a readable placeholder */ }
      if (scene.status === 'ready' && previewUrl) {
        const image = createElement(this.document, 'img')
        image.src = previewUrl
        image.alt = `${scene.label}完整场景候选`
        image.loading = 'lazy'
        image.decoding = 'async'
        figure.append(image)
      } else {
        const pending = createElement(this.document, 'div', 'pack-scene-preview-pending')
        pending.append(
          createElement(this.document, 'strong', '', invalid ? '场景图不可用' : '等待完整场景图'),
          createElement(this.document, 'span', '', invalid
            ? '来源文件无法读取，请检查资产包 spec 的 sourceName。'
            : '候选资产准备完成后会自动出现在这里。'),
        )
        figure.append(pending)
      }
      const details = [scene.layoutId, scene.width && scene.height ? `${scene.width}×${scene.height}` : '', shortHash(scene.sha256)]
        .filter((value) => value && value !== '—')
        .join(' · ')
      const caption = createElement(this.document, 'figcaption')
      caption.append(
        createElement(this.document, 'strong', '', scene.label),
        createElement(this.document, 'span', '', details || optionLabel(scene.status)),
      )
      figure.append(caption)
      fragment.append(figure)
    }
    this.elements.packScenePreviewList.append(fragment)
  }

  populateFilterOptions(filters) {
    const declaredKinds = [...new Set(this.pack.slots.map((slot) => slot.kind).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right, 'en'))
      .map((kind) => ({ value: kind, label: optionLabel(kind) }))
    const kindOptions = declaredKinds.length ? declaredKinds : filters.kind
    const selectedKind = this.filters.kind && kindOptions.some((entry) => entry.value === this.filters.kind)
      ? this.filters.kind
      : ''
    const retain = (options, value) => value && !options.some((entry) => entry.value === value)
      ? [...options, { value, label: value }]
      : options
    setOptions(this.elements.kindFilter, kindOptions, { emptyLabel: '全部类型', value: selectedKind })
    setOptions(this.elements.statusFilter, retain(filters.status, this.filters.status), { emptyLabel: '全部状态', value: this.filters.status })
    this.filters = {
      kind: this.elements.kindFilter.value,
      status: this.elements.statusFilter.value,
    }
  }

  restoreFilterQuery() {
    const query = new URLSearchParams(window.location.search)
    this.selectedPackId = query.get('pack') || query.get('packId') || ''
    this.filters = {
      kind: query.get('kind') || '',
      status: query.get('status') || '',
    }
  }

  updateFilterQuery() {
    const url = new URL(window.location.href)
    url.searchParams.delete('packId')
    url.searchParams.delete('job')
    if (this.pack.id) url.searchParams.set('pack', this.pack.id)
    else url.searchParams.delete('pack')
    for (const key of ['kind', 'status']) {
      if (this.filters[key]) url.searchParams.set(key, this.filters[key])
      else url.searchParams.delete(key)
    }
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }

  async switchPack(packId) {
    if (this.busy || packId === this.pack.id) return
    const target = this.packs.find((pack) => pack.id === String(packId))
    if (!target) return
    this.pack = target
    this.selectedPackId = target.id
    this.selectedAssetId = ''
    this.versionAId = ''
    this.filters = { kind: '', status: '' }
    this.clearBatchSelection({ notify: true })
    this.imageCache.clear()
    this.renderPackSelector()
    this.populateFilterOptions(this.bootstrapState?.filters || normalizeFilters({}))
    this.updateFilterQuery()
    this.setBusy(true, `正在切换到 ${target.name || target.id}`)
    try {
      await this.loadCatalog({ announce: false })
      this.setStatus(`已切换到 ${target.name || target.id}`, 'ready')
      this.toast(`已切换资产包：${target.name || target.id}`)
    } catch (error) {
      this.handleError(error, '资产包切换失败')
    } finally {
      this.setBusy(false)
    }
  }

  async refreshWorkspace() {
    if (this.busy) return
    this.setBusy(true, '正在刷新资产工作区')
    try {
      const payload = await this.api.bootstrap()
      this.applyBootstrap(payload)
      await this.loadCatalog({ announce: false })
      this.setStatus('资产工作区已连接', 'ready')
      this.toast('资产目录已刷新')
    } catch (error) {
      this.handleError(error, '刷新失败')
    } finally {
      this.setBusy(false)
    }
  }

  async loadCatalog({ announce = true } = {}) {
    if (announce) this.setStatus('正在筛选资产目录', 'working')
    // One request for the whole pack; the filters narrow it in the browser. Activation
    // coverage and the version history both need rows a filter would have hidden.
    const catalog = normalizeCatalog(await this.api.catalog({ packId: this.pack.id }))
    this.assets = catalog.assets
    if (catalog.revision || this.revision === 0) this.revision = catalog.revision
    this.elements.packRevisionLabel.textContent = String(this.revision)
    const visible = this.visibleAssets()
    if (!visible.some((asset) => asset.id === this.selectedAssetId)) this.selectedAssetId = visible[0]?.id || ''
    this.renderCatalog()
    if (this.selectedAssetId) this.selectAsset(this.selectedAssetId, { preserveVersions: true })
    else this.renderEmptySelection()
    if (announce) this.setStatus('资产工作区已连接', 'ready')
  }

  async applyFilters() {
    if (this.busy) return
    // Never carry a hidden selection across a filter change.
    this.clearBatchSelection({ notify: true })
    this.filters = {
      kind: this.elements.kindFilter.value,
      status: this.elements.statusFilter.value,
    }
    this.updateFilterQuery()
    this.setBusy(true, '正在筛选资产目录')
    try {
      await this.loadCatalog({ announce: false })
      this.setStatus('资产工作区已连接', 'ready')
    } catch (error) {
      this.handleError(error, '筛选失败')
    } finally {
      this.setBusy(false)
    }
  }

  clearFilters() {
    for (const select of [this.elements.kindFilter, this.elements.statusFilter]) select.value = ''
    this.applyFilters()
  }

  setMobilePane(pane) {
    if (!['catalog', 'preview', 'review'].includes(pane)) return
    if (pane !== 'catalog' && !this.selectedAsset()) return
    this.mobilePane = pane
    this.elements.workbenchGrid.dataset.mobilePane = pane
    for (const button of this.elements.mobileAssetFlow.querySelectorAll('[data-mobile-pane-target]')) {
      button.setAttribute('aria-pressed', String(button.dataset.mobilePaneTarget === pane))
    }
    this.updateMobileFlow()
  }

  advanceMobileFlow() {
    if (!this.selectedAsset()) {
      this.setMobilePane('catalog')
      return
    }
    if (this.mobilePane === 'catalog') this.setMobilePane('preview')
    else if (this.mobilePane === 'preview') this.setMobilePane('review')
    else {
      const next = buildDraftReviewQueue(this.visibleAssets(), this.selectedAssetId, this.versionAId)[0]
      if (!next) {
        this.toast('当前筛选中没有下一个待验收版本')
        return
      }
      this.selectAsset(next.assetId)
      this.versionAId = next.versionId
      this.renderSelection()
      this.setMobilePane('preview')
    }
  }

  updateMobileFlow() {
    const asset = this.selectedAsset()
    this.elements.previewTabButton.disabled = !asset
    this.elements.reviewTabButton.disabled = !asset
    this.elements.mobileNextButton.disabled = !asset
    this.elements.mobileSelectionLabel.textContent = asset
      ? `${asset.displayName} · ${asset.slot || asset.id}${asset.inherited ? asset.overridable ? ' · 继承可覆盖' : ' · 继承只读' : ''}`
      : '尚未选择资产'
    this.elements.mobileNextButton.textContent = !asset
      ? '选择资产'
      : this.mobilePane === 'catalog'
        ? '查看预览'
        : this.mobilePane === 'preview'
          ? '进入验收'
          : '下一待验收'
  }

  /** Rows currently on screen. Derived, never stored: `this.assets` and `this.filters` are the state. */
  visibleAssets() {
    return filterCatalogAssets(this.assets, this.filters)
  }

  renderCatalog() {
    const renderNonce = ++this.catalogRenderNonce
    const visible = this.visibleAssets()
    this.elements.catalogCount.textContent = String(visible.length)
    this.elements.assetList.replaceChildren()
    if (!visible.length) {
      const hasFilters = Object.values(this.filters).some(Boolean)
      this.showCatalogState(hasFilters
        ? '当前筛选没有匹配资产。清除筛选，或扫描收件箱。'
        : '资产库还是空的。导入第一张 PNG，或扫描服务端收件箱。')
      this.syncBatchControls()
      this.updateMobileFlow()
      return
    }
    const emptyLibrary = visible.every((asset) => asset.versions.length === 0)
    if (emptyLibrary) {
      this.showCatalogState('资产槽位已经准备好，但还没有任何 PNG 版本。请选择槽位后导入第一张 PNG。', { hideList: false, compact: true })
    } else {
      this.hideCatalogState()
    }
    const eligibleIds = new Set(eligibleDraftTargets(visible).map((item) => item.assetId))
    const fragment = this.document.createDocumentFragment()
    for (const asset of visible) {
      const row = createElement(this.document, 'div', `asset-row${asset.inherited ? ' is-inherited' : ''}`)
      row.dataset.assetRow = asset.id
      if (eligibleIds.has(asset.id)) {
        const cell = createElement(this.document, 'label', 'asset-select-cell')
        const checkbox = createElement(this.document, 'input', 'asset-select')
        checkbox.type = 'checkbox'
        checkbox.dataset.selectAsset = asset.id
        checkbox.setAttribute('aria-label', `选择 ${asset.displayName} 参与批量验收`)
        cell.append(checkbox)
        row.append(cell)
      } else {
        // Ineligible rows get an inert spacer instead of announced-but-dead controls.
        const spacer = createElement(this.document, 'span', 'asset-select-cell')
        spacer.setAttribute('aria-hidden', 'true')
        row.append(spacer)
      }
      const button = createElement(this.document, 'button', 'asset-row-open')
      button.type = 'button'
      button.dataset.assetId = asset.id
      button.setAttribute('aria-current', String(asset.id === this.selectedAssetId))
      const thumb = createElement(this.document, 'span', 'asset-thumb', asset.kind.slice(0, 3).toUpperCase())
      thumb.setAttribute('aria-hidden', 'true')
      const copy = createElement(this.document, 'span', 'asset-row-copy')
      copy.append(
        createElement(this.document, 'strong', '', asset.displayName),
        createElement(
          this.document,
          'small',
          '',
          asset.inherited
            ? `${asset.slot || asset.id} · 继承自 ${shortHash(asset.sourceReleaseId || this.pack.baseReleaseId)}${asset.overridable ? ' · 可覆盖' : ''}`
            : `${asset.slot || asset.id} · ${asset.versions.length} 版`,
        ),
      )
      const chip = createElement(
        this.document,
        'span',
        `status-chip is-${safeStatusClass(asset.inherited ? 'inherited' : asset.status)}`,
        asset.inherited ? asset.overridable ? '继承·可覆盖' : '继承·只读' : optionLabel(asset.status || 'pending'),
      )
      button.append(thumb, copy, chip)
      row.append(button)
      fragment.append(row)
      this.renderCatalogThumbnail(asset, thumb, renderNonce)
    }
    this.elements.assetList.append(fragment)
    this.syncBatchControls()
    this.updateMobileFlow()
  }

  async renderCatalogThumbnail(asset, host, renderNonce) {
    const version = asset.versions.find((candidate) => candidate.id === asset.selectedVersionId)
      || asset.versions.find((candidate) => candidate.status === 'draft')
      || asset.versions[0]
    if (!version) return
    try {
      const image = await this.loadVersionImage(version)
      if (renderNonce !== this.catalogRenderNonce || !host.isConnected) return
      const frame = versionFrames(version, image)[0] || {
        x: 0,
        y: 0,
        width: image.width,
        height: image.height,
      }
      const canvas = this.document.createElement('canvas')
      canvas.width = 36
      canvas.height = 36
      const context = canvas.getContext('2d')
      if (!context) return
      context.imageSmoothingEnabled = false
      const scale = Math.min(canvas.width / Math.max(1, frame.width), canvas.height / Math.max(1, frame.height))
      const width = Math.max(1, Math.floor(frame.width * scale))
      const height = Math.max(1, Math.floor(frame.height * scale))
      const x = Math.floor((canvas.width - width) / 2)
      const y = Math.floor((canvas.height - height) / 2)
      context.drawImage(image, frame.x, frame.y, frame.width, frame.height, x, y, width, height)
      host.replaceChildren(canvas)
      host.classList.add('has-image')
    } catch {
      // Keep the readable kind abbreviation when the PNG cannot be decoded.
    }
  }

  updateCatalogHighlight() {
    for (const row of this.elements.assetList.querySelectorAll('[data-asset-row]')) {
      const current = row.dataset.assetRow === this.selectedAssetId
      row.classList.toggle('is-current', current)
      row.querySelector('[data-asset-id]')?.setAttribute('aria-current', String(current))
    }
  }

  /** Single source of truth for every batch-selection surface. Never calls updateControls. */
  syncBatchControls() {
    const selection = buildBatchSelection(this.visibleAssets(), this.selectedIds)
    // A selection you cannot see is a selection you must not be able to submit.
    for (const id of selection.staleIds) this.selectedIds.delete(id)
    const selected = new Set(selection.items.map((item) => item.assetId))
    for (const row of this.elements.assetList.querySelectorAll('[data-asset-row]')) {
      const checkbox = row.querySelector('[data-select-asset]')
      if (!checkbox) continue
      checkbox.checked = selected.has(row.dataset.assetRow)
      checkbox.disabled = this.busy
      row.classList.toggle('is-checked', checkbox.checked)
    }
    const hasWriteBoundary = Boolean(this.bootstrapState?.csrfToken)
    this.elements.selectAllControl.hidden = selection.eligibleCount === 0
    this.elements.selectAllCheckbox.checked = selection.allSelected
    this.elements.selectAllCheckbox.indeterminate = selection.partial
    this.elements.selectAllCheckbox.disabled = this.busy || !hasWriteBoundary
    this.elements.batchBar.hidden = selection.selectedCount === 0
    this.elements.batchSelectionLabel.textContent = `已选 ${selection.selectedCount} 项`
    const blocked = this.busy || !hasWriteBoundary || !selection.selectedCount
    this.elements.batchAcceptButton.disabled = blocked
    this.elements.batchRejectButton.disabled = blocked
    this.elements.batchClearButton.disabled = this.busy || !selection.selectedCount
    this.elements.batchNote.disabled = blocked
  }

  toggleAssetSelection(assetId, checked) {
    if (checked) this.selectedIds.add(String(assetId))
    else this.selectedIds.delete(String(assetId))
    // Deliberately does not re-render the catalog, so focus stays on the checkbox.
    this.syncBatchControls()
  }

  setSelectAll(checked) {
    if (checked) for (const item of eligibleDraftTargets(this.visibleAssets())) this.selectedIds.add(item.assetId)
    else this.selectedIds.clear()
    this.syncBatchControls()
  }

  clearBatchSelection({ notify = false } = {}) {
    const cleared = this.selectedIds.size
    this.selectedIds.clear()
    this.elements.batchNote.value = ''
    this.syncBatchControls()
    if (notify && cleared) this.toast(`已清除 ${cleared} 项批量选择`)
  }

  openBatchConfirmation(decision) {
    if (this.busy) return
    const selection = buildBatchSelection(this.visibleAssets(), this.selectedIds)
    // Blocker lookup uses the full list: it resolves versions by id, and a narrowed list
    // would only make that lookup partial.
    const blocked = decision === 'accepted' ? batchAcceptBlockers(selection.items, this.assets) : []
    const blockedIds = new Set(blocked.map((item) => item.assetId))
    const items = selection.items.filter((item) => !blockedIds.has(item.assetId))
    if (!items.length) {
      this.toast(blocked.length
        ? '所选草稿都被人物一致性门禁阻止，无法批量接受'
        : '请先勾选要批量验收的草稿', true)
      return
    }
    let payload
    try {
      payload = buildBatchReviewPayload(decision, this.elements.batchNote.value, this.revision, items)
    } catch (error) {
      // Fires before the dialog opens, so "拒绝必须填写说明" lands on the field itself.
      this.toast(error.message, true)
      this.elements.batchNote.focus()
      return
    }
    this.pendingBatch = { decision, payload, items }
    this.elements.batchDialogTitle.textContent = decision === 'accepted' ? '确认批量接受？' : '确认批量拒绝？'
    this.elements.batchSummary.textContent = buildBatchConfirmationSummary(items, decision, {
      revision: this.revision,
      packName: this.pack.name,
      packId: this.pack.id,
      blocked,
    })
    this.elements.batchItemCount.textContent = `${items.length} 项`
    this.renderBatchItems(items, blocked)
    this.openDialog(this.elements.batchDialog)
  }

  renderBatchItems(items, blocked) {
    const fragment = this.document.createDocumentFragment()
    for (const item of items) {
      const entry = createElement(this.document, 'li')
      entry.append(
        createElement(this.document, 'span', '', '✓'),
        createElement(this.document, 'strong', '', item.displayName),
        createElement(this.document, 'small', '', `${item.slot || item.assetId} · v${item.versionNumber || item.versionId}`),
      )
      fragment.append(entry)
    }
    for (const item of blocked) {
      const entry = createElement(this.document, 'li', 'is-blocked')
      entry.append(
        createElement(this.document, 'span', '', '!'),
        createElement(this.document, 'strong', '', item.displayName),
        createElement(this.document, 'small', '', `已跳过 · ${item.reason}`),
      )
      fragment.append(entry)
    }
    this.elements.batchItemSummary.replaceChildren(fragment)
  }

  async submitBatchReview() {
    if (this.busy || !this.pendingBatch) return
    const { decision, payload, items } = this.pendingBatch
    this.setBusy(true, '正在提交批量验收')
    try {
      const result = asObject(await this.api.reviewBatch(payload))
      this.acceptMutation(result)
      this.closeDialog(this.elements.batchDialog)
      this.pendingBatch = null
      this.selectedIds.clear()
      this.elements.batchNote.value = ''
      await this.loadCatalog({ announce: false })
      this.setStatus('批量验收已保存', 'ready')
      this.toast(`批量${decision === 'accepted' ? '接受' : '拒绝'}完成：${payload.items.length} 项`)
    } catch (error) {
      // All-or-nothing: nothing was written and the revision did not move, so the dialog
      // has nothing left to do. Close it and drop the blocked rows from the selection —
      // the batch bar then rebuilds a correct payload for the next 批量接受/批量拒绝.
      // Keeping it open would re-post a byte-identical payload and fail identically.
      this.closeDialog(this.elements.batchDialog)
      this.pendingBatch = null
      const failures = asArray(asObject(asObject(error?.payload).details).failures)
      if (failures.length) {
        const { blocked, remaining } = partitionBatchReviewFailures(items, failures)
        this.selectedIds = new Set(remaining.map((item) => item.assetId))
        this.setStatus('批量验收被拒绝', 'error')
        this.toast(`${blocked.length} 项不满足批量条件，整批未写入：${
          blocked.map((item) => `${item.displayName}（${item.reason}）`).join('、')
        }`, true)
        this.syncBatchControls()
      } else {
        this.handleError(error, '批量验收失败')
      }
      await this.loadCatalog({ announce: false }).catch(() => {})
    } finally {
      this.setBusy(false)
      this.restoreFocus(this.elements.assetList, this.elements.refreshButton)
    }
  }

  showCatalogState(message, { hideList = true, compact = false } = {}) {
    this.elements.catalogState.textContent = message
    this.elements.catalogState.classList.add('is-visible')
    this.elements.catalogState.classList.toggle('is-compact', compact)
    this.elements.assetList.hidden = hideList
  }

  hideCatalogState() {
    this.elements.catalogState.classList.remove('is-visible')
    this.elements.catalogState.classList.remove('is-compact')
    this.elements.assetList.hidden = false
  }

  navigateCatalog(event) {
    // Mirrors the click delegate: the row checkboxes own their own keys.
    if (event.target.closest('input')) return
    // Arrow keys walk the rendered rows, so this must read the visible list — walking the
    // full one would land selection on a row that was never rendered.
    const visible = this.visibleAssets()
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !visible.length) return
    event.preventDefault()
    const index = Math.max(0, visible.findIndex((asset) => asset.id === this.selectedAssetId))
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? visible.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length
    this.selectAsset(visible[next].id)
    this.elements.assetList.querySelector(`[data-asset-id="${CSS.escape(visible[next].id)}"]`)?.focus()
  }

  selectedAsset() {
    return this.visibleAssets().find((asset) => asset.id === this.selectedAssetId) || null
  }

  /**
   * The selected asset's complete version list. Filtering decides which rows appear, never
   * what a row contains, so the accepted predecessor a reviewer needs as a baseline is
   * always here. Kept as a helper so the four readers share one guard.
   */
  selectedAssetVersions() {
    return this.selectedAsset()?.versions || []
  }

  selectedVersionA() {
    return this.selectedAssetVersions().find((version) => version.id === this.versionAId) || null
  }

  selectAsset(assetId, { preserveVersions = false } = {}) {
    // Only a rendered row can be selected; selecting a filtered-out asset would leave the
    // highlight with no matching [data-asset-row] to land on.
    const asset = this.visibleAssets().find((candidate) => candidate.id === String(assetId))
    if (!asset) return
    const changed = this.selectedAssetId !== asset.id
    this.selectedAssetId = asset.id
    const versions = this.selectedAssetVersions()
    if (changed || !preserveVersions || !versions.some((version) => version.id === this.versionAId)) {
      this.versionAId = versions.find((version) => version.status === 'draft')?.id
        || asset.selectedVersionId
        || versions[0]?.id
        || ''
    }
    // Only move the highlight: a full re-render would rebuild every checkbox (and re-run
    // every thumbnail decode) on each arrow-key press. renderReviewControls owns resetting
    // the decision/note, and does so only when the rendered version really changed.
    this.updateCatalogHighlight()
    this.renderSelection()
  }

  renderSelection() {
    const asset = this.selectedAsset()
    if (!asset) {
      this.renderEmptySelection()
      return
    }
    this.elements.previewHeading.textContent = asset.displayName
    const options = this.selectedAssetVersions().map((version) => ({
      value: version.id,
      label: `v${version.number || version.id} · ${optionLabel(version.status)}`,
    }))
    setOptions(this.elements.versionASelect, options, { value: this.versionAId })
    this.elements.versionASelect.disabled = !options.length
    this.renderMetadata()
    this.renderHistory()
    this.renderReviewControls()
    this.renderPreview()
    this.updateMobileFlow()
  }

  renderEmptySelection() {
    this.selectedAssetId = ''
    this.versionAId = ''
    this.elements.previewHeading.textContent = '选择一个资产'
    this.elements.previewStatus.textContent = '等待选择'
    this.elements.canvasViewport.classList.remove('has-image')
    // Drop the versions too, or the rAF loop keeps repainting the previous asset's
    // animation underneath the empty-state placeholder.
    this.preview.setVersions(null, null)
    this.elements.metadataSection.replaceChildren(createElement(this.document, 'div', 'empty-copy', '选择资产后显示类型、任务、锚点与占地信息。'))
    this.elements.versionHistory.replaceChildren()
    this.elements.historyCount.textContent = '0 个版本'
    this.elements.inheritedNotice.hidden = true
    this.elements.inheritedNotice.textContent = ''
    this.setMobilePane('catalog')
    this.updateControls()
  }

  async renderPreview() {
    const version = this.selectedVersionA()
    const nonce = ++this.previewLoadNonce
    if (!version) {
      this.elements.canvasViewport.classList.remove('has-image')
      this.elements.previewStatus.textContent = '没有可预览版本'
      this.preview.setVersions(null, null)
      return
    }
    this.elements.previewStatus.classList.remove('is-error')
    this.elements.previewStatus.textContent = '正在载入 PNG…'
    try {
      const image = await this.loadVersionImage(version)
      if (nonce !== this.previewLoadNonce) return
      this.elements.canvasViewport.classList.add('has-image')
      this.elements.previewStatus.textContent = 'PNG 已载入'
      this.preview.setVersions(version, image)
    } catch (error) {
      if (nonce !== this.previewLoadNonce) return
      this.elements.canvasViewport.classList.remove('has-image')
      this.elements.previewStatus.textContent = 'PNG 载入失败'
      this.elements.previewStatus.classList.add('is-error')
      this.preview.setVersions(null, null)
      this.toast(error.message || 'PNG 载入失败', true)
    }
  }

  loadVersionImage(version) {
    const key = version.sha256 || version.blobUrl || version.id
    if (!key) return Promise.reject(new Error('版本没有 sha256，无法读取 PNG'))
    if (!this.imageCache.has(key)) {
      this.imageCache.set(key, this.api.blob(version.sha256, version.blobUrl).then(decodePngBlob).catch((error) => {
        this.imageCache.delete(key)
        throw error
      }))
    }
    return this.imageCache.get(key)
  }

  toggleGuide(button) {
    const name = button.dataset.guide
    const value = button.getAttribute('aria-pressed') !== 'true'
    button.setAttribute('aria-pressed', String(value))
    if (name === 'checker') {
      this.elements.canvasViewport.classList.toggle('has-checker', value)
      return
    }
    this.preview.setGuides({ [name]: value })
  }

  stepFrame(direction) {
    this.preview.step(direction)
  }

  syncAnimationControls(details) {
    const actions = Array.isArray(details.availableActions) ? details.availableActions : []
    const directions = Array.isArray(details.availableDirections) ? details.availableDirections : []
    const key = JSON.stringify([
      actions,
      directions,
      details.animationAction || '',
      details.animationDirection || '',
    ])
    if (key !== this.animationControlsKey) {
      this.animationControlsKey = key
      setOptions(
        this.elements.animationActionSelect,
        actions.map((value) => ({ value, label: ANIMATION_ACTION_LABELS[value] || value })),
        { emptyLabel: actions.length ? '' : '无动作', value: details.animationAction },
      )
      setOptions(
        this.elements.animationDirectionSelect,
        directions.map((value) => ({ value, label: ANIMATION_DIRECTION_LABELS[value] || value })),
        { emptyLabel: directions.length ? '' : '无方向', value: details.animationDirection },
      )
    }
    this.elements.animationActionSelect.disabled = actions.length < 2
    this.elements.animationDirectionSelect.disabled = directions.length < 2
  }

  updatePreviewDetails(details) {
    this.syncAnimationControls(details)
    this.elements.frameLabel.textContent = details.animated
      ? `${details.frame + 1}/${details.frameCount}`
      : '静态 · 1/1'
    this.elements.previousFrameButton.disabled = !details.animated
    this.elements.nextFrameButton.disabled = !details.animated
    this.elements.playPauseButton.disabled = !details.animated
    this.elements.animationSpeedSelect.disabled = !details.animated
    this.elements.playPauseButton.textContent = details.animated
      ? details.paused ? '播放' : '暂停'
      : '静态'
  }

  renderMetadata() {
    const asset = this.selectedAsset()
    const version = this.selectedVersionA()
    if (!asset || !version) {
      this.elements.metadataSection.replaceChildren(createElement(this.document, 'div', 'empty-copy', '当前资产没有版本信息。'))
      return
    }
    const validation = validateVersionMetadata(version)
    const anchor = versionAnchor(version, { width: version.width || 1, height: version.height || 1 })
    const footprint = versionFootprint(version)
    const consistency = characterConsistencyState(asset, version)
    const consistencySummary = asObject(consistency.report?.summary)
    const motionBuild = asObject(consistency.motionBuild)
    const consistencyLabel = consistency.state === 'passed'
      ? `通过 · ${integer(consistencySummary.checkedFrames)} 帧已检查 · ${shortHash(motionBuild.rgbaSha256)}`
      : consistency.state === 'blocked'
        ? motionBuild.verified === false
          ? `阻止接受 · 确定性复编译失败（${asArray(motionBuild.errors).length} 项）`
          : `阻止接受 · ${integer(consistencySummary.failedFrames)} 帧发生身份漂移`
        : consistency.state === 'legacy-unverified'
          ? '缺少确定性构建证明 · 阻止接受'
          : '不适用'
    const list = createElement(this.document, 'dl', 'metadata-list')
    // Owner and member pack differ only for inherited slots; one row unless they do.
    const memberPackId = asset.packId || this.pack.id || ''
    const ownerPackId = asset.ownerPackId || memberPackId
    const rows = [
      ['Asset', asset.id],
      ['Slot', asset.slot || '—'],
      ['类型', asset.kind],
      ['资产包', ownerPackId && ownerPackId !== memberPackId
        ? `${memberPackId || '—'} · 来源 ${ownerPackId}`
        : memberPackId || '—'],
      ['编辑权限', asset.inherited ? asset.overridable ? '继承成员 · 允许导入本包覆盖' : '继承成员 · 只读' : '可验收'],
      ...(asset.inherited ? [['来源 release', asset.sourceReleaseId || this.pack.baseReleaseId || '—']] : []),
      ['生成任务', asset.job || '—'],
      ['版本', `v${version.number || version.id}`],
      ['状态', optionLabel(version.status)],
      ['PNG', `${version.width || '—'}×${version.height || '—'} · ${formatBytes(version.sizeBytes)}`],
      ['SHA-256', shortHash(version.sha256)],
      ['Anchor', `${anchor.x}, ${anchor.y}`],
      ['Footprint', footprint.map((cell) => `${cell.x},${cell.y}${cell.blocked ? '*' : ''}`).join(' · ') || '—'],
      ['Manifest', validation.valid ? '结构检查通过' : `${validation.errors.length} 项问题`],
      ...(consistency.required ? [
        ['人物一致性', consistencyLabel],
        ['确定性构建', motionBuild.verified === true ? `${motionBuild.policy || 'deterministic-pixel-rig-v1'} · 已验证` : '未验证'],
      ] : []),
      ['创建', dateLabel(version.createdAt)],
      ['验收', dateLabel(version.reviewedAt)],
    ]
    for (const [label, value] of rows) {
      list.append(createElement(this.document, 'dt', '', label), createElement(this.document, 'dd', '', value))
    }
    this.elements.metadataSection.replaceChildren(list)
  }

  renderHistory() {
    const versions = this.selectedAssetVersions()
    this.elements.historyCount.textContent = `${versions.length} 个版本`
    this.elements.versionHistory.replaceChildren()
    const fragment = this.document.createDocumentFragment()
    for (const version of versions) {
      const item = createElement(this.document, 'li', `version-item${version.id === this.versionAId ? ' is-selected' : ''}`)
      const button = createElement(this.document, 'button', 'version-select')
      button.type = 'button'
      button.dataset.historyVersion = version.id
      button.setAttribute('aria-current', version.id === this.versionAId ? 'true' : 'false')
      button.append(
        createElement(this.document, 'strong', '', `v${version.number || version.id} · ${shortHash(version.sha256)}`),
        createElement(this.document, 'span', `status-chip is-${safeStatusClass(version.status)}`, optionLabel(version.status)),
        createElement(this.document, 'small', '', `${dateLabel(version.createdAt)} · ${formatBytes(version.sizeBytes)}`),
      )
      item.append(button)
      fragment.append(item)
    }
    this.elements.versionHistory.append(fragment)
  }

  setDecision(decision) {
    if (isFrozenInheritedAsset(this.selectedAsset()) || !REVIEW_DECISIONS.has(decision) || this.selectedVersionA()?.status !== 'draft') return
    this.decision = decision
    for (const button of this.elements.decisionControls.querySelectorAll('[data-decision]')) {
      button.setAttribute('aria-pressed', String(button.dataset.decision === decision))
    }
    this.updateControls()
  }

  renderReviewControls() {
    const asset = this.selectedAsset()
    const version = this.selectedVersionA()
    // Reseed the decision and note only when the rendered version actually changes.
    // loadCatalog re-renders the same version after every refresh/scan/import/filter, and
    // clobbering here would discard a half-written rejection note the reviewer is typing.
    const versionKey = `${asset?.id || ''} ${version?.id || ''}`
    if (versionKey !== this.renderedVersionKey) {
      this.renderedVersionKey = versionKey
      this.decision = isFrozenInheritedAsset(asset)
        ? ''
        : (REVIEW_DECISIONS.has(version?.status) ? version.status : '')
      this.elements.reviewNote.value = version?.reviewNote || ''
    }
    this.elements.inheritedNotice.hidden = !asset?.inherited
    this.elements.inheritedNotice.textContent = asset?.inherited
      ? asset.overridable
        ? String(asset.kind).toLowerCase() === 'character'
          ? `此角色槽位继承自 ${asset.sourceReleaseId || this.pack.baseReleaseId || '基础 release'}。只允许导入 canonical 确定性 Rig 产物；继承版本保持只读，草稿覆盖可独立验收。`
          : `此槽位继承自 ${asset.sourceReleaseId || this.pack.baseReleaseId || '基础 release'}。允许导入 ${this.pack.id || '当前资产包'} 的本地覆盖；继承版本保持只读，草稿覆盖可独立验收。`
        : `此槽位继承自 ${asset.sourceReleaseId || this.pack.baseReleaseId || 'core-v0 release'}，在 ${this.pack.id || '当前资产包'} 中不可导入、修改或重复验收。`
      : ''
    for (const button of this.elements.decisionControls.querySelectorAll('[data-decision]')) {
      button.setAttribute('aria-pressed', String(button.dataset.decision === this.decision))
    }
    this.elements.reviewRevisionHint.textContent = version
      ? asset?.inherited
        ? asset.overridable ? version.status === 'draft' ? `覆盖草稿 · 提交基于 r${this.revision}` : '继承已接受 · 可导入覆盖' : '继承已接受 · 只读'
        : version.status === 'draft'
        ? `提交基于 r${this.revision}`
        : `${optionLabel(version.status)} · 只读`
      : '等待版本'
    this.updateControls()
  }

  async submitReview() {
    if (this.busy) return
    const asset = this.selectedAsset()
    const version = this.selectedVersionA()
    if (!asset || !version) return
    if (isFrozenInheritedAsset(asset)) {
      this.toast('继承资产是冻结的只读成员，无需重复验收。', true)
      return
    }
    let payload
    try {
      payload = buildReviewPayload(this.decision, this.elements.reviewNote.value, this.revision)
    } catch (error) {
      this.toast(error.message, true)
      return
    }
    // Auto-advance walks the rows the reviewer can actually see.
    const remainingDrafts = buildDraftReviewQueue(this.visibleAssets(), asset.id, version.id)
    this.setBusy(true, '正在提交验收结论')
    try {
      const result = asObject(await this.api.review(asset.id, version.id, payload))
      this.acceptMutation(result)
      await this.loadCatalog({ announce: false })
      const visible = this.visibleAssets()
      const next = remainingDrafts.find((candidate) => visible.some((entry) => (
        entry.id === candidate.assetId
        && entry.versions.some((item) => item.id === candidate.versionId && item.status === 'draft')
      )))
      if (next) {
        this.selectAsset(next.assetId)
        this.versionAId = next.versionId
        this.renderSelection()
        this.setMobilePane('preview')
      } else {
        this.updateMobileFlow()
      }
      this.setStatus('验收结论已保存', 'ready')
      this.toast(`${payload.decision === 'accepted' ? '版本已接受' : '版本已拒绝并记录反馈'}${next ? '，已切换到下一待验收版本' : ''}`)
    } catch (error) {
      this.handleError(error, '验收提交失败')
    } finally {
      this.setBusy(false)
    }
  }

  generationRequest() {
    const asset = this.selectedAsset()
    const version = this.selectedVersionA()
    if (!asset || !version) return ''
    return buildGenerationRequest(asset, version, this.style)
  }

  modificationFeedback() {
    const asset = this.selectedAsset()
    const version = this.selectedVersionA()
    if (!asset || !version) return ''
    const note = plainText(this.elements.reviewNote.value).trim() || '尚未填写具体修改说明。'
    return [
      `资产修改反馈：${asset.displayName}（${asset.id}）`,
      `检查版本：v${version.number || version.id} · SHA-256 ${shortHash(version.sha256)}`,
      `PNG：${version.width || '—'} × ${version.height || '—'} px`,
      `当前结论：${this.decision ? optionLabel(this.decision) : '未选择'}`,
      `修改说明：`,
      note,
      `复验时请同时检查：透明边缘、锚点、占地、左上入光与遮挡顺序。`,
    ].join('\n')
  }

  async copyGenerationRequest() {
    await this.copyText(this.generationRequest(), '已复制生成请求')
  }

  async copyModificationFeedback() {
    await this.copyText(this.modificationFeedback(), '已复制修改反馈')
  }

  async copyText(text, successMessage) {
    if (!text) return
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
      else {
        const textarea = createElement(this.document, 'textarea')
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
      this.toast(successMessage)
    } catch (error) {
      this.toast(error.message || '复制失败', true)
    }
  }

  async importPng() {
    if (this.busy) return
    const file = this.elements.pngInput.files?.[0]
    this.elements.importError.textContent = ''
    if (!file || (file.type && file.type !== 'image/png') || !file.name.toLowerCase().endsWith('.png')) {
      this.elements.importError.textContent = '请选择一个 PNG 文件。'
      return
    }
    const maximumBytes = finite(this.bootstrapState?.limits?.maxInputBytes)
    if (maximumBytes > 0 && file.size > maximumBytes) {
      this.elements.importError.textContent = `PNG 超过 ${formatBytes(maximumBytes)} 的导入上限。`
      return
    }
    let metadata
    try {
      metadata = JSON.parse(this.elements.metadataInput.value)
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new TypeError('metadata 必须是 JSON 对象')
      const validation = validateVersionMetadata({ metadata })
      if (!validation.valid) throw new TypeError(validation.errors.map((error) => `${error.path}: ${error.message}`).join('；'))
    } catch (error) {
      this.elements.importError.textContent = `Metadata JSON 无效：${error.message}`
      return
    }
    this.setBusy(true, '正在导入 PNG')
    try {
      const result = asObject(await this.api.importPng(file, metadata, this.pack.id))
      this.acceptMutation(result)
      this.closeDialog(this.elements.importDialog)
      this.elements.importForm.reset()
      await this.loadCatalog({ announce: false })
      const jobId = firstValue(result, ['jobId', 'job_id'], '')
      this.toast(result.deduplicated ? '相同 PNG 已存在，已复用原版本' : `PNG 已导入${jobId ? ` · Job ${jobId}` : ''}`)
      this.setStatus('PNG 导入完成', 'ready')
    } catch (error) {
      this.elements.importError.textContent = error.message || '导入失败'
      this.handleError(error, 'PNG 导入失败')
    } finally {
      this.setBusy(false)
    }
  }

  prepareImportDialog() {
    const asset = this.selectedAsset()
    if (asset?.inherited && !asset?.overridable) {
      this.toast('该槽位继承自已激活 release，在当前资产包中只读。', true)
      return
    }
    if (asset?.slot) {
      let metadata = {}
      try { metadata = asObject(JSON.parse(this.elements.metadataInput.value)) } catch { /* leave invalid text for manual repair */ }
      if (Object.keys(metadata).length) {
        metadata.slot = asset.slot
        metadata.displayName = `${asset.displayName} candidate`
        metadata.packId = this.pack.id
        delete metadata.assetId
        this.elements.metadataInput.value = JSON.stringify(metadata, null, 2)
      }
    }
    this.elements.importError.textContent = ''
    this.openDialog(this.elements.importDialog)
  }

  async scanInbox() {
    if (this.busy) return
    this.setBusy(true, '正在扫描资产收件箱')
    try {
      const result = asObject(await this.api.scanInbox(this.pack.id))
      this.acceptMutation(result)
      await this.loadCatalog({ announce: false })
      const importedValue = firstValue(result, ['imported', 'importedCount'], 0)
      const imported = Array.isArray(importedValue) ? importedValue.length : integer(importedValue)
      const errors = asArray(result.errors)
      this.toast(`扫描完成：导入 ${imported} 项${errors.length ? `，${errors.length} 项失败` : ''}`, errors.length > 0)
      this.setStatus(errors.length ? '扫描完成，存在失败项' : '收件箱扫描完成', errors.length ? 'error' : 'ready')
    } catch (error) {
      this.handleError(error, '收件箱扫描失败')
    } finally {
      this.setBusy(false)
    }
  }

  renderActivation() {
    const pack = this.pack
    const gates = [...pack.gates]
    const requiredSlots = requiredSlotsForPack(pack)
    const inheritedCount = pack.slots.filter((slot) => slot.inherited).length
    if (pack.baseReleaseId) {
      gates.unshift({
        id: 'base-release',
        label: `基础 release 已冻结 · ${inheritedCount} 个继承槽位`,
        passed: inheritedCount > 0 && Boolean(pack.baseReleaseId),
        detail: pack.baseReleaseId,
      })
    }
    if (!gates.length) {
      gates.push({
        id: 'server-gates',
        label: '等待后端返回激活门禁结果',
        passed: false,
        detail: '没有门禁证据时不会开放激活操作。',
      })
    }
    this.elements.gateList.replaceChildren()
    const fragment = this.document.createDocumentFragment()
    for (const gate of gates) {
      const item = createElement(this.document, 'li', `gate-item ${gate.passed ? 'is-passed' : 'is-blocked'}`)
      item.append(
        createElement(this.document, 'i', '', gate.passed ? '✓' : '!'),
        createElement(this.document, 'span', '', `${gate.label}${gate.detail ? `：${gate.detail}` : ''}`),
      )
      fragment.append(item)
    }
    this.elements.gateList.append(fragment)
    const gatesPassed = gates.length > 0 && gates.every((gate) => gate.passed)
    const allowed = Boolean(pack.id) && (pack.canActivate === undefined ? gatesPassed : Boolean(pack.canActivate) && gatesPassed)
    const slotSummary = requiredSlots.length
      ? ` · ${requiredSlots.length} 槽${inheritedCount ? `（继承 ${inheritedCount}）` : ''}`
      : ''
    this.elements.activationState.textContent = pack.active && !pack.hasPendingChanges
      ? '当前已激活'
      : pack.active && pack.hasPendingChanges && allowed
        ? `存在待发布修改 · r${this.revision}`
      : allowed
        ? `门禁通过 · r${this.revision}${slotSummary}`
        : `门禁阻挡 · r${this.revision}${slotSummary}`
    this.elements.activatePackButton.disabled = this.busy || !allowed || (pack.active && !pack.hasPendingChanges)
    this.elements.activatePackButton.textContent = pack.active && !pack.hasPendingChanges
      ? '当前资产包已激活'
      : pack.active && pack.hasPendingChanges
        ? '激活待发布修改'
        : '确认激活当前资产包'
  }

  openActivationConfirmation() {
    if (this.elements.activatePackButton.disabled) return
    const requiredSlots = requiredSlotsForPack(this.pack)
    // Coverage spans the whole pack: activation must never depend on a UI filter.
    const coverage = buildActivationSlotCoverage(this.assets, requiredSlots, this.pack.slots)
    const readyCount = coverage.filter((item) => item.ready).length
    const inheritedCount = coverage.filter((item) => item.inherited && !item.overridable).length
    const overrideCount = coverage.filter((item) => item.inherited && item.overridable).length
    const newCount = coverage.length - inheritedCount - overrideCount
    const gatesPassed = this.pack.gates.length > 0 && this.pack.gates.every((gate) => gate.passed)
    this.elements.activationSummary.textContent = [
      `资产包：${this.pack.name || this.pack.id}`,
      `资产包 ID：${this.pack.id}`,
      `基础 release：${this.pack.baseReleaseId || '无（独立资产包）'}`,
      `预期修订：${this.revision}`,
      `必需槽位：${readyCount}/${coverage.length} 已接受`,
      `组成：${inheritedCount} 个继承只读 · ${overrideCount} 个本包覆盖 · ${newCount} 个新增资产`,
      `已声明门禁：${gatesPassed ? '全部通过' : '存在阻挡'}`,
    ].join('\n')
    this.elements.activationSlotCount.textContent = inheritedCount
      ? `${coverage.length} 项 · 继承 ${inheritedCount} · 覆盖 ${overrideCount} · 新增 ${newCount}`
      : `${coverage.length} 项本包资产`
    this.elements.activationSlotSummary.replaceChildren()
    const fragment = this.document.createDocumentFragment()
    for (const item of coverage) {
      const row = createElement(
        this.document,
        'li',
        `activation-slot-item${item.ready ? ' is-ready' : ''}${item.inherited ? ' is-inherited' : ''}`,
      )
      row.append(
        createElement(this.document, 'i', '', item.ready ? '✓' : '!'),
        createElement(this.document, 'strong', '', item.slot),
        createElement(this.document, 'small', '', item.inherited
          ? item.overridable
            ? item.ready
              ? `本包覆盖 · v${item.versionId} · ${shortHash(item.sha256)}`
              : item.overrideRequired ? '必须接受本包覆盖' : '可选本包覆盖'
            : `继承只读 · ${shortHash(item.sourceReleaseId || this.pack.baseReleaseId)}`
          : item.ready
            ? `v${item.versionId} · ${shortHash(item.sha256)}`
          : item.state === 'missing' ? '缺少资产' : '无已接受版本'),
      )
      fragment.append(row)
    }
    this.elements.activationSlotSummary.append(fragment)
    this.elements.confirmActivationButton.disabled = this.busy || !gatesPassed || readyCount !== coverage.length
    this.openDialog(this.elements.activationDialog)
  }

  async activatePack() {
    if (this.busy || !this.pack.id) return
    const expectedRevision = this.revision
    this.setBusy(true, '正在激活资产包')
    try {
      const result = asObject(await this.api.activate(this.pack.id, expectedRevision))
      if (result.manifest) {
        const validation = validateAssetManifest(result.manifest)
        if (!validation.valid) {
          throw new Error(`后端生成的 manifest 未通过运行时校验：${validation.errors[0]?.message || '未知错误'}`)
        }
      }
      this.acceptMutation(result)
      this.closeDialog(this.elements.activationDialog)
      const refreshed = await this.api.bootstrap()
      this.applyBootstrap(refreshed)
      await this.loadCatalog({ announce: false })
      this.setStatus('资产包激活成功', 'ready')
      this.toast('资产包已激活')
    } catch (error) {
      const envelope = asObject(firstValue(asObject(error.payload), ['details', 'detail', 'error'], {}))
      const details = { ...envelope, ...asObject(envelope.details) }
      const missingSlots = asArray(details.missingSlots)
      if (missingSlots.length) {
        this.pack = normalizePack({ ...this.pack, missingSlots, canActivate: false }, this.revision)
        this.renderActivation()
      }
      this.closeDialog(this.elements.activationDialog)
      this.handleError(error, '资产包激活失败')
    } finally {
      this.setBusy(false)
      this.restoreFocus(this.elements.activationHeading, this.elements.refreshButton)
    }
  }

  acceptMutation(rawPayload) {
    const payload = asObject(unwrapPayload(rawPayload))
    if (Number.isFinite(Number(payload.revision))) this.revision = Number(payload.revision)
    // Single reviews return one `pack`; a batch returns every touched pack as `packs`.
    const changedPacks = [payload.pack, ...asArray(payload.packs)].filter(Boolean)
    for (const entry of changedPacks) {
      const changedPack = normalizePack(entry, this.revision)
      const index = this.packs.findIndex((pack) => pack.id === changedPack.id)
      if (index >= 0) this.packs.splice(index, 1, changedPack)
      else if (changedPack.id) this.packs.push(changedPack)
      if (changedPack.id === this.pack.id) this.pack = changedPack
    }
    if (changedPacks.length) this.renderPackSelector()
    this.elements.packRevisionLabel.textContent = String(this.revision)
    this.renderActivation()
  }

  updateControls() {
    const asset = this.selectedAsset()
    const version = this.selectedVersionA()
    const hasVersion = Boolean(version)
    const hasWriteBoundary = Boolean(this.bootstrapState?.csrfToken)
    const inherited = Boolean(asset?.inherited)
    const overridable = Boolean(asset?.overridable)
    const importable = !inherited || overridable
    const reviewable = hasWriteBoundary && (!inherited || overridable) && version?.status === 'draft'
    const consistency = characterConsistencyState(asset, version)
    this.elements.refreshButton.disabled = this.busy
    this.elements.scanInboxButton.disabled = this.busy || !hasWriteBoundary
    this.elements.openImportButton.disabled = this.busy || !hasWriteBoundary || !importable
    this.elements.packSelect.disabled = this.busy || this.packs.length < 2
    this.elements.clearFiltersButton.disabled = this.busy
    for (const select of [this.elements.kindFilter, this.elements.statusFilter]) select.disabled = this.busy
    for (const button of this.elements.decisionControls.querySelectorAll('[data-decision]')) {
      button.disabled = this.busy
        || !reviewable
        || (button.dataset.decision === 'accepted' && consistency.acceptanceBlocked)
    }
    this.elements.reviewNote.disabled = this.busy || !reviewable
    this.elements.submitReviewButton.disabled = this.busy
      || !reviewable
      || !REVIEW_DECISIONS.has(this.decision)
      || (this.decision === 'accepted' && consistency.acceptanceBlocked)
    this.elements.copyGenerationButton.disabled = !hasVersion || (inherited && !overridable)
    this.elements.copyFeedbackButton.disabled = !hasVersion || (inherited && !overridable)
    this.elements.submitImportButton.disabled = this.busy || !hasWriteBoundary || !importable
    this.elements.confirmActivationButton.disabled = this.busy || !hasWriteBoundary
    this.elements.confirmBatchButton.disabled = this.busy || !hasWriteBoundary
    this.syncBatchControls()
    this.renderActivation()
  }

  setBusy(value, message = '') {
    this.busy = Boolean(value)
    if (this.busy && message) this.setStatus(message, 'working')
    this.updateControls()
  }

  setStatus(message, kind) {
    this.elements.connectionBadge.className = `status-badge is-${kind}`
    this.elements.connectionBadge.querySelector('span').textContent = message
  }

  handleError(error, fallback) {
    this.setStatus(fallback, 'error')
    this.toast(error?.message || fallback, true)
  }

  toast(message, isError = false) {
    window.clearTimeout(this.toastTimer)
    this.elements.toast.textContent = String(message || '')
    this.elements.toast.classList.toggle('is-error', isError)
    this.elements.toast.classList.add('is-visible')
    this.toastTimer = window.setTimeout(() => this.elements.toast.classList.remove('is-visible'), 3_400)
  }

  openDialog(dialog) {
    if (!dialog) return
    if (typeof dialog.showModal === 'function') dialog.showModal()
    else dialog.setAttribute('open', '')
  }

  /**
   * Post-mutation focus landing. dialog.close() returns focus to the invoking control,
   * which by then is hidden (the batch bar) or disabled (the activate button), dropping
   * the keyboard user on <body>. Call this after the new state has settled.
   */
  restoreFocus(...candidates) {
    for (const element of candidates) {
      if (element && element.isConnected && !element.hidden && !element.disabled) {
        element.focus()
        return
      }
    }
  }

  closeDialog(dialog) {
    if (!dialog) return
    if (typeof dialog.close === 'function') dialog.close()
    else dialog.removeAttribute('open')
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  const start = () => {
    const workbench = new AssetsWorkbench(document)
    workbench.start()
    window.addEventListener('pagehide', () => workbench.preview.destroy(), { once: true })
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true })
  else start()
}
