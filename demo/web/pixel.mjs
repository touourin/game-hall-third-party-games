export const PALETTE = Object.freeze({
  ink: "#17343a",
  deepInk: "#0d2228",
  grassA: "#8fc6a5",
  grassB: "#84b99b",
  grassEdge: "#4e816f",
  pavingA: "#d2c9aa",
  pavingB: "#c3b99c",
  road: "#75878a",
  roadDark: "#4f666b",
  cream: "#fff2cb",
  coral: "#ed806c",
  coralDark: "#a84e49",
  blue: "#78aabc",
  blueDark: "#446f7f",
  yellow: "#f1bf65",
  yellowDark: "#a87838",
  mint: "#75bd9f",
  mintDark: "#3f7a68",
  brick: "#ba6c59",
  brickDark: "#754640",
  concrete: "#c4c5b9",
  concreteDark: "#777f79",
  glass: "#7eb4bd",
  glassLight: "#b9d9d5",
  soil: "#88634a",
  white: "#fff8df",
});

export function snap(value, size = 1) {
  return Math.round(value / size) * size;
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function hashNumber(...values) {
  let hash = 2166136261;
  for (const value of values.join(":")) {
    hash ^= value.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

export function polygon(ctx, points, fill, stroke = null, lineWidth = 1) {
  if (!points.length) return;
  ctx.beginPath();
  ctx.moveTo(snap(points[0][0]), snap(points[0][1]));
  for (let index = 1; index < points.length; index += 1) {
    ctx.lineTo(snap(points[index][0]), snap(points[index][1]));
  }
  ctx.closePath();
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fill();
  }
  if (stroke) {
    ctx.lineWidth = lineWidth;
    ctx.strokeStyle = stroke;
    ctx.stroke();
  }
}

export function pixelRect(ctx, x, y, width, height, fill) {
  ctx.fillStyle = fill;
  ctx.fillRect(snap(x), snap(y), Math.max(1, snap(width)), Math.max(1, snap(height)));
}

export function drawIsoTile(ctx, cx, cy, width, height, fill, edge = PALETTE.grassEdge) {
  polygon(
    ctx,
    [
      [cx, cy - height / 2],
      [cx + width / 2, cy],
      [cx, cy + height / 2],
      [cx - width / 2, cy],
    ],
    fill,
    edge,
  );
}

export function drawTileEdge(ctx, cx, cy, width, height, depth = 3) {
  polygon(
    ctx,
    [
      [cx - width / 2, cy],
      [cx, cy + height / 2],
      [cx, cy + height / 2 + depth],
      [cx - width / 2, cy + depth],
    ],
    "#426d61",
  );
  polygon(
    ctx,
    [
      [cx, cy + height / 2],
      [cx + width / 2, cy],
      [cx + width / 2, cy + depth],
      [cx, cy + height / 2 + depth],
    ],
    "#31584f",
  );
}

export function drawDeskIsland(ctx, cx, baseY, options = {}) {
  const width = options.width ?? 64;
  const depth = options.depth ?? 23;
  const topY = baseY - 21;
  const surface = options.surface ?? "#c99d69";
  polygon(
    ctx,
    [
      [cx, topY - depth / 2],
      [cx + width / 2, topY],
      [cx, topY + depth / 2],
      [cx - width / 2, topY],
    ],
    surface,
    PALETTE.ink,
    2,
  );
  pixelRect(ctx, cx - width / 2 + 3, topY + 1, width / 2 - 2, 5, "#8d674d");
  pixelRect(ctx, cx - width / 2 + 6, topY + 5, 4, 19, PALETTE.deepInk);
  pixelRect(ctx, cx + width / 2 - 10, topY + 5, 4, 19, PALETTE.deepInk);
  const computerCount = options.computers ?? 3;
  for (let index = 0; index < computerCount; index += 1) {
    const offset = (index - (computerCount - 1) / 2) * 18;
    pixelRect(ctx, cx + offset - 6, topY - 18, 13, 10, PALETTE.deepInk);
    pixelRect(ctx, cx + offset - 4, topY - 16, 9, 6, index % 2 ? PALETTE.glass : PALETTE.glassLight);
    pixelRect(ctx, cx + offset - 1, topY - 8, 3, 7, PALETTE.ink);
    pixelRect(ctx, cx + offset - 5, topY - 2, 11, 2, PALETTE.ink);
  }
  if (options.accent) pixelRect(ctx, cx - width / 2 + 3, topY - 2, 5, 6, options.accent);
}

export function drawMeetingTable(ctx, cx, baseY) {
  const width = 104;
  const depth = 38;
  const topY = baseY - 17;
  polygon(
    ctx,
    [
      [cx, topY - depth / 2],
      [cx + width / 2, topY],
      [cx, topY + depth / 2],
      [cx - width / 2, topY],
    ],
    "#a9b6ad",
    PALETTE.ink,
    2,
  );
  polygon(
    ctx,
    [
      [cx - width / 2, topY],
      [cx, topY + depth / 2],
      [cx, topY + depth / 2 + 5],
      [cx - width / 2, topY + 5],
    ],
    "#687c79",
  );
  pixelRect(ctx, cx - 35, topY + 5, 4, 16, PALETTE.deepInk);
  pixelRect(ctx, cx + 31, topY + 5, 4, 16, PALETTE.deepInk);
  pixelRect(ctx, cx - 7, topY - 13, 14, 9, PALETTE.blueDark);
  pixelRect(ctx, cx - 5, topY - 12, 10, 6, PALETTE.glassLight);
  pixelRect(ctx, cx - 28, topY - 5, 6, 4, PALETTE.yellow);
  pixelRect(ctx, cx + 24, topY + 3, 5, 4, PALETTE.coral);
}

export function drawOfficeSofa(ctx, cx, baseY) {
  pixelRect(ctx, cx - 35, baseY - 25, 70, 19, "#547f89");
  pixelRect(ctx, cx - 38, baseY - 22, 10, 23, PALETTE.blueDark);
  pixelRect(ctx, cx + 28, baseY - 22, 10, 23, PALETTE.blueDark);
  pixelRect(ctx, cx - 29, baseY - 9, 58, 12, PALETTE.blue);
  pixelRect(ctx, cx - 24, baseY - 19, 22, 10, "#8fbfbd");
  pixelRect(ctx, cx + 4, baseY - 19, 19, 10, "#e3b26c");
  pixelRect(ctx, cx - 31, baseY + 2, 7, 4, PALETTE.deepInk);
  pixelRect(ctx, cx + 24, baseY + 2, 7, 4, PALETTE.deepInk);
}

export function drawStorage(ctx, cx, baseY, options = {}) {
  const width = options.width ?? 26;
  const height = options.height ?? 42;
  const color = options.color ?? "#8d9b92";
  pixelRect(ctx, cx - width / 2, baseY - height, width, height, color);
  pixelRect(ctx, cx + width / 2 - 6, baseY - height + 3, 6, height - 3, "#65756f");
  pixelRect(ctx, cx - width / 2, baseY - height, width, 4, PALETTE.deepInk);
  pixelRect(ctx, cx - width / 2, baseY - 22, width - 5, 3, PALETTE.deepInk);
  pixelRect(ctx, cx - 2, baseY - 31, 4, 4, PALETTE.yellow);
  pixelRect(ctx, cx - 2, baseY - 13, 4, 4, PALETTE.coral);
}

export function drawPrinter(ctx, cx, baseY) {
  pixelRect(ctx, cx - 13, baseY - 24, 26, 24, "#9aa5a0");
  pixelRect(ctx, cx - 10, baseY - 31, 20, 12, PALETTE.cream);
  pixelRect(ctx, cx - 8, baseY - 29, 16, 8, "#dae0d6");
  pixelRect(ctx, cx - 9, baseY - 16, 18, 7, PALETTE.deepInk);
  pixelRect(ctx, cx - 6, baseY - 14, 12, 3, PALETTE.glass);
  pixelRect(ctx, cx + 7, baseY - 21, 3, 3, PALETTE.mint);
}

export function drawOfficePlant(ctx, cx, baseY, accent = PALETTE.mint) {
  pixelRect(ctx, cx - 8, baseY - 12, 16, 12, "#b36c4f");
  pixelRect(ctx, cx - 6, baseY - 17, 4, 8, PALETTE.mintDark);
  pixelRect(ctx, cx + 2, baseY - 21, 4, 12, PALETTE.mintDark);
  pixelRect(ctx, cx - 11, baseY - 25, 10, 9, accent);
  pixelRect(ctx, cx + 1, baseY - 31, 10, 13, accent);
  pixelRect(ctx, cx - 3, baseY - 36, 7, 12, "#82b877");
}

export function drawCopyCounter(ctx, cx, baseY) {
  pixelRect(ctx, cx - 48, baseY - 28, 96, 28, "#b9a982");
  pixelRect(ctx, cx - 45, baseY - 25, 90, 5, "#e1d5b1");
  pixelRect(ctx, cx - 30, baseY - 18, 3, 15, PALETTE.ink);
  pixelRect(ctx, cx + 28, baseY - 18, 3, 15, PALETTE.ink);
  pixelRect(ctx, cx - 41, baseY - 38, 22, 14, PALETTE.deepInk);
  pixelRect(ctx, cx - 38, baseY - 35, 16, 9, PALETTE.glassLight);
  pixelRect(ctx, cx + 12, baseY - 39, 15, 16, "#d7d8ca");
  pixelRect(ctx, cx + 14, baseY - 42, 11, 8, PALETTE.white);
}

export function drawWhiteboard(ctx, cx, baseY) {
  pixelRect(ctx, cx - 43, baseY - 54, 86, 47, PALETTE.deepInk);
  pixelRect(ctx, cx - 40, baseY - 51, 80, 40, "#edf0de");
  pixelRect(ctx, cx - 28, baseY - 41, 21, 4, PALETTE.coral);
  pixelRect(ctx, cx - 28, baseY - 32, 35, 3, PALETTE.blue);
  pixelRect(ctx, cx + 13, baseY - 43, 16, 15, "#f2c467");
  pixelRect(ctx, cx - 24, baseY - 8, 48, 3, PALETTE.ink);
}

// The fallback renderer mirrors the sprite rig's eight-phase gait so the two
// modes agree on timing and pose.  Legs alternate around a planted foot and the
// body dips as it takes the load; it does not simply bob as one block.
const LEGACY_WALK_LEG_SWING = [4, 2, 0, -2, -4, -3, 1, 3];
const LEGACY_WALK_ARM_SWING = [-3, -2, 1, 2, 3, 1, 0, -1];
const LEGACY_WALK_BODY_DIP = [0, 1, -1, 0, 0, 1, -1, 0];

export function drawActor(ctx, cx, baseY, actor, frame = 0, options = {}) {
  const reducedMotion = options.reducedMotion ?? false;
  const selected = options.selected ?? false;
  // Follow the animation state the sprite path uses, not the raw server flag —
  // the two disagreed about when walking starts.
  const moving = actor.animationAction === "walk" && !reducedMotion;
  const phases = LEGACY_WALK_LEG_SWING.length;
  const walkFrame = moving ? ((Math.floor(frame) % phases) + phases) % phases : 0;
  const idleFrame = !moving && !reducedMotion ? Math.floor(frame / 4) % 4 : 0;
  const legSwing = moving ? LEGACY_WALK_LEG_SWING[walkFrame] : 0;
  const armSwing = moving ? LEGACY_WALK_ARM_SWING[walkFrame] : idleFrame === 1 ? 1 : 0;
  const bob = moving ? LEGACY_WALK_BODY_DIP[walkFrame] : idleFrame === 1 ? -1 : 0;
  const y = snap(baseY + bob);
  const color = /^#[0-9a-f]{6}$/i.test(actor.color ?? "") ? actor.color : PALETTE.mint;

  // Presence is communicated by the status dot beside the actor name. Keep
  // every body fully opaque so offline teammates never read as grey ghosts.
  ctx.globalAlpha = 1;
  pixelRect(ctx, cx - 8, y + 1, 17, 4, "rgb(10 31 37 / 28%)");
  if (selected) {
    ctx.strokeStyle = PALETTE.yellow;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.ellipse(snap(cx), snap(y), 11, 5, 0, 0, Math.PI * 2);
    ctx.stroke();
  }
  pixelRect(ctx, cx - 6, y - 19, 12, 13, color);
  pixelRect(ctx, cx - 7, y - 22, 14, 5, PALETTE.deepInk);
  pixelRect(ctx, cx - 5, y - 30, 11, 10, "#f0bd91");
  pixelRect(ctx, cx - 6, y - 32, 12, 5, actor.slot % 2 ? "#473a37" : "#293b3e");
  const groundY = snap(baseY);
  pixelRect(ctx, cx - 5 - legSwing, groundY - 6, 4, 7 + bob, PALETTE.deepInk);
  pixelRect(ctx, cx + 2 + legSwing, groundY - 6, 4, 7 + bob, PALETTE.deepInk);
  pixelRect(ctx, cx - 10 - armSwing, y - 17, 4, 8, color);
  pixelRect(ctx, cx + 6 + armSwing, y - 17, 4, 8, color);

  const label = String(actor.name || `成员 ${Number(actor.slot ?? 0) + 1}`).slice(0, 9);
  ctx.font = "bold 8px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const labelWidth = Math.ceil(ctx.measureText(label).width) + 8;
  pixelRect(ctx, cx - labelWidth / 2, y - 45, labelWidth, 11, "rgb(255 248 223 / 92%)");
  pixelRect(ctx, cx - labelWidth / 2, y - 45, 2, 11, color);
  ctx.fillStyle = PALETTE.deepInk;
  ctx.fillText(label, snap(cx + 1), snap(y - 39));
  ctx.globalAlpha = 1;
}

export function drawHeartBurst(ctx, cx, cy, progress) {
  const alpha = clamp(1 - progress, 0, 1);
  const rise = progress * 28;
  ctx.globalAlpha = alpha;
  const size = progress < 0.25 ? 3 : 2;
  pixelRect(ctx, cx - size * 2, cy - rise, size * 2, size * 2, PALETTE.coral);
  pixelRect(ctx, cx, cy - rise, size * 2, size * 2, PALETTE.coral);
  pixelRect(ctx, cx - size, cy - rise + size, size * 2, size * 2, PALETTE.coral);
  pixelRect(ctx, cx - size, cy - rise + size * 3, size * 2, size, PALETTE.coralDark);
  ctx.globalAlpha = 1;
}
