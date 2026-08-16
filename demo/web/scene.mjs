import {
  PALETTE,
  clamp,
  drawActor,
  drawCopyCounter,
  drawDeskIsland,
  drawHeartBurst,
  drawIsoTile,
  drawMeetingTable,
  drawOfficePlant,
  drawOfficeSofa,
  drawPrinter,
  drawStorage,
  drawTileEdge,
  drawWhiteboard,
  hashNumber,
  pixelRect,
  polygon,
  snap,
} from "./pixel.mjs";
import { groundPointForPlacement } from "./asset-manifest.mjs";

export const LOGICAL_WIDTH = 640;
export const LOGICAL_HEIGHT = 360;
export const GRID_COLUMNS = 20;
export const GRID_ROWS = 12;
export const ACTOR_COUNT = 8;
// Kept as the frozen core-v1 compatibility point. geometryVersion >= 2 derives
// its backdrop ground from the asset anchor so the frame itself starts at 0,0.
export const BACKDROP_GROUND = Object.freeze({ x: 320, y: 108 });

const TILE_WIDTH = 32;
const TILE_HEIGHT = 16;
const MAP_CENTER_X = ((GRID_COLUMNS - GRID_ROWS) * TILE_WIDTH) / 4;
const MAP_CENTER_Y = ((GRID_COLUMNS + GRID_ROWS - 2) * TILE_HEIGHT) / 4;
const LEGACY_ORIGIN = Object.freeze({
  x: LOGICAL_WIDTH / 2 - MAP_CENTER_X,
  y: LOGICAL_HEIGHT / 2 - MAP_CENTER_Y,
});
const DISPLAY_NAMES = Object.freeze(["Ava", "Ben", "Cleo", "Drew", "Eli", "Faye", "Gus", "Hana"]);
const FALLBACK_POSITIONS = Object.freeze([
  [1, 1],
  [6, 1],
  [13, 1],
  [18, 1],
  [1, 10],
  [6, 10],
  [13, 10],
  [18, 10],
]);
const FALLBACK_COLORS = Object.freeze([
  "#ed806c",
  "#75bd9f",
  "#78aabc",
  "#f1bf65",
  "#a88bc2",
  "#dc8eb0",
  "#82ae68",
  "#dc9765",
]);

const ASSET_ANIMATION_PREFIX = "animation.gus";
const GOOD_CARD_ANIMATION = "animation.good-card-heart";
const ASSET_DIRECTIONS = new Set(["southeast", "southwest", "northwest", "northeast"]);
const ASSET_ACTIONS = ["idle", "walk", "work"];
// Frame length the fallback vector renderer steps at, matched to the sprite
// sheet so both renderers show the same pose at the same moment.
const LEGACY_FRAME_MS = 51;

// Pixel art only survives integer magnification.  At 1.45x nearest-neighbour
// sampling enlarges some source pixels once and their neighbours twice, so the
// same sprite shows uneven pixel sizes and shimmers as the camera moves.  The
// world transform already snaps its translate to whole pixels, so restricting
// the scale to these steps makes every device pixel an exact multiple of a
// source pixel — no offscreen buffer needed to get there.
export const ZOOM_STEPS = Object.freeze([1, 1.25, 1.5, 2]);
const MIN_ZOOM = ZOOM_STEPS[0];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];

function quantizeZoom(value) {
  const requested = Number(value);
  if (!Number.isFinite(requested)) return MIN_ZOOM;
  return ZOOM_STEPS.reduce(
    (best, step) => (Math.abs(step - requested) < Math.abs(best - requested) ? step : best),
    ZOOM_STEPS[0],
  );
}
const CORE_V1_LEGACY_RENDER_ASSETS = Object.freeze({
  "structure.wall-solid-nw": "structure.wall-solid-ne",
  "structure.wall-solid-ne": "structure.wall-solid-nw",
  "structure.wall-window-nw": "structure.wall-window-ne",
  "structure.wall-window-ne": "structure.wall-window-nw",
});
const CORE_V1_LEGACY_GROUND_FITS = Object.freeze({
  "structure.wall-window-nw": Object.freeze({ k: 64 / 94, s: 0.5 }),
  "structure.wall-window-ne": Object.freeze({ k: 64 / 92, s: -0.5 }),
  "structure.wall-solid-nw": Object.freeze({ k: 48 / 85, s: 0.5 }),
  "structure.wall-solid-ne": Object.freeze({ k: 48 / 60, s: -0.5 }),
  "structure.wall-door-ne": Object.freeze({ k: 48 / 85, s: -0.5 }),
});

export function renderSpecForAsset(manifest, assetId) {
  const id = String(assetId || "");
  const geometryVersion = Number(manifest?.geometryVersion ?? 1);
  if (manifest?.id !== "core-v1" || geometryVersion !== 1) {
    return { assetId: id, flipX: false, groundFit: null };
  }
  return {
    assetId: CORE_V1_LEGACY_RENDER_ASSETS[id] ?? id,
    flipX: id === "structure.wall-door-ne",
    groundFit: CORE_V1_LEGACY_GROUND_FITS[id] ?? null,
  };
}

const OFFICE_FURNITURE = Object.freeze([
  { x: 4, y: 2.5, kind: "desk", width: 76, computers: 3, accent: "#ed806c", depth: 6.6 },
  { x: 15, y: 2, kind: "desk", width: 70, computers: 3, accent: "#78aabc", depth: 17.2 },
  { x: 9.5, y: 4.5, kind: "meeting", depth: 14.4 },
  { x: 3.5, y: 8, kind: "copy-counter", depth: 12.2 },
  { x: 15, y: 8, kind: "sofa", depth: 23.2 },
  { x: 1, y: 5, kind: "storage", depth: 6.2 },
  { x: 18, y: 5, kind: "printer", depth: 23.2 },
  { x: 7, y: 9, kind: "plant", depth: 16.2 },
  { x: 12, y: 9, kind: "storage", depth: 21.2 },
  { x: 6, y: -0.4, kind: "whiteboard", depth: 0.2 },
]);

function projectionOrigin(geometry) {
  const origin = geometry?.origin ?? geometry;
  return {
    x: finiteCoordinate(origin?.x, LEGACY_ORIGIN.x),
    y: finiteCoordinate(origin?.y, LEGACY_ORIGIN.y),
  };
}

export function projectIsometric(
  x,
  y,
  camera = { x: 0, y: 0, zoom: 1 },
  geometry = { origin: LEGACY_ORIGIN },
) {
  const zoom = Number(camera.zoom) || 1;
  const origin = projectionOrigin(geometry);
  const localX = origin.x + (Number(x) - Number(y)) * (TILE_WIDTH / 2);
  const localY = origin.y + (Number(x) + Number(y)) * (TILE_HEIGHT / 2);
  return {
    x: LOGICAL_WIDTH / 2 + (Number(camera.x) || 0) + (localX - LOGICAL_WIDTH / 2) * zoom,
    y: LOGICAL_HEIGHT / 2 + (Number(camera.y) || 0) + (localY - LOGICAL_HEIGHT / 2) * zoom,
  };
}

export function unprojectIsometric(
  screenX,
  screenY,
  camera = { x: 0, y: 0, zoom: 1 },
  geometry = { origin: LEGACY_ORIGIN },
) {
  const zoom = Number(camera.zoom) || 1;
  const origin = projectionOrigin(geometry);
  const localX = LOGICAL_WIDTH / 2
    + (Number(screenX) - LOGICAL_WIDTH / 2 - (Number(camera.x) || 0)) / zoom;
  const localY = LOGICAL_HEIGHT / 2
    + (Number(screenY) - LOGICAL_HEIGHT / 2 - (Number(camera.y) || 0)) / zoom;
  const worldX = localX - origin.x;
  const worldY = localY - origin.y;
  return {
    x: (worldX / (TILE_WIDTH / 2) + worldY / (TILE_HEIGHT / 2)) / 2,
    y: (worldY / (TILE_HEIGHT / 2) - worldX / (TILE_WIDTH / 2)) / 2,
  };
}

export function sortByIsometricDepth(items = []) {
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const leftDepth = Number(left.item.depth ?? (Number(left.item.x) + Number(left.item.y))) || 0;
      const rightDepth = Number(right.item.depth ?? (Number(right.item.x) + Number(right.item.y))) || 0;
      return leftDepth - rightDepth
        || (Number(left.item.layer) || 0) - (Number(right.item.layer) || 0)
        || left.index - right.index;
    })
    .map(({ item }) => item);
}

export function backdropScreenGround(asset = {}) {
  return {
    x: Number(asset.anchor?.x || 0) - Number(asset.offset?.x || 0),
    y: Number(asset.anchor?.y || 0) - Number(asset.offset?.y || 0),
  };
}

/** Keep frozen core-v1 runs on their approved backdrop placement. */
export function backdropGroundForManifest(manifest = {}, asset = {}) {
  return Number(manifest.geometryVersion) >= 2
    ? backdropScreenGround(asset)
    : { ...BACKDROP_GROUND };
}

function verticalOffset(point, y) {
  return { x: Number(point.x), y: Number(point.y) + Number(y) };
}

function freezePoints(points) {
  return Object.freeze(points.map((point) => Object.freeze({ x: point.x, y: point.y })));
}

/**
 * Exact exposed floor edges for the 2:1 orthographic grid.
 *
 * The two viewer-facing boundaries are x=max and y=max.  Returning every tile
 * join (rather than only three diamond corners) gives the facade renderer a
 * deterministic place for each vertical curtain-wall mullion and makes the
 * shared front corner a single source of truth.
 */
export function floorFrontEdges({ columns, rows, origin } = {}) {
  const width = Number(columns);
  const height = Number(rows);
  if (!Number.isInteger(width) || width <= 0 || !Number.isInteger(height) || height <= 0) {
    throw new RangeError("floorFrontEdges columns/rows 必须是正整数");
  }
  const gridOrigin = projectionOrigin({ origin });
  const right = {
    x: gridOrigin.x + width * (TILE_WIDTH / 2),
    y: gridOrigin.y + (width - 1) * (TILE_HEIGHT / 2),
  };
  const xMax = Array.from({ length: height + 1 }, (_, index) => ({
    x: right.x - index * (TILE_WIDTH / 2),
    y: right.y + index * (TILE_HEIGHT / 2),
  }));
  const front = xMax.at(-1);
  const yMax = Array.from({ length: width + 1 }, (_, index) => ({
    x: front.x - index * (TILE_WIDTH / 2),
    y: front.y - index * (TILE_HEIGHT / 2),
  }));
  return Object.freeze({
    xMax: freezePoints(xMax),
    yMax: freezePoints(yMax),
    rightCorner: Object.freeze({ ...xMax[0] }),
    frontCorner: Object.freeze({ ...front }),
    leftCorner: Object.freeze({ ...yMax.at(-1) }),
  });
}

/** Opaque spandrel strip capping each band; the rest of the pitch is glazing. */
const SPANDREL_HEIGHT = 2;

function faceGeometry(id, topEdge, slabDepth, facadeDepth, windowBandPitch) {
  const first = topEdge[0];
  const last = topEdge.at(-1);
  const topMinY = Math.min(...topEdge.map((point) => point.y));
  const bottomMaxY = Math.max(...topEdge.map((point) => point.y)) + facadeDepth;
  // A curtain wall's floor lines are horizontal in world space, so on this 2:1
  // projection they run parallel to the eave, not parallel to the screen.
  // Bands are therefore depths below the eave rather than absolute screen y:
  // one depth describes the same slab on both faces, which is what makes them
  // meet exactly at the shared front corner without a phase fixup.
  const windowBands = [];
  const firstBandDepth = Math.ceil(slabDepth / windowBandPitch) * windowBandPitch;
  for (let depth = firstBandDepth; depth + windowBandPitch <= facadeDepth; depth += windowBandPitch) {
    windowBands.push(depth);
  }
  return Object.freeze({
    id,
    topEdge,
    facade: freezePoints([
      verticalOffset(first, slabDepth),
      verticalOffset(last, slabDepth),
      verticalOffset(last, facadeDepth),
      verticalOffset(first, facadeDepth),
    ]),
    slab: freezePoints([
      first,
      last,
      verticalOffset(last, slabDepth),
      verticalOffset(first, slabDepth),
    ]),
    ambientOcclusion: freezePoints([
      first,
      last,
      verticalOffset(last, Math.min(2, slabDepth)),
      verticalOffset(first, Math.min(2, slabDepth)),
    ]),
    mullions: Object.freeze(topEdge.map((point) => Object.freeze({
      top: Object.freeze(verticalOffset(point, slabDepth)),
      bottom: Object.freeze(verticalOffset(point, facadeDepth)),
    }))),
    windowBands: Object.freeze(windowBands),
    bounds: Object.freeze({
      left: Math.min(...topEdge.map((point) => point.x)),
      top: topMinY + slabDepth,
      right: Math.max(...topEdge.map((point) => point.x)),
      bottom: bottomMaxY,
    }),
  });
}

/** Pure local-scene geometry. The camera transform is intentionally external. */
export function towerShellGeometry(layout = {}, shell = {}) {
  const facadeDepth = Number(shell.facadeDepth);
  const slabDepth = Number(shell.slabDepth);
  const windowBandPitch = Number(shell.windowBandPitch);
  if (![facadeDepth, slabDepth, windowBandPitch].every((value) => Number.isInteger(value) && value > 0)
    || facadeDepth <= slabDepth) {
    throw new RangeError("sceneShell 深度与窗带节距必须是合法正整数");
  }
  const edges = floorFrontEdges(layout);
  return Object.freeze({
    edges,
    xMax: faceGeometry("x-max", edges.xMax, slabDepth, facadeDepth, windowBandPitch),
    yMax: faceGeometry("y-max", edges.yMax, slabDepth, facadeDepth, windowBandPitch),
    facadeDepth,
    slabDepth,
    windowBandPitch,
  });
}

/**
 * One curtain-wall band as a parallelogram swept from the eave.
 *
 * The face is the top edge extruded downwards, so a band is just that edge at
 * `depth` and at `depth + height`.  Taking the two endpoints is exact because
 * every top-edge point is collinear on the 2:1 grid.
 */
export function windowBandPolygon(face, depth, height) {
  const first = face.topEdge[0];
  const last = face.topEdge.at(-1);
  return Object.freeze([
    Object.freeze({ x: first.x, y: first.y + depth }),
    Object.freeze({ x: last.x, y: last.y + depth }),
    Object.freeze({ x: last.x, y: last.y + depth + height }),
    Object.freeze({ x: first.x, y: first.y + depth + height }),
  ]);
}

/** Apply the same centre-origin transform used by the live canvas. */
export function cameraTransformPoint(point, camera = { x: 0, y: 0, zoom: 1 }) {
  const zoom = Number(camera.zoom) || 1;
  return {
    x: LOGICAL_WIDTH / 2 + (Number(camera.x) || 0) + (Number(point.x) - LOGICAL_WIDTH / 2) * zoom,
    y: LOGICAL_HEIGHT / 2 + (Number(camera.y) || 0) + (Number(point.y) - LOGICAL_HEIGHT / 2) * zoom,
  };
}

function canvasPoints(points) {
  return points.map((point) => [point.x, point.y]);
}

function clipPolygon(ctx, points) {
  if (!points.length) return;
  ctx.beginPath();
  ctx.moveTo(snap(points[0].x), snap(points[0].y));
  for (let index = 1; index < points.length; index += 1) {
    ctx.lineTo(snap(points[index].x), snap(points[index].y));
  }
  ctx.closePath();
  ctx.clip();
}

function drawTowerFace(ctx, face, shell, { base, glass, slab }) {
  const colors = shell.colors;
  polygon(ctx, canvasPoints(face.facade), base, colors.outline, 1);
  ctx.save();
  clipPolygon(ctx, face.facade);
  const glassHeight = Math.max(1, shell.windowBandPitch - 3);
  for (const depth of face.windowBands) {
    polygon(ctx, canvasPoints(windowBandPolygon(face, depth, SPANDREL_HEIGHT)), colors.mullion);
    polygon(ctx, canvasPoints(windowBandPolygon(face, depth + SPANDREL_HEIGHT, glassHeight)), glass);
  }
  for (const mullion of face.mullions) {
    pixelRect(
      ctx,
      mullion.top.x - 1,
      mullion.top.y,
      2,
      mullion.bottom.y - mullion.top.y,
      colors.mullion,
    );
  }
  ctx.restore();

  polygon(ctx, canvasPoints(face.slab), slab, colors.outline, 1);
  polygon(ctx, canvasPoints(face.ambientOcclusion), colors.ambientOcclusion);
}

function drawCutawayOfficeTower(ctx, layout, shell) {
  const geometry = towerShellGeometry(layout, shell);
  // x=max turns away from the upper-left light source; y=max remains the
  // lighter face. Both share an identical corner and global window-band phase.
  drawTowerFace(ctx, geometry.xMax, shell, {
    base: shell.colors.facadeDark,
    glass: shell.colors.facadeLight,
    slab: shell.colors.mullion,
  });
  drawTowerFace(ctx, geometry.yMax, shell, {
    base: shell.colors.facadeLight,
    glass: shell.colors.window,
    slab: shell.colors.slab,
  });
}

function unionBounds(bounds, candidate) {
  if (!candidate) return bounds;
  const left = Number(candidate.left);
  const top = Number(candidate.top);
  const right = Number(candidate.right);
  const bottom = Number(candidate.bottom);
  if (![left, top, right, bottom].every(Number.isFinite)) return bounds;
  if (!bounds) return { left, top, right, bottom };
  return {
    left: Math.min(bounds.left, left),
    top: Math.min(bounds.top, top),
    right: Math.max(bounds.right, right),
    bottom: Math.max(bounds.bottom, bottom),
  };
}

function assetBoundsAt(asset, point) {
  if (!asset || !point) return null;
  const frame = asset.frame || {};
  const left = Number(point.x) + Number(asset.offset?.x || 0) - Number(asset.anchor?.x || 0);
  const top = Number(point.y) + Number(asset.offset?.y || 0) - Number(asset.anchor?.y || 0);
  return {
    left,
    top,
    right: left + Number(frame.width || 0),
    bottom: top + Number(frame.height || 0),
  };
}

/** Visual bounds in untransformed 640×360 scene coordinates. Backdrops are excluded. */
export function sceneVisualBounds(manifest, layout, actors = []) {
  if (!layout) return null;
  const assets = new Map((manifest?.assets || []).map((asset) => [asset.id, asset]));
  let bounds = null;
  for (const floor of layout.floors || []) {
    const point = projectIsometric(floor.x, floor.y, { x: 0, y: 0, zoom: 1 }, { origin: layout.origin });
    bounds = unionBounds(bounds, {
      left: point.x - TILE_WIDTH / 2,
      top: point.y - TILE_HEIGHT / 2,
      right: point.x + TILE_WIDTH / 2,
      bottom: point.y + TILE_HEIGHT / 2,
    });
  }
  for (const placement of layout.objects || []) {
    const asset = assets.get(placement.renderAssetId) || assets.get(placement.assetId);
    const point = projectIsometric(
      placement.renderX ?? placement.x,
      placement.renderY ?? placement.y,
      { x: 0, y: 0, zoom: 1 },
      { origin: layout.origin },
    );
    bounds = unionBounds(bounds, assetBoundsAt(asset, point));
  }
  const actorAsset = assets.get("character.gus.southeast.idle")
    || (manifest?.assets || []).find((asset) => asset.kind === "character");
  for (const actor of actors || []) {
    const point = projectIsometric(
      actor.renderX ?? actor.x,
      actor.renderY ?? actor.y,
      { x: 0, y: 0, zoom: 1 },
      { origin: layout.origin },
    );
    bounds = unionBounds(bounds, assetBoundsAt(actorAsset, { x: point.x, y: point.y + 3 }));
    const labelWidth = Math.max(24, String(actor.name || "").length * 6 + 10);
    bounds = unionBounds(bounds, {
      left: point.x - labelWidth / 2,
      top: point.y - 61,
      right: point.x + labelWidth / 2,
      bottom: point.y - 45,
    });
  }
  return bounds;
}

export function cameraForVisualBounds(bounds, {
  padding = 16,
  zoomSteps = ZOOM_STEPS,
  width = LOGICAL_WIDTH,
  height = LOGICAL_HEIGHT,
} = {}) {
  if (!bounds) return { x: 0, y: 0, zoom: 1 };
  const visualWidth = Math.max(1, Number(bounds.right) - Number(bounds.left));
  const visualHeight = Math.max(1, Number(bounds.bottom) - Number(bounds.top));
  const availableWidth = Math.max(1, Number(width) - Number(padding) * 2);
  const availableHeight = Math.max(1, Number(height) - Number(padding) * 2);
  const fitting = [...zoomSteps]
    .map(Number)
    .filter((zoom) => Number.isFinite(zoom) && zoom > 0
      && visualWidth * zoom <= availableWidth
      && visualHeight * zoom <= availableHeight)
    .sort((left, right) => left - right);
  const zoom = fitting.at(-1) ?? Math.min(...zoomSteps.map(Number).filter((value) => value > 0));
  const centerX = (Number(bounds.left) + Number(bounds.right)) / 2;
  const centerY = (Number(bounds.top) + Number(bounds.bottom)) / 2;
  return {
    x: Math.round(-(centerX - width / 2) * zoom),
    y: Math.round(-(centerY - height / 2) * zoom),
    zoom,
  };
}

/** Geometry v2 fits real visual bounds; older releases retain their frozen camera. */
export function cameraForAssetLayout(manifest, layout, actors = [], { padding = 16 } = {}) {
  if (!layout) return { x: 0, y: 0, zoom: 1 };
  if (Number(manifest?.geometryVersion ?? 1) < 2) return { ...layout.camera };
  return cameraForVisualBounds(sceneVisualBounds(manifest, layout, actors), { padding });
}

function intersectionArea(left, right) {
  const width = Math.max(0, Math.min(left.right, right.right) - Math.max(left.left, right.left));
  const height = Math.max(0, Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top));
  return width * height;
}

/** Deterministic screen-edge clamp and label-to-label avoidance in local scene space. */
export function layoutActorLabels(items = [], {
  measureText = (text) => String(text).length * 6,
  visibleBounds = { left: 0, top: 0, right: LOGICAL_WIDTH, bottom: LOGICAL_HEIGHT },
} = {}) {
  const occupied = [];
  return items.map((item) => {
    const actor = item.actor || item;
    const point = item.point || { x: Number(actor.x) || 0, y: Number(actor.y) || 0 };
    const text = String(actor.name || "成员").slice(0, 16);
    const width = Math.max(24, Math.ceil(Number(measureText(text)) || 0) + 9);
    const yCandidates = [point.y - 52, point.y - 66, point.y + 14, point.y + 28];
    const xOffsets = [0, -width * 0.65, width * 0.65];
    const candidates = yCandidates.flatMap((labelY) => xOffsets.map((offset) => {
      const y = clamp(labelY, visibleBounds.top + 6, visibleBounds.bottom - 6);
      const left = clamp(point.x - width / 2 + offset, visibleBounds.left, visibleBounds.right - width);
      return { left, y, right: left + width, top: y - 6, bottom: y + 6 };
    }));
    const candidate = candidates.find((entry) => occupied.every((used) => intersectionArea(entry, used) === 0))
      || candidates.reduce((best, entry) => {
        const overlap = occupied.reduce((total, used) => total + intersectionArea(entry, used), 0);
        return !best || overlap < best.overlap ? { ...entry, overlap } : best;
      }, null)
      || { left: point.x, y: point.y, right: point.x + width, top: point.y - 6, bottom: point.y + 6 };
    const layout = { actorId: String(actor.id || ""), text, width, left: candidate.left, y: candidate.y };
    occupied.push({ left: candidate.left, right: candidate.right, top: candidate.top, bottom: candidate.bottom });
    return layout;
  });
}

/**
 * Keep an actor who has reached a work seat visible above that seat's desk.
 *
 * The current desk island is one composite sprite.  Its footprint depth is the
 * front-most cell, so an actor at either rear seat would otherwise be drawn
 * first and then covered by the complete desk image.  Raising only an active,
 * layout-verified work actor to the matching placement depth preserves normal
 * walk/idle occlusion and still lets genuinely nearer objects draw on top.
 */
function matchingWorkPlacement(actor = {}, assetLayout = null) {
  const activity = actor.activity;
  if (activity?.type !== "work" || !assetLayout) return null;

  const placementId = String(activity.placementId || "");
  const seatId = String(activity.seatId || "");
  const seat = assetLayout.seats?.find((candidate) => (
    candidate.placementId === placementId && candidate.id === seatId
  ));
  return seat
    ? assetLayout.placements?.find((candidate) => candidate.id === placementId)
    : null;
}

export function actorDepthForOcclusion(actor = {}, assetLayout = null) {
  const renderX = Number(actor.renderX ?? actor.x);
  const renderY = Number(actor.renderY ?? actor.y);
  const baseDepth = (Number.isFinite(renderX) ? renderX : 0)
    + (Number.isFinite(renderY) ? renderY : 0)
    + 0.7;
  const placement = matchingWorkPlacement(actor, assetLayout);
  const placementDepth = Number(placement?.depth);
  return Number.isFinite(placementDepth) ? Math.max(baseDepth, placementDepth) : baseDepth;
}

export function advanceMotionPoint(current, target, deltaMs, playbackRate = 1, reducedMotion = false) {
  const ease = reducedMotion
    ? 1
    : 1 - Math.exp(-(Math.max(0, Number(deltaMs) || 0) * clamp(Number(playbackRate) || 1, 0.1, 8)) / 145);
  return {
    x: Number(current.x) + (Number(target.x) - Number(current.x)) * ease,
    y: Number(current.y) + (Number(target.y) - Number(current.y)) * ease,
  };
}

export function directionForMotion(deltaX, deltaY, fallback = "southeast") {
  const dx = Number(deltaX) || 0;
  const dy = Number(deltaY) || 0;
  const safeFallback = ASSET_DIRECTIONS.has(fallback) ? fallback : "southeast";
  if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) return safeFallback;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? "southeast" : "northwest";
  return dy >= 0 ? "southwest" : "northeast";
}

export function actorAnimationId(actor = {}, reducedMotion = false) {
  const direction = ASSET_DIRECTIONS.has(actor.facing) ? actor.facing : "southeast";
  const requestedAction = actor.activity?.type === "work" ? "work" : actor.animationAction;
  const action = requestedAction === "work"
    ? "work"
    : !reducedMotion && requestedAction === "walk"
      ? "walk"
      : "idle";
  return `${ASSET_ANIMATION_PREFIX}.${direction}.${action}`;
}

function integerCoordinate(value, label) {
  const number = Number(value);
  if (!Number.isInteger(number)) throw new TypeError(`${label} 必须是整数`);
  return number;
}

function collisionKey(cell) {
  return `${Number(cell.x)},${Number(cell.y)}`;
}

function sameCellSet(left, right) {
  const leftSet = new Set((left || []).map(collisionKey));
  const rightSet = new Set((right || []).map(collisionKey));
  return leftSet.size === rightSet.size && [...leftSet].every((key) => rightSet.has(key));
}

/** Normalise the frozen world.layout contract into render-ready floor/furniture data. */
export function resolveAssetWorldLayout(manifest, rawLayout, world = {}) {
  if (!manifest || typeof manifest !== "object") throw new TypeError("资产 manifest 不能为空");
  if (!rawLayout || typeof rawLayout !== "object" || Array.isArray(rawLayout)) {
    throw new TypeError("绑定资产的 Run 缺少 world.layout");
  }
  const assets = new Map((manifest.assets || []).map((asset) => [asset.id, asset]));
  const id = String(rawLayout.id || "").trim();
  if (!id) throw new TypeError("world.layout 缺少 id");
  const columns = integerCoordinate(rawLayout.columns ?? world.columns ?? GRID_COLUMNS, "layout.columns");
  const rows = integerCoordinate(rawLayout.rows ?? world.rows ?? GRID_ROWS, "layout.rows");
  if (columns < 1 || columns > GRID_COLUMNS || rows < 1 || rows > GRID_ROWS) {
    throw new RangeError("world.layout 尺寸超出玩家场景范围");
  }
  if (world.columns != null && Number(world.columns) !== columns) {
    throw new RangeError("world.layout.columns 与 world.columns 不一致");
  }
  if (world.rows != null && Number(world.rows) !== rows) {
    throw new RangeError("world.layout.rows 与 world.rows 不一致");
  }
  for (const [field, expected] of [["tileWidth", TILE_WIDTH], ["tileHeight", TILE_HEIGHT], ["elevation", 8]]) {
    if (rawLayout[field] != null && Number(rawLayout[field]) !== expected) {
      throw new RangeError(`world.layout.${field} 与运行时网格不一致`);
    }
  }

  const floor = rawLayout.floor;
  if (!floor || typeof floor !== "object" || Array.isArray(floor)) {
    throw new TypeError("world.layout.floor 缺失");
  }
  const defaultAssetId = String(floor.defaultAssetId || "");
  const regions = Array.isArray(floor.regions) ? floor.regions : [];
  const border = floor.border && typeof floor.border === "object" ? floor.border : null;
  const requireFloor = (assetId, label) => {
    const asset = assets.get(String(assetId || ""));
    if (!asset || asset.kind !== "floor") throw new RangeError(`${label} 引用了未知地板资产 “${assetId || ""}”`);
    return asset.id;
  };
  requireFloor(defaultAssetId, "floor.defaultAssetId");
  const normalizedRegions = regions.map((region, index) => {
    const x = integerCoordinate(region?.x, `floor.regions[${index}].x`);
    const y = integerCoordinate(region?.y, `floor.regions[${index}].y`);
    const width = integerCoordinate(region?.width, `floor.regions[${index}].width`);
    const height = integerCoordinate(region?.height, `floor.regions[${index}].height`);
    if (width < 1 || height < 1 || x < 0 || y < 0 || x + width > columns || y + height > rows) {
      throw new RangeError(`floor.regions[${index}] 超出 layout 范围`);
    }
    return { assetId: requireFloor(region.assetId, `floor.regions[${index}]`), x, y, width, height };
  });
  const borderEdges = new Set(Array.isArray(border?.edges) ? border.edges : []);
  for (const edge of borderEdges) {
    if (!["north", "east", "south", "west"].includes(edge)) throw new RangeError(`未知地板边界 “${edge}”`);
  }
  const borderAssetId = borderEdges.size ? requireFloor(border.assetId, "floor.border") : null;
  const floors = [];
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < columns; x += 1) {
      let assetId = defaultAssetId;
      for (const region of normalizedRegions) {
        if (x >= region.x && x < region.x + region.width && y >= region.y && y < region.y + region.height) {
          assetId = region.assetId;
        }
      }
      const onBorder = (borderEdges.has("north") && y === 0)
        || (borderEdges.has("east") && x === columns - 1)
        || (borderEdges.has("south") && y === rows - 1)
        || (borderEdges.has("west") && x === 0);
      if (onBorder) assetId = borderAssetId;
      floors.push({ id: `floor-${x}-${y}`, assetId, x, y, layer: -1, depth: x + y });
    }
  }

  const origin = rawLayout.origin && typeof rawLayout.origin === "object"
    ? {
        x: integerCoordinate(rawLayout.origin.x, "layout.origin.x"),
        y: integerCoordinate(rawLayout.origin.y, "layout.origin.y"),
      }
    : { ...LEGACY_ORIGIN };
  const placements = Array.isArray(rawLayout.placements) ? rawLayout.placements : [];
  const renderPlacements = placements.map((placement, index) => {
    const assetId = String(placement?.assetId || "");
    const asset = assets.get(assetId);
    if (!asset || !["backdrop", "structure", "decor", "furniture"].includes(asset.kind)) {
      throw new RangeError(`layout.placements[${index}] 引用了未知场景资产 “${assetId}”`);
    }
    const x = integerCoordinate(placement.x, `layout.placements[${index}].x`);
    const y = integerCoordinate(placement.y, `layout.placements[${index}].y`);
    if (x < 0 || y < 0 || x >= columns || y >= rows) {
      throw new RangeError(`layout.placements[${index}] 超出 layout 范围`);
    }
    const extent = Math.max(0, ...(asset.footprint || []).map((cell) => Number(cell.x) + Number(cell.y)));
    const renderGround = groundPointForPlacement(asset, { x, y });
    const renderSpec = renderSpecForAsset(manifest, assetId);
    return {
      id: String(placement.id || `furniture-${index}`),
      assetId,
      kind: asset.kind,
      x,
      y,
      renderX: renderGround.x,
      renderY: renderGround.y,
      renderAssetId: renderSpec.assetId,
      renderFlipX: renderSpec.flipX,
      renderGroundFit: renderSpec.groundFit,
      layer: Number(asset.layer) || 0,
      depth: x + y + extent,
      footprint: (asset.footprint || []).map((cell) => ({
        x: Number(cell.x),
        y: Number(cell.y),
        blocked: Boolean(cell.blocked),
      })),
      interactionPoints: (asset.interactionPoints || []).map((point) => ({
        id: String(point.id),
        kind: String(point.kind),
        x: x + Number(point.x),
        y: y + Number(point.y),
        relativeX: Number(point.x),
        relativeY: Number(point.y),
        facing: String(point.facing),
      })),
    };
  });
  const computedBlockedCells = renderPlacements.flatMap((placement) => {
    const asset = assets.get(placement.assetId);
    return (asset.collision || []).map((cell) => ({
      x: placement.x + Number(cell.x),
      y: placement.y + Number(cell.y),
    }));
  });
  const frozenBlockedCells = Array.isArray(rawLayout.blockedCells) ? rawLayout.blockedCells : [];
  if (!sameCellSet(computedBlockedCells, frozenBlockedCells)) {
    throw new RangeError("world.layout.blockedCells 与家具 collision 不一致");
  }
  if (Array.isArray(world.blockedCells) && !sameCellSet(frozenBlockedCells, world.blockedCells)) {
    throw new RangeError("world.blockedCells 与冻结 layout 不一致");
  }
  const spawnPoints = (Array.isArray(rawLayout.spawnPoints) ? rawLayout.spawnPoints : []).map((spawn, index) => {
    const x = integerCoordinate(spawn?.x, `layout.spawnPoints[${index}].x`);
    const y = integerCoordinate(spawn?.y, `layout.spawnPoints[${index}].y`);
    if (x < 0 || y < 0 || x >= columns || y >= rows) {
      throw new RangeError(`layout.spawnPoints[${index}] 超出 layout 范围`);
    }
    return {
      playerId: String(spawn.playerId || ""),
      name: String(spawn.name || spawn.playerId || `spawn-${index + 1}`),
      x,
      y,
    };
  });
  const seats = renderPlacements.flatMap((placement) => placement.interactionPoints
    .filter((point) => point.kind === "work-seat")
    .map((point) => ({ ...point, placementId: placement.id, assetId: placement.assetId })));
  const blockedSet = new Set(frozenBlockedCells.map(collisionKey));
  const seatKeys = new Set();
  for (const seat of seats) {
    const key = `${seat.placementId}:${seat.id}`;
    if (seatKeys.has(key)) throw new RangeError(`world.layout 工作座位重复：${key}`);
    seatKeys.add(key);
    if (!Number.isInteger(seat.x) || !Number.isInteger(seat.y)
      || seat.x < 0 || seat.y < 0 || seat.x >= columns || seat.y >= rows) {
      throw new RangeError(`world.layout 工作座位超出地图：${key}`);
    }
    if (blockedSet.has(collisionKey(seat))) {
      throw new RangeError(`world.layout 工作座位落在阻挡格：${key}`);
    }
  }
  const initialActivities = (Array.isArray(rawLayout.initialActivities) ? rawLayout.initialActivities : [])
    .map((activity, index) => {
      const playerId = String(activity?.playerId ?? activity?.player_id ?? "");
      const placementId = String(activity?.placementId ?? activity?.placement_id ?? "");
      const seatId = String(activity?.seatId ?? activity?.seat_id ?? "");
      const seat = seats.find((candidate) => (
        candidate.placementId === placementId && candidate.id === seatId
      ));
      if (!playerId || !seat) {
        throw new RangeError(`world.layout.initialActivities[${index}] 引用了未知玩家或座位`);
      }
      return {
        playerId,
        type: "work",
        placementId,
        seatId,
        facing: seat.facing,
        x: seat.x,
        y: seat.y,
      };
    });
  return {
    id,
    label: String(rawLayout.label || id),
    stage: String(rawLayout.stage || ""),
    requiredPackId: String(rawLayout.requiredPackId || ""),
    sha256: String(rawLayout.sha256 || ""),
    sourceFixtureId: String(rawLayout.sourceFixtureId || ""),
    columns,
    rows,
    origin,
    camera: rawLayout.camera && typeof rawLayout.camera === "object"
      ? {
          x: finiteCoordinate(rawLayout.camera.x, 0),
          y: finiteCoordinate(rawLayout.camera.y, 0),
          zoom: quantizeZoom(finiteCoordinate(rawLayout.camera.zoom, 1)),
        }
      : { x: 0, y: 10, zoom: 1 },
    floors,
    placements: renderPlacements,
    backdrops: renderPlacements.filter((placement) => placement.kind === "backdrop"),
    objects: renderPlacements.filter((placement) => placement.kind !== "backdrop"),
    furniture: renderPlacements.filter((placement) => placement.kind === "furniture"),
    structures: renderPlacements.filter((placement) => placement.kind === "structure"),
    decor: renderPlacements.filter((placement) => placement.kind === "decor"),
    spawnPoints,
    seats,
    // Frozen-map intent for diagnostics only. Runtime activity always comes
    // from the authenticated snapshot/WS stream and is never synthesized here.
    initialActivities,
    blockedCells: frozenBlockedCells.map((cell) => ({ x: Number(cell.x), y: Number(cell.y) })),
  };
}

function safeColor(value, fallback) {
  return /^#[0-9a-f]{6}$/i.test(String(value || "")) ? value : fallback;
}

function finiteCoordinate(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function unwrapMessage(message) {
  if (!message || typeof message !== "object") return {};
  if (message.payload && typeof message.payload === "object") {
    return { ...message.payload, type: message.type ?? message.payload.type };
  }
  return message;
}

function canonicalNameForId(id, slot) {
  const normalized = String(id || "").toLowerCase();
  const directIndex = DISPLAY_NAMES.findIndex((name) => name.toLowerCase() === normalized);
  if (directIndex >= 0) return DISPLAY_NAMES[directIndex];
  const numbered = normalized.match(/(?:player|member|placeholder)[-_]?(\d+)$/);
  if (numbered) {
    const index = Number(numbered[1]) - 1;
    if (DISPLAY_NAMES[index]) return DISPLAY_NAMES[index];
  }
  return DISPLAY_NAMES[slot];
}

function normaliseActivity(value) {
  if (!value || typeof value !== "object" || value.type !== "work") return null;
  const facing = ASSET_DIRECTIONS.has(value.facing) ? value.facing : "southeast";
  return {
    type: "work",
    placementId: String(value.placementId ?? value.placement_id ?? ""),
    seatId: String(value.seatId ?? value.seat_id ?? ""),
    facing,
  };
}

function placeholderActor(slot, columns = GRID_COLUMNS, rows = GRID_ROWS) {
  const [x, y] = FALLBACK_POSITIONS[slot];
  const safeX = clamp(x, 0, columns - 1);
  const safeY = clamp(y, 0, rows - 1);
  return {
    id: `placeholder-${slot + 1}`,
    name: DISPLAY_NAMES[slot],
    color: FALLBACK_COLORS[slot],
    x: safeX,
    y: safeY,
    renderX: safeX,
    renderY: safeY,
    targetX: safeX,
    targetY: safeY,
    online: false,
    moving: false,
    facing: "southeast",
    animationAction: "idle",
    animationElapsed: 0,
    activity: null,
    real: false,
    slot,
  };
}

export function normaliseActorRoster({
  player = null,
  players = [],
  columns = GRID_COLUMNS,
  rows = GRID_ROWS,
} = {}) {
  const incoming = Array.isArray(players) ? [...players.filter(Boolean)] : [];
  if (player?.id != null && !incoming.some((candidate) => String(candidate.id) === String(player.id))) {
    incoming.push(player);
  }
  const unique = [];
  const seen = new Set();
  for (const candidate of incoming) {
    if (candidate?.id == null) continue;
    const id = String(candidate.id);
    if (seen.has(id)) continue;
    seen.add(id);
    unique.push(candidate);
  }
  const actors = unique.slice(0, ACTOR_COUNT).map((source, slot) => {
    const fallback = FALLBACK_POSITIONS[slot];
    const x = clamp(finiteCoordinate(source.x, fallback[0]), 0, columns - 1);
    const y = clamp(finiteCoordinate(source.y, fallback[1]), 0, rows - 1);
    return {
      id: String(source.id),
      name: String(source.name || canonicalNameForId(source.id, slot)),
      color: safeColor(source.color, FALLBACK_COLORS[slot]),
      x,
      y,
      renderX: x,
      renderY: y,
      targetX: x,
      targetY: y,
      online: source.online !== false,
      moving: Boolean(source.moving),
      facing: ASSET_DIRECTIONS.has(source.facing) ? source.facing : "southeast",
      animationAction: ["walk", "work"].includes(source.animationAction)
        ? source.animationAction
        : "idle",
      animationElapsed: Math.max(0, Number(source.animationElapsed) || 0),
      activity: normaliseActivity(source.activity),
      real: true,
      slot,
    };
  });
  while (actors.length < ACTOR_COUNT) actors.push(placeholderActor(actors.length, columns, rows));
  return actors;
}

const MOVE_REJECTION_CODES = new Set([
  "target_blocked",
  "target_out_of_bounds",
  "target_invalid",
  "target_occupied",
  "path_unavailable",
  "rate_limited",
  "seq_invalid",
  "player_missing",
  "work_seat_missing",
  "work_seat_occupied",
  "seat_not_found",
  "seat_occupied",
  "work_path_unavailable",
  "work_target_blocked",
]);

export function reduceMoveFeedback(current = {}, message = {}) {
  const acceptedTarget = current.acceptedTarget ? { ...current.acceptedTarget } : null;
  const acceptedPath = Array.isArray(current.acceptedPath)
    ? current.acceptedPath.map((point) => ({ ...point }))
    : [];
  if (message.type === "move.accepted") {
    const fallback = current.pendingTarget ?? acceptedTarget ?? { x: 0, y: 0 };
    const target = {
      x: finiteCoordinate(message.targetX ?? message.tileX, fallback.x),
      y: finiteCoordinate(message.targetY ?? message.tileY, fallback.y),
    };
    const path = Array.isArray(message.path)
      ? message.path
          .map((point) => ({
            x: Number(point.x ?? point.tileX),
            y: Number(point.y ?? point.tileY),
          }))
          .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
      : [];
    return { acceptedTarget: target, pendingTarget: { ...target }, acceptedPath: path };
  }
  const rejected = message.type === "move.ignored" || message.type === "work.ignored"
    || (message.type === "error" && MOVE_REJECTION_CODES.has(message.code));
  if (rejected) {
    return {
      acceptedTarget,
      pendingTarget: acceptedTarget ? { ...acceptedTarget } : null,
      acceptedPath,
    };
  }
  return {
    acceptedTarget,
    pendingTarget: current.pendingTarget ? { ...current.pendingTarget } : null,
    acceptedPath,
  };
}

function clonedMovementMarkers(current = {}) {
  return {
    acceptedPath: Array.isArray(current.acceptedPath)
      ? current.acceptedPath.map((point) => ({ ...point }))
      : [],
    acceptedTarget: current.acceptedTarget ? { ...current.acceptedTarget } : null,
    pendingTarget: current.pendingTarget ? { ...current.pendingTarget } : null,
  };
}

export function reduceSelfMovementMarkers(current = {}, message = {}) {
  const markers = clonedMovementMarkers(current);
  const selfId = current.selfId == null ? null : String(current.selfId);
  if (message.type === "work.stopped") {
    const stoppedPlayerId = message.playerId == null ? null : String(message.playerId);
    if (stoppedPlayerId == null || (selfId != null && stoppedPlayerId === selfId)) {
      return { acceptedPath: [], acceptedTarget: null, pendingTarget: null };
    }
    return markers;
  }
  if (message.type !== "world.snapshot" || selfId == null) return markers;
  const positions = Array.isArray(message.positions)
    ? message.positions
    : Array.isArray(message.players)
      ? message.players
      : [];
  const self = positions.find((entry) => String(entry?.id ?? entry?.playerId ?? "") === selfId);
  const hasServerTarget = self?.targetX != null && self?.targetY != null
    && Number.isFinite(Number(self.targetX)) && Number.isFinite(Number(self.targetY));
  if (self?.moving === false && !hasServerTarget) {
    return { acceptedPath: [], acceptedTarget: null, pendingTarget: null };
  }
  return markers;
}

export function resolveWorldSnapshotDimensions(current = {}, snapshot = {}, assetLayout = null) {
  const rawColumns = Number(snapshot.columns);
  const rawRows = Number(snapshot.rows);
  if (assetLayout) {
    if (!Number.isInteger(rawColumns)
      || !Number.isInteger(rawRows)
      || rawColumns !== assetLayout.columns
      || rawRows !== assetLayout.rows) {
      const error = new RangeError("实时 world.snapshot 与冻结资产尺寸不一致");
      error.code = "asset_world_dimensions_changed";
      error.assetFailure = true;
      throw error;
    }
  }
  return {
    columns: clamp(Math.floor(rawColumns || Number(current.columns) || GRID_COLUMNS), 1, GRID_COLUMNS),
    rows: clamp(Math.floor(rawRows || Number(current.rows) || GRID_ROWS), 1, GRID_ROWS),
  };
}

export class IsometricScene {
  constructor(canvas, options = {}) {
    if (!(canvas instanceof HTMLCanvasElement)) {
      throw new TypeError("IsometricScene requires a canvas element");
    }
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    this.ctx.imageSmoothingEnabled = false;
    this.columns = GRID_COLUMNS;
    this.rows = GRID_ROWS;
    this.blockedCells = new Set();
    this.selfId = null;
    this.actors = this.#normaliseActors(null, []);
    this.origin = { ...LEGACY_ORIGIN };
    this.camera = { x: 0, y: 10, zoom: 1 };
    this.layoutCamera = { ...this.camera };
    this.cameraPreset = "full";
    this.playbackRate = 1;
    this.paused = false;
    this.overlays = {
      grid: false,
      blocked: false,
      path: false,
      target: false,
      spawn: false,
      footprint: false,
      depth: false,
    };
    this.acceptedPath = [];
    this.acceptedTarget = null;
    this.pendingTarget = null;
    this.reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.onMoveTarget = options.onMoveTarget ?? (() => {});
    this.onWorkStart = options.onWorkStart ?? (() => {});
    this.onWorkStop = options.onWorkStop ?? (() => {});
    this.onCameraChange = options.onCameraChange ?? (() => {});
    this.onInteractionError = options.onInteractionError ?? (() => {});
    this.pointers = new Map();
    this.singleGesture = null;
    this.pinchGesture = null;
    this.effects = [];
    this.seatOccupancy = [];
    this.assetRuntime = null;
    this.assetLayout = null;
    this.rendererMode = options.initialRendererMode === "loading" ? "loading" : "legacy";
    this.assetError = null;
    this.interactionEnabled = true;
    this.onAssetError = options.onAssetError ?? (() => {});
    this.lastFrameTime = performance.now();
    this.animationFrame = 0;
    this.destroyed = false;

    this.#bindInput();
    this.#tick(this.lastFrameTime);
  }

  applyBootstrap(bootstrap = {}) {
    const world = bootstrap.world ?? bootstrap;
    if (this.rendererMode === "asset") {
      const columns = Math.floor(Number(world.columns) || GRID_COLUMNS);
      const rows = Math.floor(Number(world.rows) || GRID_ROWS);
      if (columns !== this.assetLayout.columns
        || rows !== this.assetLayout.rows
        || !Array.isArray(world.blockedCells)
        || !sameCellSet(this.assetLayout.blockedCells, world.blockedCells)) {
        const error = new RangeError("bootstrap world 与冻结资产布局不一致");
        error.code = "asset_world_binding_changed";
        error.assetFailure = true;
        throw error;
      }
    }
    this.columns = clamp(Math.floor(Number(world.columns) || GRID_COLUMNS), 1, GRID_COLUMNS);
    this.rows = clamp(Math.floor(Number(world.rows) || GRID_ROWS), 1, GRID_ROWS);
    this.blockedCells = new Set(
      Array.isArray(world.blockedCells)
        ? world.blockedCells.map((cell) => `${Math.floor(Number(cell.x))},${Math.floor(Number(cell.y))}`)
        : [],
    );
    this.selfId = bootstrap.player?.id == null ? this.selfId : String(bootstrap.player.id);
    this.actors = this.#normaliseActors(bootstrap.player, bootstrap.players);
    this.setSeatOccupancy(world.seatOccupancy ?? bootstrap.seatOccupancy ?? []);
    if (this.rendererMode === "asset" && this.cameraPreset === "full") {
      this.#refreshLayoutCamera();
      this.camera = { ...this.layoutCamera };
    }
    if (bootstrap.run) {
      this.paused = Boolean(bootstrap.run.paused);
      this.playbackRate = clamp(Number(bootstrap.run.speed) || 1, 0.1, 8);
    }
    return this.getActors();
  }

  setAssetRuntime(runtime, rawLayout, world = {}) {
    if (!runtime?.manifest || typeof runtime.drawAsset !== "function" || typeof runtime.drawAnimation !== "function") {
      throw new TypeError("需要有效的 AssetRuntime");
    }
    const layout = resolveAssetWorldLayout(runtime.manifest, rawLayout, world);
    for (const direction of ASSET_DIRECTIONS) {
      for (const action of ASSET_ACTIONS) {
        runtime.animationFrame(`${ASSET_ANIMATION_PREFIX}.${direction}.${action}`, 0);
      }
    }
    runtime.animationFrame(GOOD_CARD_ANIMATION, 0);
    for (const floor of layout.floors) runtime.asset(floor.assetId);
    for (const placement of layout.placements) {
      runtime.asset(placement.assetId);
      runtime.asset(placement.renderAssetId);
    }

    if (this.assetRuntime && this.assetRuntime !== runtime) this.assetRuntime.dispose?.();
    this.assetRuntime = runtime;
    this.assetLayout = layout;
    this.columns = layout.columns;
    this.rows = layout.rows;
    this.origin = { ...layout.origin };
    this.blockedCells = new Set(layout.blockedCells.map(collisionKey));
    this.layoutCamera = { ...layout.camera };
    this.camera = { ...layout.camera };
    this.cameraPreset = "full";
    this.rendererMode = "asset";
    this.#refreshLayoutCamera();
    this.camera = { ...this.layoutCamera };
    this.assetError = null;
    this.interactionEnabled = true;
    return this.getAssetState();
  }

  useLegacyRenderer() {
    this.assetRuntime?.dispose?.();
    this.assetRuntime = null;
    this.assetLayout = null;
    this.origin = { ...LEGACY_ORIGIN };
    this.layoutCamera = { x: 0, y: 10, zoom: 1 };
    this.cameraPreset = "full";
    this.rendererMode = "legacy";
    this.assetError = null;
    this.interactionEnabled = true;
    return this.getAssetState();
  }

  blockAssetRun(error) {
    this.rendererMode = "blocked";
    this.assetError = error instanceof Error ? error : new Error(String(error || "资产加载失败"));
    this.interactionEnabled = false;
    return this.getAssetState();
  }

  setLoading() {
    this.rendererMode = "loading";
    this.assetError = null;
    this.interactionEnabled = false;
    return this.getAssetState();
  }

  setInteractionEnabled(value) {
    this.interactionEnabled = Boolean(value) && this.rendererMode !== "blocked";
  }

  getAssetState() {
    return {
      mode: this.rendererMode,
      ready: this.rendererMode === "asset" || this.rendererMode === "legacy",
      legacy: this.rendererMode === "legacy",
      layoutId: this.assetLayout?.id ?? (this.rendererMode === "legacy" ? "legacy" : null),
      layoutSha256: this.assetLayout?.sha256 ?? null,
      assetShell: Boolean(this.assetLayout?.backdrops?.length),
      error: this.assetError?.message ?? null,
    };
  }

  applyNetworkMessage(rawMessage) {
    const message = unwrapMessage(rawMessage);
    switch (message.type) {
      case "auth.ok":
        if (message.playerId != null || message.player?.id != null) {
          this.selfId = String(message.playerId ?? message.player.id);
        }
        break;
      case "world.snapshot":
        this.#applyWorldSnapshot(message.bootstrap ?? message);
        break;
      case "world.positions":
        this.updatePositions(message.positions ?? message.players ?? []);
        this.setSeatOccupancy(message.seatOccupancy ?? message.seat_occupancy ?? []);
        break;
      case "move.accepted":
      case "move.ignored": {
        const movement = reduceMoveFeedback(this, message);
        this.acceptedPath = movement.acceptedPath;
        this.acceptedTarget = movement.acceptedTarget;
        this.pendingTarget = movement.pendingTarget;
        if (message.type === "move.accepted") {
          this.#applyActivityMessage({ playerId: this.selfId, activity: null });
          this.seatOccupancy = this.seatOccupancy.filter((entry) => entry.playerId !== this.selfId);
        }
        break;
      }
      case "work.ignored": {
        const movement = reduceMoveFeedback(this, message);
        this.acceptedPath = movement.acceptedPath;
        this.acceptedTarget = movement.acceptedTarget;
        this.pendingTarget = movement.pendingTarget;
        break;
      }
      case "work.accepted": {
        const seat = this.#seatById(message.placementId, message.seatId);
        const target = message.target ?? message;
        if (seat) {
          this.acceptedTarget = {
            x: finiteCoordinate(target.targetX ?? target.tileX ?? target.x, seat.x),
            y: finiteCoordinate(target.targetY ?? target.tileY ?? target.y, seat.y),
          };
          this.pendingTarget = { ...this.acceptedTarget };
        }
        if (Array.isArray(message.path)) {
          this.acceptedPath = message.path
            .map((point) => ({ x: Number(point.x ?? point.tileX), y: Number(point.y ?? point.tileY) }))
            .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
        }
        this.#applyActivityMessage({ playerId: this.selfId, activity: null });
        this.seatOccupancy = this.seatOccupancy.filter((entry) => entry.playerId !== this.selfId);
        if (seat && this.selfId) {
          this.seatOccupancy.push({
            placementId: seat.placementId,
            seatId: seat.id,
            playerId: this.selfId,
            state: message.active === true ? "active" : "reserved",
          });
        }
        if (message.active === true) {
          this.#applyActivityMessage({ ...message, type: "work.started", playerId: message.playerId ?? this.selfId });
        }
        if (Array.isArray(message.seatOccupancy)) this.setSeatOccupancy(message.seatOccupancy);
        break;
      }
      case "work.started":
      case "activity.changed":
        this.#applyActivityMessage(message);
        if (Array.isArray(message.seatOccupancy)) this.setSeatOccupancy(message.seatOccupancy);
        break;
      case "work.stopped":
        this.#applyActivityMessage({ ...message, activity: null });
        {
          const movement = reduceSelfMovementMarkers(this, message);
          this.acceptedPath = movement.acceptedPath;
          this.acceptedTarget = movement.acceptedTarget;
          this.pendingTarget = movement.pendingTarget;
        }
        if (Array.isArray(message.seatOccupancy)) this.setSeatOccupancy(message.seatOccupancy);
        else {
          const playerId = String(message.playerId ?? this.selfId ?? "");
          this.seatOccupancy = this.seatOccupancy.filter((entry) => entry.playerId !== playerId);
        }
        break;
      case "good-card.created":
        this.addGoodCardEffect(message.recipientId ?? message.card?.recipientId ?? message.goodCard?.recipientId);
        break;
      case "review.control":
        if (message.speed != null) this.setPlaybackRate(message.speed);
        if (message.paused != null) this.setPaused(message.paused);
        if (message.overlays != null) this.setOverlays(message.overlays);
        break;
      case "error":
        {
          const movement = reduceMoveFeedback(this, message);
          this.acceptedPath = movement.acceptedPath;
          this.acceptedTarget = movement.acceptedTarget;
          this.pendingTarget = movement.pendingTarget;
        }
        break;
      default:
        break;
    }
  }

  updatePositions(entries) {
    if (!Array.isArray(entries)) return;
    const byId = new Map(this.actors.map((actor) => [actor.id, actor]));
    for (const entry of entries) {
      const id = entry?.id ?? entry?.playerId;
      if (id == null) continue;
      const actor = byId.get(String(id));
      if (!actor) continue;
      const nextTargetX = clamp(finiteCoordinate(entry.x, actor.targetX), 0, this.columns - 1);
      const nextTargetY = clamp(finiteCoordinate(entry.y, actor.targetY), 0, this.rows - 1);
      actor.facing = directionForMotion(
        nextTargetX - actor.targetX,
        nextTargetY - actor.targetY,
        actor.facing,
      );
      actor.targetX = nextTargetX;
      actor.targetY = nextTargetY;
      actor.moving = entry.moving == null
        ? Math.hypot(actor.targetX - actor.renderX, actor.targetY - actor.renderY) > 0.025
        : Boolean(entry.moving);
      if (Object.prototype.hasOwnProperty.call(entry, "activity")) {
        actor.activity = normaliseActivity(entry.activity);
        if (actor.activity) actor.facing = actor.activity.facing;
      }
      if (entry.online != null) actor.online = Boolean(entry.online);
      if (this.reducedMotion) {
        actor.renderX = actor.targetX;
        actor.renderY = actor.targetY;
      }
    }
    const self = this.actors.find((actor) => actor.id === this.selfId);
    if (
      self &&
      this.pendingTarget &&
      Math.hypot(self.targetX - this.pendingTarget.x, self.targetY - this.pendingTarget.y) < 0.08 &&
      !self.moving
    ) {
      this.pendingTarget = null;
    }
  }

  setSeatOccupancy(entries) {
    if (!Array.isArray(entries)) return;
    this.seatOccupancy = entries
      .filter((entry) => entry && typeof entry === "object")
      .map((entry) => ({
        placementId: String(entry.placementId ?? entry.placement_id ?? ""),
        seatId: String(entry.seatId ?? entry.seat_id ?? ""),
        playerId: String(entry.playerId ?? entry.player_id ?? ""),
        state: entry.state === "active" ? "active" : "reserved",
      }))
      .filter((entry) => entry.placementId && entry.seatId && entry.playerId);
  }

  setSelfTarget(x, y) {
    const target = {
      x: clamp(Math.round(Number(x)), 0, this.columns - 1),
      y: clamp(Math.round(Number(y)), 0, this.rows - 1),
    };
    this.pendingTarget = { ...target };
    return target;
  }

  setPlaybackRate(value) {
    this.playbackRate = clamp(Number(value) || 1, 0.1, 8);
  }

  zoomBy(factor) {
    // Step through the allowed magnifications rather than multiplying, so a
    // small gesture cannot land between two of them and get rounded back.
    const direction = (Number(factor) || 1) >= 1 ? 1 : -1;
    const index = ZOOM_STEPS.indexOf(quantizeZoom(this.camera.zoom));
    const next = ZOOM_STEPS[clamp(index + direction, 0, ZOOM_STEPS.length - 1)];
    this.cameraPreset = "custom";
    this.#zoomAt(LOGICAL_WIDTH / 2, LOGICAL_HEIGHT / 2, next);
  }

  setPaused(value) {
    this.paused = Boolean(value);
  }

  setOverlays(value) {
    if (typeof value === "boolean") {
      this.overlays = { ...this.overlays, grid: value };
      return;
    }
    if (!value || typeof value !== "object") return;
    for (const key of ["grid", "blocked", "path", "target", "spawn", "footprint", "depth"]) {
      if (value[key] != null) this.overlays[key] = Boolean(value[key]);
    }
  }

  setCameraPreset(value) {
    const preset = String(value || "full");
    if (!["full", "gus", "desk"].includes(preset)) return this.getCameraState();
    if (preset === "full") {
      this.#refreshLayoutCamera();
      this.camera = { ...this.layoutCamera };
    } else {
      const desk = this.assetLayout?.furniture?.find(({ assetId }) => assetId === "furniture.desk-island");
      const target = preset === "desk" && desk
        ? { x: desk.x + 1, y: desk.y + 0.5 }
        : this.actors.find((actor) => actor.id === this.selfId);
      if (!target) return this.getCameraState();
      const zoom = MAX_ZOOM;
      const local = this.#localGridPoint(target.x, target.y);
      this.camera = {
        x: -(local.x - LOGICAL_WIDTH / 2) * zoom,
        y: -(local.y - LOGICAL_HEIGHT / 2) * zoom + (preset === "desk" ? 34 : 24),
        zoom,
      };
      this.#clampCamera();
    }
    this.cameraPreset = preset;
    this.onCameraChange(this.getCameraState());
    return this.getCameraState();
  }

  addGoodCardEffect(recipientId) {
    if (recipientId == null) return;
    const actor = this.actors.find((candidate) => candidate.id === String(recipientId));
    if (!actor) return;
    const assetDuration = this.rendererMode === "asset"
      ? this.assetRuntime.animationDuration(GOOD_CARD_ANIMATION)
      : 1100;
    this.effects.push({ id: actor.id, elapsed: 0, duration: this.reducedMotion ? 220 : assetDuration });
  }

  getActors() {
    return this.actors.map((actor) => ({ ...actor }));
  }

  getCameraState() {
    return { ...this.camera };
  }

  getDirectorState() {
    const self = this.actors.find((actor) => actor.id === this.selfId);
    const reservation = this.seatOccupancy.find((entry) => entry.playerId === this.selfId);
    const reservedSeat = reservation
      ? this.#seatById(reservation.placementId, reservation.seatId)
      : null;
    const activity = self?.activity
      ? { ...self.activity, phase: "active" }
      : reservation
        ? {
            type: "work",
            placementId: reservation.placementId,
            seatId: reservation.seatId,
            facing: reservedSeat?.facing ?? "southeast",
            phase: reservation.state,
          }
        : null;
    return {
      camera: this.getCameraState(),
      activity: structuredCloneSafe(activity),
      seatOccupancy: structuredCloneSafe(this.seatOccupancy),
      layout: this.assetLayout
        ? {
            id: this.assetLayout.id,
            sha256: this.assetLayout.sha256 || null,
            stage: this.assetLayout.stage || null,
            columns: this.assetLayout.columns,
            rows: this.assetLayout.rows,
            origin: { ...this.assetLayout.origin },
          }
        : null,
      overlays: structuredCloneSafe(this.overlays),
      target: structuredCloneSafe(this.acceptedTarget ?? this.pendingTarget),
    };
  }

  snapshot() {
    return {
      columns: this.columns,
      rows: this.rows,
      blockedCells: [...this.blockedCells],
      selfId: this.selfId,
      actors: this.getActors(),
      camera: { ...this.camera },
      origin: { ...this.origin },
      paused: this.paused,
      playbackRate: this.playbackRate,
      overlays: structuredCloneSafe(this.overlays),
      acceptedPath: structuredCloneSafe(this.acceptedPath),
      acceptedTarget: structuredCloneSafe(this.acceptedTarget),
      pendingTarget: structuredCloneSafe(this.pendingTarget),
      seatOccupancy: structuredCloneSafe(this.seatOccupancy),
    };
  }

  restore(snapshot = {}) {
    if (snapshot.columns != null) this.columns = clamp(Number(snapshot.columns), 1, GRID_COLUMNS);
    if (snapshot.rows != null) this.rows = clamp(Number(snapshot.rows), 1, GRID_ROWS);
    if (Array.isArray(snapshot.actors)) {
      this.actors = snapshot.actors.slice(0, ACTOR_COUNT).map((actor, slot) => ({
        ...actor,
        slot,
        renderX: finiteCoordinate(actor.renderX, actor.x),
        renderY: finiteCoordinate(actor.renderY, actor.y),
        targetX: finiteCoordinate(actor.targetX, actor.x),
        targetY: finiteCoordinate(actor.targetY, actor.y),
        facing: ASSET_DIRECTIONS.has(actor.facing) ? actor.facing : "southeast",
        animationAction: ["walk", "work"].includes(actor.animationAction) ? actor.animationAction : "idle",
        animationElapsed: Math.max(0, Number(actor.animationElapsed) || 0),
        activity: normaliseActivity(actor.activity),
      }));
      while (this.actors.length < ACTOR_COUNT) {
        const slot = this.actors.length;
        this.actors.push(this.#placeholderActor(slot));
      }
    }
    this.selfId = snapshot.selfId ?? this.selfId;
    this.blockedCells = new Set(snapshot.blockedCells ?? []);
    if (snapshot.camera) this.camera = { ...this.camera, ...snapshot.camera };
    if (snapshot.origin) this.origin = { ...this.origin, ...snapshot.origin };
    if (snapshot.paused != null) this.paused = Boolean(snapshot.paused);
    if (snapshot.playbackRate != null) this.setPlaybackRate(snapshot.playbackRate);
    if (snapshot.overlays != null) this.overlays = structuredCloneSafe(snapshot.overlays);
    this.acceptedPath = structuredCloneSafe(snapshot.acceptedPath ?? []);
    this.acceptedTarget = structuredCloneSafe(snapshot.acceptedTarget ?? null);
    this.pendingTarget = structuredCloneSafe(snapshot.pendingTarget ?? null);
    this.setSeatOccupancy(snapshot.seatOccupancy ?? []);
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.animationFrame);
    this.assetRuntime?.dispose?.();
    this.assetRuntime = null;
  }

  worldToScreen(x, y) {
    return projectIsometric(x, y, this.camera, { origin: this.origin });
  }

  screenToGrid(screenX, screenY) {
    const projected = unprojectIsometric(screenX, screenY, this.camera, { origin: this.origin });
    const { x, y } = projected;
    if (x < -0.5 || y < -0.5 || x > this.columns - 0.5 || y > this.rows - 0.5) return null;
    return {
      x: clamp(Math.round(x), 0, this.columns - 1),
      y: clamp(Math.round(y), 0, this.rows - 1),
    };
  }

  #localGridPoint(x, y) {
    return projectIsometric(x, y, { x: 0, y: 0, zoom: 1 }, { origin: this.origin });
  }

  #seatById(placementId, seatId) {
    return this.assetLayout?.seats?.find((seat) => (
      seat.placementId === String(placementId ?? "") && seat.id === String(seatId ?? "")
    )) ?? null;
  }

  #applyActivityMessage(message = {}) {
    const playerId = String(message.playerId ?? message.player?.id ?? message.id ?? this.selfId ?? "");
    const actor = this.actors.find((candidate) => candidate.id === playerId);
    if (!actor) return;
    const announcedActivity = message.activity ?? (message.type === "work.started"
      ? {
          type: "work",
          placementId: message.placementId,
          seatId: message.seatId,
          facing: message.facing,
        }
      : null);
    actor.activity = normaliseActivity(announcedActivity);
    if (actor.activity) {
      actor.facing = actor.activity.facing;
      actor.animationAction = "work";
      actor.animationElapsed = 0;
    } else {
      actor.animationAction = "idle";
      actor.animationElapsed = 0;
    }
  }

  #applyWorldSnapshot(snapshot = {}) {
    let dimensions;
    try {
      dimensions = resolveWorldSnapshotDimensions(
        this,
        snapshot,
        this.rendererMode === "asset" ? this.assetLayout : null,
      );
    } catch (error) {
      this.blockAssetRun(error);
      this.onAssetError(error);
      return;
    }
    this.columns = dimensions.columns;
    this.rows = dimensions.rows;
    if (Array.isArray(snapshot.blockedCells)) {
      if (this.rendererMode === "asset" && !sameCellSet(this.assetLayout.blockedCells, snapshot.blockedCells)) {
        const error = new RangeError("实时 world.snapshot 与冻结资产碰撞不一致");
        error.code = "asset_world_collision_changed";
        this.blockAssetRun(error);
        this.onAssetError(error);
        return;
      }
      this.blockedCells = new Set(
        snapshot.blockedCells.map((cell) => `${Math.floor(Number(cell.x))},${Math.floor(Number(cell.y))}`),
      );
    }
    const positions = Array.isArray(snapshot.positions)
      ? snapshot.positions
      : Array.isArray(snapshot.players)
        ? snapshot.players
        : [];
    if (positions.length) {
      const existingById = new Map(this.actors.filter((actor) => actor.real).map((actor) => [actor.id, actor]));
      const hasUnknownId = positions.some((position) => !existingById.has(String(position.id ?? position.playerId)));
      if (hasUnknownId) {
        const enriched = positions.map((position) => {
          const existing = existingById.get(String(position.id ?? position.playerId));
          return existing
            ? { ...position, name: existing.name, color: existing.color }
            : position;
        });
        this.actors = normaliseActorRoster({
          players: enriched,
          columns: this.columns,
          rows: this.rows,
        });
      } else {
        this.updatePositions(positions);
      }
    }
    this.setSeatOccupancy(snapshot.seatOccupancy ?? snapshot.seat_occupancy ?? []);
    if (snapshot.paused != null) this.paused = Boolean(snapshot.paused);
    if (snapshot.speed != null) this.setPlaybackRate(snapshot.speed);
    const movement = reduceSelfMovementMarkers(this, { ...snapshot, type: "world.snapshot" });
    this.acceptedPath = movement.acceptedPath;
    this.acceptedTarget = movement.acceptedTarget;
    this.pendingTarget = movement.pendingTarget;
  }

  #normaliseActors(player, players) {
    return normaliseActorRoster({
      player,
      players,
      columns: this.columns,
      rows: this.rows,
    });
  }

  #placeholderActor(slot) {
    return placeholderActor(slot, this.columns, this.rows);
  }

  #bindInput() {
    this.canvas.addEventListener("contextmenu", (event) => event.preventDefault());
    this.canvas.addEventListener("pointerdown", (event) => this.#pointerDown(event));
    this.canvas.addEventListener("pointermove", (event) => this.#pointerMove(event));
    this.canvas.addEventListener("pointerup", (event) => this.#pointerUp(event));
    this.canvas.addEventListener("pointercancel", (event) => this.#pointerUp(event, true));
    this.canvas.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const point = this.#clientToLogical(event.clientX, event.clientY);
        const factor = event.deltaY > 0 ? 0.9 : 1.1;
        this.#zoomAt(point.x, point.y, this.camera.zoom * factor);
      },
      { passive: false },
    );
    this.canvas.addEventListener("keydown", (event) => {
      if (!this.interactionEnabled) return;
      const movement = {
        ArrowLeft: [-1, 0],
        a: [-1, 0],
        A: [-1, 0],
        ArrowRight: [1, 0],
        d: [1, 0],
        D: [1, 0],
        ArrowUp: [0, -1],
        w: [0, -1],
        W: [0, -1],
        ArrowDown: [0, 1],
        s: [0, 1],
        S: [0, 1],
      }[event.key];
      if (movement) {
        event.preventDefault();
        const self = this.actors.find((actor) => actor.id === this.selfId);
        if (!self) return;
        const base = this.pendingTarget ?? { x: self.targetX, y: self.targetY };
        const target = this.setSelfTarget(base.x + movement[0], base.y + movement[1]);
        this.onMoveTarget(target);
      } else if (event.key === "0") {
        this.setCameraPreset("full");
      } else if (event.key === "Escape") {
        const self = this.actors.find((actor) => actor.id === this.selfId);
        if (self?.activity?.type === "work") this.onWorkStop();
      } else if (["+", "="].includes(event.key)) {
        this.zoomBy(1.15);
      } else if (event.key === "-") {
        this.zoomBy(0.87);
      }
    });
  }

  #pointerDown(event) {
    if (!this.interactionEnabled) return;
    this.canvas.setPointerCapture?.(event.pointerId);
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (this.pointers.size === 1) {
      this.singleGesture = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        moved: false,
      };
      this.pinchGesture = null;
    } else if (this.pointers.size === 2) {
      this.singleGesture = null;
      this.pinchGesture = this.#pinchSnapshot();
    }
  }

  #pointerMove(event) {
    if (!this.interactionEnabled) return;
    if (!this.pointers.has(event.pointerId)) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (this.pointers.size >= 2) {
      this.cameraPreset = "custom";
      const current = this.#pinchSnapshot();
      const previous = this.pinchGesture ?? current;
      const previousLogical = this.#clientToLogical(previous.midX, previous.midY);
      const currentLogical = this.#clientToLogical(current.midX, current.midY);
      this.camera.x += currentLogical.x - previousLogical.x;
      this.camera.y += currentLogical.y - previousLogical.y;
      const ratio = previous.distance > 0 ? current.distance / previous.distance : 1;
      this.#zoomAt(currentLogical.x, currentLogical.y, this.camera.zoom * ratio);
      this.pinchGesture = current;
      this.#clampCamera();
      return;
    }
    const gesture = this.singleGesture;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const totalDistance = Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY);
    if (totalDistance > 7) gesture.moved = true;
    if (gesture.moved) {
      this.cameraPreset = "custom";
      const currentLogical = this.#clientToLogical(event.clientX, event.clientY);
      const previousLogical = this.#clientToLogical(gesture.lastX, gesture.lastY);
      this.camera.x += currentLogical.x - previousLogical.x;
      this.camera.y += currentLogical.y - previousLogical.y;
      this.#clampCamera();
    }
    gesture.lastX = event.clientX;
    gesture.lastY = event.clientY;
  }

  #pointerUp(event, cancelled = false) {
    if (!this.interactionEnabled) return;
    const gesture = this.singleGesture;
    const wasSingleTap =
      !cancelled &&
      this.pointers.size === 1 &&
      gesture?.pointerId === event.pointerId &&
      !gesture.moved;
    this.pointers.delete(event.pointerId);
    this.canvas.releasePointerCapture?.(event.pointerId);
    if (wasSingleTap) {
      const point = this.#clientToLogical(event.clientX, event.clientY);
      const seat = this.#seatAtScreen(point.x, point.y);
      const self = this.actors.find((actor) => actor.id === this.selfId);
      const seatOccupant = seat ? this.seatOccupancy.find((entry) => (
        entry.placementId === seat.placementId && entry.seatId === seat.id
      )) : null;
      const isCurrentSeat = seat && (
        (self?.activity?.type === "work"
          && self.activity.placementId === seat.placementId
          && self.activity.seatId === seat.id)
        || seatOccupant?.playerId === this.selfId
      );
      if (isCurrentSeat) {
        this.onWorkStop();
      } else if (seat) {
        if (seatOccupant) {
          this.onInteractionError("这个座位正在使用中");
        } else {
          this.setSelfTarget(seat.x, seat.y);
          this.onWorkStart({ placementId: seat.placementId, seatId: seat.id });
        }
      } else {
        const target = this.screenToGrid(point.x, point.y);
        if (!target) {
          this.onInteractionError("这里不在办公室范围内");
        } else {
          this.setSelfTarget(target.x, target.y);
          // Blocked targets are still sent. The server is authoritative and may reject them.
          this.onMoveTarget(target);
        }
      }
    }
    if (gesture?.moved) this.onCameraChange(this.getCameraState());
    if (this.pointers.size === 1) {
      const [pointerId, point] = this.pointers.entries().next().value;
      this.singleGesture = {
        pointerId,
        startX: point.x,
        startY: point.y,
        lastX: point.x,
        lastY: point.y,
        moved: true,
      };
      this.pinchGesture = null;
    } else if (this.pointers.size === 0) {
      this.singleGesture = null;
      this.pinchGesture = null;
    }
  }

  #pinchSnapshot() {
    const [first, second] = [...this.pointers.values()].slice(0, 2);
    return {
      midX: (first.x + second.x) / 2,
      midY: (first.y + second.y) / 2,
      distance: Math.hypot(second.x - first.x, second.y - first.y),
    };
  }

  #seatAtScreen(screenX, screenY) {
    if (this.rendererMode !== "asset" || !this.assetLayout?.seats?.length) return null;
    let closest = null;
    let closestDistance = Infinity;
    for (const seat of this.assetLayout.seats) {
      const point = this.worldToScreen(seat.x, seat.y);
      const distance = Math.hypot(point.x - screenX, (point.y - screenY) * 1.6);
      if (distance <= 18 && distance < closestDistance) {
        closest = seat;
        closestDistance = distance;
      }
    }
    return closest;
  }

  #clientToLogical(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const objectFit = getComputedStyle(this.canvas).objectFit;
    const scale = objectFit === "cover"
      ? Math.max(rect.width / LOGICAL_WIDTH, rect.height / LOGICAL_HEIGHT)
      : Math.min(rect.width / LOGICAL_WIDTH, rect.height / LOGICAL_HEIGHT);
    const renderedWidth = LOGICAL_WIDTH * scale;
    const renderedHeight = LOGICAL_HEIGHT * scale;
    const offsetX = (rect.width - renderedWidth) / 2;
    const offsetY = (rect.height - renderedHeight) / 2;
    return {
      x: (clientX - rect.left - offsetX) / scale,
      y: (clientY - rect.top - offsetY) / scale,
    };
  }

  #zoomAt(screenX, screenY, requestedZoom) {
    const previousZoom = this.camera.zoom;
    const nextZoom = quantizeZoom(clamp(requestedZoom, MIN_ZOOM, MAX_ZOOM));
    if (previousZoom === nextZoom) return;
    this.cameraPreset = "custom";
    const worldX = (screenX - LOGICAL_WIDTH / 2 - this.camera.x) / previousZoom;
    const worldY = (screenY - LOGICAL_HEIGHT / 2 - this.camera.y) / previousZoom;
    this.camera.zoom = nextZoom;
    this.camera.x = screenX - LOGICAL_WIDTH / 2 - worldX * nextZoom;
    this.camera.y = screenY - LOGICAL_HEIGHT / 2 - worldY * nextZoom;
    this.#clampCamera();
    this.onCameraChange(this.getCameraState());
  }

  #clampCamera() {
    this.camera.x = clamp(this.camera.x, -420, 420);
    this.camera.y = clamp(this.camera.y, -300, 300);
  }

  #refreshLayoutCamera() {
    if (this.rendererMode !== "asset" || !this.assetLayout || !this.assetRuntime) return;
    this.layoutCamera = cameraForAssetLayout(
      this.assetRuntime.manifest,
      this.assetLayout,
      this.actors,
      { padding: 16 },
    );
  }

  #tick(now) {
    if (this.destroyed) return;
    const delta = Math.min(50, now - this.lastFrameTime);
    this.lastFrameTime = now;
    if (!this.paused) this.#update(delta);
    try {
      this.#render(now);
    } catch (error) {
      if (this.rendererMode === "asset") {
        this.blockAssetRun(error);
        this.onAssetError(this.assetError);
        this.#render(now);
      } else {
        throw error;
      }
    }
    this.animationFrame = requestAnimationFrame((time) => this.#tick(time));
  }

  /**
   * How far to advance an actor's animation clock this frame.
   *
   * A walk whose sprite declares a stride is driven by the distance actually
   * covered on screen, not by wall-clock time: the sheet moves the planted foot
   * back `strideScreenPx / framesPerStep` per frame, so converting displacement
   * into frames makes the foot stick to the floor at any speed. It also means
   * an actor easing to a halt slows its legs down instead of sprinting in place.
   * Everything else still advances on time.
   */
  #animationAdvanceMs(actor, next, delta) {
    const timeAdvance = delta * this.playbackRate;
    if (actor.animationAction !== "walk" || !this.assetRuntime) return timeAdvance;
    let motion = null;
    try {
      motion = this.assetRuntime.animationMotion(actorAnimationId(actor, this.reducedMotion));
    } catch {
      return timeAdvance;
    }
    if (!motion || !(motion.strideScreenPx > 0)) return timeAdvance;
    const worldX = next.x - actor.renderX;
    const worldY = next.y - actor.renderY;
    const movedPx = Math.hypot(
      (worldX - worldY) * (TILE_WIDTH / 2),
      (worldX + worldY) * (TILE_HEIGHT / 2),
    );
    return movedPx * (motion.framesPerStep * motion.frameDurationMs) / motion.strideScreenPx;
  }

  #update(delta) {
    for (const actor of this.actors) {
      const deltaX = actor.targetX - actor.renderX;
      const deltaY = actor.targetY - actor.renderY;
      const displayMoving = !this.reducedMotion && Math.hypot(deltaX, deltaY) > 0.002;
      const nextFacing = !displayMoving && actor.activity?.type === "work"
        ? actor.activity.facing
        : directionForMotion(deltaX, deltaY, actor.facing);
      const nextAction = displayMoving ? "walk" : actor.activity?.type === "work" ? "work" : "idle";
      // Only an action change restarts the cycle.  Turning a corner used to
      // reset it too, and with four-neighbour pathfinding that meant the walk
      // almost never completed a lap — the legs snapped back to the contact
      // pose at every corner.
      const actionChanged = actor.animationAction !== nextAction;
      actor.facing = nextFacing;
      actor.animationAction = nextAction;
      const next = advanceMotionPoint(
        { x: actor.renderX, y: actor.renderY },
        { x: actor.targetX, y: actor.targetY },
        delta,
        this.playbackRate,
        this.reducedMotion,
      );
      if (actionChanged) {
        actor.animationElapsed = 0;
      } else if (this.reducedMotion) {
        actor.animationElapsed = 0;
      } else {
        actor.animationElapsed = Math.max(0, Number(actor.animationElapsed) || 0)
          + this.#animationAdvanceMs(actor, next, delta);
      }
      actor.renderX = next.x;
      actor.renderY = next.y;
      if (Math.abs(actor.targetX - actor.renderX) < 0.002) actor.renderX = actor.targetX;
      if (Math.abs(actor.targetY - actor.renderY) < 0.002) actor.renderY = actor.targetY;
      actor.x = actor.targetX;
      actor.y = actor.targetY;
    }
    for (const effect of this.effects) effect.elapsed += delta * this.playbackRate;
    this.effects = this.effects.filter((effect) => effect.elapsed < effect.duration);
  }

  #render(now) {
    const ctx = this.ctx;
    const assetShell = this.rendererMode === "asset" && this.assetLayout?.backdrops?.length > 0;
    const sceneShell = this.rendererMode === "asset" ? this.assetRuntime?.manifest?.sceneShell : null;
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    const modernShell = assetShell && Number(this.assetRuntime?.manifest?.geometryVersion) >= 2;
    ctx.fillStyle = modernShell ? "#a9d1e8" : assetShell ? "#202b2e" : "#aeb7b4";
    ctx.fillRect(0, 0, LOGICAL_WIDTH, LOGICAL_HEIGHT);
    if (!assetShell) this.#drawBackdrop(ctx);
    if (assetShell) this.#drawAssetBackdrops(ctx);
    if (this.rendererMode === "blocked" || this.rendererMode === "loading") {
      ctx.restore();
      return;
    }
    ctx.translate(snap(LOGICAL_WIDTH / 2 + this.camera.x), snap(LOGICAL_HEIGHT / 2 + this.camera.y));
    ctx.scale(this.camera.zoom, this.camera.zoom);
    ctx.translate(-LOGICAL_WIDTH / 2, -LOGICAL_HEIGHT / 2);
    if (sceneShell) drawCutawayOfficeTower(ctx, this.assetLayout, sceneShell);
    this.#drawGround(ctx);
    this.#drawWorldObjects(ctx, now);
    if (Object.values(this.overlays).some(Boolean)) this.#drawOverlays(ctx);
    ctx.restore();
  }

  #drawBackdrop(ctx) {
    pixelRect(ctx, 0, 0, LOGICAL_WIDTH, 68, "#d5d8cc");
    pixelRect(ctx, 0, 64, LOGICAL_WIDTH, 7, "#677879");
    for (let x = 24; x < LOGICAL_WIDTH; x += 104) {
      pixelRect(ctx, x, 12, 72, 39, PALETTE.deepInk);
      pixelRect(ctx, x + 4, 16, 30, 31, "#8eb8bc");
      pixelRect(ctx, x + 38, 16, 30, 31, "#a5c9c5");
      pixelRect(ctx, x + 31, 16, 4, 31, "#f1bf65");
    }
    pixelRect(ctx, 0, 70, LOGICAL_WIDTH, LOGICAL_HEIGHT - 70, "#94a29f");
    for (let y = 86; y < LOGICAL_HEIGHT; y += 24) {
      for (let x = y % 48 ? 8 : 20; x < LOGICAL_WIDTH; x += 48) {
        pixelRect(ctx, x, y, 6, 3, "rgb(43 65 67 / 13%)");
      }
    }
  }

  #drawAssetBackdrops(ctx) {
    for (const backdrop of this.assetLayout.backdrops) {
      const asset = this.assetRuntime.asset(backdrop.renderAssetId);
      const ground = backdropGroundForManifest(this.assetRuntime.manifest, asset);
      this.assetRuntime.drawAsset(
        ctx,
        backdrop.renderAssetId,
        ground.x,
        ground.y,
        { flipX: backdrop.renderFlipX },
      );
    }
  }

  #drawGround(ctx) {
    if (this.rendererMode === "asset") {
      const hasCutawayShell = Boolean(this.assetRuntime?.manifest?.sceneShell);
      for (const floor of sortByIsometricDepth(this.assetLayout.floors)) {
        const point = this.#removeCameraTransform(this.worldToScreen(floor.x, floor.y));
        this.assetRuntime.drawAsset(ctx, floor.assetId, point.x, point.y);
        // Frozen manifests retain their approved per-tile edge pixels. The
        // cutaway shell owns exactly x=max and y=max as continuous faces.
        if (!hasCutawayShell && (floor.x === 0 || floor.y === this.rows - 1)) {
          drawTileEdge(ctx, point.x, point.y, TILE_WIDTH, TILE_HEIGHT, 3);
        }
      }
      return;
    }
    for (let diagonal = 0; diagonal < this.columns + this.rows - 1; diagonal += 1) {
      for (let x = 0; x < this.columns; x += 1) {
        const y = diagonal - x;
        if (y < 0 || y >= this.rows) continue;
        const point = this.worldToScreen(x, y);
        const local = this.#removeCameraTransform(point);
        const meetingZone = x >= 7 && x <= 12 && y >= 3 && y <= 6;
        const focusZone = (x >= 2 && x <= 6 && y >= 1 && y <= 4) || (x >= 13 && x <= 17 && y >= 1 && y <= 3);
        const loungeZone = x >= 13 && x <= 17 && y >= 7 && y <= 9;
        let fill = (x + y) % 2 ? "#c7c4b7" : "#d0ccbd";
        let edge = "#8b8d83";
        if (meetingZone) {
          fill = (x + y) % 2 ? "#8ea9ad" : "#9bb3b4";
          edge = "#637d81";
        } else if (focusZone) {
          fill = (x + y) % 2 ? "#b8ad99" : "#c5baa4";
          edge = "#867b69";
        } else if (loungeZone) {
          fill = (x + y) % 2 ? "#c4988d" : "#cda69a";
          edge = "#8d6964";
        }
        drawIsoTile(ctx, local.x, local.y, TILE_WIDTH, TILE_HEIGHT, fill, edge);
        if (x === 0 || y === this.rows - 1) drawTileEdge(ctx, local.x, local.y, TILE_WIDTH, TILE_HEIGHT, 3);
        if (!meetingZone && !focusZone && !loungeZone && hashNumber(x, y) > 0.82) {
          pixelRect(ctx, local.x - 1, local.y - 2, 3, 2, "#a9aa9e");
        }
      }
    }
  }

  #drawWorldObjects(ctx, now) {
    if (this.rendererMode === "asset") {
      this.#drawAssetWorldObjects(ctx);
      return;
    }
    const objects = [];
    for (const furniture of OFFICE_FURNITURE) objects.push({ ...furniture, layer: 0 });
    for (const actor of this.actors) objects.push({ actor, kind: "actor", layer: 2, depth: actor.renderX + actor.renderY + 0.7 });
    const sortedObjects = sortByIsometricDepth(objects);

    if (this.pendingTarget) {
      const targetPoint = this.#removeCameraTransform(this.worldToScreen(this.pendingTarget.x, this.pendingTarget.y));
      polygon(
        ctx,
        [
          [targetPoint.x, targetPoint.y - 5],
          [targetPoint.x + 10, targetPoint.y],
          [targetPoint.x, targetPoint.y + 5],
          [targetPoint.x - 10, targetPoint.y],
        ],
        "rgb(241 191 101 / 68%)",
        PALETTE.yellowDark,
        2,
      );
    }

    for (const object of sortedObjects) {
      if (object.kind === "actor") {
        const actor = object.actor;
        const point = this.#removeCameraTransform(this.worldToScreen(actor.renderX, actor.renderY));
        drawActor(ctx, point.x, point.y + 3, actor, actor.animationElapsed / LEGACY_FRAME_MS, {
          reducedMotion: this.reducedMotion,
          selected: actor.id === this.selfId,
        });
        for (const effect of this.effects.filter((candidate) => candidate.id === actor.id)) {
          drawHeartBurst(ctx, point.x, point.y - 35, effect.elapsed / effect.duration);
        }
        continue;
      }
      const point = this.#removeCameraTransform(this.worldToScreen(object.x, object.y));
      if (object.kind === "desk") drawDeskIsland(ctx, point.x, point.y + 3, object);
      else if (object.kind === "meeting") drawMeetingTable(ctx, point.x, point.y + 3);
      else if (object.kind === "copy-counter") drawCopyCounter(ctx, point.x, point.y + 3);
      else if (object.kind === "sofa") drawOfficeSofa(ctx, point.x, point.y + 3);
      else if (object.kind === "storage") drawStorage(ctx, point.x, point.y + 3, object);
      else if (object.kind === "printer") drawPrinter(ctx, point.x, point.y + 3);
      else if (object.kind === "plant") drawOfficePlant(ctx, point.x, point.y + 3);
      else if (object.kind === "whiteboard") drawWhiteboard(ctx, point.x, point.y + 3);
    }
  }

  #drawAssetWorldObjects(ctx) {
    const objects = [];
    this.#drawSeatMarkers(ctx);
    const underlaidWorkActorIds = new Set();
    for (const actor of this.actors) {
      if (!matchingWorkPlacement(actor, this.assetLayout)) continue;
      const point = this.#removeCameraTransform(this.worldToScreen(actor.renderX, actor.renderY));
      this.#drawActorEmphasis(ctx, point, actor);
      underlaidWorkActorIds.add(actor.id);
    }
    for (const placement of this.assetLayout.objects) {
      objects.push({ ...placement, renderKind: "asset-placement" });
    }
    for (const actor of this.actors) {
      objects.push({
        actor,
        kind: "asset-actor",
        layer: 2,
        depth: actorDepthForOcclusion(actor, this.assetLayout),
      });
    }
    const sortedObjects = sortByIsometricDepth(objects);
    const visibleBounds = this.#visibleLocalBounds(5);
    const labelLayouts = new Map(layoutActorLabels(
      sortedObjects
        .filter((object) => object.kind === "asset-actor")
        .map((object) => ({
          actor: object.actor,
          point: this.#removeCameraTransform(this.worldToScreen(
            object.actor.renderX,
            object.actor.renderY,
          )),
        })),
      {
        visibleBounds,
        measureText: (text) => {
          ctx.save();
          ctx.font = "bold 8px ui-monospace, monospace";
          const width = ctx.measureText(text).width;
          ctx.restore();
          return width;
        },
      },
    ).map((layout) => [layout.actorId, layout]));

    if (this.pendingTarget) {
      const targetPoint = this.#removeCameraTransform(this.worldToScreen(this.pendingTarget.x, this.pendingTarget.y));
      polygon(
        ctx,
        [
          [targetPoint.x, targetPoint.y - 5],
          [targetPoint.x + 10, targetPoint.y],
          [targetPoint.x, targetPoint.y + 5],
          [targetPoint.x - 10, targetPoint.y],
        ],
        "rgb(241 191 101 / 68%)",
        PALETTE.yellowDark,
        2,
      );
    }

    for (const object of sortedObjects) {
      const point = this.#removeCameraTransform(this.worldToScreen(
        object.renderX ?? object.x ?? object.actor?.renderX,
        object.renderY ?? object.y ?? object.actor?.renderY,
      ));
      if (object.renderKind === "asset-placement") {
        this.assetRuntime.drawAsset(
          ctx,
          object.renderAssetId,
          point.x,
          point.y,
          { flipX: object.renderFlipX, groundTransform: object.renderGroundFit },
        );
        continue;
      }
      const actor = object.actor;
      if (!underlaidWorkActorIds.has(actor.id)) this.#drawActorEmphasis(ctx, point, actor);
      this.assetRuntime.drawAnimation(
        ctx,
        actorAnimationId(actor, this.reducedMotion),
        actor.animationElapsed,
        point.x,
        point.y + 3,
        { alpha: 1 },
      );
      this.#drawActorName(ctx, point, actor, labelLayouts.get(actor.id));
      for (const effect of this.effects.filter((candidate) => candidate.id === actor.id)) {
        this.assetRuntime.drawAnimation(
          ctx,
          GOOD_CARD_ANIMATION,
          effect.elapsed,
          point.x,
          point.y - 29,
        );
      }
    }
  }

  #drawSeatMarkers(ctx) {
    for (const seat of this.assetLayout?.seats ?? []) {
      const point = this.#localGridPoint(seat.x, seat.y);
      const occupancy = this.seatOccupancy.find((entry) => (
        entry.placementId === seat.placementId && entry.seatId === seat.id
      ));
      const isSelf = occupancy?.playerId === this.selfId;
      const fill = occupancy
        ? isSelf ? "rgb(241 191 101 / 62%)" : "rgb(105 118 121 / 58%)"
        : "rgb(63 139 151 / 46%)";
      const stroke = occupancy ? isSelf ? PALETTE.yellowDark : "#536267" : "#235e69";
      polygon(
        ctx,
        [
          [point.x, point.y - 6],
          [point.x + 11, point.y],
          [point.x, point.y + 6],
          [point.x - 11, point.y],
        ],
        fill,
        stroke,
        1,
      );
      pixelRect(ctx, point.x - 1, point.y - 1, 3, 3, occupancy ? stroke : "#b8e1df");
    }
  }

  #drawActorEmphasis(ctx, point, actor) {
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.fillStyle = actor.color;
    ctx.strokeStyle = actor.id === this.selfId ? "#fff8df" : "#17343a";
    ctx.lineWidth = actor.id === this.selfId ? 3 : 2;
    ctx.beginPath();
    ctx.ellipse(snap(point.x), snap(point.y + 2), actor.id === this.selfId ? 11 : 9, 5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  #drawActorName(ctx, point, actor, layout = null) {
    ctx.save();
    ctx.font = "bold 8px ui-monospace, monospace";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    const text = layout?.text ?? String(actor.name || "成员").slice(0, 16);
    const textWidth = Math.ceil(ctx.measureText(text).width);
    const labelWidth = layout?.width ?? textWidth + 9;
    const visible = this.#visibleLocalBounds(5);
    let labelY = layout?.y ?? snap(point.y - 52);
    if (!layout && labelY - 6 < visible.top) labelY = snap(point.y + 14);
    labelY = clamp(labelY, visible.top + 6, visible.bottom - 6);
    const left = layout?.left ?? clamp(point.x - labelWidth / 2, visible.left, visible.right - labelWidth);
    const dotColor = actor.online ? "#66d58a" : "#7f8a91";
    pixelRect(ctx, left, labelY - 2, 4, 4, "#10242c");
    pixelRect(ctx, left + 1, labelY - 1, 2, 2, dotColor);
    ctx.lineWidth = 3;
    ctx.lineJoin = "miter";
    ctx.strokeStyle = "#10242c";
    ctx.fillStyle = "#ffffff";
    const textX = snap(left + 7);
    if (typeof ctx.strokeText === "function") ctx.strokeText(text, textX, labelY);
    ctx.fillText(text, textX, labelY);
    ctx.restore();
  }

  #visibleLocalBounds(padding = 0) {
    const zoom = Number(this.camera.zoom) || 1;
    return {
      left: LOGICAL_WIDTH / 2 + (padding - LOGICAL_WIDTH / 2 - this.camera.x) / zoom,
      top: LOGICAL_HEIGHT / 2 + (padding - LOGICAL_HEIGHT / 2 - this.camera.y) / zoom,
      right: LOGICAL_WIDTH / 2 + (LOGICAL_WIDTH - padding - LOGICAL_WIDTH / 2 - this.camera.x) / zoom,
      bottom: LOGICAL_HEIGHT / 2 + (LOGICAL_HEIGHT - padding - LOGICAL_HEIGHT / 2 - this.camera.y) / zoom,
    };
  }

  #drawOverlays(ctx) {
    ctx.font = "bold 7px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let x = 0; x < this.columns; x += 1) {
      for (let y = 0; y < this.rows; y += 1) {
        const point = this.#removeCameraTransform(this.worldToScreen(x, y));
        if (this.overlays.blocked && this.blockedCells.has(`${x},${y}`)) {
          ctx.strokeStyle = PALETTE.coralDark;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(point.x - 6, point.y - 4);
          ctx.lineTo(point.x + 6, point.y + 4);
          ctx.moveTo(point.x + 6, point.y - 4);
          ctx.lineTo(point.x - 6, point.y + 4);
          ctx.stroke();
        }
        if (this.overlays.grid) {
          ctx.fillStyle = "rgb(13 34 40 / 72%)";
          ctx.fillText(`${x},${y}`, snap(point.x), snap(point.y + 7));
        }
      }
    }
    if (this.overlays.path && this.acceptedPath.length) {
      ctx.strokeStyle = PALETTE.yellow;
      ctx.lineWidth = 3;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      this.acceptedPath.forEach((step, index) => {
        const point = this.#removeCameraTransform(this.worldToScreen(step.x, step.y));
        if (index === 0) ctx.moveTo(point.x, point.y);
        else ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();
      ctx.setLineDash([]);
      for (const step of this.acceptedPath) {
        const point = this.#removeCameraTransform(this.worldToScreen(step.x, step.y));
        pixelRect(ctx, point.x - 2, point.y - 2, 5, 5, PALETTE.yellow);
      }
    }
    const target = this.acceptedTarget ?? this.pendingTarget;
    if (this.overlays.target && target) {
      const point = this.#removeCameraTransform(this.worldToScreen(target.x, target.y));
      ctx.strokeStyle = PALETTE.coralDark;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(point.x, point.y, 9, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (this.overlays.spawn) {
      for (const [index, spawn] of (this.assetLayout?.spawnPoints ?? []).entries()) {
        const point = this.#localGridPoint(spawn.x, spawn.y);
        ctx.fillStyle = "rgb(59 189 129 / 42%)";
        ctx.strokeStyle = "#16724d";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(point.x, point.y, 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "#ecfff6";
        ctx.fillText(spawn.name || String(index + 1), snap(point.x), snap(point.y - 10));
      }
    }
    if (this.overlays.footprint) {
      for (const placement of (this.assetLayout?.objects ?? [])) {
        for (const cell of placement.footprint) {
          const point = this.#localGridPoint(placement.x + cell.x, placement.y + cell.y);
          ctx.strokeStyle = cell.blocked ? "#e66d61" : "#4f98bd";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(point.x, point.y - TILE_HEIGHT / 2);
          ctx.lineTo(point.x + TILE_WIDTH / 2, point.y);
          ctx.lineTo(point.x, point.y + TILE_HEIGHT / 2);
          ctx.lineTo(point.x - TILE_WIDTH / 2, point.y);
          ctx.closePath();
          ctx.stroke();
        }
      }
    }
    if (this.overlays.depth) {
      for (const placement of (this.assetLayout?.objects ?? [])) {
        const point = this.#localGridPoint(placement.renderX, placement.renderY);
        const copy = `${placement.id}:${placement.depth}`;
        const width = Math.ceil(ctx.measureText(copy).width) + 6;
        pixelRect(ctx, point.x - width / 2, point.y - 18, width, 10, "rgb(16 31 37 / 82%)");
        ctx.fillStyle = "#f5c95f";
        ctx.fillText(copy, snap(point.x), snap(point.y - 13));
      }
      for (const actor of this.actors) {
        const point = this.#localGridPoint(actor.renderX, actor.renderY);
        ctx.fillStyle = "#fff3ce";
        ctx.fillText(`${actor.name}:${(actor.renderX + actor.renderY + 0.7).toFixed(1)}`, point.x, point.y + 13);
      }
    }
  }

  #removeCameraTransform(screenPoint) {
    return {
      x: LOGICAL_WIDTH / 2 + (screenPoint.x - LOGICAL_WIDTH / 2 - this.camera.x) / this.camera.zoom,
      y: LOGICAL_HEIGHT / 2 + (screenPoint.y - LOGICAL_HEIGHT / 2 - this.camera.y) / this.camera.zoom,
    };
  }
}

function structuredCloneSafe(value) {
  if (typeof structuredClone === "function") return structuredClone(value);
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export function createScene(canvas, options) {
  return new IsometricScene(canvas, options);
}
