import {
  animationTotalDuration,
  assertAssetManifest,
  depthForPlacement,
  fixtureCollisionCells,
  projectFixture,
  resolveAnimationFrame,
  sortFixtureForOcclusion,
  stableManifestStringify,
} from './asset-manifest.mjs'

const SHA256_PATTERN = /^[0-9a-f]{64}$/i

export class AssetIntegrityError extends Error {
  constructor(message, { code = 'asset_integrity_failed', expected = null, actual = null } = {}) {
    super(message)
    this.name = 'AssetIntegrityError'
    this.code = code
    this.expected = expected
    this.actual = actual
  }
}

function isBlobLike(value) {
  return Boolean(value) && typeof value.text === 'function' && typeof value.arrayBuffer === 'function'
}

function atlasValue(collection, atlas) {
  if (collection instanceof Map) return collection.get(atlas.id) ?? collection.get(atlas.source)
  if (collection && typeof collection === 'object') return collection[atlas.id] ?? collection[atlas.source]
  return undefined
}

function responseError(response, label) {
  return new Error(`${label} 加载失败（HTTP ${response?.status ?? 'unknown'}）`)
}

export function normaliseSha256(value, label = 'SHA-256') {
  const normalized = String(value || '').trim().toLowerCase()
  if (!SHA256_PATTERN.test(normalized)) {
    throw new AssetIntegrityError(`${label} 必须是 64 位十六进制摘要`, {
      code: 'asset_hash_invalid',
      actual: normalized || null,
    })
  }
  return normalized
}

function bytesFrom(value) {
  if (value instanceof Uint8Array) return value
  if (value instanceof ArrayBuffer) return new Uint8Array(value)
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  }
  if (typeof value === 'string') return new TextEncoder().encode(value)
  throw new TypeError('SHA-256 输入必须是字符串、ArrayBuffer 或 Uint8Array')
}

export async function sha256Hex(value, cryptoImpl = globalThis.crypto) {
  if (!cryptoImpl?.subtle?.digest) {
    throw new AssetIntegrityError('当前环境无法校验资产 SHA-256', {
      code: 'asset_hash_unsupported',
    })
  }
  const digest = await cryptoImpl.subtle.digest('SHA-256', bytesFrom(value))
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function assertSha256(value, expectedValue, label, cryptoImpl) {
  const expected = normaliseSha256(expectedValue, `${label} SHA-256`)
  const actual = await sha256Hex(value, cryptoImpl)
  if (actual !== expected) {
    throw new AssetIntegrityError(`${label} 完整性校验失败`, {
      code: 'asset_hash_mismatch',
      expected,
      actual,
    })
  }
  return actual
}

function canonicalJsonValue(value) {
  if (Array.isArray(value)) return value.map(canonicalJsonValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalJsonValue(value[key])]),
  )
}

export function canonicalJsonStringify(value) {
  return JSON.stringify(canonicalJsonValue(value))
}

export async function assertFrozenLayoutSha256(layout, expectedValue, cryptoImpl = globalThis.crypto) {
  if (!layout || typeof layout !== 'object' || Array.isArray(layout)) {
    throw new AssetIntegrityError('world.layout 必须是对象', { code: 'asset_layout_invalid' })
  }
  const canonicalLayout = JSON.parse(JSON.stringify(layout))
  delete canonicalLayout.sha256
  delete canonicalLayout.layoutSha256
  delete canonicalLayout.layout_sha256
  const expected = normaliseSha256(expectedValue, 'layout SHA-256')
  const actual = await sha256Hex(canonicalJsonStringify(canonicalLayout), cryptoImpl)
  if (actual !== expected) {
    throw new AssetIntegrityError('world.layout 完整性校验失败', {
      code: 'asset_layout_hash_mismatch',
      expected,
      actual,
    })
  }
  return actual
}

function assertAllowedOrigin(url, allowedOrigin, label) {
  if (!allowedOrigin) return
  const normalized = new URL(String(allowedOrigin)).origin
  if (url.origin !== normalized) {
    throw new AssetIntegrityError(`${label} 必须与游戏页面同源`, {
      code: 'asset_origin_mismatch',
      expected: normalized,
      actual: url.origin,
    })
  }
}

function expectedAtlasHash(collection, atlas) {
  if (!collection) return null
  if (typeof collection === 'string') return collection
  return atlasValue(collection, atlas) ?? null
}

export async function loadActiveManifest(source, {
  fetchImpl = (...args) => globalThis.fetch(...args),
  baseUrl,
} = {}) {
  if (!source) throw new TypeError('必须提供 active manifest 对象、Blob 或 URL')
  if (typeof source === 'object' && !isBlobLike(source) && typeof source.json !== 'function') {
    return {
      manifest: assertAssetManifest(source),
      baseUrl: new URL('.', baseUrl || globalThis.location?.href || import.meta.url),
    }
  }

  if (typeof source === 'string' || source instanceof URL) {
    const url = new URL(String(source), baseUrl || globalThis.location?.href || import.meta.url)
    const response = await fetchImpl(url)
    if (!response?.ok) throw responseError(response, 'manifest')
    const manifest = await response.json()
    return { manifest: assertAssetManifest(manifest), baseUrl: new URL('.', url) }
  }

  const manifest = typeof source.json === 'function'
    ? await source.json()
    : JSON.parse(await source.text())
  return {
    manifest: assertAssetManifest(manifest),
    baseUrl: new URL('.', baseUrl || globalThis.location?.href || import.meta.url),
  }
}

export async function decodeAtlasBlob(blob, {
  createImageBitmapImpl = globalThis.createImageBitmap,
  ImageCtor = globalThis.Image,
  URLImpl = globalThis.URL,
} = {}) {
  if (!blob) throw new TypeError('atlas Blob 不能为空')
  if (typeof createImageBitmapImpl === 'function') {
    try {
      return await createImageBitmapImpl(blob, {
        premultiplyAlpha: 'none',
        colorSpaceConversion: 'none',
      })
    } catch (error) {
      if (typeof ImageCtor !== 'function') throw error
    }
  }
  if (typeof ImageCtor !== 'function'
    || typeof URLImpl?.createObjectURL !== 'function'
    || typeof URLImpl?.revokeObjectURL !== 'function') {
    throw new Error('当前环境无法解码 atlas Blob')
  }

  const objectUrl = URLImpl.createObjectURL(blob)
  try {
    const image = new ImageCtor()
    image.decoding = 'sync'
    image.src = objectUrl
    if (typeof image.decode === 'function') await image.decode()
    else {
      await new Promise((resolve, reject) => {
        image.addEventListener('load', resolve, { once: true })
        image.addEventListener('error', () => reject(new Error('atlas 图片解码失败')), { once: true })
      })
    }
    return image
  } finally {
    URLImpl.revokeObjectURL(objectUrl)
  }
}

function imageDimensions(image) {
  return {
    width: Number(image?.naturalWidth ?? image?.videoWidth ?? image?.width),
    height: Number(image?.naturalHeight ?? image?.videoHeight ?? image?.height),
  }
}

function assertAtlasDimensions(atlas, image) {
  const dimensions = imageDimensions(image)
  if (dimensions.width !== atlas.width || dimensions.height !== atlas.height) {
    throw new RangeError(
      `atlas “${atlas.id}” 尺寸应为 ${atlas.width}×${atlas.height}，实际为 ${dimensions.width}×${dimensions.height}`,
    )
  }
}

export async function loadActiveAssetPack({
  manifestSource,
  manifestUrl,
  atlasBlobs,
  atlasImages,
  expectedAtlasSha256,
  allowedOrigin,
  fetchImpl = (...args) => globalThis.fetch(...args),
  decodeImage = (blob) => decodeAtlasBlob(blob),
  cryptoImpl = globalThis.crypto,
} = {}) {
  const { manifest, baseUrl } = await loadActiveManifest(manifestSource, {
    fetchImpl,
    baseUrl: manifestUrl,
  })
  const images = new Map()
  for (const atlas of manifest.atlases) {
    let image = atlasValue(atlasImages, atlas)
    if (!image) {
      let blob = atlasValue(atlasBlobs, atlas)
      if (!blob) {
        const atlasUrl = new URL(atlas.source, baseUrl)
        assertAllowedOrigin(atlasUrl, allowedOrigin, `atlas “${atlas.id}”`)
        const response = await fetchImpl(atlasUrl)
        if (!response?.ok) throw responseError(response, `atlas “${atlas.id}”`)
        blob = await response.blob()
      }
      const expectedHash = expectedAtlasHash(expectedAtlasSha256, atlas)
      if (expectedHash) {
        await assertSha256(await blob.arrayBuffer(), expectedHash, `atlas “${atlas.id}”`, cryptoImpl)
      }
      image = await decodeImage(blob, atlas)
    } else if (expectedAtlasHash(expectedAtlasSha256, atlas)) {
      throw new AssetIntegrityError(`atlas “${atlas.id}” 只有解码图像，无法执行内容校验`, {
        code: 'asset_hash_unverifiable',
      })
    }
    assertAtlasDimensions(atlas, image)
    images.set(atlas.id, image)
  }
  return new AssetRuntime(manifest, images)
}

function manifestBytesFromBinding(binding) {
  if (typeof binding.manifestJson === 'string') {
    return new TextEncoder().encode(binding.manifestJson)
  }
  if (binding.manifest && typeof binding.manifest === 'object') {
    return new TextEncoder().encode(stableManifestStringify(binding.manifest, 0))
  }
  return null
}

/**
 * Load the immutable asset pack named by a run bootstrap binding.
 * A bound run never trusts the mutable active-manifest endpoint without first
 * checking the exact manifest digest supplied by the authenticated bootstrap.
 */
export async function loadPinnedAssetPack(binding, {
  baseUrl = globalThis.location?.href || import.meta.url,
  allowedOrigin = new URL(baseUrl).origin,
  fetchImpl = (...args) => globalThis.fetch(...args),
  decodeImage = (blob) => decodeAtlasBlob(blob),
  cryptoImpl = globalThis.crypto,
} = {}) {
  if (!binding || typeof binding !== 'object') throw new TypeError('必须提供 run 资产绑定')
  const manifestSha256 = normaliseSha256(binding.manifestSha256, 'manifest SHA-256')
  let manifestBytes = manifestBytesFromBinding(binding)
  let manifestUrl = binding.manifestUrl ? new URL(binding.manifestUrl, baseUrl) : null

  if (!manifestBytes) {
    if (!manifestUrl) {
      throw new AssetIntegrityError('资产绑定缺少不可变 manifest URL', {
        code: 'asset_manifest_url_missing',
      })
    }
    assertAllowedOrigin(manifestUrl, allowedOrigin, 'manifest')
    const response = await fetchImpl(manifestUrl)
    if (!response?.ok) throw responseError(response, 'manifest')
    manifestBytes = new Uint8Array(await response.arrayBuffer())
  }

  await assertSha256(manifestBytes, manifestSha256, 'manifest', cryptoImpl)
  let manifest
  try {
    manifest = JSON.parse(new TextDecoder().decode(manifestBytes))
  } catch {
    throw new AssetIntegrityError('manifest 不是有效 JSON', { code: 'asset_manifest_invalid_json' })
  }
  manifestUrl ??= new URL('./manifest.json', baseUrl)
  if (binding.packId && String(manifest.id || '') !== String(binding.packId)) {
    throw new AssetIntegrityError('manifest packId 与 Run 绑定不一致', {
      code: 'asset_pack_mismatch',
      expected: String(binding.packId),
      actual: String(manifest.id || ''),
    })
  }
  if (binding.atlasUrl) {
    const atlases = Array.isArray(manifest.atlases) ? manifest.atlases : []
    if (atlases.length !== 1) {
      throw new AssetIntegrityError('固定资产包必须恰好包含一个 atlas', {
        code: 'asset_atlas_contract',
      })
    }
    const expectedAtlasUrl = new URL(binding.atlasUrl, baseUrl)
    const manifestAtlasUrl = new URL(atlases[0].source, manifestUrl)
    assertAllowedOrigin(expectedAtlasUrl, allowedOrigin, 'Run atlas')
    if (manifestAtlasUrl.href !== expectedAtlasUrl.href) {
      throw new AssetIntegrityError('manifest atlasUrl 与 Run 绑定不一致', {
        code: 'asset_atlas_url_mismatch',
        expected: expectedAtlasUrl.href,
        actual: manifestAtlasUrl.href,
      })
    }
  }

  return loadActiveAssetPack({
    manifestSource: manifest,
    manifestUrl: manifestUrl.href,
    expectedAtlasSha256: binding.atlasSha256,
    allowedOrigin,
    fetchImpl,
    decodeImage,
    cryptoImpl,
  })
}

export function groundAxisAffineMatrix({ k, s } = {}) {
  const scale = Number(k)
  const slope = Number(s)
  if (!Number.isFinite(scale) || scale <= 0 || !Number.isFinite(slope)) {
    throw new TypeError('地面轴变换需要正数 k 与有限数 s')
  }
  return { a: scale, b: slope * (scale - 1), c: 0, d: 1 }
}

export function drawNearestFrame(ctx, image, frame, destination, {
  alpha = 1,
  flipX = false,
  flipOriginX = null,
  transformOrigin = null,
  groundTransform = null,
} = {}) {
  if (!ctx || typeof ctx.drawImage !== 'function') throw new TypeError('需要 Canvas 2D context')
  if (!image) throw new TypeError('atlas image 不能为空')
  const previousSmoothing = ctx.imageSmoothingEnabled
  const previousAlpha = ctx.globalAlpha
  const useTransform = flipX || groundTransform != null
  if (useTransform && (typeof ctx.save !== 'function'
    || typeof ctx.restore !== 'function'
    || typeof ctx.translate !== 'function'
    || typeof ctx.scale !== 'function')) {
    throw new TypeError('几何校正需要支持 Canvas transform 的 2D context')
  }
  if (groundTransform != null && typeof ctx.transform !== 'function') {
    throw new TypeError('地面轴校正需要支持 Canvas affine transform 的 2D context')
  }
  if (useTransform) ctx.save()
  ctx.imageSmoothingEnabled = false
  ctx.globalAlpha = Number.isFinite(alpha) ? Math.max(0, Math.min(1, alpha)) : 1
  try {
    if (useTransform) {
      const originX = Number.isFinite(Number(transformOrigin?.x))
        ? Number(transformOrigin.x)
        : Number.isFinite(Number(flipOriginX))
          ? Number(flipOriginX)
          : Number(destination.x) + Number(destination.width ?? frame.width) / 2
      const originY = Number.isFinite(Number(transformOrigin?.y))
        ? Number(transformOrigin.y)
        : Number(destination.y) + Number(destination.height ?? frame.height)
      ctx.translate(originX, originY)
      if (groundTransform != null) {
        const matrix = groundAxisAffineMatrix(groundTransform)
        ctx.transform(matrix.a, matrix.b, matrix.c, matrix.d, 0, 0)
      }
      if (flipX) ctx.scale(-1, 1)
      ctx.translate(-originX, -originY)
    }
    ctx.drawImage(
      image,
      frame.x,
      frame.y,
      frame.width,
      frame.height,
      Math.round(destination.x),
      Math.round(destination.y),
      Math.max(1, Math.round(destination.width ?? frame.width)),
      Math.max(1, Math.round(destination.height ?? frame.height)),
    )
  } finally {
    if (useTransform) ctx.restore()
    ctx.imageSmoothingEnabled = previousSmoothing
    ctx.globalAlpha = previousAlpha
  }
}

export class AssetRuntime {
  constructor(manifest, atlasImages) {
    this.manifest = assertAssetManifest(manifest)
    this.atlasImages = atlasImages instanceof Map
      ? new Map(atlasImages)
      : new Map(Object.entries(atlasImages || {}))
    this.assets = new Map(this.manifest.assets.map((asset) => [asset.id, asset]))
    this.animations = new Map(this.manifest.animations.map((animation) => [animation.id, animation]))
    for (const atlas of this.manifest.atlases) {
      const image = this.atlasImages.get(atlas.id)
      if (!image) throw new RangeError(`缺少 atlas image “${atlas.id}”`)
      assertAtlasDimensions(atlas, image)
    }
  }

  asset(assetId) {
    const asset = this.assets.get(assetId)
    if (!asset) throw new RangeError(`未知资产 “${assetId}”`)
    return asset
  }

  animationFrame(animationId, elapsedMs = 0) {
    return resolveAnimationFrame(this.manifest, animationId, elapsedMs)
  }

  animationDuration(animationId) {
    const animation = this.animations.get(animationId)
    if (!animation) throw new RangeError(`未知动画 “${animationId}”`)
    return animationTotalDuration(animation)
  }

  /** Gait metadata for animations whose phase should follow real displacement. */
  animationMotion(animationId) {
    const animation = this.animations.get(animationId)
    if (!animation) throw new RangeError(`未知动画 “${animationId}”`)
    if (!animation.motion) return null
    return {
      strideScreenPx: Number(animation.motion.strideScreenPx),
      framesPerStep: Number(animation.motion.framesPerStep),
      frameDurationMs: Number(animation.frameDurationMs),
    }
  }

  placementDepth(assetId, placement) {
    return depthForPlacement(this.asset(assetId), placement)
  }

  fixtureCollisionCells(fixtureOrId) {
    return fixtureCollisionCells(this.manifest, fixtureOrId)
  }

  drawAsset(ctx, assetId, groundX, groundY, options = {}) {
    const asset = this.asset(assetId)
    const zoom = Number(options.zoom) || 1
    const destination = {
      x: Number(groundX) + (asset.offset.x - asset.anchor.x) * zoom,
      y: Number(groundY) + (asset.offset.y - asset.anchor.y) * zoom,
      width: asset.frame.width * zoom,
      height: asset.frame.height * zoom,
    }
    drawNearestFrame(ctx, this.atlasImages.get(asset.atlas), asset.frame, destination, {
      ...options,
      flipOriginX: options.flipOriginX
        ?? Number(groundX) + asset.offset.x * zoom,
      transformOrigin: options.transformOrigin ?? {
        x: Number(groundX) + asset.offset.x * zoom,
        y: Number(groundY) + asset.offset.y * zoom,
      },
    })
    return { asset, destination }
  }

  drawAnimation(ctx, animationId, elapsedMs, groundX, groundY, options = {}) {
    const resolved = this.animationFrame(animationId, elapsedMs)
    const drawn = this.drawAsset(
      ctx,
      resolved.assetId,
      Number(groundX) + Number(resolved.proceduralOffset?.x || 0),
      Number(groundY) + Number(resolved.proceduralOffset?.y || 0),
      options,
    )
    return { ...resolved, destination: drawn.destination }
  }

  projectFixture(fixtureOrId, options = {}) {
    return sortFixtureForOcclusion(projectFixture(this.manifest, fixtureOrId, options))
  }

  drawFixture(ctx, fixtureOrId, options = {}) {
    const placements = this.projectFixture(fixtureOrId, options)
    for (const placement of placements) {
      drawNearestFrame(
        ctx,
        this.atlasImages.get(placement.asset.atlas),
        placement.asset.frame,
        placement.destination,
        options,
      )
    }
    return placements
  }

  dispose() {
    for (const image of this.atlasImages.values()) {
      if (typeof image?.close === 'function') image.close()
    }
    this.atlasImages.clear()
  }
}
