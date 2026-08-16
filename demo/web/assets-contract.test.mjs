import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  AssetsApi,
  batchAcceptBlockers,
  buildActivationSlotCoverage,
  buildBatchConfirmationSummary,
  buildBatchReviewPayload,
  buildBatchSelection,
  buildDraftReviewQueue,
  buildGenerationRequest,
  buildReviewPayload,
  characterConsistencyState,
  eligibleDraftTargets,
  filterCatalogAssets,
  normalizeAsset,
  normalizeBootstrap,
  normalizeCatalog,
  partitionBatchReviewFailures,
  plainText,
  requiredSlotsForPack,
  selectBootstrapPack,
  isFrozenInheritedAsset,
  unwrapPayload,
} from './assets.mjs'
import {
  CORE_V0_REQUIRED_SLOTS,
  CORE_V1_NEW_REQUIRED_SLOTS,
  CORE_V1_REQUIRED_SLOTS,
  CORE_V2_REQUIRED_SLOTS,
  groundPointForPlacement,
  projectGridPoint,
} from './asset-manifest.mjs'
import {
  FIXTURE_IDS,
  advanceAnimationElapsed,
  frameIndexAtElapsed,
  resolveAnimationSelection,
  selectedPlacement,
  stepAnimationFrame,
  versionFrames,
} from './assets-preview.mjs'

const htmlUrl = new URL('./assets.html', import.meta.url)
const cssUrl = new URL('./assets.css', import.meta.url)
const clientUrl = new URL('./assets.mjs', import.meta.url)
const previewUrl = new URL('./assets-preview.mjs', import.meta.url)

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

test('multi-cell fixture previews share the runtime footprint-centre projection', () => {
  const layout = {
    origin: { x: 320, y: 72 },
    selected: { x: 5, y: 3 },
  }
  const cases = [
    {
      id: 'furniture.desk-island',
      frame: { width: 96, height: 80 },
      anchor: { x: 48, y: 64 },
      footprint: [
        { x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 },
        { x: 0, y: 1 }, { x: 1, y: 1 }, { x: 2, y: 1 },
      ],
    },
    {
      id: 'structure.wall-window-nw',
      frame: { width: 128, height: 96 },
      anchor: { x: 64, y: 88 },
      footprint: [
        { x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }, { x: 3, y: 0 },
      ],
    },
  ]

  for (const version of cases) {
    const runtimeGround = groundPointForPlacement(version, layout.selected)
    const runtimeScreenPoint = projectGridPoint(runtimeGround.x, runtimeGround.y, {
      origin: layout.origin,
    })
    const preview = selectedPlacement(version, version.frame, layout)

    assert.deepEqual(preview.ground, runtimeScreenPoint, `${version.id} preview drifted from runtime`)
    assert.deepEqual(preview.destination, {
      x: Math.round(runtimeScreenPoint.x - version.anchor.x),
      y: Math.round(runtimeScreenPoint.y - version.anchor.y),
      width: version.frame.width,
      height: version.frame.height,
    })
  }
})

test('bootstrap accepts direct and ok-wrapped AssetLab payloads', () => {
  const direct = {
    schemaVersion: 1,
    revision: 7,
    csrfToken: 'csrf-memory-only',
    styleProfile: {
      id: 'beijing-modern-isometric-v1',
      name: 'Beijing Modern Isometric',
      worldPalette: Array.from({ length: 32 }, (_, index) => `#0000${index.toString(16).padStart(2, '0')}`),
      playerAccents: Array.from({ length: 8 }, (_, index) => `#ff00${index.toString(16).padStart(2, '0')}`),
    },
    pack: {
      id: 'core-v0',
      name: 'Core v0',
      missingSlots: [],
      invalidSlots: [],
      activation: { enabled: true, active: false, hasPendingChanges: false },
    },
    filters: {
      kinds: ['floor', 'furniture'],
      statuses: ['draft', 'accepted'],
      jobs: ['job-1'],
    },
  }
  const normalized = normalizeBootstrap(direct)
  assert.equal(normalized.revision, 7)
  assert.equal(normalized.csrfToken, 'csrf-memory-only')
  assert.equal(normalized.style.id, 'beijing-modern-isometric-v1')
  assert.equal(normalized.pack.canActivate, true)
  assert.equal(normalized.pack.gates.length, 2)
  assert.equal(normalized.pack.gates.every((gate) => gate.passed), true)
  assert.deepEqual(normalizeBootstrap({ ok: true, data: direct }), normalized)
  assert.deepEqual(unwrapPayload({ ok: true, value: 1 }), { ok: true, value: 1 })
})

test('bootstrap exposes multiple packs while preserving the singular pack contract', () => {
  const baseReleaseId = 'release-core-v0-frozen'
  const direct = {
    revision: 31,
    csrfToken: 'csrf',
    pack: { id: 'core-v0', name: 'Core v0', slots: [] },
    packs: [
      { id: 'core-v0', name: 'Core v0', slots: [] },
      {
        id: 'core-v1',
        name: 'Core v1',
        baseReleaseId,
        slots: CORE_V1_REQUIRED_SLOTS.map((slot, index) => ({
          slot,
          required: true,
          inherited: index < CORE_V0_REQUIRED_SLOTS.length,
          overridable: slot === 'character.gus',
          sourceReleaseId: index < CORE_V0_REQUIRED_SLOTS.length ? baseReleaseId : null,
          selectedVersionId: index < CORE_V0_REQUIRED_SLOTS.length ? `base-v${index}` : null,
          selectedStatus: index < CORE_V0_REQUIRED_SLOTS.length ? 'accepted' : null,
        })),
      },
    ],
  }
  const normalized = normalizeBootstrap(direct)
  assert.equal(normalized.pack.id, 'core-v0')
  assert.deepEqual(normalized.packs.map((pack) => pack.id), ['core-v0', 'core-v1'])
  assert.equal(normalized.packs[1].baseReleaseId, baseReleaseId)
  assert.equal(normalized.packs[1].slots.filter((slot) => slot.inherited).length, 11)
  assert.equal(normalized.packs[1].slots.find((slot) => slot.slot === 'character.gus').overridable, true)
  assert.deepEqual(requiredSlotsForPack(normalized.packs[1]), [...CORE_V1_REQUIRED_SLOTS])
  assert.equal(selectBootstrapPack(normalized.packs, normalized.pack).id, 'core-v1')
  assert.equal(selectBootstrapPack(normalized.packs, normalized.pack, 'core-v0').id, 'core-v0')

  const singular = normalizeBootstrap({ revision: 1, pack: { id: 'only-pack', name: 'Only' } })
  assert.equal(singular.pack.id, 'only-pack')
  assert.deepEqual(singular.packs.map((pack) => pack.id), ['only-pack'])
})

test('core-v2 is selected by default and exposes complete draft scene previews', () => {
  const previewScenes = [
    {
      id: 'opening', label: '光秃开局', layoutId: 'world.opening-empty-v2', status: 'ready',
      blobUrl: `/api/assets/derived/${'a'.repeat(64)}.png`, width: 640, height: 360, sha256: 'a'.repeat(64),
    },
    { id: 'mid-growth', label: '发展中期', layoutId: 'world.mid-growth-v3', status: 'pending' },
  ]
  const normalized = normalizeBootstrap({
    revision: 42,
    pack: { id: 'core-v1', slots: [] },
    packs: [
      { id: 'core-v1', slots: [] },
      { id: 'core-v2', slots: CORE_V2_REQUIRED_SLOTS.map((slot) => ({ slot })), previewScenes },
    ],
  })
  assert.equal(selectBootstrapPack(normalized.packs, normalized.pack).id, 'core-v2')
  assert.equal(normalized.packs[1].previewScenes[0].layoutId, 'world.opening-empty-v2')
  assert.equal(normalized.packs[1].previewScenes[0].width, 640)
  assert.equal(normalized.packs[1].previewScenes[1].blobUrl, '')
  assert.deepEqual(requiredSlotsForPack({ id: 'core-v2' }), [...CORE_V2_REQUIRED_SLOTS])
})

test('catalog normalizes AssetLab versions and generation job metadata', () => {
  const catalog = normalizeCatalog({
    revision: 4,
    assets: [{
      id: 'asset-desk',
      packId: 'core-v0',
      slot: 'furniture.desk-island',
      kind: 'furniture',
      displayName: 'Desk Island',
      revision: 2,
      selectedVersionId: 'version-1',
      versions: [
        {
          id: 'version-1',
          number: 1,
          status: 'accepted',
          sha256: 'a'.repeat(64),
          blobUrl: `/api/assets/blobs/${'a'.repeat(64)}`,
          width: 32,
          height: 24,
          sizeBytes: 123,
          metadata: {
            displayName: '  北京协作桌岛  ',
            jobId: 'generation-job-1',
            frames: [{ x: 0, y: 0, width: 32, height: 24 }],
          },
        },
        {
          id: 'version-2',
          number: 2,
          status: 'draft',
          sha256: 'b'.repeat(64),
          width: 32,
          height: 24,
          metadata: { displayName: '最新草稿桌岛' },
        },
      ],
    }],
  })
  assert.equal(catalog.revision, 4)
  assert.equal(catalog.assets[0].job, 'generation-job-1')
  assert.equal(catalog.assets[0].displayName, '北京协作桌岛')
  assert.deepEqual(catalog.assets[0].versions.map((version) => version.id), ['version-2', 'version-1'])
  assert.equal(catalog.assets[0].selectedVersionId, 'version-1')
})

test('catalog preserves inherited ownership and frozen source release metadata', () => {
  const catalog = normalizeCatalog({
    revision: 9,
    assets: [{
      id: 'asset-core-v0-floor-raw-concrete',
      packId: 'core-v1',
      ownerPackId: 'core-v0',
      slot: 'floor.raw-concrete',
      kind: 'floor',
      inherited: true,
      overridable: true,
      sourceReleaseId: 'release-frozen-base',
      selectedVersionId: 'version-base',
      versions: [{ id: 'version-base', status: 'accepted', sha256: 'a'.repeat(64) }],
    }],
  })
  assert.equal(catalog.assets[0].packId, 'core-v1')
  assert.equal(catalog.assets[0].ownerPackId, 'core-v0')
  assert.equal(catalog.assets[0].inherited, true)
  assert.equal(catalog.assets[0].overridable, true)
  assert.equal(catalog.assets[0].sourceReleaseId, 'release-frozen-base')
})

test('asset display name uses the selected sidecar, then latest sidecar, then asset fallback', () => {
  const versions = [
    { id: 'v1', number: 1, status: 'accepted', metadata: { displayName: '旧版中文名' } },
    { id: 'v2', number: 2, status: 'draft', metadata: { displayName: '最新中文名' } },
  ]
  assert.equal(normalizeAsset({
    id: 'desk', displayName: 'Desk Island', selectedVersionId: 'v1', versions,
  }).displayName, '旧版中文名')
  assert.equal(normalizeAsset({
    id: 'desk', displayName: 'Desk Island', versions,
  }).displayName, '最新中文名')
  assert.equal(normalizeAsset({
    id: 'desk', displayName: 'Desk Island', selectedVersionId: 'v2',
    versions: [{ id: 'v2', number: 2, status: 'draft', metadata: { displayName: '   ' } }],
  }).displayName, 'Desk Island')
})

test('catalog GET emits only packId', async () => {
  let request = null
  const api = new AssetsApi('/api/assets', async (url, options) => {
    request = { url, options }
    return jsonResponse({ revision: 0, assets: [] })
  })
  // Every filter is client-side now: the catalog request carries nothing but the pack.
  await api.catalog({ packId: 'core-v1', kind: 'furniture', status: 'draft', job: 'job/a', ignored: 'never' })
  const url = new URL(request.url, 'http://assets.test')
  assert.equal(url.pathname, '/api/assets/catalog')
  assert.deepEqual(Object.fromEntries(url.searchParams), { packId: 'core-v1' })
  assert.equal(request.options.method, 'GET')
})

test('pack-scoped catalog, import, and inbox scan carry core-v1 explicitly', async () => {
  const requests = []
  const api = new AssetsApi('/api/assets', async (url, options) => {
    requests.push({ url, options })
    return jsonResponse({ revision: 1, assets: [] })
  })
  api.setCsrfToken('csrf-pack')
  await api.catalog({ packId: 'core-v1' })
  const png = new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' })
  Object.defineProperty(png, 'name', { value: 'wall.png' })
  await api.importPng(png, { slot: 'structure.wall-solid-nw' }, 'core-v1')
  await api.scanInbox('core-v1')

  assert.equal(requests[0].url, '/api/assets/catalog?packId=core-v1')
  assert.equal(JSON.parse(requests[1].options.body.get('metadata')).packId, 'core-v1')
  assert.equal(requests[2].url, '/api/assets/inbox/scan?packId=core-v1')
  assert.equal(requests[2].options.headers['X-CSRF-Token'], 'csrf-pack')
})

test('all write requests carry the in-memory CSRF token and CAS revision', async () => {
  const requests = []
  const api = new AssetsApi('/api/assets', async (url, options) => {
    requests.push({ url, options })
    return jsonResponse({ revision: 10 })
  })
  api.setCsrfToken('csrf-test')
  const review = buildReviewPayload('rejected', 'silhouette needs one more pixel', 8)
  await api.review('asset/desk', 'version one', review)
  await api.activate('core-v0', 9)
  const batch = buildBatchReviewPayload('accepted', '', 10, [
    { assetId: 'desk', versionId: 'desk-v3' },
    { assetId: 'cabinet', versionId: 'cabinet-v1' },
    { assetId: 'plant', versionId: 'plant-v1' },
  ])
  await api.reviewBatch(batch)
  // Three decisions, one round trip: that is the whole point of the batch endpoint.
  assert.equal(requests.length, 3)
  for (const request of requests) {
    assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-test')
    assert.equal(request.options.headers['Content-Type'], 'application/json')
    assert.equal(request.options.credentials, 'same-origin')
  }
  assert.equal(requests[0].url, '/api/assets/asset%2Fdesk/versions/version%20one/review')
  assert.deepEqual(JSON.parse(requests[0].options.body), review)
  assert.deepEqual(JSON.parse(requests[1].options.body), { expectedRevision: 9 })
  assert.equal(requests[2].url, '/api/assets/reviews/batch')
  assert.deepEqual(JSON.parse(requests[2].options.body), batch)
})

test('PNG import is multipart with png and metadata and no manual content-type', async () => {
  let request = null
  const api = new AssetsApi('/api/assets', async (url, options) => {
    request = { url, options }
    return jsonResponse({ revision: 1, deduplicated: false })
  })
  api.setCsrfToken('csrf-import')
  const png = new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' })
  Object.defineProperty(png, 'name', { value: 'desk.png' })
  await api.importPng(png, { slot: 'furniture.desk-island', jobId: 'job-1' })
  assert.equal(request.url, '/api/assets/import')
  assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-import')
  assert.equal('Content-Type' in request.options.headers, false)
  assert.equal(request.options.body.get('png').name, 'desk.png')
  assert.deepEqual(JSON.parse(request.options.body.get('metadata')), {
    slot: 'furniture.desk-island',
    jobId: 'job-1',
  })
})

test('review payload is plain text, revision-checked, and requires rejection feedback', () => {
  assert.deepEqual(buildReviewPayload('accepted', '<b>looks good</b>\u0000', 3), {
    decision: 'accepted',
    note: '<b>looks good</b>',
    expectedRevision: 3,
  })
  assert.throws(() => buildReviewPayload('rejected', '   ', 3), /必须填写/)
  assert.throws(() => buildReviewPayload('maybe', 'note', 3), /accepted/)
  assert.equal(plainText('x'.repeat(2_100)).length, 2_000)
})

test('character generation is reference-only and both deterministic build and identity gates are required', () => {
  const asset = {
    id: 'asset-core-v0-character-gus',
    slot: 'character.gus',
    kind: 'character',
    displayName: 'Gus',
  }
  const baseVersion = {
    id: 'gus-v3',
    number: 3,
    sha256: 'a'.repeat(64),
    width: 168,
    height: 192,
    metadata: {
      anchor: { x: 12, y: 46 },
      footprint: [{ x: 0, y: 0, blocked: false }],
    },
  }
  assert.deepEqual(characterConsistencyState(asset, baseVersion), {
    required: true,
    state: 'legacy-unverified',
    acceptanceBlocked: true,
    report: null,
    motionBuild: null,
  })
  const failed = {
    ...baseVersion,
    metadata: {
      ...baseVersion.metadata,
      characterConsistency: { ok: false, summary: { failedFrames: 4 } },
    },
  }
  assert.equal(characterConsistencyState(asset, failed).acceptanceBlocked, true)
  const passed = {
    ...baseVersion,
    metadata: {
      ...baseVersion.metadata,
      characterConsistency: { ok: true, summary: { checkedFrames: 24 } },
      motionBuild: {
        policy: 'deterministic-pixel-rig-v1',
        verified: true,
        rgbaSha256: 'b'.repeat(64),
      },
    },
  }
  assert.equal(characterConsistencyState(asset, passed).acceptanceBlocked, false)
  const forged = {
    ...passed,
    metadata: { ...passed.metadata, motionBuild: { verified: false, errors: [{ code: 'pixels' }] } },
  }
  assert.equal(characterConsistencyState(asset, forged).acceptanceBlocked, true)

  const request = buildGenerationRequest(asset, baseVersion, { name: 'Beijing Modern Isometric' })
  for (const contract of [
    '只生成 Gus 的四方向中立站姿转面参考图',
    '不要生成任何正式动画帧',
    'assets/gus-rig/rig.json',
    'deterministic-pixel-rig-v1',
    'motionBuild 复编译逐像素比对',
  ]) assert.ok(request.includes(contract), `missing character generation contract: ${contract}`)
})

test('review queue advances to the next draft and activation checks all 11 core slots', () => {
  const assets = [
    {
      id: 'desk', slot: 'furniture.desk-island', selectedVersionId: 'desk-v2',
      versions: [
        { id: 'desk-v2', status: 'draft', sha256: 'd'.repeat(64) },
        { id: 'desk-v1', status: 'accepted', sha256: 'a'.repeat(64) },
      ],
    },
    {
      id: 'gus', slot: 'character.gus', selectedVersionId: 'gus-v1',
      versions: [{ id: 'gus-v1', status: 'draft', sha256: 'g'.repeat(64) }],
    },
  ]
  assert.deepEqual(buildDraftReviewQueue(assets, 'desk', 'desk-v2'), [
    { assetId: 'gus', versionId: 'gus-v1' },
  ])
  const coverage = buildActivationSlotCoverage(assets)
  assert.equal(coverage.length, 11)
  assert.deepEqual(coverage.map((item) => item.slot), [...CORE_V0_REQUIRED_SLOTS])
  assert.equal(coverage.find((item) => item.slot === 'furniture.desk-island').ready, true)
  assert.equal(coverage.find((item) => item.slot === 'character.gus').state, 'unaccepted')
  assert.equal(coverage.find((item) => item.slot === 'floor.raw-concrete').state, 'missing')
})

function batchFixtureAssets() {
  return [
    {
      id: 'desk',
      slot: 'furniture.desk-island',
      kind: 'furniture',
      displayName: 'Desk Island',
      inherited: false,
      versions: [
        { id: 'desk-v3', number: 3, status: 'draft' },
        { id: 'desk-v2', number: 2, status: 'draft' },
        { id: 'desk-v1', number: 1, status: 'accepted' },
      ],
    },
    {
      id: 'cabinet',
      slot: 'furniture.storage-cabinet',
      kind: 'furniture',
      displayName: 'Cabinet',
      inherited: false,
      versions: [{ id: 'cabinet-v1', number: 1, status: 'draft' }],
    },
    {
      id: 'frozen',
      slot: 'floor.raw-concrete',
      kind: 'floor',
      displayName: 'Frozen floor',
      inherited: true,
      versions: [{ id: 'frozen-v1', number: 1, status: 'draft' }],
    },
    {
      id: 'settled',
      slot: 'furniture.meeting-table',
      kind: 'furniture',
      displayName: 'Meeting table',
      inherited: false,
      versions: [{ id: 'settled-v1', number: 1, status: 'accepted' }],
    },
  ]
}

test('eligibleDraftTargets skips inherited assets and picks the newest draft', () => {
  assert.deepEqual(eligibleDraftTargets(batchFixtureAssets()), [
    {
      assetId: 'desk',
      versionId: 'desk-v3',
      versionNumber: 3,
      displayName: 'Desk Island',
      slot: 'furniture.desk-island',
      kind: 'furniture',
    },
    {
      assetId: 'cabinet',
      versionId: 'cabinet-v1',
      versionNumber: 1,
      displayName: 'Cabinet',
      slot: 'furniture.storage-cabinet',
      kind: 'furniture',
    },
  ])
})

test('core-v2 inherited overrides remain reviewable while frozen inherited slots stay read-only', () => {
  const assets = [
    {
      id: 'window', inherited: true, overridable: true, slot: 'structure.wall-window-nw',
      versions: [{ id: 'window-v2', number: 2, status: 'draft' }],
    },
    {
      id: 'plant', inherited: true, overridable: false, slot: 'decor.floor-plant',
      versions: [{ id: 'plant-v1', number: 1, status: 'draft' }],
    },
  ]
  assert.deepEqual(eligibleDraftTargets(assets).map((item) => item.assetId), ['window'])
  assert.equal(isFrozenInheritedAsset(assets[0]), false)
  assert.equal(isFrozenInheritedAsset(assets[1]), true)
})

test('required overrides cannot borrow an accepted base version for activation coverage', () => {
  const assets = [{
    id: 'window', slot: 'structure.wall-window-nw', inherited: true, overridable: true,
    selectedVersionId: 'override-draft',
    versions: [
      { id: 'override-draft', status: 'draft', sha256: 'b'.repeat(64) },
      { id: 'base-accepted', status: 'accepted', sha256: 'a'.repeat(64) },
    ],
  }]
  const packSlots = [{
    slot: 'structure.wall-window-nw', inherited: true, overridable: true, overrideRequired: true,
    selectedVersionId: 'override-draft', selectedStatus: 'draft',
  }]
  const blocked = buildActivationSlotCoverage(assets, ['structure.wall-window-nw'], packSlots)[0]
  assert.equal(blocked.ready, false)
  const ready = buildActivationSlotCoverage(
    [{ ...assets[0], selectedVersionId: 'base-accepted' }],
    ['structure.wall-window-nw'],
    [{ ...packSlots[0], selectedVersionId: 'base-accepted', selectedStatus: 'accepted' }],
  )[0]
  assert.equal(ready.ready, true)
})

test('filterCatalogAssets narrows rows only and never truncates a version history', () => {
  const assets = normalizeCatalog({
    revision: 3,
    assets: [
      {
        id: 'desk', slot: 'furniture.desk-island', kind: 'furniture',
        versions: [
          { id: 'd2', number: 2, status: 'draft' },
          { id: 'd1', number: 1, status: 'accepted' },
        ],
      },
      {
        id: 'wall', slot: 'structure.wall-solid-nw', kind: 'structure',
        versions: [{ id: 'w1', number: 1, status: 'accepted' }],
      },
      { id: 'plant', slot: 'decor.plant', kind: 'decor', versions: [] },
    ],
  }).assets
  const byId = (list) => list.map((asset) => asset.id)

  assert.deepEqual(byId(filterCatalogAssets(assets, {})), byId(assets))
  assert.deepEqual(byId(filterCatalogAssets(assets, { kind: 'furniture' })), ['desk'])

  const drafts = filterCatalogAssets(assets, { status: 'draft' })
  assert.deepEqual(byId(drafts), ['desk'])
  // Same object, whole history: a status filter hides rows, never versions.
  assert.equal(drafts[0], assets.find((asset) => asset.id === 'desk'))
  assert.deepEqual(drafts[0].versions.map((version) => version.status), ['draft', 'accepted'])

  // Versionless slots drop out of a status filter, exactly as the server used to drop them.
  assert.deepEqual(byId(filterCatalogAssets(assets, { status: 'accepted' })), ['desk', 'wall'])
  assert.deepEqual(byId(filterCatalogAssets(assets, { kind: 'furniture', status: 'accepted' })), ['desk'])
  assert.deepEqual(byId(filterCatalogAssets(assets, { status: 'rejected' })), [])
})

test('buildBatchSelection reports select-all state and prunes stale ids', () => {
  const assets = batchFixtureAssets()
  const empty = buildBatchSelection(assets, new Set())
  assert.equal(empty.selectedCount, 0)
  assert.equal(empty.eligibleCount, 2)
  assert.equal(empty.allSelected, false)
  assert.equal(empty.partial, false)

  const partial = buildBatchSelection(assets, new Set(['cabinet']))
  assert.deepEqual(partial.items.map((item) => item.assetId), ['cabinet'])
  assert.equal(partial.partial, true)
  assert.equal(partial.allSelected, false)

  const all = buildBatchSelection(assets, ['desk', 'cabinet'])
  assert.equal(all.allSelected, true)
  assert.equal(all.partial, false)
  // Catalog order, not selection order.
  assert.deepEqual(all.items.map((item) => item.assetId), ['desk', 'cabinet'])

  // Inherited, already-settled and unknown ids are all stale, never submittable.
  const stale = buildBatchSelection(assets, ['desk', 'frozen', 'settled', 'ghost'])
  assert.deepEqual(stale.items.map((item) => item.assetId), ['desk'])
  assert.deepEqual(stale.staleIds.sort(), ['frozen', 'ghost', 'settled'])
})

test('batchAcceptBlockers excludes character drafts without a verified deterministic build', () => {
  const assets = [
    {
      id: 'gus',
      slot: 'character.gus',
      kind: 'character',
      displayName: 'Gus',
      inherited: false,
      versions: [{
        id: 'gus-v2',
        number: 2,
        status: 'draft',
        metadata: {
          motionBuild: { verified: true },
          characterConsistency: { ok: false, summary: { failedFrames: 2 } },
        },
      }],
    },
    {
      id: 'gus-ok',
      slot: 'character.gus',
      kind: 'character',
      displayName: 'Gus verified',
      inherited: false,
      versions: [{
        id: 'gus-ok-v1',
        number: 1,
        status: 'draft',
        metadata: {
          motionBuild: { verified: true },
          characterConsistency: { ok: true },
        },
      }],
    },
    ...batchFixtureAssets(),
  ]
  const items = eligibleDraftTargets(assets)
  const blockers = batchAcceptBlockers(items, assets)
  assert.deepEqual(blockers.map((item) => item.assetId), ['gus'])
  assert.equal(blockers[0].reason, '未通过角色身份一致性门禁')
})

test('batch review payload dedupes, requires rejection feedback, and is revision-checked', () => {
  const items = [
    { assetId: 'a', versionId: 'v1' },
    { assetId: 'a', versionId: 'v1' },
    { assetId: 'b', versionId: 'v2' },
  ]
  assert.deepEqual(buildBatchReviewPayload('accepted', '<b>looks good</b>\u0000', 3, items), {
    items: [
      { assetId: 'a', versionId: 'v1', decision: 'accepted' },
      { assetId: 'b', versionId: 'v2', decision: 'accepted' },
    ],
    note: '<b>looks good</b>',
    expectedRevision: 3,
  })
  assert.throws(() => buildBatchReviewPayload('rejected', '   ', 3, items), /必须填写/)
  assert.throws(() => buildBatchReviewPayload('maybe', 'note', 3, items), /accepted/)
  assert.throws(() => buildBatchReviewPayload('accepted', '', -1, items), /expectedRevision/)
  assert.throws(() => buildBatchReviewPayload('accepted', '', 3, []), /至少/)
  assert.throws(() => buildBatchReviewPayload('accepted', '', 3, [{ assetId: 'a' }]), /assetId 与 versionId/)
  assert.throws(
    () => buildBatchReviewPayload('accepted', '', 3, [{ assetId: 'a', versionId: 'v1' }, { assetId: 'a', versionId: 'v2' }]),
    /同一资产/,
  )
  // Two rejects on one asset are order-independent and stay legal.
  assert.equal(
    buildBatchReviewPayload('rejected', 'fix the anchor', 3, [
      { assetId: 'a', versionId: 'v1' },
      { assetId: 'a', versionId: 'v2' },
    ]).items.length,
    2,
  )
  assert.throws(
    () => buildBatchReviewPayload('accepted', '', 3, Array.from({ length: 201 }, (_, index) => ({
      assetId: `asset-${index}`, versionId: `v${index}`,
    }))),
    /最多/,
  )
  assert.equal(
    buildBatchReviewPayload('rejected', 'x'.repeat(2_100), 3, items).note.length,
    2_000,
  )
})

test('partitionBatchReviewFailures keeps retryable items after an all-or-nothing failure', () => {
  const items = [
    { assetId: 'desk', versionId: 'desk-v3', displayName: 'Desk Island' },
    { assetId: 'gus', versionId: 'gus-v2', displayName: 'Gus' },
    { assetId: 'cabinet', versionId: 'cabinet-v1', displayName: 'Cabinet' },
  ]
  const { blocked, remaining } = partitionBatchReviewFailures(items, [
    { index: 1, assetId: 'gus', versionId: 'gus-v2', code: 'review.character_motion_unverified', message: '角色动作表不是已验证的确定性像素 Rig 编译结果' },
    { index: 2, assetId: 'cabinet', versionId: 'cabinet-v1', code: 'version.not_draft' },
  ])
  assert.deepEqual(blocked.map((item) => item.displayName), ['Gus', 'Cabinet'])
  assert.equal(blocked[0].reason, '角色动作表不是已验证的确定性像素 Rig 编译结果')
  // Generic server codes carry English messages; they are translated, not leaked.
  assert.equal(blocked[1].reason, '已不是待验收草稿')
  assert.deepEqual(remaining.map((item) => item.assetId), ['desk'])
})

test('batch confirmation summary is plain Chinese and counts skipped items', () => {
  const summary = buildBatchConfirmationSummary(
    [{ assetId: 'desk' }, { assetId: 'cabinet' }],
    'rejected',
    { revision: 7, packName: '核心包 v0', packId: 'core-v0', blocked: [{ assetId: 'gus' }] },
  )
  assert.ok(summary.includes('资产包：核心包 v0'))
  assert.ok(summary.includes('资产包 ID：core-v0'))
  assert.ok(summary.includes('批量结论：已拒绝'))
  assert.ok(summary.includes('提交项数：2'))
  assert.ok(summary.includes('跳过项数：1'))
  assert.ok(summary.includes('预期修订：r7'))
})

test('core-v1 review queue skips inherited drafts and activation covers all 22 slots', () => {
  const baseReleaseId = 'release-base-v0'
  const inherited = CORE_V0_REQUIRED_SLOTS.map((slot, index) => ({
    id: `base-${index}`,
    slot,
    inherited: true,
    sourceReleaseId: baseReleaseId,
    selectedVersionId: `base-version-${index}`,
    versions: [{ id: `base-version-${index}`, status: 'accepted', sha256: `${index}`.padStart(64, '0') }],
  }))
  inherited[0].versions.unshift({ id: 'impossible-inherited-draft', status: 'draft' })
  const editable = CORE_V1_NEW_REQUIRED_SLOTS.map((slot, index) => ({
    id: `new-${index}`,
    slot,
    inherited: false,
    selectedVersionId: `new-version-${index}`,
    versions: [{ id: `new-version-${index}`, status: index === 0 ? 'draft' : 'accepted', sha256: 'f'.repeat(64) }],
  }))
  assert.deepEqual(buildDraftReviewQueue([...inherited, ...editable]), [
    { assetId: 'new-0', versionId: 'new-version-0' },
  ])

  const packSlots = [...inherited, ...editable].map((asset) => ({
    slot: asset.slot,
    assetId: asset.id,
    selectedVersionId: asset.selectedVersionId,
    selectedStatus: asset.versions.find((version) => version.id === asset.selectedVersionId)?.status,
    inherited: asset.inherited,
    sourceReleaseId: asset.sourceReleaseId,
  }))
  const coverage = buildActivationSlotCoverage([...inherited, ...editable], CORE_V1_REQUIRED_SLOTS, packSlots)
  assert.equal(coverage.length, 22)
  assert.equal(coverage.filter((item) => item.inherited).length, 11)
  assert.equal(coverage.filter((item) => item.ready).length, 21)
  assert.equal(coverage[0].sourceReleaseId, baseReleaseId)
  assert.equal(coverage.at(-1).inherited, false)
})

test('real AssetLab Gus metadata defaults to four-frame walk+southeast and supports work/stepping', () => {
  const metadata = {
    slot: 'character.gus',
    frameWidth: 24,
    frameHeight: 48,
    columns: 7,
    frameCount: 28,
    anchor: { x: 1, y: 2 },
    animations: {
      idle: {
        southeast: [0], southwest: [7], northwest: [14], northeast: [21],
      },
      walk: {
        southeast: [1, 2, 3, 4], southwest: [8, 9, 10, 11],
        northwest: [15, 16, 17, 18], northeast: [22, 23, 24, 25],
      },
      work: {
        southeast: [5, 6], southwest: [12, 13], northwest: [19, 20], northeast: [26, 27],
      },
    },
  }
  const image = { width: 168, height: 192 }
  const version = { width: 168, height: 192, metadata }
  const selection = resolveAnimationSelection(version)
  assert.equal(selection.action, 'walk')
  assert.equal(selection.direction, 'southeast')
  assert.deepEqual(selection.actions, ['walk', 'idle', 'work'])
  assert.deepEqual(selection.directions, ['southeast', 'southwest', 'northwest', 'northeast'])

  const walk = versionFrames(version, image)
  assert.equal(walk.length, 4)
  assert.deepEqual(walk.map((frame) => [frame.x, frame.y]), [
    [24, 0], [48, 0], [72, 0], [96, 0],
  ])
  let elapsedMs = 0
  const rafFrames = [frameIndexAtElapsed(walk, elapsedMs)]
  for (let tick = 0; tick < 4; tick += 1) {
    elapsedMs = advanceAnimationElapsed(elapsedMs, 125, { speed: 1, paused: false })
    rafFrames.push(frameIndexAtElapsed(walk, elapsedMs))
  }
  assert.deepEqual(rafFrames, [0, 1, 2, 3, 0])
  assert.equal(advanceAnimationElapsed(elapsedMs, 125, { paused: true }), elapsedMs)

  assert.deepEqual(stepAnimationFrame(walk, 0, 1), { index: 1, elapsedMs: 125 })
  assert.deepEqual(stepAnimationFrame(walk, 0, -1), { index: 3, elapsedMs: 375 })
  const work = versionFrames(version, image, { action: 'work', direction: 'southeast' })
  assert.deepEqual(work.map((frame) => [frame.x, frame.y]), [[120, 0], [144, 0]])
  const southwestIdle = versionFrames(version, image, { action: 'idle', direction: 'southwest' })
  assert.deepEqual(southwestIdle.map((frame) => [frame.x, frame.y]), [[0, 48]])

  assert.deepEqual(Object.values(FIXTURE_IDS), [
    'opening-empty', 'mid-growth', 'eight-player', 'occlusion-stress',
  ])
})

test('assets page keeps workbench, responsive, accessibility, and privacy contracts', async () => {
  const [html, css, client, preview] = await Promise.all([
    readFile(fileURLToPath(htmlUrl), 'utf8'),
    readFile(fileURLToPath(cssUrl), 'utf8'),
    readFile(fileURLToPath(clientUrl), 'utf8'),
    readFile(fileURLToPath(previewUrl), 'utf8'),
  ])
  for (const marker of [
    'class="workbench-grid"',
    'class="back-link" href="/review"',
    'id="assetList"',
    'id="versionASelect"',
    'id="animationActionSelect"',
    'id="animationDirectionSelect"',
    'data-scale="1"',
    'data-scale="2"',
    'data-scale="4"',
    'data-guide="checker"',
    'data-guide="grid"',
    'data-guide="anchor"',
    'data-guide="footprint"',
    'data-guide="light"',
    'value="bare"',
    'value="growth"',
    'value="team"',
    'value="occlusion"',
    'id="reviewForm"',
    'id="activationDialog"',
    'id="mobileAssetFlow"',
    'id="assetTabButton"',
    'id="previewTabButton"',
    'id="reviewTabButton"',
    'id="mobileSelectionLabel"',
    'id="mobileNextButton"',
    'id="activationSlotSummary"',
    'id="packSelect"',
    'id="packContext"',
    'id="baseReleaseSummary"',
    'id="inheritedNotice"',
    'id="packScenePreviewSection"',
    'id="packScenePreviewList"',
    'id="activationSlotCount"',
    'id="activationHeading" tabindex="-1"',
    'id="selectAllCheckbox"',
    'id="batchBar"',
    'id="batchNote"',
    'id="batchAcceptButton"',
    'id="batchRejectButton"',
    'id="batchDialog"',
    'id="batchItemSummary"',
  ]) assert.ok(html.includes(marker), `missing assets marker: ${marker}`)
  for (const removed of [
    'id="jobFilter"',
    'id="versionBSelect"',
    'data-compare-mode',
    'id="opacityControl"',
    'id="diffLabel"',
    'PREVIEW + COMPARE',
    'class="eyebrow"',
    'class="workspace-actions"',
    'role="listbox"',
    // The caption reported canvas geometry, not the PNG's size, contradicting the
    // inspector's PNG row; the batch error box is superseded by closing the dialog.
    'id="dimensionLabel"',
    'class="preview-caption"',
    'id="batchError"',
    // #packSelect's option text is a strict superset of #packLabel, and #packContext is
    // the single slot-composition readout.
    'id="packLabel"',
    'id="workspaceHint"',
    // Class names carrying no CSS rule; .modal-card and .inspector-section are the hooks.
    'confirmation-card',
    'metadata-section',
    'review-section',
    'is-idle',
    // Two buttons that can both be unset are not a radiogroup; they match #scaleControls.
    'role="radio"',
    'aria-checked',
  ]) assert.equal(html.includes(removed), false, `removed assets marker remains: ${removed}`)
  for (const marker of [
    '@media (max-width: 1180px)', '@media (max-width: 760px)', '@media (max-width: 430px)',
    'min-height: 44px', '.batch-bar', '.asset-select',
    // The chip vocabulary must cover what the backend actually emits.
    '.status-chip.is-draft', '.status-chip.is-superseded', '.pack-scene-preview.is-invalid',
  ]) {
    assert.ok(css.includes(marker), `missing CSS contract: ${marker}`)
  }
  // Dead chip variants: the backend never emits approved/review/active for a version.
  assert.equal(/\.status-chip\.is-(approved|review|active)/.test(css), false)
  // The 1180px inspector grid put this rule on the wrong column edge.
  assert.equal(/border-right/.test(css), false)
  // The decision buttons moved to aria-pressed. Forgetting these two CSS selectors would
  // silently drop the selected-state styling with nothing else to catch it.
  assert.equal(/aria-checked/.test(css), false)
  assert.ok(css.includes('.decision-controls button[aria-pressed="true"][data-decision="accepted"]'))
  assert.ok(css.includes('.decision-controls button[aria-pressed="true"][data-decision="rejected"]'))
  assert.equal(/localStorage|sessionStorage/.test(client), false)
  assert.equal(/setCompareMode|updateCompareControls|selectedVersionB|versionBId/.test(client), false)
  assert.equal(client.includes('this.filters.job'), false)
  assert.equal(/computePixelDiff|setOpacity\(|setMode\(/.test(preview), false)
  assert.ok(client.includes("event.key === 'ArrowLeft'"))
  assert.ok(client.includes("event.key === 'ArrowRight'"))
  assert.ok(client.includes('renderCatalogThumbnail'))
  assert.ok(client.includes('buildDraftReviewQueue'))
  assert.ok(client.includes('eligibleDraftTargets'))
  assert.ok(client.includes('buildBatchSelection'))
  assert.ok(client.includes('buildBatchReviewPayload'))
  assert.ok(client.includes('data-select-asset'))
  assert.ok(client.includes('this.api.reviewBatch'))
  // The inspector reads the unfiltered version list, and only reseeds the note/decision
  // when the rendered version actually changed.
  assert.ok(client.includes('selectedAssetVersions'))
  assert.ok(client.includes('renderedVersionKey'))
  // One catalog fetch. Rows are narrowed in the browser, and no code path truncates a
  // version list, so the inspector, the thumbnail and the "N 版" count all agree.
  assert.ok(client.includes('filterCatalogAssets'))
  assert.ok(client.includes('visibleAssets'))
  assert.equal(/hasServerFilters|catalogAssets/.test(client), false)
  // Dead normalization: the server emits no gate list and always a decoded metadata object.
  assert.equal(/normalizeGate|activationGates|parseMetadata/.test(client), false)
  assert.equal(/'assetKinds'|'reviewStatuses'/.test(client), false)
  assert.equal(/JSON\.parse/.test(preview), false)
  // renderReviewControls owns the decision; only the constructor seeds it.
  assert.equal(client.match(/this\.decision = ''/g).length, 1)
  assert.equal(client.includes('is-blank'), false)
  assert.equal(client.includes('packLabel'), false)
  assert.equal(client.includes('workspaceHint'), false)
  // Owner and member pack collapse into one inspector row.
  assert.equal(client.includes('所属资产包'), false)
  // An unreadable scene source must not render as "waiting" forever.
  assert.ok(client.includes('场景图不可用'))
  // The scene gallery is pack-wide context for 激活资产包, not a preview-panel tool.
  assert.ok(html.indexOf('id="packScenePreviewSection"') > html.indexOf('id="inspectorHeading"'))
  assert.ok(html.indexOf('id="packScenePreviewSection"') < html.indexOf('id="activationHeading"'))
  assert.equal(/id="packScenePreviewList"[^>]*aria-live/.test(html), false)
  // Sticky-to-bottom, so it must be last in flow.
  assert.ok(html.indexOf('class="mobile-selection-bar"') > html.indexOf('id="workbenchGrid"'))
  // The pane switch belongs to the tap gesture, not to selection: switching panes from
  // selectAsset hid the catalog out from under the keydown listener bound to #assetList.
  assert.equal(/if \(changed\) this\.setMobilePane/.test(client), false)
  assert.equal(client.match(/event\.target\.closest\('input'\)/g).length, 2)
  // advanceMobileFlow has no inherited branch; step 2 always opens the review pane.
  assert.equal(client.includes('查看来源'), false)
  // The write buttons must be gated while bootstrap installs the CSRF token.
  assert.ok(client.includes("this.setBusy(true, '正在读取资产工作区')"))
  assert.ok(client.includes('restoreFocus'))
  assert.equal(/this\.elements\.reviewNote\.value = ''/.test(client), false)
  for (const label of ['批量结论：', '提交项数：', '跳过项数：', '预期修订：']) {
    assert.ok(client.includes(label), `missing Chinese batch summary label: ${label}`)
  }
  assert.ok(client.includes('CORE_V0_REQUIRED_SLOTS'))
  assert.ok(client.includes('CORE_V1_REQUIRED_SLOTS'))
  assert.ok(client.includes('CORE_V2_REQUIRED_SLOTS'))
  assert.ok(client.includes('renderPackScenePreviews'))
  assert.ok(client.includes("query.get('pack')"))
  assert.ok(client.includes("url.searchParams.set('pack', this.pack.id)"))
  assert.ok(client.includes("asset.overridable ? '继承·可覆盖' : '继承·只读'"))
  assert.ok(client.includes('只允许导入 canonical 确定性 Rig 产物'))
  for (const label of ['资产包：', '资产包 ID：', '基础 release：', '预期修订：', '必需槽位：', '组成：', '已声明门禁：']) {
    assert.ok(client.includes(label), `missing Chinese activation summary label: ${label}`)
  }
  for (const label of ['Pack:', 'Pack ID:', 'Expected revision:', 'Required slots:', 'All declared gates:']) {
    assert.equal(client.includes(label), false, `English activation summary label remains: ${label}`)
  }
  assert.ok(preview.includes("from './asset-runtime.mjs'"))
  assert.ok(preview.includes("from './asset-manifest.mjs'"))
  assert.ok(preview.includes('requestAnimationFrame((time) => this.tick(time))'))
})
