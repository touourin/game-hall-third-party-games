from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codex_v0.realtime as realtime_module
from codex_v0.config import Settings
from codex_v0.db import Database
from codex_v0.main import create_app
from codex_v0.pathfinding import FURNITURE_BLOCKED, astar
from codex_v0.realtime import PlayerMotion, RealtimeManager, RunRuntime
from codex_v0.service import GameService
from tests.support import install_test_core_v1_release, install_test_core_v2_release


ADMIN_TOKEN = "ws-bootstrap-admin"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(
        Settings(
            database_path=tmp_path / "ws.sqlite3",
            web_dir=WEB_DIR,
            admin_token=ADMIN_TOKEN,
            movement_action_interval_seconds=0.05,
            position_flush_seconds=0.05,
        )
    )
    with TestClient(app) as active_client:
        install_test_core_v1_release(app)
        yield active_client


@pytest.fixture
def v2_client(tmp_path: Path):
    app = create_app(
        Settings(
            database_path=tmp_path / "v2-ws.sqlite3",
            web_dir=WEB_DIR,
            admin_token=ADMIN_TOKEN,
            movement_action_interval_seconds=0.05,
            position_flush_seconds=0.05,
        )
    )
    with TestClient(app) as active_client:
        install_test_core_v2_release(app)
        yield active_client


def create_run(
    client: TestClient,
    *,
    layout_id: str = "world.mid-growth-v2",
) -> dict:
    response = client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": "ws", "layoutId": layout_id},
    )
    assert response.status_code == 200
    return response.json()


def receive_type(socket, expected: str, attempts: int = 30) -> dict:
    for _ in range(attempts):
        message = socket.receive_json()
        if message.get("type") == expected:
            return message
    raise AssertionError(f"did not receive {expected}")


def authenticate(socket, token: str) -> tuple[dict, dict]:
    socket.send_json({"type": "auth", "token": token})
    auth = receive_type(socket, "auth.ok")
    snapshot = receive_type(socket, "world.snapshot")
    return auth, snapshot


def test_initial_work_restores_only_for_players_still_at_the_frozen_seat() -> None:
    runtime = RunRuntime(
        run_id="run-initial-work",
        motions={
            "ava": PlayerMotion(x=4, y=4),
            "ben": PlayerMotion(x=3, y=4),
        },
        paused=False,
        speed=1,
        columns=20,
        rows=12,
        blocked=frozenset(),
        work_seats={
            ("desk", "seat-a"): {
                "placementId": "desk",
                "seatId": "seat-a",
                "x": 4,
                "y": 4,
                "facing": "northwest",
            },
            ("desk", "seat-b"): {
                "placementId": "desk",
                "seatId": "seat-b",
                "x": 6,
                "y": 4,
                "facing": "northeast",
            },
        },
    )

    RealtimeManager._restore_initial_work(
        runtime,
        [
            {
                "playerId": "ava",
                "type": "work",
                "placementId": "desk",
                "seatId": "seat-a",
                "facing": "northwest",
            },
            {
                "playerId": "ben",
                "type": "work",
                "placementId": "desk",
                "seatId": "seat-b",
                "facing": "northeast",
            },
        ],
    )

    assert runtime.motions["ava"].activity == {
        "type": "work",
        "placementId": "desk",
        "seatId": "seat-a",
        "facing": "northwest",
    }
    assert runtime.motions["ben"].activity is None
    assert list(runtime.seat_occupancy) == [("desk", "seat-a")]


def test_v2_websocket_broadcasts_initial_work_and_move_releases_it_without_reward(
    v2_client: TestClient,
) -> None:
    created = create_run(v2_client, layout_id="world.mid-growth-v3")
    run_id = created["run"]["id"]
    gus = next(player for player in created["players"] if player["id"] == "gus")
    ava = next(player for player in created["players"] if player["id"] == "ava")

    with v2_client.websocket_connect(f"/ws/{run_id}") as gus_socket:
        _, initial = authenticate(gus_socket, gus["token"])
        assert sum(player["activity"] is not None for player in initial["players"]) == 7
        assert len(initial["seatOccupancy"]) == 7
        assert next(
            player for player in initial["players"] if player["id"] == "gus"
        )["activity"] is None

        with v2_client.websocket_connect(f"/ws/{run_id}") as ava_socket:
            authenticate(ava_socket, ava["token"])
            ava_socket.send_json(
                {"type": "move.target", "tileX": 7, "tileY": 5, "clientSeq": 0}
            )
            assert receive_type(ava_socket, "move.accepted")["clientSeq"] == 0
            for _ in range(30):
                positions = receive_type(ava_socket, "world.positions")
                ava_state = next(
                    player for player in positions["players"] if player["id"] == "ava"
                )
                if ava_state["activity"] is None:
                    break
            assert ava_state["activity"] is None
            assert len(positions["seatOccupancy"]) == 6

            bootstrap = v2_client.get(
                f"/api/bootstrap?run={run_id}",
                headers={"Authorization": f"Bearer {ava['token']}"},
            ).json()
            assert bootstrap["player"]["balanceCents"] == 0
            with v2_client.app.state.database.connection() as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM ledger WHERE run_id = ?", (run_id,)
                ).fetchone()[0] == 0

            reset = v2_client.post(
                f"/api/review/runs/{run_id}/reset",
                headers={"Authorization": f"Bearer {created['controllerToken']}"},
            )
            assert reset.status_code == 200
            restored = receive_type(ava_socket, "world.snapshot")
            assert len(restored["seatOccupancy"]) == 7
            assert sum(
                player["activity"] is not None for player in restored["players"]
            ) == 7


def test_astar_routes_around_furniture_and_rejects_blocked_goal() -> None:
    route = astar((1, 1), (6, 3), columns=20, rows=12)
    assert route[0] == (1, 1)
    assert route[-1] == (6, 3)
    assert not set(route) & FURNITURE_BLOCKED
    assert astar((1, 1), (3, 2), columns=20, rows=12) == []


def test_websocket_auth_and_authoritative_movement_contract(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")

    with client.websocket_connect(f"/ws/{run_id}") as socket:
        auth, snapshot = authenticate(socket, ava["token"])
        assert auth == {
            "type": "auth.ok",
            "runId": run_id,
            "playerId": "ava",
            "lastClientSeq": -1,
        }
        assert snapshot["columns"] == 20
        assert snapshot["rows"] == 12
        assert snapshot["serverSeq"] == snapshot["tick"]

        socket.send_json(
            {"type": "move.target", "tileX": 3, "tileY": 2, "clientSeq": 1}
        )
        blocked = receive_type(socket, "error")
        assert blocked["code"] == "target_blocked"
        assert blocked["clientSeq"] == 1

        time.sleep(0.06)
        socket.send_json(
            {"type": "move.target", "tileX": 20, "tileY": 1, "clientSeq": 2}
        )
        out_of_bounds = receive_type(socket, "error")
        assert out_of_bounds["code"] == "target_out_of_bounds"
        assert out_of_bounds["clientSeq"] == 2

        time.sleep(0.06)
        socket.send_json(
            {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": 3}
        )
        accepted = receive_type(socket, "move.accepted")
        assert accepted["clientSeq"] == 3
        assert accepted["tileX"] == 2
        assert accepted["tileY"] == 1
        assert accepted["path"][-1]["tileX"] == 2

        socket.send_json(
            {"type": "move.target", "tileX": 2, "tileY": 2, "clientSeq": 4}
        )
        limited = receive_type(socket, "error")
        assert limited["code"] == "rate_limited"
        assert 1 <= limited["retryAfterMs"] <= 50

        socket.send_json(
            {"type": "move.target", "tileX": 2, "tileY": 2, "clientSeq": 3}
        )
        stale = receive_type(socket, "move.ignored")
        assert stale["clientSeq"] == 3
        assert stale["reason"] == "stale"

        for _ in range(20):
            positions = receive_type(socket, "world.positions")
            ava_position = next(
                player for player in positions["players"] if player["id"] == "ava"
            )
            if ava_position["x"] > 1:
                break
        assert ava_position["x"] > 1
        assert positions["serverSeq"] == positions["tick"]

    with client.websocket_connect(f"/ws/{run_id}") as reconnected:
        _, reconnect_snapshot = authenticate(reconnected, ava["token"])
        restored = next(
            player
            for player in reconnect_snapshot["players"]
            if player["id"] == "ava"
        )
        assert restored["x"] > 1


def test_websocket_move_requires_canonical_fields_and_sequence(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")

    invalid_messages = [
        ({"type": "move.target", "x": 2, "y": 1, "clientSeq": 0}, "message_invalid"),
        ({"type": "move.target", "tileX": 2, "tileY": 1, "seq": 0}, "message_invalid"),
        (
            {
                "type": "move.target",
                "tileX": 2,
                "tileY": 1,
                "clientSeq": 0,
                "x": 2,
            },
            "message_invalid",
        ),
        ({"type": "move.target", "tileX": 2, "tileY": 1}, "seq_invalid"),
        (
            {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": -1},
            "seq_invalid",
        ),
        (
            {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": True},
            "seq_invalid",
        ),
        (
            {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": 1.0},
            "seq_invalid",
        ),
    ]
    with client.websocket_connect(f"/ws/{run_id}") as socket:
        auth, _ = authenticate(socket, ava["token"])
        assert auth["lastClientSeq"] == -1
        for message, expected_code in invalid_messages:
            socket.send_json(message)
            error = receive_type(socket, "error")
            assert error["code"] == expected_code

        socket.send_json(
            {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": 0}
        )
        assert receive_type(socket, "move.accepted")["clientSeq"] == 0


def test_reconnect_exposes_last_sequence_for_immediate_work_and_move(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")

    with client.websocket_connect(f"/ws/{run_id}") as first:
        auth, _ = authenticate(first, ava["token"])
        assert auth["lastClientSeq"] == -1
        first.send_json(
            {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": 7}
        )
        assert receive_type(first, "move.accepted")["clientSeq"] == 7

    time.sleep(0.06)
    with client.websocket_connect(f"/ws/{run_id}") as second:
        auth, _ = authenticate(second, ava["token"])
        assert auth["lastClientSeq"] == 7
        second.send_json(
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "clientSeq": auth["lastClientSeq"] + 1,
            }
        )
        assert receive_type(second, "work.accepted")["clientSeq"] == 8

    time.sleep(0.06)
    with client.websocket_connect(f"/ws/{run_id}") as third:
        auth, _ = authenticate(third, ava["token"])
        assert auth["lastClientSeq"] == 8
        third.send_json(
            {
                "type": "move.target",
                "tileX": 2,
                "tileY": 1,
                "clientSeq": auth["lastClientSeq"] + 1,
            }
        )
        assert receive_type(third, "move.accepted")["clientSeq"] == 9


def test_websocket_target_occupancy_prefers_other_players_target(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    ben = next(player for player in created["players"] if player["id"] == "ben")

    with client.websocket_connect(f"/ws/{run_id}") as ava_socket:
        authenticate(ava_socket, ava["token"])
        with client.websocket_connect(f"/ws/{run_id}") as ben_socket:
            authenticate(ben_socket, ben["token"])

            ben_socket.send_json(
                {
                    "type": "move.target",
                    "tileX": 7,
                    "tileY": 1,
                    "clientSeq": 1,
                }
            )
            assert receive_type(ben_socket, "move.accepted")["tileX"] == 7

            ava_socket.send_json(
                {
                    "type": "move.target",
                    "tileX": 7,
                    "tileY": 1,
                    "clientSeq": 1,
                }
            )
            occupied = receive_type(ava_socket, "error")
            assert occupied["code"] == "target_occupied"
            assert occupied["occupiedBy"] == "ben"


def test_websocket_rejects_invalid_first_message(client: TestClient) -> None:
    created = create_run(client)
    with client.websocket_connect(f"/ws/{created['run']['id']}") as socket:
        socket.send_json({"type": "move.target", "tileX": 2, "tileY": 1})
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "auth_required"


def test_opening_snapshot_positions_and_collision_stay_within_14_by_9(
    client: TestClient,
) -> None:
    created = create_run(client, layout_id="world.opening-empty-v1")
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")

    with client.websocket_connect(f"/ws/{run_id}") as socket:
        _, snapshot = authenticate(socket, ava["token"])

    assert (snapshot["columns"], snapshot["rows"]) == (14, 9)
    assert all(
        0 <= cell["x"] < snapshot["columns"]
        and 0 <= cell["y"] < snapshot["rows"]
        for cell in snapshot["blockedCells"]
    )
    assert all(
        0 <= player["x"] < snapshot["columns"]
        and 0 <= player["y"] < snapshot["rows"]
        and (
            player["targetX"] is None
            or 0 <= player["targetX"] < snapshot["columns"]
        )
        and (
            player["targetY"] is None
            or 0 <= player["targetY"] < snapshot["rows"]
        )
        for player in snapshot["players"]
    )


def test_work_messages_require_non_negative_integer_sequence_and_reject_ordering(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    invalid = [
        (
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
            },
            "seq_invalid",
        ),
        (
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "seq": 0,
            },
            "message_invalid",
        ),
        (
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "clientSeq": 0,
                "seq": 0,
            },
            "message_invalid",
        ),
        (
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "clientSeq": -1,
            },
            "seq_invalid",
        ),
        ({"type": "work.stop"}, "seq_invalid"),
        ({"type": "work.stop", "seq": 0}, "message_invalid"),
        (
            {"type": "work.stop", "clientSeq": 0, "seq": 0},
            "message_invalid",
        ),
        ({"type": "work.stop", "clientSeq": True}, "seq_invalid"),
    ]

    with client.websocket_connect(f"/ws/{run_id}") as socket:
        authenticate(socket, ava["token"])
        for message, expected_code in invalid:
            socket.send_json(message)
            assert receive_type(socket, "error")["code"] == expected_code

        socket.send_json(
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "clientSeq": 0,
            }
        )
        assert receive_type(socket, "work.accepted")["clientSeq"] == 0

        socket.send_json({"type": "work.stop", "clientSeq": 1})
        limited = receive_type(socket, "error")
        assert limited["code"] == "rate_limited"
        assert 1 <= limited["retryAfterMs"] <= 50

        socket.send_json({"type": "work.stop", "clientSeq": 0})
        stale = receive_type(socket, "work.ignored")
        assert stale == {
            "type": "work.ignored",
            "clientSeq": 0,
            "reason": "stale",
        }

        time.sleep(0.06)
        socket.send_json({"type": "work.stop", "clientSeq": 1})
        stopped = receive_type(socket, "work.stopped")
        assert stopped["clientSeq"] == 1
        assert stopped["stopped"] is True


def test_work_seat_is_authoritative_exclusive_and_has_no_economy_reward(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    ben = next(player for player in created["players"] if player["id"] == "ben")
    ava_headers = {"Authorization": f"Bearer {ava['token']}"}
    before = client.get(f"/api/bootstrap?run={run_id}", headers=ava_headers).json()
    with client.app.state.database.connection() as connection:
        ledger_rows_before = connection.execute(
            "SELECT COUNT(*) FROM ledger WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

    with client.websocket_connect(f"/ws/{run_id}") as ava_socket:
        _, initial = authenticate(ava_socket, ava["token"])
        assert initial["seatOccupancy"] == []
        assert all(player["activity"] is None for player in initial["players"])
        ava_socket.send_json(
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "clientSeq": 1,
            }
        )
        accepted = receive_type(ava_socket, "work.accepted")
        assert (accepted["tileX"], accepted["tileY"]) == (4, 4)
        assert accepted["path"][-1]["tileX"] == 4

        with client.websocket_connect(f"/ws/{run_id}") as ben_socket:
            authenticate(ben_socket, ben["token"])
            ben_socket.send_json(
                {
                    "type": "work.start",
                    "placementId": "growth-v2-desk",
                    "seatId": "seat-se",
                    "clientSeq": 1,
                }
            )
            occupied = receive_type(ben_socket, "error")
            assert occupied["code"] == "seat_occupied"
            assert occupied["occupiedBy"] == "ava"

        for _ in range(40):
            positions = receive_type(ava_socket, "world.positions")
            ava_state = next(player for player in positions["players"] if player["id"] == "ava")
            if ava_state["activity"] is not None:
                break
        assert ava_state["activity"] == {
            "type": "work",
            "placementId": "growth-v2-desk",
            "seatId": "seat-se",
            "facing": "northwest",
        }
        assert positions["seatOccupancy"] == [
            {
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "playerId": "ava",
                "state": "active",
            }
        ]

        ava_socket.send_json({"type": "work.stop", "clientSeq": 2})
        assert receive_type(ava_socket, "work.stopped")["stopped"] is True

    after = client.get(f"/api/bootstrap?run={run_id}", headers=ava_headers).json()
    assert after["player"]["balanceCents"] == before["player"]["balanceCents"] == 0
    with client.app.state.database.connection() as connection:
        ledger_rows_after = connection.execute(
            "SELECT COUNT(*) FROM ledger WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    assert ledger_rows_after == ledger_rows_before


def test_move_releases_work_seat_and_reset_clears_all_reservations(
    client: TestClient,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    ben = next(player for player in created["players"] if player["id"] == "ben")

    with client.websocket_connect(f"/ws/{run_id}") as ava_socket:
        authenticate(ava_socket, ava["token"])
        with client.websocket_connect(f"/ws/{run_id}") as ben_socket:
            authenticate(ben_socket, ben["token"])
            ava_socket.send_json(
                {
                    "type": "work.start",
                    "placementId": "growth-v2-desk",
                    "seatId": "seat-ne",
                    "clientSeq": 0,
                }
            )
            assert receive_type(ava_socket, "work.accepted")["seatId"] == "seat-ne"

            time.sleep(0.06)
            ava_socket.send_json(
                {"type": "move.target", "tileX": 2, "tileY": 1, "clientSeq": 1}
            )
            assert receive_type(ava_socket, "move.accepted")["clientSeq"] == 1

            ben_socket.send_json(
                {
                    "type": "work.start",
                    "placementId": "growth-v2-desk",
                    "seatId": "seat-ne",
                    "clientSeq": 0,
                }
            )
            assert receive_type(ben_socket, "work.accepted")["seatId"] == "seat-ne"

            reset = client.post(
                f"/api/review/runs/{run_id}/reset",
                headers={
                    "Authorization": f"Bearer {created['controllerToken']}"
                },
            )
            assert reset.status_code == 200

            ava_socket.send_json(
                {
                    "type": "work.start",
                    "placementId": "growth-v2-desk",
                    "seatId": "seat-ne",
                    "clientSeq": 0,
                }
            )
            accepted_after_reset = receive_type(ava_socket, "work.accepted")
            assert accepted_after_reset["seatId"] == "seat-ne"


def test_failed_work_path_does_not_reserve_seat(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    ben = next(player for player in created["players"] if player["id"] == "ben")
    original_astar = realtime_module.astar

    with client.websocket_connect(f"/ws/{run_id}") as ava_socket:
        authenticate(ava_socket, ava["token"])
        monkeypatch.setattr(realtime_module, "astar", lambda *args, **kwargs: [])
        ava_socket.send_json(
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-se",
                "clientSeq": 0,
            }
        )
        unavailable = receive_type(ava_socket, "error")
        assert unavailable["code"] == "path_unavailable"

        monkeypatch.setattr(realtime_module, "astar", original_astar)
        with client.websocket_connect(f"/ws/{run_id}") as ben_socket:
            authenticate(ben_socket, ben["token"])
            ben_socket.send_json(
                {
                    "type": "work.start",
                    "placementId": "growth-v2-desk",
                    "seatId": "seat-se",
                    "clientSeq": 0,
                }
            )
            assert receive_type(ben_socket, "work.accepted")["seatId"] == "seat-se"


def test_disconnect_releases_reserved_work_seat(client: TestClient) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    ben = next(player for player in created["players"] if player["id"] == "ben")
    with client.websocket_connect(f"/ws/{run_id}") as ava_socket:
        authenticate(ava_socket, ava["token"])
        ava_socket.send_json(
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-ne",
                "clientSeq": 1,
            }
        )
        assert receive_type(ava_socket, "work.accepted")["seatId"] == "seat-ne"

    with client.websocket_connect(f"/ws/{run_id}") as ben_socket:
        authenticate(ben_socket, ben["token"])
        ben_socket.send_json(
            {
                "type": "work.start",
                "placementId": "growth-v2-desk",
                "seatId": "seat-ne",
                "clientSeq": 1,
            }
        )
        assert receive_type(ben_socket, "work.accepted")["seatId"] == "seat-ne"


@pytest.mark.asyncio
async def test_send_to_player_does_not_deliver_to_third_party(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "private-ws.sqlite3", web_dir=WEB_DIR)
    database = Database(settings.database_path)
    database.initialize()
    service = GameService(database, settings)
    created = service.create_run("private delivery")
    manager = RealtimeManager(service, settings)
    runtime = await manager.runtime(created["run"]["id"])

    class FakeSocket:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.messages.append(payload)

    recipient = FakeSocket()
    third_party = FakeSocket()
    async with runtime.lock:
        runtime.connections["hana"].add(recipient)  # type: ignore[arg-type]
        runtime.connections["ava"].add(third_party)  # type: ignore[arg-type]

    event = {"type": "good-card.created", "card": {"recipientId": "hana"}}
    await manager.send_to_player(runtime.run_id, "hana", event)

    assert recipient.messages == [event]
    assert third_party.messages == []
