from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .config import Settings
from .identity import PlayerIdentity
from .pathfinding import astar, in_bounds
from .service import GameService, GameServiceError, utc_now_iso


@dataclass
class PlayerMotion:
    x: float
    y: float
    path: list[tuple[float, float]] = field(default_factory=list)
    target_x: float | None = None
    target_y: float | None = None
    last_seq: int = -1
    last_action_at: float = 0.0
    dirty: bool = False
    activity: dict[str, Any] | None = None

    @property
    def moving(self) -> bool:
        return bool(self.path)


@dataclass
class RunRuntime:
    run_id: str
    motions: dict[str, PlayerMotion]
    paused: bool
    speed: float
    columns: int
    rows: int
    blocked: frozenset[tuple[int, int]]
    work_seats: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    seat_occupancy: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    player_work: dict[str, dict[str, Any]] = field(default_factory=dict)
    tick: int = 0
    connections: dict[str, set[WebSocket]] = field(
        default_factory=lambda: defaultdict(set)
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_flush_at: float = field(default_factory=time.monotonic)


class RealtimeManager:
    def __init__(self, service: GameService, settings: Settings) -> None:
        self.service = service
        self.settings = settings
        self.runtimes: dict[str, RunRuntime] = {}
        self._runtime_lock = asyncio.Lock()
        self._ticker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._ticker is None:
            self._ticker = asyncio.create_task(self._tick_forever())

    async def stop(self) -> None:
        ticker = self._ticker
        self._ticker = None
        if ticker is not None:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass
        for runtime in list(self.runtimes.values()):
            await self._flush(runtime, force=True)

    async def runtime(self, run_id: str) -> RunRuntime:
        existing = self.runtimes.get(run_id)
        if existing is not None:
            return existing
        async with self._runtime_lock:
            existing = self.runtimes.get(run_id)
            if existing is not None:
                return existing
            state = await asyncio.to_thread(self.service.review_state, run_id)
            columns, rows, blocked = await asyncio.to_thread(
                self.service.collision_for_run, run_id
            )
            work_seats = await asyncio.to_thread(
                self.service.work_seats_for_run, run_id
            )
            initial_activities = await asyncio.to_thread(
                self.service.initial_activities_for_run, run_id
            )
            run = state["run"]
            runtime = RunRuntime(
                run_id=run_id,
                motions={
                    player["id"]: PlayerMotion(
                        x=float(player["x"]), y=float(player["y"])
                    )
                    for player in state["players"]
                },
                paused=bool(run["paused"]),
                speed=float(run["speed"]),
                columns=columns,
                rows=rows,
                blocked=blocked,
                work_seats={
                    (seat["placementId"], seat["seatId"]): seat
                    for seat in work_seats
                },
            )
            self._restore_initial_work(runtime, initial_activities)
            self.runtimes[run_id] = runtime
            return runtime

    def online_ids(self, run_id: str) -> set[str]:
        runtime = self.runtimes.get(run_id)
        if runtime is None:
            return set()
        return {
            player_id
            for player_id, sockets in runtime.connections.items()
            if sockets
        }

    async def connect(
        self,
        websocket: WebSocket,
        identity: PlayerIdentity,
    ) -> None:
        runtime = await self.runtime(identity.run_id)
        async with runtime.lock:
            runtime.connections[identity.player_id].add(websocket)
            auth_payload = {
                "type": "auth.ok",
                "runId": identity.run_id,
                "playerId": identity.player_id,
                "lastClientSeq": runtime.motions[identity.player_id].last_seq,
            }
            snapshot = self.snapshot_payload(runtime)
        await websocket.send_json(auth_payload)
        await websocket.send_json(snapshot)
        await self.broadcast_positions(runtime)

    async def disconnect(
        self,
        websocket: WebSocket,
        identity: PlayerIdentity,
    ) -> None:
        runtime = self.runtimes.get(identity.run_id)
        if runtime is None:
            return
        async with runtime.lock:
            sockets = runtime.connections.get(identity.player_id)
            disconnected_last_socket = False
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    runtime.connections.pop(identity.player_id, None)
                    disconnected_last_socket = True
            motion = runtime.motions.get(identity.player_id)
            position = (
                {identity.player_id: (motion.x, motion.y)} if motion is not None else {}
            )
            if motion is not None:
                motion.dirty = False
                if disconnected_last_socket:
                    if self._cancel_work_locked(runtime, identity.player_id, motion):
                        motion.path = []
                        motion.target_x = None
                        motion.target_y = None
        if position:
            await asyncio.to_thread(
                self.service.persist_positions, identity.run_id, position
            )
        await self.broadcast_positions(runtime)

    async def handle_message(
        self,
        websocket: WebSocket,
        identity: PlayerIdentity,
        message: Any,
    ) -> None:
        if not isinstance(message, dict):
            await self.send_error(websocket, "message_invalid", "消息必须是对象")
            return
        message_type = message.get("type")
        if message_type == "ping":
            await websocket.send_json(
                {"type": "pong", "serverTime": utc_now_iso()}
            )
            return
        if message_type == "move.target":
            await self.set_move_target(websocket, identity, message)
            return
        if message_type == "work.start":
            await self.start_work(websocket, identity, message)
            return
        if message_type == "work.stop":
            await self.stop_work(websocket, identity, message)
            return
        if message_type not in {"move.target", "work.start", "work.stop"}:
            await self.send_error(websocket, "message_unsupported", "不支持这个实时消息")
            return

    async def set_move_target(
        self,
        websocket: WebSocket,
        identity: PlayerIdentity,
        message: dict[str, Any],
    ) -> None:
        unexpected_fields = set(message) - {"type", "tileX", "tileY", "clientSeq"}
        if unexpected_fields:
            await self.send_error(
                websocket,
                "message_invalid",
                "移动消息包含不支持的字段",
                fields=sorted(unexpected_fields),
            )
            return
        raw_x = message.get("tileX")
        raw_y = message.get("tileY")
        if (
            isinstance(raw_x, bool)
            or isinstance(raw_y, bool)
            or not isinstance(raw_x, (int, float))
            or not isinstance(raw_y, (int, float))
            or not math.isfinite(float(raw_x))
            or not math.isfinite(float(raw_y))
        ):
            await self.send_error(websocket, "target_invalid", "移动目标坐标无效")
            return
        raw_seq = message.get("clientSeq")
        if (
            isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 0
        ):
            await self.send_error(websocket, "seq_invalid", "移动序号无效")
            return

        runtime = await self.runtime(identity.run_id)
        reply: dict[str, Any]
        async with runtime.lock:
            motion = runtime.motions.get(identity.player_id)
            if motion is None:
                reply = self.error_payload("player_missing", "玩家不存在")
            elif raw_seq <= motion.last_seq:
                reply = {
                    "type": "move.ignored",
                    "clientSeq": raw_seq,
                    "seq": raw_seq,
                    "reason": "stale",
                }
            else:
                now = time.monotonic()
                elapsed = now - motion.last_action_at
                minimum = self.settings.movement_action_interval_seconds
                if motion.last_action_at and elapsed < minimum:
                    retry_after_ms = max(1, math.ceil((minimum - elapsed) * 1000))
                    reply = self.error_payload(
                        "rate_limited",
                        "移动指令过于频繁",
                        retryAfterMs=retry_after_ms,
                        clientSeq=raw_seq,
                    )
                else:
                    motion.last_action_at = now
                    reply = self._plan_move(
                        runtime,
                        identity.player_id,
                        motion,
                        raw_x,
                        raw_y,
                        raw_seq,
                    )
        await websocket.send_json(reply)

    def _plan_move(
        self,
        runtime: RunRuntime,
        player_id: str,
        motion: PlayerMotion,
        raw_x: int | float,
        raw_y: int | float,
        raw_seq: int,
    ) -> dict[str, Any]:
        requested = (int(round(float(raw_x))), int(round(float(raw_y))))
        if not in_bounds(requested, runtime.columns, runtime.rows):
            return self.error_payload(
                "target_out_of_bounds", "移动目标超出场景", clientSeq=raw_seq
            )
        if requested in runtime.blocked:
            return self.error_payload(
                "target_blocked",
                "家具所在格不能作为移动目标",
                clientSeq=raw_seq,
            )
        for other_id, other in runtime.motions.items():
            if other_id == player_id:
                continue
            if other.target_x is not None and other.target_y is not None:
                occupied = (int(round(other.target_x)), int(round(other.target_y)))
            else:
                occupied = (int(round(other.x)), int(round(other.y)))
            if requested == occupied:
                return self.error_payload(
                    "target_occupied",
                    "其他玩家已占用这个目标格",
                    clientSeq=raw_seq,
                    occupiedBy=other_id,
                )
        goal = requested
        start = (
            min(max(int(round(motion.x)), 0), runtime.columns - 1),
            min(max(int(round(motion.y)), 0), runtime.rows - 1),
        )
        if start in runtime.blocked:
            return self.error_payload(
                "position_invalid",
                "玩家当前位置与阻挡格冲突",
                clientSeq=raw_seq,
            )
        route = astar(
            start,
            goal,
            columns=runtime.columns,
            rows=runtime.rows,
            blocked=runtime.blocked,
        )
        if not route:
            return self.error_payload(
                "path_unavailable", "无法到达这个位置", clientSeq=raw_seq
            )
        points = [(float(x), float(y)) for x, y in route]
        if points and math.hypot(
            points[0][0] - motion.x, points[0][1] - motion.y
        ) < 0.05:
            points.pop(0)
        self._cancel_work_locked(runtime, player_id, motion)
        motion.path = points
        motion.target_x = float(goal[0])
        motion.target_y = float(goal[1])
        motion.last_seq = raw_seq
        return {
            "type": "move.accepted",
            "clientSeq": raw_seq,
            "seq": raw_seq,
            "tileX": int(motion.target_x),
            "tileY": int(motion.target_y),
            "targetX": motion.target_x,
            "targetY": motion.target_y,
            "path": [
                {"tileX": int(x), "tileY": int(y), "x": x, "y": y}
                for x, y in points
            ],
        }

    async def start_work(
        self,
        websocket: WebSocket,
        identity: PlayerIdentity,
        message: dict[str, Any],
    ) -> None:
        unexpected_fields = set(message) - {
            "type",
            "placementId",
            "seatId",
            "clientSeq",
        }
        if unexpected_fields:
            await self.send_error(
                websocket,
                "message_invalid",
                "工作消息包含不支持的字段",
                fields=sorted(unexpected_fields),
            )
            return
        placement_id = message.get("placementId")
        seat_id = message.get("seatId")
        raw_seq = message.get("clientSeq")
        if (
            not isinstance(placement_id, str)
            or not placement_id
            or len(placement_id) > 128
            or not isinstance(seat_id, str)
            or not seat_id
            or len(seat_id) > 128
        ):
            await self.send_error(websocket, "seat_invalid", "工作座位无效")
            return
        if isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 0:
            await self.send_error(websocket, "seq_invalid", "工作序号无效")
            return

        runtime = await self.runtime(identity.run_id)
        async with runtime.lock:
            motion = runtime.motions.get(identity.player_id)
            if motion is None:
                reply = self.error_payload("player_missing", "玩家不存在")
            elif raw_seq <= motion.last_seq:
                reply = {
                    "type": "work.ignored",
                    "clientSeq": raw_seq,
                    "reason": "stale",
                }
            else:
                now = time.monotonic()
                elapsed = now - motion.last_action_at
                minimum = self.settings.movement_action_interval_seconds
                if motion.last_action_at and elapsed < minimum:
                    reply = self.error_payload(
                        "rate_limited",
                        "工作指令过于频繁",
                        retryAfterMs=max(1, math.ceil((minimum - elapsed) * 1000)),
                        clientSeq=raw_seq,
                    )
                else:
                    motion.last_action_at = now
                    reply = self._plan_work(
                        runtime,
                        identity.player_id,
                        motion,
                        placement_id,
                        seat_id,
                        raw_seq,
                    )
        await websocket.send_json(reply)

    def _plan_work(
        self,
        runtime: RunRuntime,
        player_id: str,
        motion: PlayerMotion,
        placement_id: str,
        seat_id: str,
        raw_seq: int,
    ) -> dict[str, Any]:
        key = (placement_id, seat_id)
        seat = runtime.work_seats.get(key)
        if seat is None:
            return self.error_payload(
                "seat_not_found", "这个地图没有该工作座位", clientSeq=raw_seq
            )
        occupied = runtime.seat_occupancy.get(key)
        if occupied is not None and occupied["playerId"] != player_id:
            return self.error_payload(
                "seat_occupied",
                "这个工作座位已被占用",
                clientSeq=raw_seq,
                occupiedBy=occupied["playerId"],
            )
        goal = (int(seat["x"]), int(seat["y"]))
        for other_id, other in runtime.motions.items():
            if other_id == player_id:
                continue
            other_goal = (
                (int(round(other.target_x)), int(round(other.target_y)))
                if other.target_x is not None and other.target_y is not None
                else (int(round(other.x)), int(round(other.y)))
            )
            if goal == other_goal:
                return self.error_payload(
                    "target_occupied",
                    "其他玩家已占用这个工作座位",
                    clientSeq=raw_seq,
                    occupiedBy=other_id,
                )
        start = (
            min(max(int(round(motion.x)), 0), runtime.columns - 1),
            min(max(int(round(motion.y)), 0), runtime.rows - 1),
        )
        route = astar(
            start,
            goal,
            columns=runtime.columns,
            rows=runtime.rows,
            blocked=runtime.blocked,
        )
        if not route:
            return self.error_payload(
                "path_unavailable", "无法到达这个工作座位", clientSeq=raw_seq
            )
        points = [(float(x), float(y)) for x, y in route]
        if points and math.hypot(points[0][0] - motion.x, points[0][1] - motion.y) < 0.05:
            points.pop(0)

        self._cancel_work_locked(runtime, player_id, motion)
        reservation = {
            "placementId": placement_id,
            "seatId": seat_id,
            "playerId": player_id,
            "state": "reserved" if points else "active",
            "facing": seat["facing"],
        }
        runtime.player_work[player_id] = reservation
        runtime.seat_occupancy[key] = reservation
        motion.path = points
        motion.target_x = float(goal[0])
        motion.target_y = float(goal[1])
        motion.last_seq = raw_seq
        if not points:
            motion.activity = {
                "type": "work",
                "placementId": placement_id,
                "seatId": seat_id,
                "facing": seat["facing"],
            }
        return {
            "type": "work.accepted",
            "clientSeq": raw_seq,
            "placementId": placement_id,
            "seatId": seat_id,
            "facing": seat["facing"],
            "tileX": goal[0],
            "tileY": goal[1],
            "active": reservation["state"] == "active",
            "path": [
                {"tileX": int(x), "tileY": int(y), "x": x, "y": y}
                for x, y in points
            ],
        }

    async def stop_work(
        self,
        websocket: WebSocket,
        identity: PlayerIdentity,
        message: dict[str, Any],
    ) -> None:
        unexpected_fields = set(message) - {"type", "clientSeq"}
        if unexpected_fields:
            await self.send_error(
                websocket,
                "message_invalid",
                "停止工作消息包含不支持的字段",
                fields=sorted(unexpected_fields),
            )
            return
        raw_seq = message.get("clientSeq")
        if isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 0:
            await self.send_error(websocket, "seq_invalid", "工作序号无效")
            return
        runtime = await self.runtime(identity.run_id)
        async with runtime.lock:
            motion = runtime.motions.get(identity.player_id)
            if motion is None:
                reply = self.error_payload("player_missing", "玩家不存在")
            elif raw_seq <= motion.last_seq:
                reply = {
                    "type": "work.ignored",
                    "clientSeq": raw_seq,
                    "reason": "stale",
                }
            else:
                now = time.monotonic()
                elapsed = now - motion.last_action_at
                minimum = self.settings.movement_action_interval_seconds
                if motion.last_action_at and elapsed < minimum:
                    reply = self.error_payload(
                        "rate_limited",
                        "工作指令过于频繁",
                        retryAfterMs=max(1, math.ceil((minimum - elapsed) * 1000)),
                        clientSeq=raw_seq,
                    )
                else:
                    had_work = identity.player_id in runtime.player_work
                    self._cancel_work_locked(runtime, identity.player_id, motion)
                    motion.path = []
                    motion.target_x = None
                    motion.target_y = None
                    motion.last_seq = raw_seq
                    motion.last_action_at = now
                    reply = {
                        "type": "work.stopped",
                        "clientSeq": raw_seq,
                        "stopped": had_work,
                    }
        await websocket.send_json(reply)

    @staticmethod
    def _cancel_work_locked(
        runtime: RunRuntime,
        player_id: str,
        motion: PlayerMotion,
    ) -> bool:
        reservation = runtime.player_work.pop(player_id, None)
        motion.activity = None
        if reservation is None:
            return False
        key = (str(reservation["placementId"]), str(reservation["seatId"]))
        current = runtime.seat_occupancy.get(key)
        if current is reservation or (
            current is not None and current.get("playerId") == player_id
        ):
            runtime.seat_occupancy.pop(key, None)
        return True

    @staticmethod
    def _restore_initial_work(
        runtime: RunRuntime,
        initial_activities: list[dict[str, Any]],
    ) -> None:
        """Restore only activities whose player is still standing at that seat."""

        for activity in initial_activities:
            player_id = str(activity["playerId"])
            key = (str(activity["placementId"]), str(activity["seatId"]))
            motion = runtime.motions.get(player_id)
            seat = runtime.work_seats.get(key)
            if motion is None or seat is None or key in runtime.seat_occupancy:
                continue
            if math.hypot(motion.x - float(seat["x"]), motion.y - float(seat["y"])) > 0.05:
                continue
            reservation = {
                "placementId": key[0],
                "seatId": key[1],
                "playerId": player_id,
                "state": "active",
                "facing": str(seat["facing"]),
            }
            runtime.player_work[player_id] = reservation
            runtime.seat_occupancy[key] = reservation
            motion.activity = {
                "type": "work",
                "placementId": key[0],
                "seatId": key[1],
                "facing": str(seat["facing"]),
            }

    async def reload_run(self, run_id: str) -> None:
        state = await asyncio.to_thread(self.service.review_state, run_id)
        work_seats = await asyncio.to_thread(self.service.work_seats_for_run, run_id)
        initial_activities = await asyncio.to_thread(
            self.service.initial_activities_for_run, run_id
        )
        runtime = await self.runtime(run_id)
        async with runtime.lock:
            runtime.paused = bool(state["run"]["paused"])
            runtime.speed = float(state["run"]["speed"])
            runtime.motions = {
                player["id"]: PlayerMotion(
                    x=float(player["x"]), y=float(player["y"])
                )
                for player in state["players"]
            }
            runtime.work_seats = {
                (seat["placementId"], seat["seatId"]): seat
                for seat in work_seats
            }
            runtime.seat_occupancy.clear()
            runtime.player_work.clear()
            self._restore_initial_work(runtime, initial_activities)
            snapshot = self.snapshot_payload(runtime)
        await self.broadcast_event(run_id, snapshot)
        await self.control_changed(run_id)

    async def control_changed(self, run_id: str) -> None:
        state = await asyncio.to_thread(self.service.review_state, run_id)
        runtime = await self.runtime(run_id)
        async with runtime.lock:
            runtime.paused = bool(state["run"]["paused"])
            runtime.speed = float(state["run"]["speed"])
        await self.broadcast_event(
            run_id,
            {
                "type": "review.control",
                **state["run"],
            },
        )

    async def broadcast_event(self, run_id: str, payload: dict[str, Any]) -> None:
        runtime = self.runtimes.get(run_id)
        if runtime is None:
            return
        async with runtime.lock:
            sockets = [
                websocket
                for player_sockets in runtime.connections.values()
                for websocket in tuple(player_sockets)
            ]
        await self._send_sockets(runtime, sockets, payload)

    async def send_to_player(
        self,
        run_id: str,
        player_id: str,
        payload: dict[str, Any],
    ) -> None:
        runtime = self.runtimes.get(run_id)
        if runtime is None:
            return
        async with runtime.lock:
            sockets = list(runtime.connections.get(player_id, ()))
        await self._send_sockets(runtime, sockets, payload)

    @staticmethod
    async def _send_sockets(
        runtime: RunRuntime,
        sockets: list[WebSocket],
        payload: dict[str, Any],
    ) -> None:
        if not sockets:
            return
        results = await asyncio.gather(
            *(websocket.send_json(payload) for websocket in sockets),
            return_exceptions=True,
        )
        failed = [
            websocket
            for websocket, result in zip(sockets, results, strict=True)
            if isinstance(result, Exception)
        ]
        if failed:
            async with runtime.lock:
                disconnected: list[str] = []
                for player_id, player_sockets in list(runtime.connections.items()):
                    for websocket in failed:
                        player_sockets.discard(websocket)
                    if not player_sockets:
                        runtime.connections.pop(player_id, None)
                        disconnected.append(player_id)
                for player_id in disconnected:
                    motion = runtime.motions.get(player_id)
                    if motion is not None:
                        if RealtimeManager._cancel_work_locked(runtime, player_id, motion):
                            motion.path = []
                            motion.target_x = None
                            motion.target_y = None

    async def broadcast_positions(self, runtime: RunRuntime) -> None:
        async with runtime.lock:
            payload = self.positions_payload(runtime)
        await self.broadcast_event(runtime.run_id, payload)

    def positions_payload(self, runtime: RunRuntime) -> dict[str, Any]:
        online = {
            player_id
            for player_id, sockets in runtime.connections.items()
            if sockets
        }
        return {
            "type": "world.positions",
            "serverTime": utc_now_iso(),
            "tick": runtime.tick,
            "serverSeq": runtime.tick,
            "paused": runtime.paused,
            "speed": runtime.speed,
            "players": [
                {
                    "id": player_id,
                    "x": round(motion.x, 4),
                    "y": round(motion.y, 4),
                    "targetX": motion.target_x,
                    "targetY": motion.target_y,
                    "moving": motion.moving,
                    "online": player_id in online,
                    "activity": motion.activity,
                }
                for player_id, motion in sorted(runtime.motions.items())
            ],
            "seatOccupancy": [
                {
                    "placementId": value["placementId"],
                    "seatId": value["seatId"],
                    "playerId": value["playerId"],
                    "state": value["state"],
                }
                for _, value in sorted(runtime.seat_occupancy.items())
            ],
        }

    def snapshot_payload(self, runtime: RunRuntime) -> dict[str, Any]:
        return {
            "type": "world.snapshot",
            "runId": runtime.run_id,
            "columns": runtime.columns,
            "rows": runtime.rows,
            "blockedCells": [
                {"x": x, "y": y} for x, y in sorted(runtime.blocked)
            ],
            **{
                key: value
                for key, value in self.positions_payload(runtime).items()
                if key != "type"
            },
        }

    @staticmethod
    def error_payload(code: str, message: str, **details: Any) -> dict[str, Any]:
        return {"type": "error", "code": code, "message": message, **details}

    @classmethod
    async def send_error(
        cls, websocket: WebSocket, code: str, message: str, **details: Any
    ) -> None:
        await websocket.send_json(cls.error_payload(code, message, **details))

    async def _tick_forever(self) -> None:
        interval = 1 / self.settings.simulation_hz
        broadcast_every = max(
            1, round(self.settings.simulation_hz / self.settings.broadcast_hz)
        )
        previous = time.monotonic()
        while True:
            started = time.monotonic()
            delta = min(0.1, max(0.0, started - previous))
            previous = started
            for runtime in list(self.runtimes.values()):
                async with runtime.lock:
                    runtime.tick += 1
                    if not runtime.paused:
                        self._advance(runtime, delta)
                    should_broadcast = runtime.tick % broadcast_every == 0
                if should_broadcast:
                    await self.broadcast_positions(runtime)
                await self._flush(runtime)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    def _advance(self, runtime: RunRuntime, delta: float) -> None:
        max_distance = self.settings.movement_tiles_per_second * runtime.speed * delta
        for player_id, motion in runtime.motions.items():
            remaining = max_distance
            while motion.path and remaining > 0:
                target_x, target_y = motion.path[0]
                dx = target_x - motion.x
                dy = target_y - motion.y
                distance = math.hypot(dx, dy)
                if distance <= remaining or distance < 1e-8:
                    motion.x = target_x
                    motion.y = target_y
                    motion.path.pop(0)
                    remaining = max(0.0, remaining - distance)
                else:
                    ratio = remaining / distance
                    motion.x += dx * ratio
                    motion.y += dy * ratio
                    remaining = 0
                motion.dirty = True
            reservation = runtime.player_work.get(player_id)
            if reservation is not None and reservation["state"] == "reserved" and not motion.path:
                seat = runtime.work_seats.get(
                    (reservation["placementId"], reservation["seatId"])
                )
                if seat is None or math.hypot(motion.x - seat["x"], motion.y - seat["y"]) > 0.05:
                    self._cancel_work_locked(runtime, player_id, motion)
                    motion.target_x = None
                    motion.target_y = None
                    continue
                reservation["state"] = "active"
                motion.activity = {
                    "type": "work",
                    "placementId": reservation["placementId"],
                    "seatId": reservation["seatId"],
                    "facing": reservation["facing"],
                }

    async def _flush(self, runtime: RunRuntime, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - runtime.last_flush_at < self.settings.position_flush_seconds:
            return
        async with runtime.lock:
            positions = {
                player_id: (motion.x, motion.y)
                for player_id, motion in runtime.motions.items()
                if force or motion.dirty
            }
            for player_id in positions:
                runtime.motions[player_id].dirty = False
            runtime.last_flush_at = now
        if positions:
            try:
                await asyncio.to_thread(
                    self.service.persist_positions, runtime.run_id, positions
                )
            except GameServiceError:
                return
