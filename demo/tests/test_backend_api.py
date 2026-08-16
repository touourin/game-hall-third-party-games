from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codex_v0.config import Settings
from codex_v0.identity import PlayerIdentity
from codex_v0.main import create_app
from codex_v0.service import GameServiceError
from tests.support import install_test_core_v1_release, install_test_core_v2_release


ADMIN_TOKEN = "test-bootstrap-admin"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture
def app(tmp_path: Path):
    settings = Settings(
        database_path=tmp_path / "game.sqlite3",
        web_dir=WEB_DIR,
        admin_token=ADMIN_TOKEN,
        movement_action_interval_seconds=0.05,
    )
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, client=("testclient", 50000)) as active_client:
        install_test_core_v1_release(app)
        yield active_client


@pytest.fixture
def v2_client(tmp_path: Path):
    v2_app = create_app(
        Settings(
            database_path=tmp_path / "v2-api.sqlite3",
            web_dir=WEB_DIR,
            admin_token=ADMIN_TOKEN,
            movement_action_interval_seconds=0.05,
        )
    )
    with TestClient(v2_app, client=("testclient", 50000)) as active_client:
        install_test_core_v2_release(v2_app)
        yield active_client


def create_run(client: TestClient, label: str = "backend-test") -> dict:
    response = client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": label, "layoutId": "world.mid-growth-v2"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def player_headers(player: dict, *, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {player['token']}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def controller_headers(run: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {run['controllerToken']}"}


def test_health_wal_and_loopback_review_creation(app, client: TestClient) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "database": "ok",
        "timeZone": "Asia/Taipei",
    }
    assert client.get("/").status_code == 200
    assert client.get("/review").status_code == 200
    with app.state.database.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    unauthorized = client.post("/api/review/runs", json={"label": "remote"})
    assert unauthorized.status_code == 401

    loopback_app = create_app(app.state.settings)
    with TestClient(loopback_app, client=("127.0.0.1", 50001)) as loopback:
        created = loopback.post(
            "/api/review/runs",
            json={"label": "local", "layoutId": "world.mid-growth-v2"},
        )
    assert created.status_code == 200
    assert created.json()["run"]["label"] == "local"


def test_run_creation_uses_isolated_random_credentials_and_fixed_names(
    client: TestClient,
) -> None:
    first = create_run(client, "first")
    second = create_run(client, "second")

    assert first["ok"] is True
    assert first["run"]["revision"] == 0
    assert [player["name"] for player in first["players"]] == [
        "Ava",
        "Ben",
        "Cleo",
        "Drew",
        "Eli",
        "Faye",
        "Gus",
        "Hana",
    ]
    assert [player["id"] for player in first["players"]] == [
        "ava",
        "ben",
        "cleo",
        "drew",
        "eli",
        "faye",
        "gus",
        "hana",
    ]
    assert len({player["token"] for player in first["players"]}) == 8
    assert first["controllerToken"] != second["controllerToken"]
    assert {player["token"] for player in first["players"]}.isdisjoint(
        {player["token"] for player in second["players"]}
    )
    assert first["reviewUrl"].endswith(
        f"#adminToken={first['controllerToken']}"
    )
    assert all(
        player["url"].endswith(f"#token={player['token']}")
        for player in first["players"]
    )

    run_id = first["run"]["id"]
    global_admin = client.get(
        f"/api/review/runs/{run_id}",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert global_admin.status_code == 401
    controlled = client.get(
        f"/api/review/runs/{run_id}", headers=controller_headers(first)
    )
    assert controlled.status_code == 200

    wrong_run = client.get(
        f"/api/bootstrap?run={second['run']['id']}",
        headers=player_headers(first["players"][0]),
    )
    assert wrong_run.status_code == 401


def test_review_layouts_require_explicit_available_choice(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.get(
        "/api/review/layouts",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 200
    layouts = response.json()["layouts"]
    assert [layout["id"] for layout in layouts] == [
        "world.opening-empty-v1",
        "world.mid-growth-v2",
        "world.opening-empty-v2",
        "world.mid-growth-v3",
    ]
    assert [(layout["columns"], layout["rows"]) for layout in layouts] == [
        (14, 9),
        (20, 12),
        (14, 9),
        (20, 12),
    ]
    assert [layout["requiredPackId"] for layout in layouts] == [
        "core-v1",
        "core-v1",
        "core-v2",
        "core-v2",
    ]
    # Both generations of a map answer to the same generation-free name, so
    # the picker text does not change when the active pack does.
    assert [layout["label"] for layout in layouts] == [
        "光秃开局办公室",
        "丰富中期办公室",
        "光秃开局办公室",
        "丰富中期办公室",
    ]
    assert [layout["available"] for layout in layouts] == [True, True, False, False]
    assert all(layout["reason"] is None for layout in layouts[:2])
    assert all(layout["reason"] for layout in layouts[2:])

    opening = client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": "opening", "layoutId": "world.opening-empty-v1"},
    ).json()
    frozen = opening["run"]["worldLayout"]
    assert (frozen["columns"], frozen["rows"]) == (14, 9)
    assert frozen["origin"] == {"x": 320, "y": 72}
    assert frozen["stage"] == "opening"

    missing = client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": "missing map"},
    )
    assert missing.status_code == 422
    unknown = client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": "unknown map", "layoutId": "world.unknown"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "layout_not_found"

    unavailable_app = create_app(
        Settings(
            database_path=tmp_path / "unavailable" / "game.sqlite3",
            web_dir=WEB_DIR,
            admin_token=ADMIN_TOKEN,
        )
    )
    with TestClient(unavailable_app) as unavailable:
        unknown_without_assets = unavailable.post(
            "/api/review/runs",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"label": "unknown without assets", "layoutId": "world.unknown"},
        )
        assert unknown_without_assets.status_code == 422
        assert unknown_without_assets.json()["error"] == "layout_not_found"

        blocked = unavailable.post(
            "/api/review/runs",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"label": "no assets", "layoutId": "world.opening-empty-v1"},
        )
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "asset_pack_unavailable"


def test_core_v2_api_exposes_and_freezes_both_new_maps(v2_client: TestClient) -> None:
    response = v2_client.get(
        "/api/review/layouts",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 200
    layouts = response.json()["layouts"]
    assert [layout["available"] for layout in layouts] == [False, False, True, True]

    old = v2_client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": "old", "layoutId": "world.mid-growth-v2"},
    )
    assert old.status_code == 409
    assert old.json()["error"] == "asset_pack_unavailable"

    for layout_id, activity_count in (
        ("world.opening-empty-v2", 3),
        ("world.mid-growth-v3", 7),
    ):
        created = v2_client.post(
            "/api/review/runs",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"label": layout_id, "layoutId": layout_id},
        )
        assert created.status_code == 200
        payload = created.json()
        frozen = payload["run"]["worldLayout"]
        assert payload["run"]["assetPack"]["packId"] == "core-v2"
        assert frozen["id"] == layout_id
        assert len(frozen["initialActivities"]) == activity_count
        gus = next(player for player in payload["players"] if player["id"] == "gus")
        bootstrap = v2_client.get(
            f"/api/bootstrap?run={payload['run']['id']}",
            headers=player_headers(gus),
        )
        assert bootstrap.status_code == 200
        assert bootstrap.json()["world"]["layout"] == frozen


def test_activating_core_v2_does_not_rebind_an_existing_v1_run(
    client: TestClient,
) -> None:
    original = create_run(client, "frozen-v1")
    original_binding = original["run"]["assetPack"]
    original_layout = original["run"]["worldLayout"]

    install_test_core_v2_release(client.app)
    current = client.get(
        f"/api/review/runs/{original['run']['id']}",
        headers=controller_headers(original),
    )
    assert current.status_code == 200
    assert current.json()["run"]["assetPack"] == original_binding
    assert current.json()["run"]["worldLayout"] == original_layout

    reset = client.post(
        f"/api/review/runs/{original['run']['id']}/reset",
        headers=controller_headers(original),
    )
    assert reset.status_code == 200
    assert reset.json()["run"]["assetPack"] == original_binding
    assert reset.json()["run"]["worldLayout"] == original_layout

    new_run = client.post(
        "/api/review/runs",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"label": "v2", "layoutId": "world.mid-growth-v3"},
    )
    assert new_run.status_code == 200
    assert new_run.json()["run"]["assetPack"]["packId"] == "core-v2"


def test_bootstrap_contract_and_run_scope(client: TestClient) -> None:
    created = create_run(client)
    player = created["players"][6]
    response = client.get(
        f"/api/bootstrap?run={created['run']['id']}",
        headers=player_headers(player),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["player"]["id"] == "gus"
    assert data["player"]["balanceCents"] == 0
    assert data["player"]["spin"] == {"available": True, "reward": None}
    assert data["player"]["goodCard"] == {
        "available": True,
        "recipientId": None,
    }
    assert len(data["players"]) == 8
    assert data["world"]["columns"] == 20
    assert data["world"]["rows"] == 12
    assert data["world"]["tileWidth"] == 32
    assert data["world"]["tileHeight"] == 16
    assert data["world"]["wheel"] == [1, 1, 2, 2, 3, 5, 10, 20]
    assert {"x": 3, "y": 2} in data["world"]["blockedCells"]


def test_spin_idempotency_is_scoped_by_review_day_and_daily_reset(
    client: TestClient, app
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    player = created["players"][0]
    control = controller_headers(created)
    play = player_headers(player, key="same-key")

    forced = client.post(
        f"/api/review/runs/{run_id}/force-wheel",
        headers=control,
        json={"reward": 20},
    ).json()
    assert forced["run"]["revision"] == 1

    first = client.post(f"/api/checkin/spin?run={run_id}", headers=play)
    assert first.status_code == 200
    first_data = first.json()
    expected = {
        "reward": 20,
        "rewardCents": 2000,
        "balanceCents": 2000,
        "alreadySpun": False,
        "replayed": False,
        "revision": 2,
    }
    assert {key: first_data[key] for key in expected} == expected
    replay = client.post(f"/api/checkin/spin?run={run_id}", headers=play).json()
    assert replay["balanceCents"] == 2000
    assert replay["replayed"] is True
    assert replay["revision"] == 2

    already = client.post(
        f"/api/checkin/spin?run={run_id}",
        headers=player_headers(player, key="different-key"),
    ).json()
    assert already["alreadySpun"] is True
    assert already["balanceCents"] == 2000
    assert already["revision"] == 2

    advanced = client.post(
        f"/api/review/runs/{run_id}/advance-day",
        headers=control,
        json={"days": 1},
    ).json()
    next_day = advanced["run"]["day"]
    assert advanced["run"]["revision"] == 3
    second_day = client.post(
        f"/api/checkin/spin?run={run_id}", headers=play
    ).json()
    assert second_day["day"] == next_day
    assert second_day["replayed"] is False
    assert second_day["balanceCents"] == 4000
    assert second_day["revision"] == 4

    reset = client.post(
        f"/api/review/runs/{run_id}/reset-daily", headers=control
    ).json()
    assert reset["run"]["revision"] == 5
    assert next(player for player in reset["players"] if player["id"] == "ava")[
        "balanceCents"
    ] == 2000

    with app.state.database.connection() as connection:
        spins = connection.execute(
            "SELECT local_day FROM daily_spins WHERE run_id = ? ORDER BY local_day",
            (run_id,),
        ).fetchall()
        endpoints = {
            str(row[0])
            for row in connection.execute(
                "SELECT endpoint FROM idempotency_keys WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
    assert len(spins) == 1
    assert f"daily-spin:{next_day}" not in endpoints
    assert any(endpoint.startswith("daily-spin:") for endpoint in endpoints)

    replay_after_reset = client.post(
        f"/api/checkin/spin?run={run_id}", headers=play
    ).json()
    assert replay_after_reset["replayed"] is False
    assert replay_after_reset["balanceCents"] == 4000
    assert replay_after_reset["revision"] == 6


def test_good_card_requires_idempotency_and_allows_one_atomic_send_per_day(
    client: TestClient, app
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    gus = next(player for player in created["players"] if player["id"] == "gus")
    no_key = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(gus),
        json={"recipientId": "hana"},
    )
    assert no_key.status_code == 422

    first = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(gus, key="card-key"),
        json={"recipientId": "hana"},
    )
    assert first.status_code == 200
    assert first.json()["card"]["recipientName"] == "Hana"
    assert first.json()["replayed"] is False
    assert first.json()["revision"] == 1

    replay = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(gus, key="card-key"),
        json={"recipientId": "hana"},
    ).json()
    assert replay["card"] == first.json()["card"]
    assert replay["replayed"] is True
    assert replay["revision"] == 1

    conflict = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(gus, key="new-key"),
        json={"recipientId": "ava"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "good_card_already_sent"

    advanced = client.post(
        f"/api/review/runs/{run_id}/advance-day",
        headers=controller_headers(created),
        json={"days": 1},
    ).json()
    next_day = advanced["run"]["day"]
    next_day_same_key = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(gus, key="card-key"),
        json={"recipientId": "hana"},
    )
    assert next_day_same_key.status_code == 200
    assert next_day_same_key.json()["replayed"] is False

    reset = client.post(
        f"/api/review/runs/{run_id}/reset-daily",
        headers=controller_headers(created),
    )
    assert reset.status_code == 200
    resend_after_reset = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(gus, key="card-key"),
        json={"recipientId": "hana"},
    )
    assert resend_after_reset.status_code == 200
    assert resend_after_reset.json()["replayed"] is False

    with app.state.database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM good_cards WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 2
        endpoints = {
            str(row[0])
            for row in connection.execute(
                "SELECT endpoint FROM idempotency_keys WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
    assert f"good-card:{next_day}" in endpoints
    assert len({item for item in endpoints if item.startswith("good-card:")}) == 2


def test_concurrent_good_cards_with_different_keys_only_insert_once(
    client: TestClient, app
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    gus = next(player for player in created["players"] if player["id"] == "gus")
    identity = PlayerIdentity(run_id, "gus", "Gus", gus["color"])

    def send(key: str):
        try:
            return app.state.service.send_good_card(identity, "hana", key)
        except GameServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, ("concurrent-a", "concurrent-b")))

    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("good_card_already_sent") == 1
    with app.state.database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM good_cards WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT revision FROM runs WHERE id = ?", (run_id,)
        ).fetchone()[0] == 1


def test_concurrent_spins_create_one_reward_and_one_ledger_entry(
    client: TestClient, app
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    ava = next(player for player in created["players"] if player["id"] == "ava")
    identity = PlayerIdentity(run_id, "ava", "Ava", ava["color"])
    app.state.service.force_wheel(run_id, 20)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda key: app.state.service.spin(identity, key),
                ("parallel-a", "parallel-b"),
            )
        )

    assert sorted(result["alreadySpun"] for result in results) == [False, True]
    assert {result["balanceCents"] for result in results} == {2000}
    with app.state.database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_spins WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM ledger WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT balance_cents FROM players WHERE run_id = ? AND id = 'ava'",
            (run_id,),
        ).fetchone()[0] == 2000


def test_taipei_midnight_changes_daily_scope_for_same_idempotency_key(
    tmp_path: Path,
) -> None:
    class MutableClock:
        def __init__(self, value: datetime) -> None:
            self.value = value

        def __call__(self) -> datetime:
            return self.value

    clock = MutableClock(datetime(2026, 8, 9, 15, 59, tzinfo=timezone.utc))
    app = create_app(
        Settings(
            database_path=tmp_path / "midnight.sqlite3",
            web_dir=WEB_DIR,
            admin_token=ADMIN_TOKEN,
            timezone_name="Asia/Taipei",
        ),
        now=clock,
    )
    with TestClient(app) as client:
        install_test_core_v1_release(app)
        created = create_run(client, "midnight")
        run_id = created["run"]["id"]
        ava = created["players"][0]
        control = controller_headers(created)
        headers = player_headers(ava, key="same-around-midnight")
        client.post(
            f"/api/review/runs/{run_id}/force-wheel",
            headers=control,
            json={"reward": 1},
        )

        before = client.post(
            f"/api/checkin/spin?run={run_id}", headers=headers
        ).json()
        assert before["day"] == "2026-08-09"

        clock.value = datetime(2026, 8, 9, 16, 1, tzinfo=timezone.utc)
        bootstrap = client.get(
            f"/api/bootstrap?run={run_id}", headers=player_headers(ava)
        ).json()
        assert bootstrap["run"]["day"] == "2026-08-10"
        assert bootstrap["player"]["spin"]["available"] is True

        after = client.post(
            f"/api/checkin/spin?run={run_id}", headers=headers
        ).json()
        assert after["day"] == "2026-08-10"
        assert after["replayed"] is False
        assert after["balanceCents"] == 200

    with app.state.database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_spins WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(DISTINCT endpoint) FROM idempotency_keys WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 2


def test_good_card_notifications_and_balances_are_private(
    client: TestClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = create_run(client)
    run_id = created["run"]["id"]
    by_id = {player["id"]: player for player in created["players"]}
    deliveries: list[tuple[str, str, dict]] = []

    async def capture(run: str, player_id: str, payload: dict) -> None:
        deliveries.append((run, player_id, payload))

    monkeypatch.setattr(app.state.realtime, "send_to_player", capture)
    sent = client.post(
        f"/api/good-cards?run={run_id}",
        headers=player_headers(by_id["gus"], key="private-card"),
        json={"recipientId": "hana"},
    )
    assert sent.status_code == 200
    assert [(run, player_id) for run, player_id, _ in deliveries] == [
        (run_id, "hana")
    ]
    assert deliveries[0][2]["type"] == "good-card.created"

    client.post(
        f"/api/review/runs/{run_id}/force-wheel",
        headers=controller_headers(created),
        json={"reward": 1},
    )
    spun = client.post(
        f"/api/checkin/spin?run={run_id}",
        headers=player_headers(by_id["gus"], key="private-spin"),
    )
    assert spun.status_code == 200
    assert [(run, player_id) for run, player_id, _ in deliveries] == [
        (run_id, "hana"),
        (run_id, "gus"),
    ]
    assert deliveries[1][2]["type"] == "economy.changed"

    def bootstrap(player_id: str) -> dict:
        return client.get(
            f"/api/bootstrap?run={run_id}",
            headers=player_headers(by_id[player_id]),
        ).json()

    recipient_view = bootstrap("hana")
    sender_view = bootstrap("gus")
    third_party_view = bootstrap("ava")
    assert [card["senderId"] for card in recipient_view["goodCards"]] == ["gus"]
    assert sender_view["goodCards"] == []
    assert third_party_view["goodCards"] == []
    for view in (recipient_view, sender_view, third_party_view):
        assert all("balance" not in player for player in view["players"])
        assert all("balanceCents" not in player for player in view["players"])
    assert "balanceCents" in recipient_view["player"]


def test_review_controls_increment_revision_and_controller_is_run_scoped(
    client: TestClient,
) -> None:
    first = create_run(client)
    second = create_run(client)
    run_id = first["run"]["id"]
    control = controller_headers(first)

    wrong = client.post(
        f"/api/review/runs/{run_id}/pause",
        headers=controller_headers(second),
        json={"paused": True},
    )
    assert wrong.status_code == 401

    paused = client.post(
        f"/api/review/runs/{run_id}/pause",
        headers=control,
        json={"paused": True},
    ).json()
    assert paused["run"]["paused"] is True
    assert paused["run"]["revision"] == 1
    sped = client.post(
        f"/api/review/runs/{run_id}/speed",
        headers=control,
        json={"speed": 2},
    ).json()
    assert sped["run"]["speed"] == 2
    assert sped["run"]["revision"] == 2
    reset = client.post(
        f"/api/review/runs/{run_id}/reset", headers=control
    ).json()
    assert reset["run"]["paused"] is False
    assert reset["run"]["speed"] == 1
    assert reset["run"]["revision"] == 3


def test_web_sources_must_revalidate_so_stale_modules_cannot_survive(client: TestClient) -> None:
    """Web sources are served from stable URLs with no cache-busting.

    Without an explicit Cache-Control the browser's heuristic cache can keep an
    old ES module across an asset-contract change, which surfaces as validation
    errors quoting frame counts that no longer exist in the tree.
    """

    for path in ("/", "/scene.mjs", "/asset-manifest.mjs"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-store", path
