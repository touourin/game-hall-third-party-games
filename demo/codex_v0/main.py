from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .asset_lab import (
    MAX_INPUT_BYTES,
    MAX_REVIEW_BATCH_ITEMS,
    SHA256_RE,
    AssetLab,
    AssetLabError,
)
from .config import Settings
from .db import Database
from .identity import (
    IdentityError,
    PlayerIdentity,
    RunControllerBoundary,
    RunTokenIdentityBoundary,
)
from .realtime import RealtimeManager
from .service import GameService, GameServiceError
from .world_layout import WorldLayoutRegistry


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        return None
    return token.strip()


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str | None = Field(default=None, max_length=80)
    layout_id: str = Field(alias="layoutId", min_length=1, max_length=128)


class AdvanceDayRequest(BaseModel):
    days: int = Field(default=1, ge=1, le=365)


class ForceWheelRequest(BaseModel):
    reward: int | None = None


class PauseRequest(BaseModel):
    paused: bool


class SpeedRequest(BaseModel):
    speed: float = Field(ge=0.1, le=8.0)


class GoodCardRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    recipient_id: str = Field(alias="recipientId", min_length=1, max_length=64)


class AssetReviewRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=16)
    note: str = Field(default="", max_length=2000)
    expected_revision: int = Field(alias="expectedRevision", ge=0)


class AssetBatchReviewItem(BaseModel):
    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)
    version_id: str = Field(alias="versionId", min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=16)


class AssetBatchReviewRequest(BaseModel):
    items: list[AssetBatchReviewItem] = Field(min_length=1, max_length=MAX_REVIEW_BATCH_ITEMS)
    note: str = Field(default="", max_length=2000)
    expected_revision: int = Field(alias="expectedRevision", ge=0)


class AssetActivationRequest(BaseModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)


def create_app(
    settings: Settings | None = None,
    now: Callable[[], datetime] | None = None,
) -> FastAPI:
    active_settings = settings or Settings.from_env()
    database = Database(active_settings.database_path)
    asset_lab = AssetLab(active_settings.database_path.parent)
    world_layouts = WorldLayoutRegistry()
    service = GameService(
        database,
        active_settings,
        now=now,
        asset_lab=asset_lab,
        world_layouts=world_layouts,
    )
    player_identities = RunTokenIdentityBoundary(database)
    controller_identities = RunControllerBoundary(database)
    realtime = RealtimeManager(service, active_settings)
    asset_csrf_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        asset_lab.bootstrap()
        await realtime.start()
        try:
            yield
        finally:
            await realtime.stop()

    application = FastAPI(
        title="Codex v0 Shared Pixel Scene",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = active_settings
    application.state.database = database
    application.state.service = service
    application.state.identity_boundary = player_identities
    application.state.controller_boundary = controller_identities
    application.state.realtime = realtime
    application.state.asset_lab = asset_lab
    application.state.world_layouts = world_layouts

    @application.exception_handler(GameServiceError)
    async def game_service_error(_: Request, error: GameServiceError):
        return JSONResponse(
            status_code=error.status_code,
            content={"ok": False, "error": error.code, "detail": str(error)},
        )

    @application.exception_handler(AssetLabError)
    async def asset_lab_error(_: Request, error: AssetLabError):
        if error.code == "revision.conflict":
            status_code = status.HTTP_409_CONFLICT
        elif error.code.endswith(".not_found"):
            status_code = status.HTTP_404_NOT_FOUND
        elif error.code in {"image.too_large", "metadata.too_large"}:
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        elif error.code.startswith("storage.") or error.code == "import.failed":
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        else:
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return JSONResponse(
            status_code=status_code,
            content={"ok": False, "error": error.code, "detail": str(error), "details": error.details},
        )

    def is_loopback_request(request: Request) -> bool:
        client_host = request.client.host if request.client is not None else ""
        if client_host.casefold() in {"localhost", "testclient"}:
            return True
        try:
            return ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            return False

    def require_asset_loopback(request: Request) -> None:
        if not is_loopback_request(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="资产实验室只接受本机访问",
            )

    def require_asset_write(
        request: Request,
        x_csrf_token: Annotated[
            str | None, Header(alias="X-CSRF-Token")
        ] = None,
    ) -> None:
        require_asset_loopback(request)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            request_host = request.headers.get("host", "")
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.netloc.casefold() != request_host.casefold()
                or parsed.scheme.casefold() != request.url.scheme.casefold()
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="资产写入请求必须来自同源页面",
                )
        if not x_csrf_token or not secrets.compare_digest(
            x_csrf_token, asset_csrf_token
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="资产写入校验已失效，请刷新页面",
            )

    def require_bootstrap_admin(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        x_admin_token: Annotated[
            str | None, Header(alias="X-Admin-Token")
        ] = None,
    ) -> None:
        client_host = request.client.host if request.client is not None else ""
        try:
            is_loopback = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            is_loopback = client_host.casefold() == "localhost"
        if is_loopback:
            return
        candidate = bearer_token(authorization) or x_admin_token or ""
        if not candidate or not secrets.compare_digest(
            candidate, active_settings.admin_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="验收创建凭证无效",
            )

    def require_controller(
        run_id: str,
        authorization: Annotated[str | None, Header()] = None,
        x_admin_token: Annotated[
            str | None, Header(alias="X-Admin-Token")
        ] = None,
    ) -> None:
        candidate = bearer_token(authorization) or x_admin_token or ""
        try:
            controller_identities.authenticate(run_id, candidate)
        except IdentityError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error

    def require_player(
        run: Annotated[str, Query(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> PlayerIdentity:
        try:
            return player_identities.authenticate(
                run, bearer_token(authorization) or ""
            )
        except IdentityError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error

    @application.get("/health")
    def health() -> dict[str, str]:
        with database.connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {
            "status": "ok",
            "database": "ok",
            "timeZone": active_settings.timezone_name,
        }

    @application.post("/api/review/runs")
    def create_review_run(
        payload: CreateRunRequest,
        _: None = Depends(require_bootstrap_admin),
    ) -> dict[str, Any]:
        created = service.create_run(payload.label, payload.layout_id)
        return {"ok": True, **created}

    @application.get("/api/review/layouts")
    def review_layouts(
        _: None = Depends(require_bootstrap_admin),
    ) -> dict[str, Any]:
        return {"ok": True, "layouts": service.review_layouts()}

    @application.get("/api/review/runs/{run_id}")
    def review_run(
        run_id: str,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        return {
            "ok": True,
            **service.review_state(run_id, realtime.online_ids(run_id)),
        }

    @application.post("/api/review/runs/{run_id}/reset")
    async def reset_review_run(
        run_id: str,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        state = service.reset_run(run_id)
        await realtime.reload_run(run_id)
        return {"ok": True, **state}

    @application.post("/api/review/runs/{run_id}/reset-daily")
    async def reset_review_daily(
        run_id: str,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        state = service.reset_daily(run_id)
        await realtime.broadcast_event(
            run_id,
            {
                "type": "review.daily-reset",
                "day": state["run"]["day"],
                "revision": state["run"]["revision"],
            },
        )
        for player in state["players"]:
            await realtime.send_to_player(
                run_id,
                player["id"],
                {
                    "type": "economy.changed",
                    "playerId": player["id"],
                    "balance": player["balance"],
                    "balanceCents": player["balanceCents"],
                    "source": "daily-reset",
                    "day": state["run"]["day"],
                    "revision": state["run"]["revision"],
                },
            )
        return {"ok": True, **state}

    @application.post("/api/review/runs/{run_id}/advance-day")
    async def advance_review_day(
        run_id: str,
        payload: AdvanceDayRequest,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        state = service.advance_day(run_id, payload.days)
        await realtime.control_changed(run_id)
        return {"ok": True, **state}

    @application.post("/api/review/runs/{run_id}/force-wheel")
    async def force_review_wheel(
        run_id: str,
        payload: ForceWheelRequest,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        state = service.force_wheel(run_id, payload.reward)
        await realtime.control_changed(run_id)
        return {"ok": True, **state}

    @application.post("/api/review/runs/{run_id}/pause")
    async def pause_review_run(
        run_id: str,
        payload: PauseRequest,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        state = service.set_paused(run_id, payload.paused)
        await realtime.control_changed(run_id)
        return {"ok": True, **state}

    @application.post("/api/review/runs/{run_id}/speed")
    async def speed_review_run(
        run_id: str,
        payload: SpeedRequest,
        _: None = Depends(require_controller),
    ) -> dict[str, Any]:
        state = service.set_speed(run_id, payload.speed)
        await realtime.control_changed(run_id)
        return {"ok": True, **state}

    @application.get("/api/bootstrap")
    def bootstrap(
        identity: PlayerIdentity = Depends(require_player),
    ) -> dict[str, Any]:
        payload = service.bootstrap(
            identity, realtime.online_ids(identity.run_id)
        )
        return {"ok": True, **payload}

    @application.post("/api/checkin/spin")
    async def checkin_spin(
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
        identity: PlayerIdentity = Depends(require_player),
    ) -> dict[str, Any]:
        result = service.spin(identity, idempotency_key)
        if not result["replayed"] and not result["alreadySpun"]:
            await realtime.send_to_player(
                identity.run_id,
                identity.player_id,
                {
                    "type": "economy.changed",
                    "playerId": identity.player_id,
                    "balance": result["balance"],
                    "balanceCents": result["balanceCents"],
                    "reward": result["reward"],
                    "rewardCents": result["rewardCents"],
                    "source": "daily-wheel",
                    "day": result["day"],
                    "revision": result["revision"],
                },
            )
        return {"ok": True, **result}

    @application.post("/api/good-cards")
    async def create_good_card(
        payload: GoodCardRequest,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
        identity: PlayerIdentity = Depends(require_player),
    ) -> dict[str, Any]:
        result = service.send_good_card(
            identity, payload.recipient_id, idempotency_key
        )
        if not result["replayed"]:
            await realtime.send_to_player(
                identity.run_id,
                result["card"]["recipientId"],
                {
                    "type": "good-card.created",
                    "card": result["card"],
                    "revision": result["revision"],
                },
            )
        return {"ok": True, **result}

    @application.get("/api/assets/bootstrap")
    def asset_bootstrap(
        request: Request,
        _: None = Depends(require_asset_loopback),
    ) -> dict[str, Any]:
        del request
        payload = asset_lab.bootstrap()
        payload["csrfToken"] = asset_csrf_token
        return {"ok": True, **payload}

    @application.get("/api/assets/catalog")
    def asset_catalog(
        request: Request,
        pack_id: str | None = Query(default=None, alias="packId", max_length=64),
        _: None = Depends(require_asset_loopback),
    ) -> dict[str, Any]:
        del request
        return {
            "ok": True,
            **asset_lab.catalog({"packId": pack_id} if pack_id is not None else {}),
        }

    @application.post("/api/assets/import")
    async def import_asset(
        file: Annotated[UploadFile, File(alias="png")],
        metadata: Annotated[str, Form(max_length=256 * 1024)],
        _: None = Depends(require_asset_write),
    ) -> dict[str, Any]:
        if file.content_type not in {None, "image/png"}:
            raise AssetLabError("only PNG images are accepted", code="image.not_png")
        content = await file.read(MAX_INPUT_BYTES + 1)
        if len(content) > MAX_INPUT_BYTES:
            raise AssetLabError(
                "PNG exceeds the 16 MiB input limit",
                code="image.too_large",
                details={"maxInputBytes": MAX_INPUT_BYTES},
            )
        try:
            decoded_metadata = json.loads(metadata)
        except json.JSONDecodeError as error:
            raise AssetLabError(
                "metadata is not valid JSON", code="metadata.invalid_json"
            ) from error
        if not isinstance(decoded_metadata, dict):
            raise AssetLabError("metadata must be an object", code="metadata.invalid")
        decoded_metadata.setdefault("sourceName", Path(file.filename or "upload.png").name)
        return {"ok": True, **asset_lab.import_png(content, decoded_metadata)}

    @application.post("/api/assets/inbox/scan")
    def scan_asset_inbox(
        pack_id: str | None = Query(default=None, alias="packId", max_length=64),
        _: None = Depends(require_asset_write),
    ) -> dict[str, Any]:
        return {"ok": True, **asset_lab.scan_inbox(pack_id)}

    @application.post("/api/assets/reviews/batch")
    def review_asset_versions(
        payload: AssetBatchReviewRequest,
        _: None = Depends(require_asset_write),
    ) -> dict[str, Any]:
        return {
            "ok": True,
            **asset_lab.review_batch(
                [item.model_dump(by_alias=True) for item in payload.items],
                payload.note,
                payload.expected_revision,
            ),
        }

    @application.post("/api/assets/{asset_id}/versions/{version_id}/review")
    def review_asset_version(
        asset_id: str,
        version_id: str,
        payload: AssetReviewRequest,
        _: None = Depends(require_asset_write),
    ) -> dict[str, Any]:
        return {
            "ok": True,
            **asset_lab.review(
                asset_id,
                version_id,
                payload.decision,
                payload.note,
                payload.expected_revision,
            ),
        }

    @application.post("/api/assets/packs/{pack_id}/activate")
    def activate_asset_pack(
        pack_id: str,
        payload: AssetActivationRequest,
        _: None = Depends(require_asset_write),
    ) -> dict[str, Any]:
        return {
            "ok": True,
            **asset_lab.activate(pack_id, payload.expected_revision),
        }

    @application.get("/api/assets/active/manifest")
    def active_asset_manifest() -> dict[str, Any]:
        manifest = asset_lab.active_manifest()
        if manifest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="尚未激活资产包",
            )
        return manifest

    @application.get("/api/assets/manifests/{manifest_sha256}")
    def immutable_asset_manifest(manifest_sha256: str) -> Response:
        if SHA256_RE.fullmatch(manifest_sha256) is None:
            raise HTTPException(status_code=404, detail="资产清单不存在")
        manifest_json = asset_lab.manifest_json_by_sha(manifest_sha256)
        return Response(
            content=manifest_json,
            media_type="application/json",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": f'"{manifest_sha256}"',
                "X-Manifest-SHA256": manifest_sha256,
                "X-Content-Type-Options": "nosniff",
            },
        )

    def content_addressed_file(root: Path, sha: str) -> FileResponse:
        if SHA256_RE.fullmatch(sha) is None:
            raise HTTPException(status_code=404, detail="资产不存在")
        target = root / sha[:2] / f"{sha}.png"
        if not target.is_file() or target.is_symlink():
            raise HTTPException(status_code=404, detail="资产不存在")
        return FileResponse(
            target,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.get("/api/assets/blobs/{sha}")
    def asset_blob(sha: str) -> FileResponse:
        return content_addressed_file(asset_lab.blobs_dir, sha)

    @application.get("/api/assets/derived/{sha}.png")
    def derived_asset_blob(sha: str) -> FileResponse:
        return content_addressed_file(asset_lab.derived_dir, sha)

    @application.websocket("/ws/{run_id}")
    async def world_socket(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        identity: PlayerIdentity | None = None
        try:
            try:
                first_message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=5.0
                )
            except asyncio.TimeoutError:
                await realtime.send_error(
                    websocket, "auth_timeout", "实时连接认证超时"
                )
                await websocket.close(code=4401)
                return
            if (
                not isinstance(first_message, dict)
                or first_message.get("type") != "auth"
                or not isinstance(first_message.get("token"), str)
            ):
                await realtime.send_error(
                    websocket, "auth_required", "第一条消息必须提供玩家凭证"
                )
                await websocket.close(code=4401)
                return
            try:
                identity = player_identities.authenticate(
                    run_id, first_message["token"]
                )
            except IdentityError as error:
                await realtime.send_error(websocket, "auth_invalid", str(error))
                await websocket.close(code=4401)
                return
            await realtime.connect(websocket, identity)
            while True:
                await realtime.handle_message(
                    websocket, identity, await websocket.receive_json()
                )
        except WebSocketDisconnect:
            pass
        finally:
            if identity is not None:
                await realtime.disconnect(websocket, identity)

    web_dir = active_settings.web_dir.resolve()

    def web_file(name: str) -> FileResponse:
        target = (web_dir / name).resolve()
        if not target.is_relative_to(web_dir) or not target.is_file():
            raise HTTPException(status_code=404, detail="页面尚未构建")
        # These URLs carry no version, so a cached copy is indistinguishable from
        # a current one.  A stale ES module once outlived an asset-contract
        # change and rejected a perfectly good manifest with frame counts that
        # no longer existed anywhere in the tree — an hour of debugging aimed at
        # the wrong layer.  `no-cache` was not enough: it only governs responses
        # the browser fetches afterwards, so copies already stored under
        # heuristic freshness kept being served.  `no-store` forbids keeping
        # them at all.  These are small local files; correctness beats caching.
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    @application.get("/review", include_in_schema=False)
    @application.get("/review/", include_in_schema=False)
    def review_page() -> FileResponse:
        return web_file("review.html")

    @application.get("/assets", include_in_schema=False)
    @application.get("/assets/", include_in_schema=False)
    def assets_page() -> FileResponse:
        return web_file("assets.html")

    @application.get("/", include_in_schema=False)
    def index_page() -> FileResponse:
        return web_file("index.html")

    @application.get("/{asset_path:path}", include_in_schema=False)
    def static_asset(asset_path: str) -> FileResponse:
        return web_file(asset_path)

    return application


app = create_app()
