from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from codex_v0.asset_lab import (
    CORE_PACK_SPEC_PATH,
    CORE_V1_PACK_SPEC_PATH,
    CORE_V2_PACK_SPEC_PATH,
)
from codex_v0.config import Settings
from codex_v0.db import Database
from codex_v0.identity import PlayerIdentity
from codex_v0.pathfinding import FURNITURE_BLOCKED
from codex_v0.realtime import RealtimeManager
from codex_v0.service import PLAYER_SPAWNS, GameService, GameServiceError
from codex_v0.world_layout import DEFAULT_LAYOUTS_PATH, WorldLayoutRegistry, canonical_json


class ReleaseSource:
    def __init__(self) -> None:
        self.revision = 41
        self.manifest = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))

    def active_release(self) -> dict:
        manifest_sha = hashlib.sha256(
            canonical_json(self.manifest).encode("utf-8")
        ).hexdigest()
        atlas_sha = hashlib.sha256(f"atlas-{self.revision}".encode()).hexdigest()
        return {
            "id": f"release-{self.revision}",
            "packId": self.manifest["id"],
            "catalogRevision": self.revision,
            "manifestSha256": manifest_sha,
            "atlasSha256": atlas_sha,
            "manifest": copy.deepcopy(self.manifest),
            "catalog": {},
        }

    def advance(self) -> None:
        self.revision += 1
        self.manifest = copy.deepcopy(self.manifest)
        self.manifest["atlases"][0]["source"] = f"release-{self.revision}.png"


def runtime_manifest_v2() -> dict:
    manifest = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    v1 = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(CORE_V2_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    manifest["id"] = "core-v1"
    for asset in manifest["assets"]:
        patch = v1["baseAssetPatches"].get(asset["id"])
        if patch:
            asset.update(copy.deepcopy(patch))
    manifest["assets"].extend(copy.deepcopy(v1["assets"]))
    manifest["id"] = "core-v2"
    manifest["geometryVersion"] = 2
    manifest["palette"] = copy.deepcopy(v2["palette"])
    overrides = {asset["id"]: asset for asset in v2["assets"]}
    manifest["assets"] = [
        copy.deepcopy(overrides.pop(asset["id"], asset))
        for asset in manifest["assets"]
    ]
    manifest["assets"].extend(copy.deepcopy(list(overrides.values())))
    return manifest


def bound_service(
    tmp_path: Path,
    *,
    layout_path: Path = DEFAULT_LAYOUTS_PATH,
) -> tuple[GameService, ReleaseSource]:
    settings = Settings(database_path=tmp_path / "game.sqlite3")
    database = Database(settings.database_path)
    database.initialize()
    releases = ReleaseSource()
    service = GameService(
        database,
        settings,
        asset_lab=releases,  # type: ignore[arg-type]
        world_layouts=WorldLayoutRegistry(layout_path),
    )
    return service, releases


def v2_bound_service(tmp_path: Path) -> tuple[GameService, ReleaseSource]:
    service, releases = bound_service(tmp_path)
    releases.manifest = runtime_manifest_v2()
    return service, releases


def test_run_freezes_release_layout_and_collision_across_controls(tmp_path: Path) -> None:
    service, releases = bound_service(tmp_path)
    first = service.create_run("first bound run")
    run_id = first["run"]["id"]
    original_pack = first["run"]["assetPack"]
    original_layout = first["run"]["worldLayout"]

    assert original_pack["releaseId"] == "release-41"
    assert original_pack["catalogRevision"] == 41
    assert original_layout["id"] == "world.mid-growth-v1"
    assert original_pack["manifestUrl"].endswith(original_pack["manifestSha256"])
    columns, rows, blocked = service.collision_for_run(run_id)
    assert (columns, rows) == (20, 12)
    assert (3, 2) in blocked
    assert (14, 2) not in blocked
    state = service.review_state(run_id)
    positions = {player["id"]: (player["x"], player["y"]) for player in state["players"]}
    assert positions == {
        spawn["playerId"]: (spawn["x"], spawn["y"])
        for spawn in original_layout["spawnPoints"]
    }

    identity = PlayerIdentity(run_id, "ava", "Ava", first["players"][0]["color"])
    bootstrap = service.bootstrap(identity)
    assert bootstrap["assetPack"] == original_pack
    assert bootstrap["world"]["layout"] == original_layout
    assert {(cell["x"], cell["y"]) for cell in bootstrap["world"]["blockedCells"]} == blocked

    releases.advance()
    second = service.create_run("second bound run")
    assert second["run"]["assetPack"]["releaseId"] == "release-42"
    assert second["run"]["assetPack"] != original_pack

    assert service.advance_day(run_id, 1)["run"]["assetPack"] == original_pack
    reset = service.reset_run(run_id)["run"]
    assert reset["assetPack"] == original_pack
    assert reset["worldLayout"] == original_layout


def test_legacy_run_keeps_programmatic_collision_when_no_release(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "legacy.sqlite3")
    database = Database(settings.database_path)
    database.initialize()
    service = GameService(database, settings)
    created = service.create_run("legacy")

    assert created["run"]["assetPack"] is None
    assert created["run"]["worldLayout"] is None
    _, _, blocked = service.collision_for_run(created["run"]["id"])
    assert blocked == FURNITURE_BLOCKED
    state = service.review_state(created["run"]["id"])
    assert [(player["x"], player["y"]) for player in state["players"]] == list(
        PLAYER_SPAWNS
    )


def test_bound_run_uses_and_resets_to_frozen_layout_spawn(tmp_path: Path) -> None:
    layout = json.loads(DEFAULT_LAYOUTS_PATH.read_text(encoding="utf-8"))
    layout["layouts"][0]["spawnPoints"][0].update({"x": 2, "y": 1})
    layout_path = tmp_path / "custom-layouts.json"
    layout_path.write_text(json.dumps(layout), encoding="utf-8")
    service, _ = bound_service(
        tmp_path / "bound",
        layout_path=layout_path,
    )
    created = service.create_run("custom frozen spawn")
    run_id = created["run"]["id"]
    assert created["run"]["worldLayout"]["spawnPoints"][0] == {
        "playerId": "ava",
        "name": "Ava",
        "x": 2,
        "y": 1,
    }
    first_state = service.review_state(run_id)
    ava = next(player for player in first_state["players"] if player["id"] == "ava")
    assert (ava["x"], ava["y"]) == (2.0, 1.0)

    service.persist_positions(run_id, {"ava": (9.0, 9.0)})
    reset = service.reset_run(run_id)
    reset_ava = next(player for player in reset["players"] if player["id"] == "ava")
    assert (reset_ava["x"], reset_ava["y"]) == (2.0, 1.0)
    assert reset["run"]["worldLayout"]["spawnPoints"][0]["x"] == 2


@pytest.mark.asyncio
async def test_realtime_uses_bound_run_collision_not_legacy_global(tmp_path: Path) -> None:
    service, _ = bound_service(tmp_path)
    created = service.create_run("bound realtime")
    manager = RealtimeManager(service, service.settings)
    runtime = await manager.runtime(created["run"]["id"])

    assert (3, 2) in runtime.blocked
    assert (14, 2) not in runtime.blocked
    blocked = manager._plan_move(runtime, "ava", runtime.motions["ava"], 3, 2, 1)
    assert blocked["type"] == "error"
    assert blocked["code"] == "target_blocked"
    accepted = manager._plan_move(runtime, "ava", runtime.motions["ava"], 14, 2, 2)
    assert accepted["type"] == "move.accepted"
    assert manager.snapshot_payload(runtime)["blockedCells"] == [
        {"x": x, "y": y} for x, y in sorted(runtime.blocked)
    ]


@pytest.mark.asyncio
async def test_v2_initial_work_restores_only_at_seats_and_movement_has_no_economy(
    tmp_path: Path,
) -> None:
    service, _ = v2_bound_service(tmp_path)
    assert [entry["available"] for entry in service.review_layouts()] == [
        False,
        False,
        True,
        True,
    ]
    with pytest.raises(GameServiceError) as unavailable:
        service.create_run("old map unavailable", "world.mid-growth-v2")
    assert unavailable.value.code == "asset_pack_unavailable"
    created = service.create_run("v2 initial work", "world.mid-growth-v3")
    run_id = created["run"]["id"]
    manager = RealtimeManager(service, service.settings)
    runtime = await manager.runtime(run_id)

    assert len(runtime.work_seats) == 8
    assert len(runtime.player_work) == 7
    assert runtime.motions["gus"].activity is None
    assert ("growth-v3-desk-team", "seat-ne") not in runtime.seat_occupancy
    assert runtime.motions["ava"].activity == {
        "type": "work",
        "placementId": "growth-v3-desk-team",
        "seatId": "seat-nw",
        "facing": "southeast",
    }
    snapshot = manager.snapshot_payload(runtime)
    assert len(snapshot["seatOccupancy"]) == 7
    assert sum(player["activity"] is not None for player in snapshot["players"]) == 7

    moved = manager._plan_move(runtime, "ava", runtime.motions["ava"], 7, 5, 1)
    assert moved["type"] == "move.accepted"
    assert runtime.motions["ava"].activity is None
    assert len(runtime.player_work) == 6
    service.persist_positions(run_id, {"ava": (7.0, 5.0)})

    rebuilt = RealtimeManager(service, service.settings)
    rebuilt_runtime = await rebuilt.runtime(run_id)
    assert rebuilt_runtime.motions["ava"].activity is None
    assert len(rebuilt_runtime.player_work) == 6

    service.reset_run(run_id)
    await rebuilt.reload_run(run_id)
    assert len(rebuilt_runtime.player_work) == 7
    assert rebuilt_runtime.motions["ava"].activity is not None

    state = service.review_state(run_id)
    assert all(player["balanceCents"] == 0 for player in state["players"])
    with service.database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM ledger WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 0


def test_database_migrates_nullable_asset_binding_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                controller_token_hash TEXT NOT NULL,
                day_offset INTEGER NOT NULL DEFAULT 0,
                paused INTEGER NOT NULL DEFAULT 0,
                speed REAL NOT NULL DEFAULT 1.0,
                forced_wheel INTEGER NULL,
                revision INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
    assert {
        "asset_release_id",
        "asset_pack_id",
        "asset_catalog_revision",
        "asset_manifest_sha256",
        "asset_atlas_sha256",
        "world_layout_id",
        "world_layout_json",
    }.issubset(columns)
