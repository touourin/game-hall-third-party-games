import {
  AssetBindingError,
  GameNetwork,
  isTrustedReviewCommand,
  normaliseBootstrapAssetBinding,
  parseLaunchContext,
} from "./network.mjs";
import {
  AssetIntegrityError,
  assertFrozenLayoutSha256,
  loadPinnedAssetPack,
} from "./asset-runtime.mjs";
import { createScene } from "./scene.mjs";
import { moneyFromCents } from "./ui-format.mjs";

const launch = parseLaunchContext();
const elements = {
  balance: document.querySelector("#balance"),
  checkinButton: document.querySelector("#checkin-button"),
  cardButton: document.querySelector("#card-button"),
  canvas: document.querySelector("#game-canvas"),
  toast: document.querySelector("#toast"),
  wheelDialog: document.querySelector("#wheel-dialog"),
  wheel: document.querySelector("#reward-wheel"),
  wheelResult: document.querySelector("#wheel-result"),
  spinButton: document.querySelector("#spin-button"),
  cardDialog: document.querySelector("#card-dialog"),
  recipientList: document.querySelector("#recipient-list"),
  cardResult: document.querySelector("#card-result"),
  sendCardButton: document.querySelector("#send-card-button"),
  zoomOutButton: document.querySelector("#zoom-out-button"),
  zoomInButton: document.querySelector("#zoom-in-button"),
  connectionStatus: document.querySelector("#connection-status"),
  connectionBlocker: document.querySelector("#connection-blocker"),
  connectionBlockerMessage: document.querySelector("#connection-blocker-message"),
  connectionRetry: document.querySelector("#connection-retry"),
  connectionBack: document.querySelector("#connection-back"),
  interactionHint: document.querySelector("#interaction-hint"),
};

const state = {
  balanceCents: 0,
  selfId: null,
  wheelValues: [1, 1, 2, 2, 3, 5, 10, 20],
  spinAvailable: true,
  goodCardAvailable: true,
  selectedRecipientId: "",
  spinPending: false,
  cardPending: false,
  wheelTurns: 0,
  revision: 0,
  bootstrapSnapshot: null,
  asset: {
    status: "loading",
    binding: null,
    packId: null,
    releaseId: null,
    manifestSha256: null,
    atlasSha256: null,
    layoutId: null,
    layoutSha256: null,
    error: null,
  },
  replay: {
    before: null,
    events: [],
    startedAt: 0,
    running: false,
    queuedLive: [],
  },
};

const network = new GameNetwork(launch);
const scene = createScene(elements.canvas, {
  initialRendererMode: "loading",
  onMoveTarget(target) {
    network.sendMove(target);
    postGameMessage("event", {
      eventType: "move.requested",
      data: { tileX: target.x, tileY: target.y },
    });
  },
  onWorkStart(target) {
    network.sendWorkStart(target);
    postGameMessage("event", { eventType: "work.requested", data: safeEventData(target) });
  },
  onWorkStop() {
    network.sendWorkStop();
    postGameMessage("event", { eventType: "work.stop-requested", data: {} });
  },
  onCameraChange() {
    postDirectorState();
  },
  onInteractionError(message) {
    showToast(message, true);
  },
  onAssetError(error) {
    blockAssetRun(error, state.asset.binding);
  },
});

let toastTimer = 0;
let replayTimer = 0;
let loadPromise = null;
let lastDirectorStateKey = "";
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const parentOrigin = (() => {
  try {
    return document.referrer ? new URL(document.referrer).origin : window.location.origin;
  } catch {
    return window.location.origin;
  }
})();
const parentTargetOrigin = parentOrigin === "null" ? "*" : parentOrigin;

const CONNECTION_COPY = Object.freeze({
  idle: "等待连接",
  loading: "正在准备办公室…",
  connecting: "正在连接…",
  authenticating: "正在验证身份…",
  connected: "已连接",
  reconnecting: "连接中断，正在重试…",
  closed: "连接已断开",
  error: "连接失败",
  blocked: "资产未能安全加载",
  "missing-token": "缺少玩家凭证",
});

function setConnectionStatus(connectionState, copy = CONNECTION_COPY[connectionState] || String(connectionState || "")) {
  if (!elements.connectionStatus) return;
  elements.connectionStatus.dataset.state = String(connectionState || "idle");
  elements.connectionStatus.textContent = copy;
}

function setConnectionBlocker(blocked, message = "") {
  if (elements.connectionBlocker) elements.connectionBlocker.hidden = !blocked;
  if (elements.connectionBlockerMessage) elements.connectionBlockerMessage.textContent = String(message || "");
}

function setCoreInteractionEnabled(enabled) {
  const allow = Boolean(enabled);
  scene.setInteractionEnabled(allow);
  elements.checkinButton.disabled = !allow;
  elements.cardButton.disabled = !allow;
  elements.zoomOutButton.disabled = !allow;
  elements.zoomInButton.disabled = !allow;
  if (!allow) {
    elements.spinButton.disabled = true;
    elements.sendCardButton.disabled = true;
  }
}

function assetMessagePayload() {
  const ready = ["ready", "legacy"].includes(state.asset.status);
  return {
    ready,
    legacy: state.asset.status === "legacy",
    packId: state.asset.packId,
    releaseId: state.asset.releaseId,
    manifestSha256: state.asset.manifestSha256,
    atlasSha256: state.asset.atlasSha256,
    layoutId: state.asset.layoutId,
    layoutSha256: state.asset.layoutSha256,
    error: state.asset.error,
  };
}

function postAssetState() {
  const asset = assetMessagePayload();
  const director = scene.getDirectorState();
  postGameMessage("assetReady", {
    asset,
    assetReady: asset.ready,
    manifestSha256: asset.manifestSha256,
    layoutSha256: asset.layoutSha256,
    camera: director.camera,
    activity: director.activity,
    target: director.target,
    seatOccupancy: director.seatOccupancy,
    state: director,
  });
}

function assetErrorCopy(error) {
  const code = error?.code;
  if (code === "asset_hash_mismatch") return "资产文件校验不一致。为了避免两个玩家看到不同画面，本局已停止加载。";
  if (code === "asset_layout_hash_mismatch") return "地图快照校验不一致，本局已停止加载。";
  if (code === "asset_origin_mismatch") return "资产地址不属于当前游戏，已阻止加载。";
  if (code === "asset_binding_layout_missing" || code === "asset_binding_layout_id_missing") {
    return "本局没有完整的办公室布局，暂时无法进入。";
  }
  return error?.message ? `办公室资产加载失败：${error.message}` : "办公室资产加载失败，请重试或返回验收台。";
}

function asAssetFailure(error, binding) {
  if (error?.assetFailure) return error;
  const wrapped = error instanceof Error ? error : new Error(String(error || "资产加载失败"));
  wrapped.assetFailure = true;
  wrapped.assetBinding = binding ?? null;
  return wrapped;
}

function blockAssetRun(rawError, binding = state.asset.binding) {
  const error = asAssetFailure(rawError, binding);
  state.asset = {
    status: "blocked",
    binding: binding ?? null,
    packId: binding?.packId || null,
    releaseId: binding?.releaseId || null,
    manifestSha256: binding?.manifestSha256 || null,
    atlasSha256: binding?.atlasSha256 || null,
    layoutId: binding?.layoutId || null,
    layoutSha256: binding?.layoutSha256 || null,
    error: error.message || "资产加载失败",
  };
  if (scene.getAssetState().mode !== "blocked") scene.blockAssetRun(error);
  network.close();
  setCoreInteractionEnabled(false);
  const message = assetErrorCopy(error);
  setConnectionStatus("blocked");
  setConnectionBlocker(true, message);
  if (elements.connectionRetry) elements.connectionRetry.disabled = false;
  postAssetState();
  postGameMessage("event", {
    eventType: "asset.blocked",
    data: {
      code: error.code || "asset_load_failed",
      packId: state.asset.packId,
      manifestSha256: state.asset.manifestSha256,
      layoutId: state.asset.layoutId,
    },
  });
  showToast(message, true);
}

function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = String(message || "");
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 2600);
}

function setBalanceFrom(source) {
  const candidate = source?.balanceCents ?? source?.player?.balanceCents;
  if (Number.isFinite(Number(candidate))) {
    state.balanceCents = Number(candidate);
  } else {
    const dollars = source?.balance ?? source?.player?.balance;
    if (Number.isFinite(Number(dollars))) state.balanceCents = Math.round(Number(dollars) * 100);
  }
  elements.balance.value = moneyFromCents(state.balanceCents);
  elements.balance.textContent = moneyFromCents(state.balanceCents);
}

function humanError(error) {
  const code = error?.code ?? error?.data?.code;
  const messages = {
    missing_token: "这个链接缺少玩家凭证",
    target_blocked: "那里放着家具，换一块空地吧",
    target_out_of_bounds: "移动目标超出办公室范围",
    path_unavailable: "现在走不到那个位置",
    work_seat_missing: "这个工作位不存在",
    seat_not_found: "这个工作位不存在",
    work_seat_occupied: "这个座位正在使用中",
    seat_occupied: "这个座位正在使用中",
    work_path_unavailable: "现在走不到这个座位",
    spin_unavailable: "今天已经签过到了",
    good_card_unavailable: "今天的好人卡已经送出",
    recipient_invalid: "请选择另一位同伴",
  };
  return messages[code] || error?.message || "操作没有完成，请稍后再试";
}

function openDialog(dialog) {
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
}

function bindDialogBackdrop(dialog) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

function renderWheelLabels() {
  elements.wheel.querySelectorAll(".wheel-label").forEach((label) => label.remove());
  state.wheelValues.forEach((reward, index) => {
    const label = document.createElement("span");
    label.className = "wheel-label";
    label.textContent = `$${reward}`;
    const angle = index * (360 / state.wheelValues.length) + 360 / state.wheelValues.length / 2;
    label.style.transform = `rotate(${angle}deg) translateY(-88px) rotate(${-angle}deg)`;
    elements.wheel.append(label);
  });
}

function renderWheelState() {
  const interactive = ["ready", "legacy"].includes(state.asset.status);
  elements.spinButton.disabled = !interactive || state.spinPending || !state.spinAvailable;
  elements.spinButton.textContent = state.spinPending
    ? "正在转…"
    : state.spinAvailable
      ? "开始转盘"
      : "今日已签到";
  elements.checkinButton.dataset.complete = String(!state.spinAvailable);
  elements.checkinButton.textContent = state.spinAvailable ? "每日签到" : "已签到 ✓";
}

function renderRecipients() {
  const actors = scene.getActors();
  const recipients = actors.filter((actor) => actor.real && actor.id !== state.selfId);
  elements.recipientList.replaceChildren();
  if (!recipients.length) {
    const empty = document.createElement("p");
    empty.className = "recipient-empty";
    empty.textContent = launch.token ? "还没有可选择的同伴" : "连接到验收局后可选择同伴";
    elements.recipientList.append(empty);
  } else {
    for (const actor of recipients) {
      const option = document.createElement("div");
      option.className = "recipient-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "recipient";
      input.id = `recipient-${actor.slot}`;
      input.value = actor.id;
      input.checked = actor.id === state.selectedRecipientId;
      input.disabled = !state.goodCardAvailable || state.cardPending;
      const label = document.createElement("label");
      label.htmlFor = input.id;
      const dot = document.createElement("span");
      dot.className = "recipient-dot";
      dot.style.setProperty("--actor-color", actor.color);
      const name = document.createElement("span");
      name.textContent = actor.name;
      label.append(dot, name);
      option.append(input, label);
      elements.recipientList.append(option);
    }
  }
  elements.sendCardButton.disabled =
    !["ready", "legacy"].includes(state.asset.status)
    || state.cardPending || !state.goodCardAvailable || !state.selectedRecipientId || !recipients.length;
  elements.sendCardButton.textContent = state.cardPending
    ? "正在送出…"
    : state.goodCardAvailable
      ? "送出好人卡"
      : "今日已送出";
  elements.cardButton.dataset.complete = String(!state.goodCardAvailable);
  elements.cardButton.textContent = state.goodCardAvailable ? "今日好人卡" : "已送卡 ✓";
}

async function prepareAssetRenderer(payload) {
  let binding;
  try {
    binding = normaliseBootstrapAssetBinding(payload);
  } catch (error) {
    throw asAssetFailure(error, null);
  }

  if (binding.legacy) {
    if (state.asset.status === "ready" && state.asset.manifestSha256) {
      throw asAssetFailure(new AssetBindingError("刷新后资产绑定消失，已阻止本局切换到旧场景", {
        code: "asset_binding_changed",
      }), state.asset.binding);
    }
    scene.useLegacyRenderer();
    state.asset = {
      status: "legacy",
      binding,
      packId: null,
      releaseId: null,
      manifestSha256: null,
      atlasSha256: null,
      layoutId: "legacy",
      layoutSha256: null,
      error: null,
    };
    setConnectionBlocker(false);
    setCoreInteractionEnabled(true);
    postAssetState();
    return binding;
  }

  if (state.asset.status === "ready") {
    const unchanged = state.asset.packId === binding.packId
      && state.asset.releaseId === (binding.releaseId || null)
      && state.asset.manifestSha256 === binding.manifestSha256
      && state.asset.atlasSha256 === binding.atlasSha256
      && state.asset.layoutId === binding.layoutId
      && state.asset.layoutSha256 === (binding.layoutSha256 || null);
    if (!unchanged) {
      throw asAssetFailure(new AssetBindingError("Run 的固定资产版本在刷新时发生变化", {
        code: "asset_binding_changed",
      }), binding);
    }
    postAssetState();
    return binding;
  }

  state.asset = {
    status: "loading",
    binding,
    packId: binding.packId || null,
    releaseId: binding.releaseId || null,
    manifestSha256: binding.manifestSha256,
    atlasSha256: binding.atlasSha256,
    layoutId: binding.layoutId,
    layoutSha256: binding.layoutSha256 || null,
    error: null,
  };
  scene.setLoading();
  setCoreInteractionEnabled(false);
  setConnectionBlocker(false);
  setConnectionStatus("loading", "正在校验办公室资产…");
  let runtime = null;
  try {
    await assertFrozenLayoutSha256(binding.layout, binding.layoutSha256);
    runtime = await loadPinnedAssetPack(binding, {
      baseUrl: network.baseUrl,
      allowedOrigin: new URL(network.baseUrl).origin,
    });
    if (binding.packId && runtime.manifest.id !== binding.packId) {
      throw new AssetIntegrityError("manifest packId 与 Run 绑定不一致", {
        code: "asset_pack_mismatch",
        expected: binding.packId,
        actual: runtime.manifest.id,
      });
    }
    const atlas = runtime.manifest.atlases[0];
    if (!atlas || runtime.manifest.atlases.length !== 1) {
      throw new AssetIntegrityError("运行时资产包必须只有一个 atlas", { code: "asset_atlas_contract" });
    }
    if (binding.atlasUrl) {
      const expectedAtlasUrl = new URL(binding.atlasUrl, network.baseUrl).href;
      const manifestDocumentUrl = new URL(binding.manifestUrl || "./manifest.json", network.baseUrl);
      const manifestAtlasUrl = new URL(atlas.source, manifestDocumentUrl).href;
      if (expectedAtlasUrl !== manifestAtlasUrl) {
        throw new AssetIntegrityError("manifest atlasUrl 与 Run 绑定不一致", {
          code: "asset_atlas_url_mismatch",
          expected: expectedAtlasUrl,
          actual: manifestAtlasUrl,
        });
      }
    }
    scene.setAssetRuntime(runtime, binding.layout, payload.world ?? {});
    runtime = null;
  } catch (error) {
    runtime?.dispose?.();
    throw asAssetFailure(error, binding);
  }

  state.asset = {
    status: "ready",
    binding,
    packId: binding.packId || null,
    releaseId: binding.releaseId || null,
    manifestSha256: binding.manifestSha256,
    atlasSha256: binding.atlasSha256,
    layoutId: binding.layoutId,
    layoutSha256: binding.layoutSha256 || null,
    error: null,
  };
  setConnectionBlocker(false);
  setCoreInteractionEnabled(true);
  postAssetState();
  return binding;
}

function applyBootstrap(payload) {
  state.selfId = payload.player?.id == null ? null : String(payload.player.id);
  state.wheelValues = Array.isArray(payload.world?.wheel) && payload.world.wheel.length
    ? payload.world.wheel.map(Number)
    : state.wheelValues;
  state.spinAvailable = payload.player?.spin?.available !== false;
  state.goodCardAvailable = payload.player?.goodCard?.available !== false;
  state.selectedRecipientId = "";
  state.revision = Number(payload.run?.revision) || state.revision;
  scene.applyBootstrap(payload);
  setBalanceFrom(payload);
  renderWheelLabels();
  renderWheelState();
  renderRecipients();
  state.bootstrapSnapshot = scene.snapshot();
  const receivedCards = Array.isArray(payload.goodCards)
    ? payload.goodCards.filter((card) => String(card.recipientId ?? card.recipient_id) === state.selfId)
    : [];
  const latestReceived = receivedCards.at(-1);
  if (latestReceived) {
    const senderId = String(latestReceived.senderId ?? latestReceived.sender_id ?? "");
    const sender = scene.getActors().find((actor) => actor.id === senderId);
    const senderName = latestReceived.senderName ?? latestReceived.sender_name ?? sender?.name ?? "一位同伴";
    scene.addGoodCardEffect(state.selfId);
    showToast(`${senderName} 今天送给你一张好人卡`);
  }
  postDirectorState(true);
}

function safeEventData(value) {
  if (!value || typeof value !== "object") return value;
  const clone = Array.isArray(value) ? [] : {};
  for (const [key, item] of Object.entries(value)) {
    if (/token|authorization|secret/i.test(key)) continue;
    clone[key] = item && typeof item === "object" ? safeEventData(item) : item;
  }
  return clone;
}

function postGameMessage(type, details = {}) {
  if (window.parent === window) return;
  window.parent.postMessage(
    {
      channel: "codex-game",
      type,
      runId: launch.run,
      revision: state.revision,
      ...details,
    },
    parentTargetOrigin,
  );
}

function postDirectorState(force = false) {
  const director = scene.getDirectorState();
  const report = {
    playerId: state.selfId,
    camera: director.camera,
    activity: director.activity,
    target: director.target,
    seatOccupancy: director.seatOccupancy,
    layout: director.layout,
    asset: assetMessagePayload(),
  };
  const key = JSON.stringify(report);
  if (!force && key === lastDirectorStateKey) return;
  lastDirectorStateKey = key;
  postGameMessage("state", {
    camera: director.camera,
    activity: director.activity,
    target: director.target,
    seatOccupancy: director.seatOccupancy,
    layout: director.layout,
    player: { id: state.selfId, activity: director.activity },
    state: report,
    asset: report.asset,
  });
}

function recordReplayEvent(message) {
  const now = performance.now();
  if (["move.accepted", "work.accepted"].includes(message.type)) {
    state.replay.before = scene.snapshot();
    state.replay.events = [];
    state.replay.startedAt = now;
  }
  if (state.replay.before
    && ["move.accepted", "work.accepted", "work.stopped", "world.positions"].includes(message.type)) {
    state.replay.events.push({ at: now - state.replay.startedAt, message: safeEventData(message) });
    if (state.replay.events.length > 90) state.replay.events.shift();
  }
}

function applyLiveMessage(message) {
  recordReplayEvent(message);
  const previousActivity = scene.getDirectorState().activity;
  scene.applyNetworkMessage(message);
  const currentActivity = scene.getDirectorState().activity;
  if (previousActivity?.phase === "reserved" && currentActivity?.phase === "active") {
    showToast("已到达座位，开始工作");
  }
  if (message.type === "economy.changed" && String(message.playerId) === state.selfId) {
    setBalanceFrom(message);
  }
  if (message.type === "good-card.created") {
    const card = message.card ?? message.goodCard ?? message;
    if (String(card.senderId ?? card.sender_id ?? "") === state.selfId) {
      state.goodCardAvailable = false;
      renderRecipients();
    }
    if (String(card.recipientId ?? card.recipient_id ?? "") === state.selfId) {
      const senderId = String(card.senderId ?? card.sender_id ?? "");
      const sender = scene.getActors().find((actor) => actor.id === senderId);
      const senderName = card.senderName ?? card.sender_name ?? sender?.name ?? "一位同伴";
      showToast(`${senderName} 送给你一张好人卡`);
    }
  }
  if (message.type === "work.accepted") {
    showToast(message.active ? "已在桌边开始工作" : "正在前往这个工作位…");
  }
  if (message.type === "work.stopped") showToast("已离开工作位");
  if (message.type === "world.snapshot") renderRecipients();
  if (message.type === "review.daily-reset") {
    refreshBootstrap().catch((error) => {
      if (error?.assetFailure) blockAssetRun(error, error.assetBinding);
      else showToast(humanError(error), true);
    });
  }
  if (message.type === "error") showToast(humanError(message), true);
  if (Number.isFinite(Number(message.revision))) state.revision = Number(message.revision);
  postDirectorState();
  postGameMessage("event", {
    eventType: message.type || "unknown",
    eventId: message.eventId ?? message.id,
    data: safeEventData(message),
  });
}

async function replayLast(providedEvents) {
  if (state.replay.running) return;
  const events = Array.isArray(providedEvents) && providedEvents.length
    ? providedEvents.map((entry, index) => ({
        at: Number(entry.at ?? entry.offset ?? index * 100),
        message: entry.message ?? entry,
      }))
    : state.replay.events;
  const before = state.replay.before ?? state.bootstrapSnapshot;
  if (!before || !events.length) {
    postGameMessage("event", { eventType: "replay.empty", data: {} });
    showToast("还没有可以重放的移动");
    return;
  }
  clearTimeout(replayTimer);
  state.replay.running = true;
  state.replay.queuedLive = [];
  const liveSnapshot = scene.snapshot();
  scene.restore(before);
  scene.setPaused(false);
  postGameMessage("event", { eventType: "replay.started", data: { count: events.length } });

  const baseAt = Number(events[0].at) || 0;
  let previousAt = baseAt;
  for (const entry of events) {
    const at = Math.max(previousAt, Number(entry.at) || previousAt);
    const wait = reducedMotion ? 0 : Math.min(300, Math.max(0, at - previousAt) / scene.playbackRate);
    if (wait > 0) await new Promise((resolve) => { replayTimer = window.setTimeout(resolve, wait); });
    scene.applyNetworkMessage(entry.message);
    previousAt = at;
  }
  if (!reducedMotion) await new Promise((resolve) => { replayTimer = window.setTimeout(resolve, 320); });
  scene.restore(liveSnapshot);
  for (const queued of state.replay.queuedLive) applyLiveMessage(queued);
  state.replay.queuedLive = [];
  state.replay.running = false;
  postGameMessage("event", { eventType: "replay.completed", data: { count: events.length } });
}

async function refreshBootstrap() {
  const payload = await network.bootstrap();
  await prepareAssetRenderer(payload);
  applyBootstrap(payload);
  return payload;
}

async function loadGame({ retry = false } = {}) {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    if (!launch.token) {
      scene.setLoading();
      setCoreInteractionEnabled(false);
      setConnectionStatus("missing-token");
      setConnectionBlocker(true, "这个玩家链接缺少凭证，请从验收台重新打开。");
      renderWheelLabels();
      renderRecipients();
      postGameMessage("ready", {
        connected: false,
        state: "missing-token",
        asset: assetMessagePayload(),
      });
      return;
    }
    if (elements.connectionRetry) elements.connectionRetry.disabled = true;
    if (retry) {
      state.asset.error = null;
      scene.setLoading();
    }
    setCoreInteractionEnabled(false);
    setConnectionBlocker(false);
    setConnectionStatus("loading");
    try {
      const payload = await refreshBootstrap();
      network.connect();
      setConnectionStatus(network.authenticated ? "connected" : "connecting");
      postGameMessage("state", {
        connected: network.authenticated,
        asset: assetMessagePayload(),
        state: {
          playerId: state.selfId,
          balanceCents: state.balanceCents,
          spinAvailable: state.spinAvailable,
          goodCardAvailable: state.goodCardAvailable,
        },
      });
    } catch (error) {
      if (error?.assetFailure || error instanceof AssetBindingError || error instanceof AssetIntegrityError) {
        blockAssetRun(error, error.assetBinding ?? state.asset.binding);
      } else {
        network.close();
        scene.setLoading();
        setCoreInteractionEnabled(false);
        setConnectionStatus("error");
        setConnectionBlocker(true, `暂时无法读取本局数据：${humanError(error)}`);
        showToast(humanError(error), true);
      }
    } finally {
      if (elements.connectionRetry) elements.connectionRetry.disabled = false;
      postGameMessage("ready", {
        connected: network.authenticated,
        state: state.asset.status === "blocked" ? "blocked" : network.socketState,
        asset: assetMessagePayload(),
        assetReady: assetMessagePayload().ready,
        manifestSha256: state.asset.manifestSha256,
      });
    }
  })();
  try {
    await loadPromise;
  } finally {
    loadPromise = null;
  }
}

elements.checkinButton.addEventListener("click", () => {
  elements.wheelResult.textContent = state.spinAvailable
    ? "转一下，领取今天的工作室分红。"
    : "今天已经签到，明天再来。";
  renderWheelState();
  openDialog(elements.wheelDialog);
});

elements.spinButton.addEventListener("click", async () => {
  if (state.spinPending || !state.spinAvailable) return;
  state.spinPending = true;
  renderWheelState();
  elements.wheelResult.textContent = "正在确认今天的分红…";
  try {
    const result = await network.spin();
    state.spinAvailable = false;
    setBalanceFrom(result);
    if (Number.isFinite(Number(result.revision))) state.revision = Math.max(state.revision, Number(result.revision));
    const count = Math.max(1, state.wheelValues.length);
    const wheelIndex = Math.max(0, Math.min(count - 1, Number(result.wheelIndex) || 0));
    state.wheelTurns += 5;
    const segment = 360 / count;
    const rotation = state.wheelTurns * 360 - (wheelIndex * segment + segment / 2);
    elements.wheel.style.transform = `rotate(${rotation}deg)`;
    const rewardCents = Number(result.rewardCents);
    const reward = Number.isFinite(rewardCents) ? moneyFromCents(rewardCents) : `$${Number(result.reward) || 0}`;
    elements.wheelResult.textContent = result.alreadySpun
      ? `今天已经领取过，当前余额 ${moneyFromCents(state.balanceCents)}。`
      : `签到完成：获得 ${reward}，当前余额 ${moneyFromCents(state.balanceCents)}。`;
    postGameMessage("event", { eventType: "checkin.completed", data: safeEventData(result) });
  } catch (error) {
    elements.wheelResult.textContent = humanError(error);
    showToast(humanError(error), true);
  } finally {
    state.spinPending = false;
    renderWheelState();
  }
});

elements.cardButton.addEventListener("click", () => {
  elements.cardResult.textContent = state.goodCardAvailable
    ? "每天一张，只表达感谢，不增加战斗力。"
    : "今天的好人卡已经送出。";
  renderRecipients();
  openDialog(elements.cardDialog);
});

elements.recipientList.addEventListener("change", (event) => {
  if (event.target instanceof HTMLInputElement && event.target.name === "recipient") {
    state.selectedRecipientId = event.target.value;
    renderRecipients();
  }
});

elements.sendCardButton.addEventListener("click", async () => {
  if (state.cardPending || !state.goodCardAvailable || !state.selectedRecipientId) return;
  state.cardPending = true;
  renderRecipients();
  elements.cardResult.textContent = "正在送出这份感谢…";
  try {
    const result = await network.sendGoodCard(state.selectedRecipientId);
    state.goodCardAvailable = false;
    if (Number.isFinite(Number(result.revision))) state.revision = Math.max(state.revision, Number(result.revision));
    const actor = scene.getActors().find((candidate) => candidate.id === state.selectedRecipientId);
    scene.addGoodCardEffect(state.selectedRecipientId);
    elements.cardResult.textContent = `已把今天的好人卡送给 ${actor?.name ?? "同伴"}。`;
    postGameMessage("event", { eventType: "good-card.sent", data: safeEventData(result) });
  } catch (error) {
    elements.cardResult.textContent = humanError(error);
    showToast(humanError(error), true);
  } finally {
    state.cardPending = false;
    renderRecipients();
  }
});

elements.zoomOutButton.addEventListener("click", () => scene.zoomBy(0.84));
elements.zoomInButton.addEventListener("click", () => scene.zoomBy(1.19));

network.addEventListener("message", (event) => {
  const message = event.detail;
  if (state.replay.running) state.replay.queuedLive.push(message);
  else applyLiveMessage(message);
});

network.addEventListener("connection", (event) => {
  const connection = event.detail;
  const connected = connection.state === "connected";
  if (state.asset.status !== "blocked") setConnectionStatus(connection.state);
  postGameMessage("connection", {
    connected,
    state: connection.state,
    asset: assetMessagePayload(),
  });
});

network.addEventListener("protocol-error", (event) => {
  postGameMessage("event", { eventType: "protocol.error", data: event.detail });
});

window.addEventListener("message", (event) => {
  if (!isTrustedReviewCommand(event, {
    parentWindow: window.parent,
    parentOrigin,
    runId: launch.run,
  })) return;
  const message = event.data;
  if (Number.isFinite(Number(message.revision))) state.revision = Math.max(state.revision, Number(message.revision));
  switch (message.type) {
    case "speed":
      scene.setPlaybackRate(message.value);
      break;
    case "pause":
      scene.setPaused(message.value ?? message.action === "pause");
      break;
    case "overlays":
    case "overlay":
      scene.setOverlays(message.value ?? message.overlays);
      break;
    case "camera":
      scene.setCameraPreset(message.value?.preset ?? message.preset ?? message.value);
      break;
    case "delay":
      network.setArtificialDelay(message.value ?? message.delayMs);
      break;
    case "replay":
      replayLast(message.events);
      break;
    default:
      return;
  }
  postGameMessage("event", {
    eventType: `review.${message.type}`,
    eventId: message.commandId,
    data: safeEventData({ value: message.value, action: message.action }),
  });
  postDirectorState(true);
});

bindDialogBackdrop(elements.wheelDialog);
bindDialogBackdrop(elements.cardDialog);
elements.connectionRetry?.addEventListener("click", () => loadGame({ retry: true }));
renderWheelLabels();
renderWheelState();
renderRecipients();
loadGame();

window.addEventListener("pagehide", () => {
  network.close();
  scene.destroy();
}, { once: true });

export { network, scene };
