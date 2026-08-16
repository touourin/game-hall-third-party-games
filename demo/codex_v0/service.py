from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

from .asset_lab import SHA256_RE, AssetLab, AssetLabError
from .config import Settings
from .db import Database
from .identity import PlayerIdentity
from .pathfinding import FURNITURE_BLOCKED
from .world_layout import (
    DEFAULT_LAYOUT_ID,
    WorldLayoutError,
    WorldLayoutRegistry,
    canonical_json,
)


WHEEL_DOLLARS = (1, 1, 2, 2, 3, 5, 10, 20)
PLAYER_COLORS = (
    "#ef6f6c",
    "#f4a261",
    "#e9c46a",
    "#72b01d",
    "#2a9d8f",
    "#4d96ff",
    "#8b5cf6",
    "#d65db1",
)
PLAYER_SPAWNS = (
    (1.0, 1.0),
    (6.0, 1.0),
    (13.0, 1.0),
    (18.0, 1.0),
    (1.0, 10.0),
    (6.0, 10.0),
    (13.0, 10.0),
    (18.0, 10.0),
)
PLAYER_NAMES = ("Ava", "Ben", "Cleo", "Drew", "Eli", "Faye", "Gus", "Hana")


class GameServiceError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "invalid") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class CreatedPlayer:
    id: str
    name: str
    color: str
    token: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class GameService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
        *,
        asset_lab: AssetLab | None = None,
        world_layouts: WorldLayoutRegistry | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.asset_lab = asset_lab
        self.world_layouts = world_layouts

    def now(self) -> datetime:
        moment = self._now()
        if moment.tzinfo is None:
            raise RuntimeError("GameService clock must return a timezone-aware datetime")
        return moment.astimezone(timezone.utc)

    def now_iso(self) -> str:
        return self.now().isoformat(timespec="milliseconds")

    def create_run(
        self,
        label: str | None = None,
        layout_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = f"run-{secrets.token_hex(5)}"
        controller_token = secrets.token_urlsafe(32)
        created_at = self.now_iso()
        normalized_label = " ".join((label or "验收轮次").strip().split())[:80]
        if not normalized_label:
            normalized_label = "验收轮次"
        binding = self._new_run_binding(layout_id)
        spawn_points = binding["spawnPoints"]
        if spawn_points is None:
            spawn_points = [
                {
                    "playerId": name.casefold(),
                    "name": name,
                    "x": x,
                    "y": y,
                }
                for name, (x, y) in zip(PLAYER_NAMES, PLAYER_SPAWNS, strict=True)
            ]
        created_players: list[CreatedPlayer] = []
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, label, created_at, controller_token_hash,
                    asset_release_id, asset_pack_id, asset_catalog_revision,
                    asset_manifest_sha256,
                    asset_atlas_sha256, world_layout_id, world_layout_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    normalized_label,
                    created_at,
                    token_hash(controller_token),
                    binding["assetReleaseId"],
                    binding["assetPackId"],
                    binding["assetCatalogRevision"],
                    binding["assetManifestSha256"],
                    binding["assetAtlasSha256"],
                    binding["worldLayoutId"],
                    binding["worldLayoutJson"],
                ),
            )
            for spawn, color in zip(spawn_points, PLAYER_COLORS, strict=True):
                player_id = str(spawn["playerId"])
                name = str(spawn["name"])
                x = float(spawn["x"])
                y = float(spawn["y"])
                token = secrets.token_urlsafe(32)
                connection.execute(
                    """
                    INSERT INTO players(
                        run_id, id, display_name, color, token_hash,
                        balance_cents, x, y, spawn_x, spawn_y
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (run_id, player_id, name, color, token_hash(token), x, y, x, y),
                )
                created_players.append(CreatedPlayer(player_id, name, color, token))
        return {
            "run": self.review_state(run_id)["run"],
            "controllerToken": controller_token,
            "reviewUrl": f"/review?run={run_id}#adminToken={controller_token}",
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "color": player.color,
                    "token": player.token,
                    "url": f"/?run={run_id}#token={player.token}",
                }
                for player in created_players
            ],
        }

    def run_row(self, run_id: str, *, connection=None):
        if connection is not None:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        else:
            with self.database.connection() as own_connection:
                row = own_connection.execute(
                    "SELECT * FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
        if row is None:
            raise GameServiceError(
                "验收轮次不存在", status_code=404, code="run_not_found"
            )
        return row

    def local_day(self, run_row) -> str:
        now = self.now().astimezone(self.settings.timezone)
        return (now.date() + timedelta(days=int(run_row["day_offset"]))).isoformat()

    def run_payload(self, row) -> dict[str, Any]:
        asset_pack = self.asset_pack_payload(row)
        world_layout = self.world_layout_payload(row)
        return {
            "id": str(row["id"]),
            "label": str(row["label"]),
            "createdAt": str(row["created_at"]),
            "day": self.local_day(row),
            "dayOffset": int(row["day_offset"]),
            "paused": bool(row["paused"]),
            "speed": float(row["speed"]),
            "forcedWheel": (
                int(row["forced_wheel"])
                if row["forced_wheel"] is not None
                else None
            ),
            "revision": int(row["revision"]),
            "assetPack": asset_pack,
            "worldLayout": world_layout,
        }

    def review_layouts(self) -> list[dict[str, Any]]:
        if self.world_layouts is None:
            return []
        active_pack_id: str | None = None
        if self.asset_lab is not None:
            try:
                release = self.asset_lab.active_release()
                if release is not None:
                    active_pack_id = str(release["packId"])
            except AssetLabError:
                active_pack_id = None
        return self.world_layouts.descriptors(active_pack_id=active_pack_id)

    def _new_run_binding(self, layout_id: str | None = None) -> dict[str, Any]:
        empty = {
            "assetReleaseId": None,
            "assetPackId": None,
            "assetCatalogRevision": None,
            "assetManifestSha256": None,
            "assetAtlasSha256": None,
            "worldLayoutId": None,
            "worldLayoutJson": None,
            "spawnPoints": None,
        }

        if layout_id is not None and self.world_layouts is not None:
            try:
                self.world_layouts.required_pack_id(layout_id)
            except WorldLayoutError as exc:
                if "world layout not found" in str(exc):
                    raise GameServiceError(
                        "地图不存在",
                        status_code=422,
                        code="layout_not_found",
                    ) from exc
                raise GameServiceError(
                    "地图配置无法验证",
                    status_code=503,
                    code="asset_binding_invalid",
                ) from exc

        if self.asset_lab is None or self.world_layouts is None:
            if layout_id is not None:
                raise GameServiceError(
                    "当前没有可用于该地图的已激活资产包",
                    status_code=409,
                    code="asset_pack_unavailable",
                )
            return empty
        try:
            release = self.asset_lab.active_release()
            if release is None:
                if layout_id is not None:
                    raise GameServiceError(
                        "请先在资产验收台激活地图所需的资产包",
                        status_code=409,
                        code="asset_pack_unavailable",
                    )
                return empty
            manifest = release.get("manifest")
            if not isinstance(manifest, dict):
                raise WorldLayoutError("active release manifest is missing")
            selected_layout_id = layout_id or DEFAULT_LAYOUT_ID
            layout = self.world_layouts.build_snapshot(manifest, selected_layout_id)
        except GameServiceError:
            raise
        except WorldLayoutError as exc:
            message = str(exc)
            if layout_id is not None and "world layout not found" in message:
                raise GameServiceError(
                    "地图不存在",
                    status_code=422,
                    code="layout_not_found",
                ) from exc
            if layout_id is not None and "requires asset pack" in message:
                raise GameServiceError(
                    "当前激活资产包不支持所选地图",
                    status_code=409,
                    code="asset_pack_unavailable",
                ) from exc
            raise GameServiceError(
                "当前资产版本无法创建场景",
                status_code=503,
                code="asset_binding_invalid",
            ) from exc
        except (AssetLabError, OSError, ValueError) as exc:
            raise GameServiceError(
                "当前资产版本无法创建场景",
                status_code=503,
                code="asset_binding_invalid",
            ) from exc
        if (
            int(layout["columns"]) > self.settings.world_columns
            or int(layout["rows"]) > self.settings.world_rows
        ):
            raise GameServiceError(
                "资产场景尺寸超过服务器上限",
                status_code=503,
                code="asset_binding_invalid",
            )
        blocked = {
            (int(cell["x"]), int(cell["y"])) for cell in layout["blockedCells"]
        }
        spawn_points = layout.get("spawnPoints")
        if not isinstance(spawn_points, list):
            raise GameServiceError(
                "资产场景缺少玩家出生点",
                status_code=503,
                code="asset_binding_invalid",
            )
        occupied_spawns = [
            {"x": int(spawn["x"]), "y": int(spawn["y"])}
            for spawn in spawn_points
            if (int(spawn["x"]), int(spawn["y"])) in blocked
        ]
        if occupied_spawns:
            raise GameServiceError(
                "资产场景占用了玩家出生点",
                status_code=503,
                code="asset_binding_invalid",
            )
        return {
            "assetReleaseId": str(release["id"]),
            "assetPackId": str(release["packId"]),
            "assetCatalogRevision": int(release["catalogRevision"]),
            "assetManifestSha256": str(release["manifestSha256"]),
            "assetAtlasSha256": str(release["atlasSha256"]),
            "worldLayoutId": str(layout["id"]),
            "worldLayoutJson": canonical_json(layout),
            "spawnPoints": spawn_points,
        }

    @staticmethod
    def asset_pack_payload(row) -> dict[str, Any] | None:
        values = (
            row["asset_release_id"],
            row["asset_pack_id"],
            row["asset_catalog_revision"],
            row["asset_manifest_sha256"],
            row["asset_atlas_sha256"],
        )
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise GameServiceError(
                "验收轮次资产绑定不完整",
                status_code=500,
                code="run_asset_binding_invalid",
            )
        manifest_sha = str(row["asset_manifest_sha256"])
        atlas_sha = str(row["asset_atlas_sha256"])
        if (
            SHA256_RE.fullmatch(manifest_sha) is None
            or SHA256_RE.fullmatch(atlas_sha) is None
            or int(row["asset_catalog_revision"]) < 0
        ):
            raise GameServiceError(
                "验收轮次资产绑定损坏",
                status_code=500,
                code="run_asset_binding_invalid",
            )
        return {
            "releaseId": str(row["asset_release_id"]),
            "packId": str(row["asset_pack_id"]),
            "catalogRevision": int(row["asset_catalog_revision"]),
            "manifestSha256": manifest_sha,
            "manifestUrl": f"/api/assets/manifests/{manifest_sha}",
            "atlasSha256": atlas_sha,
            "atlasUrl": f"/api/assets/derived/{atlas_sha}.png",
        }

    @staticmethod
    def world_layout_payload(row) -> dict[str, Any] | None:
        layout_id = row["world_layout_id"]
        raw = row["world_layout_json"]
        if layout_id is None and raw is None:
            return None
        if layout_id is None or raw is None:
            raise GameServiceError(
                "验收轮次场景绑定不完整",
                status_code=500,
                code="run_asset_binding_invalid",
            )
        try:
            layout = json.loads(str(raw))
            if not isinstance(layout, dict) or layout.get("id") != layout_id:
                raise ValueError("layout id mismatch")
            claimed_sha = layout.get("sha256")
            unsigned = {key: value for key, value in layout.items() if key != "sha256"}
            actual_sha = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
            if claimed_sha != actual_sha:
                raise ValueError("layout hash mismatch")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise GameServiceError(
                "验收轮次场景快照损坏",
                status_code=500,
                code="run_asset_binding_invalid",
            ) from exc
        return layout

    def collision_for_run(self, run_id: str) -> tuple[int, int, frozenset[tuple[int, int]]]:
        """Return the frozen collision contract used by bootstrap and realtime."""

        with self.database.connection() as connection:
            run = self.run_row(run_id, connection=connection)
            layout = self.world_layout_payload(run)
        if layout is None:
            return (
                self.settings.world_columns,
                self.settings.world_rows,
                FURNITURE_BLOCKED,
            )
        blocked = frozenset(
            (int(cell["x"]), int(cell["y"])) for cell in layout["blockedCells"]
        )
        return int(layout["columns"]), int(layout["rows"]), blocked

    def work_seats_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return frozen, non-economic work-seat contracts for realtime state."""

        with self.database.connection() as connection:
            run = self.run_row(run_id, connection=connection)
            layout = self.world_layout_payload(run)
        if layout is None:
            return []
        raw = layout.get("workSeats", [])
        if not isinstance(raw, list):
            raise GameServiceError(
                "验收轮次工作座位快照损坏",
                status_code=500,
                code="run_asset_binding_invalid",
            )
        result: list[dict[str, Any]] = []
        for seat in raw:
            if not isinstance(seat, dict):
                raise GameServiceError(
                    "验收轮次工作座位快照损坏",
                    status_code=500,
                    code="run_asset_binding_invalid",
                )
            result.append(
                {
                    "placementId": str(seat["placementId"]),
                    "seatId": str(seat["seatId"]),
                    "x": int(seat["x"]),
                    "y": int(seat["y"]),
                    "facing": str(seat["facing"]),
                }
            )
        return result

    def initial_activities_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return frozen initial activities; positions remain authoritative."""

        with self.database.connection() as connection:
            run = self.run_row(run_id, connection=connection)
            layout = self.world_layout_payload(run)
        if layout is None:
            return []
        raw = layout.get("initialActivities", [])
        if not isinstance(raw, list):
            raise GameServiceError(
                "验收轮次初始活动快照损坏",
                status_code=500,
                code="run_asset_binding_invalid",
            )
        result: list[dict[str, Any]] = []
        for activity in raw:
            if not isinstance(activity, dict) or activity.get("type") != "work":
                raise GameServiceError(
                    "验收轮次初始活动快照损坏",
                    status_code=500,
                    code="run_asset_binding_invalid",
                )
            try:
                result.append(
                    {
                        "playerId": str(activity["playerId"]),
                        "type": "work",
                        "placementId": str(activity["placementId"]),
                        "seatId": str(activity["seatId"]),
                        "facing": str(activity["facing"]),
                    }
                )
            except KeyError as exc:
                raise GameServiceError(
                    "验收轮次初始活动快照损坏",
                    status_code=500,
                    code="run_asset_binding_invalid",
                ) from exc
        return result

    @staticmethod
    def bump_revision(connection, run_id: str) -> int:
        connection.execute(
            "UPDATE runs SET revision = revision + 1 WHERE id = ?", (run_id,)
        )
        row = connection.execute(
            "SELECT revision FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise GameServiceError(
                "验收轮次不存在", status_code=404, code="run_not_found"
            )
        return int(row["revision"])

    @staticmethod
    def player_payload(row, *, online: bool = False) -> dict[str, Any]:
        balance_cents = int(row["balance_cents"])
        return {
            "id": str(row["id"]),
            "name": str(row["display_name"]),
            "color": str(row["color"]),
            "x": round(float(row["x"]), 4),
            "y": round(float(row["y"]), 4),
            "online": online,
            "balanceCents": balance_cents,
            "balance": balance_cents / 100,
        }

    @staticmethod
    def presence_payload(row, *, online: bool = False) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "name": str(row["display_name"]),
            "color": str(row["color"]),
            "x": round(float(row["x"]), 4),
            "y": round(float(row["y"]), 4),
            "online": online,
        }

    def review_state(self, run_id: str, online_ids: set[str] | None = None) -> dict[str, Any]:
        online = online_ids or set()
        with self.database.connection() as connection:
            run = self.run_row(run_id, connection=connection)
            players = connection.execute(
                "SELECT * FROM players WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return {
            "run": self.run_payload(run),
            "players": [
                self.player_payload(player, online=str(player["id"]) in online)
                for player in players
            ],
        }

    def bootstrap(
        self,
        identity: PlayerIdentity,
        online_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        online = online_ids or set()
        with self.database.connection() as connection:
            run = self.run_row(identity.run_id, connection=connection)
            day = self.local_day(run)
            players = connection.execute(
                "SELECT * FROM players WHERE run_id = ? ORDER BY id", (identity.run_id,)
            ).fetchall()
            self_row = next(
                (row for row in players if row["id"] == identity.player_id), None
            )
            if self_row is None:
                raise GameServiceError(
                    "玩家不存在", status_code=401, code="identity_invalid"
                )
            spin = connection.execute(
                """
                SELECT reward_cents FROM daily_spins
                WHERE run_id = ? AND player_id = ? AND local_day = ?
                """,
                (identity.run_id, identity.player_id, day),
            ).fetchone()
            sent_card = connection.execute(
                """
                SELECT recipient_id FROM good_cards
                WHERE run_id = ? AND sender_id = ? AND local_day = ?
                """,
                (identity.run_id, identity.player_id, day),
            ).fetchone()
            card_rows = connection.execute(
                """
                SELECT
                    card.sender_id, sender.display_name AS sender_name,
                    card.recipient_id, recipient.display_name AS recipient_name,
                    card.local_day, card.created_at
                FROM good_cards AS card
                JOIN players AS sender
                  ON sender.run_id = card.run_id AND sender.id = card.sender_id
                JOIN players AS recipient
                  ON recipient.run_id = card.run_id AND recipient.id = card.recipient_id
                WHERE card.run_id = ? AND card.local_day = ?
                  AND card.recipient_id = ?
                ORDER BY card.created_at, card.id
                """,
                (identity.run_id, day, identity.player_id),
            ).fetchall()
        player = self.player_payload(self_row, online=identity.player_id in online)
        player.update(
            {
                "spin": {
                    "available": spin is None,
                    "reward": (
                        int(spin["reward_cents"]) // 100 if spin is not None else None
                    ),
                },
                "goodCard": {
                    "available": sent_card is None,
                    "recipientId": (
                        str(sent_card["recipient_id"])
                        if sent_card is not None
                        else None
                    ),
                },
            }
        )
        layout = self.world_layout_payload(run)
        asset_pack = self.asset_pack_payload(run)
        columns, rows, blocked = self.collision_for_run(identity.run_id)
        tile_width = int(layout["tileWidth"]) if layout is not None else self.settings.tile_width
        tile_height = int(layout["tileHeight"]) if layout is not None else self.settings.tile_height
        return {
            "run": self.run_payload(run),
            "assetPack": asset_pack,
            "world": {
                "columns": columns,
                "rows": rows,
                "tileSize": tile_width,
                "tileWidth": tile_width,
                "tileHeight": tile_height,
                "blockedCells": [
                    {"x": x, "y": y} for x, y in sorted(blocked)
                ],
                "wheel": list(WHEEL_DOLLARS),
                "layout": layout,
            },
            "player": player,
            "players": [
                self.presence_payload(row, online=str(row["id"]) in online)
                for row in players
            ],
            "goodCards": [self.card_payload(row) for row in card_rows],
        }

    @staticmethod
    def card_payload(row) -> dict[str, Any]:
        return {
            "senderId": str(row["sender_id"]),
            "senderName": str(row["sender_name"]),
            "recipientId": str(row["recipient_id"]),
            "recipientName": str(row["recipient_name"]),
            "day": str(row["local_day"]),
            "createdAt": str(row["created_at"]),
        }

    def spin(self, identity: PlayerIdentity, idempotency_key: str) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 128:
            raise GameServiceError(
                "Idempotency-Key 必须是 1–128 个字符",
                status_code=400,
                code="idempotency_key_invalid",
            )
        now = self.now_iso()
        with self.database.transaction() as connection:
            run = self.run_row(identity.run_id, connection=connection)
            day = self.local_day(run)
            endpoint = f"daily-spin:{day}"
            replay = connection.execute(
                """
                SELECT response_json FROM idempotency_keys
                WHERE run_id = ? AND player_id = ? AND endpoint = ? AND key = ?
                """,
                (identity.run_id, identity.player_id, endpoint, key),
            ).fetchone()
            if replay is not None:
                response = json.loads(str(replay["response_json"]))
                response["replayed"] = True
                return response

            player = connection.execute(
                """
                SELECT balance_cents FROM players
                WHERE run_id = ? AND id = ?
                """,
                (identity.run_id, identity.player_id),
            ).fetchone()
            if player is None:
                raise GameServiceError(
                    "玩家不存在", status_code=401, code="identity_invalid"
                )
            existing = connection.execute(
                """
                SELECT wheel_index, reward_cents FROM daily_spins
                WHERE run_id = ? AND player_id = ? AND local_day = ?
                """,
                (identity.run_id, identity.player_id, day),
            ).fetchone()
            if existing is not None:
                response = self.spin_response(
                    identity,
                    day,
                    wheel_index=int(existing["wheel_index"]),
                    reward_cents=int(existing["reward_cents"]),
                    balance_cents=int(player["balance_cents"]),
                    already_spun=True,
                    revision=int(run["revision"]),
                )
            else:
                forced = run["forced_wheel"]
                if forced is None:
                    wheel_index = secrets.randbelow(len(WHEEL_DOLLARS))
                else:
                    wheel_index = WHEEL_DOLLARS.index(int(forced))
                reward_cents = WHEEL_DOLLARS[wheel_index] * 100
                source_key = f"daily-wheel:{day}"
                connection.execute(
                    """
                    INSERT INTO daily_spins(
                        run_id, player_id, local_day, wheel_index, reward_cents, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.run_id,
                        identity.player_id,
                        day,
                        wheel_index,
                        reward_cents,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ledger(
                        run_id, player_id, delta_cents, source_type, source_key, created_at
                    ) VALUES (?, ?, ?, 'daily-wheel', ?, ?)
                    """,
                    (
                        identity.run_id,
                        identity.player_id,
                        reward_cents,
                        source_key,
                        now,
                    ),
                )
                balance_cents = int(player["balance_cents"]) + reward_cents
                connection.execute(
                    """
                    UPDATE players SET balance_cents = ?
                    WHERE run_id = ? AND id = ?
                    """,
                    (balance_cents, identity.run_id, identity.player_id),
                )
                revision = self.bump_revision(connection, identity.run_id)
                response = self.spin_response(
                    identity,
                    day,
                    wheel_index=wheel_index,
                    reward_cents=reward_cents,
                    balance_cents=balance_cents,
                    already_spun=False,
                    revision=revision,
                )
            connection.execute(
                """
                INSERT INTO idempotency_keys(
                    run_id, player_id, endpoint, key, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.run_id,
                    identity.player_id,
                    endpoint,
                    key,
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
        return response

    @staticmethod
    def spin_response(
        identity: PlayerIdentity,
        day: str,
        *,
        wheel_index: int,
        reward_cents: int,
        balance_cents: int,
        already_spun: bool,
        revision: int,
    ) -> dict[str, Any]:
        return {
            "runId": identity.run_id,
            "playerId": identity.player_id,
            "day": day,
            "wheelIndex": wheel_index,
            "reward": reward_cents // 100,
            "rewardCents": reward_cents,
            "balance": balance_cents / 100,
            "balanceCents": balance_cents,
            "alreadySpun": already_spun,
            "replayed": False,
            "revision": revision,
        }

    def send_good_card(
        self,
        identity: PlayerIdentity,
        recipient_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 128:
            raise GameServiceError(
                "Idempotency-Key 必须是 1–128 个字符",
                status_code=400,
                code="idempotency_key_invalid",
            )
        recipient = recipient_id.strip()
        if recipient == identity.player_id:
            raise GameServiceError(
                "好人卡不能送给自己", status_code=400, code="self_card"
            )
        now = self.now_iso()
        with self.database.transaction() as connection:
            run = self.run_row(identity.run_id, connection=connection)
            day = self.local_day(run)
            endpoint = f"good-card:{day}"
            replay = connection.execute(
                """
                SELECT response_json FROM idempotency_keys
                WHERE run_id = ? AND player_id = ? AND endpoint = ? AND key = ?
                """,
                (identity.run_id, identity.player_id, endpoint, key),
            ).fetchone()
            if replay is not None:
                response = json.loads(str(replay["response_json"]))
                response["replayed"] = True
                return response
            recipient_row = connection.execute(
                """
                SELECT display_name FROM players WHERE run_id = ? AND id = ?
                """,
                (identity.run_id, recipient),
            ).fetchone()
            if recipient_row is None:
                raise GameServiceError(
                    "接收者不存在", status_code=404, code="recipient_not_found"
                )
            existing = connection.execute(
                """
                SELECT 1 FROM good_cards
                WHERE run_id = ? AND sender_id = ? AND local_day = ?
                """,
                (identity.run_id, identity.player_id, day),
            ).fetchone()
            if existing is not None:
                raise GameServiceError(
                    "今天已经送过好人卡了",
                    status_code=409,
                    code="good_card_already_sent",
                )
            connection.execute(
                """
                INSERT INTO good_cards(
                    run_id, sender_id, recipient_id, local_day, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (identity.run_id, identity.player_id, recipient, day, now),
            )
            revision = self.bump_revision(connection, identity.run_id)
            card = {
                "senderId": identity.player_id,
                "senderName": identity.name,
                "recipientId": recipient,
                "recipientName": str(recipient_row["display_name"]),
                "day": day,
                "createdAt": now,
            }
            response = {
                "card": card,
                "available": False,
                "replayed": False,
                "revision": revision,
            }
            connection.execute(
                """
                INSERT INTO idempotency_keys(
                    run_id, player_id, endpoint, key, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.run_id,
                    identity.player_id,
                    endpoint,
                    key,
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
        return response

    def reset_run(self, run_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            self.run_row(run_id, connection=connection)
            connection.execute("DELETE FROM idempotency_keys WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM good_cards WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM daily_spins WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM ledger WHERE run_id = ?", (run_id,))
            connection.execute(
                """
                UPDATE players
                SET balance_cents = 0, x = spawn_x, y = spawn_y
                WHERE run_id = ?
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE runs
                SET day_offset = 0, paused = 0, speed = 1.0,
                    forced_wheel = NULL, revision = revision + 1
                WHERE id = ?
                """,
                (run_id,),
            )
        return self.review_state(run_id)

    def reset_daily(self, run_id: str) -> dict[str, Any]:
        """Undo only the currently selected review day's daily actions."""

        with self.database.transaction() as connection:
            run = self.run_row(run_id, connection=connection)
            day = self.local_day(run)
            spin_rows = connection.execute(
                """
                SELECT player_id, reward_cents FROM daily_spins
                WHERE run_id = ? AND local_day = ?
                """,
                (run_id, day),
            ).fetchall()
            for spin in spin_rows:
                connection.execute(
                    """
                    UPDATE players
                    SET balance_cents = balance_cents - ?
                    WHERE run_id = ? AND id = ?
                    """,
                    (int(spin["reward_cents"]), run_id, str(spin["player_id"])),
                )
            connection.execute(
                """
                DELETE FROM ledger
                WHERE run_id = ? AND source_type = 'daily-wheel'
                  AND source_key = ?
                """,
                (run_id, f"daily-wheel:{day}"),
            )
            connection.execute(
                "DELETE FROM daily_spins WHERE run_id = ? AND local_day = ?",
                (run_id, day),
            )
            connection.execute(
                "DELETE FROM good_cards WHERE run_id = ? AND local_day = ?",
                (run_id, day),
            )
            connection.execute(
                """
                DELETE FROM idempotency_keys
                WHERE run_id = ? AND endpoint IN (?, ?)
                """,
                (run_id, f"daily-spin:{day}", f"good-card:{day}"),
            )
            self.bump_revision(connection, run_id)
        return self.review_state(run_id)

    def advance_day(self, run_id: str, days: int) -> dict[str, Any]:
        if not 1 <= days <= 365:
            raise GameServiceError(
                "推进天数必须是 1–365", code="days_invalid"
            )
        with self.database.transaction() as connection:
            self.run_row(run_id, connection=connection)
            connection.execute(
                """
                UPDATE runs
                SET day_offset = day_offset + ?, revision = revision + 1
                WHERE id = ?
                """,
                (days, run_id),
            )
        return self.review_state(run_id)

    def force_wheel(self, run_id: str, reward: int | None) -> dict[str, Any]:
        if reward is not None and reward not in WHEEL_DOLLARS:
            raise GameServiceError(
                "强制转盘值必须来自 1,1,2,2,3,5,10,20",
                code="wheel_reward_invalid",
            )
        with self.database.transaction() as connection:
            self.run_row(run_id, connection=connection)
            connection.execute(
                """
                UPDATE runs SET forced_wheel = ?, revision = revision + 1
                WHERE id = ?
                """,
                (reward, run_id),
            )
        return self.review_state(run_id)

    def set_paused(self, run_id: str, paused: bool) -> dict[str, Any]:
        with self.database.transaction() as connection:
            self.run_row(run_id, connection=connection)
            connection.execute(
                """
                UPDATE runs SET paused = ?, revision = revision + 1
                WHERE id = ?
                """,
                (int(paused), run_id),
            )
        return self.review_state(run_id)

    def set_speed(self, run_id: str, speed: float) -> dict[str, Any]:
        if not 0.1 <= speed <= 8.0:
            raise GameServiceError(
                "速度倍率必须在 0.1–8.0 之间", code="speed_invalid"
            )
        with self.database.transaction() as connection:
            self.run_row(run_id, connection=connection)
            connection.execute(
                """
                UPDATE runs SET speed = ?, revision = revision + 1
                WHERE id = ?
                """,
                (speed, run_id),
            )
        return self.review_state(run_id)

    def persist_positions(self, run_id: str, positions: dict[str, tuple[float, float]]) -> None:
        if not positions:
            return
        with self.database.transaction() as connection:
            self.run_row(run_id, connection=connection)
            connection.executemany(
                """
                UPDATE players SET x = ?, y = ? WHERE run_id = ? AND id = ?
                """,
                [
                    (float(x), float(y), run_id, player_id)
                    for player_id, (x, y) in positions.items()
                ],
            )
