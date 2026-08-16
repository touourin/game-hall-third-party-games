let inMemoryToken = "";
const SHA256_PATTERN = /^[0-9a-f]{64}$/i;

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function randomIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const random = Math.random().toString(36).slice(2);
  return `spin-${Date.now().toString(36)}-${random}`;
}

function parseBoolean(value) {
  if (value == null) return false;
  return !["", "0", "false", "no"].includes(String(value).toLowerCase());
}

function stripFragmentTokens(url) {
  const fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
  fragment.delete("token");
  fragment.delete("playerToken");
  const nextHash = fragment.toString();
  const next = `${url.pathname}${url.search}${nextHash ? `#${nextHash}` : ""}`;
  globalThis.history?.replaceState?.(globalThis.history.state, "", next);
}

export function parseLaunchContext(locationLike = globalThis.location) {
  const url = new URL(locationLike?.href ?? String(locationLike), globalThis.location?.origin ?? "http://localhost");
  const fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
  const tokenFromFragment = fragment.get("token") || fragment.get("playerToken") || "";
  if (tokenFromFragment) inMemoryToken = tokenFromFragment;
  const token = tokenFromFragment || inMemoryToken;
  const run = url.searchParams.get("run") || fragment.get("run") || fragment.get("reviewRun") || "demo";
  const review = parseBoolean(fragment.get("review")) || fragment.has("reviewRun");

  if (tokenFromFragment && locationLike === globalThis.location) stripFragmentTokens(url);
  return { run, token, review };
}

export function rememberToken(token) {
  inMemoryToken = String(token || "");
}

export function isTrustedReviewCommand(event, {
  parentWindow,
  parentOrigin,
  runId,
} = {}) {
  if (!event || event.source !== parentWindow) return false;
  if (!parentOrigin || parentOrigin === "null" || event.origin !== parentOrigin) return false;
  const message = event.data;
  if (!message || typeof message !== "object" || message.channel !== "codex-review") return false;
  const messageRunId = typeof message.runId === "string" ? message.runId.trim() : "";
  const expectedRunId = String(runId || "").trim();
  return Boolean(messageRunId && expectedRunId && messageRunId === expectedRunId);
}

export class ApiError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code ?? "request_failed";
    this.data = options.data ?? null;
  }
}

export class AssetBindingError extends Error {
  constructor(message, { code = "asset_binding_invalid", data = null } = {}) {
    super(message);
    this.name = "AssetBindingError";
    this.code = code;
    this.data = data;
  }
}

function firstValue(...values) {
  return values.find((value) => value != null && value !== "");
}

function requiredHash(value, label) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!SHA256_PATTERN.test(normalized)) {
    throw new AssetBindingError(`${label} 缺失或格式不正确`, {
      code: "asset_binding_hash_invalid",
      data: { field: label },
    });
  }
  return normalized;
}

/**
 * Normalise the additive run asset contract while leaving an absent/null
 * binding on the legacy renderer. Any non-null binding is strict: incomplete
 * metadata must block the player instead of falling back to procedural art.
 */
export function normaliseBootstrapAssetBinding(bootstrap = {}) {
  const run = bootstrap?.run && typeof bootstrap.run === "object" ? bootstrap.run : {};
  const world = bootstrap?.world && typeof bootstrap.world === "object" ? bootstrap.world : {};
  const topAssetPack = firstValue(bootstrap?.assetPack, bootstrap?.asset_pack);
  const runAssetPack = firstValue(run.assetPack, run.asset_pack);
  const raw = firstValue(topAssetPack, runAssetPack);
  if (topAssetPack && runAssetPack && typeof topAssetPack === "object" && typeof runAssetPack === "object") {
    const identityFields = [
      ["packId", firstValue(topAssetPack.packId, topAssetPack.pack_id), firstValue(runAssetPack.packId, runAssetPack.pack_id)],
      ["manifestSha256", firstValue(topAssetPack.manifestSha256, topAssetPack.manifest_sha256), firstValue(runAssetPack.manifestSha256, runAssetPack.manifest_sha256)],
      ["atlasSha256", firstValue(topAssetPack.atlasSha256, topAssetPack.atlas_sha256), firstValue(runAssetPack.atlasSha256, runAssetPack.atlas_sha256)],
    ];
    if (identityFields.some(([, topValue, runValue]) => String(topValue || "") !== String(runValue || ""))) {
      throw new AssetBindingError("顶层 assetPack 与 run.assetPack 不一致", {
        code: "asset_binding_inconsistent",
      });
    }
  }
  const topLayout = firstValue(world.layout, world.worldLayout, world.world_layout);
  const runLayout = firstValue(run.worldLayout, run.world_layout);
  if (topLayout && runLayout && typeof topLayout === "object" && typeof runLayout === "object") {
    const topIdentity = `${topLayout.id || ""}:${topLayout.sha256 || ""}`;
    const runIdentity = `${runLayout.id || ""}:${runLayout.sha256 || ""}`;
    if (topIdentity !== runIdentity) {
      throw new AssetBindingError("world.layout 与 run.worldLayout 不一致", {
        code: "asset_binding_inconsistent",
      });
    }
  }
  const layout = firstValue(
    topLayout,
    runLayout,
    raw && typeof raw === "object" ? raw.layout : null,
  );
  if (raw == null) {
    if (layout != null) {
      throw new AssetBindingError("world.layout 存在但 assetPack 为空", {
        code: "asset_binding_inconsistent",
      });
    }
    return Object.freeze({ mode: "legacy", legacy: true, layoutId: "legacy" });
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new AssetBindingError("assetPack 必须是对象或 null");
  }

  if (!layout || typeof layout !== "object" || Array.isArray(layout)) {
    throw new AssetBindingError("绑定资产的 Run 缺少 world.layout", {
      code: "asset_binding_layout_missing",
    });
  }

  const manifestUrl = firstValue(raw.manifestUrl, raw.manifest_url, raw.manifest?.url);
  if (!manifestUrl && !raw.manifest && typeof raw.manifestJson !== "string") {
    throw new AssetBindingError("绑定资产的 Run 缺少 manifestUrl", {
      code: "asset_binding_manifest_url_missing",
    });
  }
  const layoutId = String(firstValue(layout.id, layout.layoutId, layout.layout_id) || "").trim();
  if (!layoutId) {
    throw new AssetBindingError("world.layout 缺少 id", {
      code: "asset_binding_layout_id_missing",
    });
  }
  const packId = String(firstValue(raw.packId, raw.pack_id, raw.id) || "").trim();
  if (!packId) {
    throw new AssetBindingError("assetPack 缺少 packId", {
      code: "asset_binding_pack_id_missing",
    });
  }
  const atlasUrl = String(firstValue(raw.atlasUrl, raw.atlas_url) || "").trim();
  if (!atlasUrl) {
    throw new AssetBindingError("assetPack 缺少 atlasUrl", {
      code: "asset_binding_atlas_url_missing",
    });
  }

  return Object.freeze({
    mode: "asset",
    legacy: false,
    releaseId: String(firstValue(raw.releaseId, raw.release_id) || ""),
    packId,
    manifestSha256: requiredHash(
      firstValue(raw.manifestSha256, raw.manifest_sha256, raw.manifestHash, raw.manifest_hash),
      "manifestSha256",
    ),
    manifestUrl: manifestUrl ? String(manifestUrl) : null,
    manifest: raw.manifest && typeof raw.manifest === "object" ? raw.manifest : null,
    manifestJson: typeof raw.manifestJson === "string" ? raw.manifestJson : null,
    atlasSha256: requiredHash(
      firstValue(raw.atlasSha256, raw.atlas_sha256, raw.atlasHash, raw.atlas_hash),
      "atlasSha256",
    ),
    atlasUrl,
    catalogRevision: Number(firstValue(raw.catalogRevision, raw.catalog_revision)) || 0,
    layoutId,
    layoutSha256: requiredHash(
      firstValue(layout.sha256, layout.layoutSha256, layout.layout_sha256),
      "layoutSha256",
    ),
    layout,
  });
}

export class GameNetwork extends EventTarget {
  constructor({ run, token, baseUrl = globalThis.location?.origin } = {}) {
    super();
    this.run = String(run || "demo");
    this.token = String(token || inMemoryToken || "");
    if (this.token) inMemoryToken = this.token;
    this.baseUrl = baseUrl || "http://localhost";
    this.socket = null;
    this.socketState = "idle";
    this.authenticated = false;
    this.pendingMove = null;
    this.pendingWork = null;
    this.sequence = 0;
    this.reconnectAttempt = 0;
    this.reconnectTimer = 0;
    this.artificialDelay = 0;
    this.closedByClient = false;
    this.goodCardInFlight = null;
  }

  async bootstrap() {
    return this.#request("/api/bootstrap", { method: "GET" });
  }

  async spin(idempotencyKey = randomIdempotencyKey()) {
    return this.#request("/api/checkin/spin", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  }

  sendGoodCard(recipientId, idempotencyKey = randomIdempotencyKey()) {
    const normalizedRecipientId = String(recipientId);
    if (this.goodCardInFlight?.recipientId === normalizedRecipientId) {
      return this.goodCardInFlight.promise;
    }
    const request = this.#request("/api/good-cards", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ recipientId: normalizedRecipientId }),
    });
    const inFlight = {
      recipientId: normalizedRecipientId,
      idempotencyKey,
      promise: request.finally(() => {
        if (this.goodCardInFlight === inFlight) this.goodCardInFlight = null;
      }),
    };
    this.goodCardInFlight = inFlight;
    return inFlight.promise;
  }

  connect() {
    if (!this.token) {
      this.#emit("connection", { state: "missing-token" });
      return;
    }
    if (this.socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(this.socket.readyState)) return;
    this.closedByClient = false;
    const httpUrl = new URL(this.baseUrl);
    const protocol = httpUrl.protocol === "https:" ? "wss:" : "ws:";
    const socketUrl = `${protocol}//${httpUrl.host}/ws/${encodeURIComponent(this.run)}`;
    this.socketState = "connecting";
    this.#emit("connection", { state: this.socketState });

    const socket = new WebSocket(socketUrl);
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (socket !== this.socket) return;
      this.socketState = "authenticating";
      this.authenticated = false;
      // Authentication is deliberately the first frame on every connection.
      socket.send(JSON.stringify({ type: "auth", token: this.token }));
      this.#emit("connection", { state: this.socketState });
    });
    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        this.#emit("protocol-error", { code: "invalid_json", raw: event.data });
        return;
      }
      const deliver = () => this.#handleSocketMessage(message);
      if (this.artificialDelay > 0) setTimeout(deliver, this.artificialDelay);
      else deliver();
    });
    socket.addEventListener("error", () => {
      this.#emit("connection", { state: "error" });
    });
    socket.addEventListener("close", (event) => {
      if (socket !== this.socket) return;
      this.authenticated = false;
      this.socketState = "closed";
      this.#emit("connection", { state: "closed", code: event.code, reason: event.reason });
      if (!this.closedByClient) this.#scheduleReconnect();
    });
  }

  sendMove(target) {
    const move = {
      type: "move.target",
      tileX: Math.round(Number(target.x)),
      tileY: Math.round(Number(target.y)),
      clientSeq: ++this.sequence,
    };
    if (!this.authenticated || this.socket?.readyState !== WebSocket.OPEN) {
      this.pendingMove = move;
      return false;
    }
    this.socket.send(JSON.stringify(move));
    return true;
  }

  sendWorkStart({ placementId, seatId } = {}) {
    const command = {
      type: "work.start",
      placementId: String(placementId || ""),
      seatId: String(seatId || ""),
      clientSeq: ++this.sequence,
    };
    if (!command.placementId || !command.seatId) return false;
    return this.#sendWorkCommand(command);
  }

  sendWorkStop() {
    return this.#sendWorkCommand({
      type: "work.stop",
      clientSeq: ++this.sequence,
    });
  }

  setArtificialDelay(value) {
    this.artificialDelay = Math.max(0, Math.min(5000, Math.round(Number(value) || 0)));
  }

  close() {
    this.closedByClient = true;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = 0;
    this.socket?.close(1000, "client closed");
  }

  async #request(path, options) {
    if (!this.token) {
      throw new ApiError("缺少玩家凭证", { status: 401, code: "missing_token" });
    }
    const url = new URL(path, this.baseUrl);
    url.searchParams.set("run", this.run);
    const response = await fetch(url, {
      ...options,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${this.token}`,
        ...(options.headers ?? {}),
      },
    });
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (this.artificialDelay > 0) await delay(this.artificialDelay);
    if (!response.ok || data?.ok === false) {
      throw new ApiError(data?.message || data?.detail || `请求失败（${response.status}）`, {
        status: response.status,
        code: data?.code || "request_failed",
        data,
      });
    }
    return data ?? {};
  }

  #handleSocketMessage(message) {
    const serverSequence = Number(message?.lastClientSeq);
    const hasServerSequence = Number.isSafeInteger(serverSequence) && serverSequence >= 0
      && (message?.type === "auth.ok" || message?.type === "world.snapshot");
    if (hasServerSequence) this.sequence = Math.max(this.sequence, serverSequence);
    if (message?.type === "auth.ok") {
      this.authenticated = true;
      this.socketState = "connected";
      this.reconnectAttempt = 0;
      this.#emit("connection", { state: this.socketState });
      if (this.pendingMove) {
        const commands = [this.pendingMove, this.pendingWork]
          .filter(Boolean)
          .sort((left, right) => left.clientSeq - right.clientSeq);
        for (const command of commands) {
          if (hasServerSequence && command.clientSeq <= serverSequence) {
            command.clientSeq = ++this.sequence;
          }
          this.socket.send(JSON.stringify(command));
        }
        this.pendingMove = null;
        this.pendingWork = null;
      } else if (this.pendingWork) {
        if (hasServerSequence && this.pendingWork.clientSeq <= serverSequence) {
          this.pendingWork.clientSeq = ++this.sequence;
        }
        this.socket.send(JSON.stringify(this.pendingWork));
        this.pendingWork = null;
      }
    }
    this.#emit("message", message);
  }


  #sendWorkCommand(command) {
    if (!this.authenticated || this.socket?.readyState !== WebSocket.OPEN) {
      this.pendingWork = command;
      return false;
    }
    this.socket.send(JSON.stringify(command));
    return true;
  }

  #scheduleReconnect() {
    clearTimeout(this.reconnectTimer);
    const wait = Math.min(8000, 600 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.socketState = "reconnecting";
    this.#emit("connection", { state: this.socketState, retryIn: wait });
    this.reconnectTimer = setTimeout(() => this.connect(), wait);
  }

  #emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}
