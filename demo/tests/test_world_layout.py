from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from codex_v0.asset_lab import (
    CORE_PACK_SPEC_PATH,
    CORE_V1_PACK_SPEC_PATH,
    CORE_V2_PACK_SPEC_PATH,
)
from codex_v0.service import PLAYER_NAMES, PLAYER_SPAWNS
from codex_v0.world_layout import (
    DEFAULT_LAYOUT_ID,
    DEFAULT_LAYOUTS_PATH,
    WorldLayoutError,
    WorldLayoutRegistry,
)


def runtime_manifest() -> dict:
    return json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))


def runtime_manifest_v1() -> dict:
    base = runtime_manifest()
    extension = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    base["id"] = "core-v1"
    base["requiredSlots"] += extension["requiredNewSlots"]
    for asset in base["assets"]:
        patch = extension["baseAssetPatches"].get(asset["id"])
        if patch:
            asset.update(copy.deepcopy(patch))
    base["assets"] += copy.deepcopy(extension["assets"])
    return base


def runtime_manifest_v2() -> dict:
    base = runtime_manifest_v1()
    extension = json.loads(CORE_V2_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    base["id"] = "core-v2"
    base["geometryVersion"] = extension["geometryVersion"]
    base["palette"] = copy.deepcopy(extension["palette"])
    overrides = {asset["id"]: asset for asset in extension["assets"]}
    base["assets"] = [
        copy.deepcopy(overrides.pop(asset["id"], asset)) for asset in base["assets"]
    ]
    base["assets"].extend(copy.deepcopy(list(overrides.values())))
    base["requiredSlots"] = [asset["id"] for asset in base["assets"]]
    return base


def test_mid_growth_layout_derives_collision_from_manifest() -> None:
    registry = WorldLayoutRegistry()
    snapshot = registry.build_snapshot(runtime_manifest())

    assert snapshot["id"] == DEFAULT_LAYOUT_ID
    assert snapshot["columns"] == 20
    assert snapshot["rows"] == 12
    assert snapshot["tileWidth"] == 32
    assert snapshot["tileHeight"] == 16
    assert [placement["assetId"] for placement in snapshot["placements"]] == [
        "furniture.desk-island",
        "furniture.meeting-table",
        "furniture.storage-cabinet",
        "furniture.tea-coffee-bar",
        "furniture.moving-box",
    ]
    blocked = {(cell["x"], cell["y"]) for cell in snapshot["blockedCells"]}
    assert blocked == {
        (3, 2), (4, 2), (5, 2),
        (3, 3), (4, 3), (5, 3),
        (8, 4), (9, 4), (10, 4), (11, 4),
        (8, 5), (9, 5), (10, 5), (11, 5),
        (1, 5), (2, 5),
        (2, 8), (3, 8),
        (16, 8),
    }
    assert len(blocked) == 19
    assert blocked.isdisjoint(
        {(int(x), int(y)) for x, y in PLAYER_SPAWNS}
    )
    assert snapshot["spawnPoints"] == [
        {"playerId": name.casefold(), "name": name, "x": int(x), "y": int(y)}
        for name, (x, y) in zip(PLAYER_NAMES, PLAYER_SPAWNS, strict=True)
    ]
    assert snapshot["floor"]["defaultAssetId"] == "floor.raw-concrete"
    assert len(snapshot["sha256"]) == 64


def test_layout_collision_changes_only_when_bound_manifest_changes() -> None:
    registry = WorldLayoutRegistry()
    original = runtime_manifest()
    changed = copy.deepcopy(original)
    desk = next(asset for asset in changed["assets"] if asset["id"] == "furniture.desk-island")
    desk["collision"] = desk["collision"][:-1]
    desk["footprint"][-1]["blocked"] = False

    first = registry.build_snapshot(original)
    second = registry.build_snapshot(changed)
    assert {tuple(cell.values()) for cell in first["blockedCells"]} != {
        tuple(cell.values()) for cell in second["blockedCells"]
    }
    assert first["sha256"] != second["sha256"]


@pytest.mark.parametrize(
    ("layout_id", "size", "blocked_count", "placement_count"),
    [
        ("world.opening-empty-v1", (14, 9), 30, 13),
        ("world.mid-growth-v2", (20, 12), 60, 19),
    ],
)
def test_core_v1_growth_maps_validate_geometry_reachability_and_work_seats(
    layout_id: str,
    size: tuple[int, int],
    blocked_count: int,
    placement_count: int,
) -> None:
    registry = WorldLayoutRegistry()
    snapshot = registry.build_snapshot(runtime_manifest_v1(), layout_id)
    assert (snapshot["columns"], snapshot["rows"]) == size
    assert snapshot["requiredPackId"] == "core-v1"
    assert len(snapshot["blockedCells"]) == blocked_count
    assert len(snapshot["placements"]) == placement_count
    assert len(snapshot["spawnPoints"]) == 8
    assert [seat["seatId"] for seat in snapshot["workSeats"]] == [
        "seat-se", "seat-sw", "seat-nw", "seat-ne"
    ]
    blocked = {(cell["x"], cell["y"]) for cell in snapshot["blockedCells"]}
    assert blocked.isdisjoint((seat["x"], seat["y"]) for seat in snapshot["workSeats"])
    assert len(snapshot["sha256"]) == 64


@pytest.mark.parametrize(
    ("layout_id", "size", "blocked_count", "placement_count", "activity_count"),
    [
        ("world.opening-empty-v2", (14, 9), 30, 13, 3),
        ("world.mid-growth-v3", (20, 12), 93, 31, 7),
    ],
)
def test_core_v2_maps_validate_geometry_spawns_seats_and_initial_work(
    layout_id: str,
    size: tuple[int, int],
    blocked_count: int,
    placement_count: int,
    activity_count: int,
) -> None:
    snapshot = WorldLayoutRegistry().build_snapshot(runtime_manifest_v2(), layout_id)
    assert (snapshot["columns"], snapshot["rows"]) == size
    assert snapshot["requiredPackId"] == "core-v2"
    assert len(snapshot["blockedCells"]) == blocked_count
    assert len(snapshot["placements"]) == placement_count
    assert len(snapshot["spawnPoints"]) == 8
    assert len(snapshot["initialActivities"]) == activity_count
    expected_seat_count = 4 if layout_id == "world.opening-empty-v2" else 8
    assert len(snapshot["workSeats"]) == expected_seat_count
    blocked = {(cell["x"], cell["y"]) for cell in snapshot["blockedCells"]}
    assert blocked.isdisjoint(
        (spawn["x"], spawn["y"]) for spawn in snapshot["spawnPoints"]
    )
    assert blocked.isdisjoint((seat["x"], seat["y"]) for seat in snapshot["workSeats"])
    spawns = {spawn["playerId"]: (spawn["x"], spawn["y"]) for spawn in snapshot["spawnPoints"]}
    seats = {
        (seat["placementId"], seat["seatId"]): (seat["x"], seat["y"])
        for seat in snapshot["workSeats"]
    }
    assert all(
        spawns[activity["playerId"]]
        == seats[(activity["placementId"], activity["seatId"])]
        for activity in snapshot["initialActivities"]
    )
    assert len(snapshot["sha256"]) == 64

    if layout_id == "world.opening-empty-v2":
        assert snapshot["origin"] == {"x": 280, "y": 136}
        assert snapshot["camera"]["zoom"] == 1.5
    else:
        assert snapshot["origin"] == {"x": 256, "y": 100}
        assert spawns["gus"] == (10, 4)
        assert spawns["hana"] == (12, 9)
        placements = {
            placement["id"]: (placement["x"], placement["y"])
            for placement in snapshot["placements"]
        }
        expected_visibility_placements = {
            "growth-v3-storage-b": (1, 8),
            "growth-v3-focus-d": (13, 8),
            "growth-v3-tea": (2, 10),
            "growth-v3-bookcase": (5, 10),
            "growth-v3-lounge": (9, 9),
            "growth-v3-printer": (17, 1),
        }
        assert {
            placement_id: placements[placement_id]
            for placement_id in expected_visibility_placements
        } == expected_visibility_placements
        assert {activity["playerId"] for activity in snapshot["initialActivities"]} == {
            "ava", "ben", "cleo", "drew", "eli", "faye", "hana"
        }
        occupied = {
            (activity["placementId"], activity["seatId"])
            for activity in snapshot["initialActivities"]
        }
        assert ("growth-v3-desk-team", "seat-ne") not in occupied


def test_layout_descriptors_expose_versioned_choices_for_the_active_pack() -> None:
    descriptors = WorldLayoutRegistry().descriptors(active_pack_id="core-v1")
    assert [entry["id"] for entry in descriptors] == [
        "world.opening-empty-v1",
        "world.mid-growth-v2",
        "world.opening-empty-v2",
        "world.mid-growth-v3",
    ]
    assert [entry["available"] for entry in descriptors] == [True, True, False, False]
    assert all(entry["reason"] for entry in descriptors[2:])

    current = WorldLayoutRegistry().descriptors(active_pack_id="core-v2")
    assert [entry["available"] for entry in current] == [False, False, True, True]
    assert all(entry["reason"] for entry in current[:2])


def test_creatable_maps_present_one_generation_free_name_per_map() -> None:
    """The picker names a map, not an asset-pack generation.

    Each map keeps a per-generation variant, but both variants answer to the
    same display name, so the offered menu is invariant across a pack switch.
    """

    descriptors = WorldLayoutRegistry().descriptors(active_pack_id="core-v2")
    assert {entry["id"]: entry["label"] for entry in descriptors} == {
        "world.opening-empty-v1": "光秃开局办公室",
        "world.mid-growth-v2": "丰富中期办公室",
        "world.opening-empty-v2": "光秃开局办公室",
        "world.mid-growth-v3": "丰富中期办公室",
    }
    assert not any(
        entry["label"].endswith((" v1", " v2", " v3")) for entry in descriptors
    )

    offered = [entry["label"] for entry in descriptors if entry["available"]]
    previous = WorldLayoutRegistry().descriptors(active_pack_id="core-v1")
    assert offered == ["光秃开局办公室", "丰富中期办公室"]
    assert [entry["label"] for entry in previous if entry["available"]] == offered

    # The display name is deliberately NOT the snapshot label: the snapshot
    # label is hashed, so it must keep its generation suffix.
    snapshot = WorldLayoutRegistry().build_snapshot(
        runtime_manifest_v2(), "world.mid-growth-v3"
    )
    assert snapshot["label"] == "丰富中期办公室 v3"
    assert len(snapshot["sha256"]) == 64


def test_initial_work_is_frozen_only_when_the_player_spawns_at_the_seat(
    tmp_path: Path,
) -> None:
    payload = json.loads(DEFAULT_LAYOUTS_PATH.read_text(encoding="utf-8"))
    layout = next(
        entry for entry in payload["layouts"] if entry["id"] == "world.opening-empty-v1"
    )
    layout["spawnPoints"][0].update({"x": 6, "y": 5})
    layout["initialActivities"] = [
        {
            "playerId": "ava",
            "type": "work",
            "placementId": "opening-desk",
            "seatId": "seat-se",
        }
    ]
    path = tmp_path / "initial-work-layouts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = WorldLayoutRegistry(path).build_snapshot(
        runtime_manifest_v1(), "world.opening-empty-v1"
    )
    assert snapshot["initialActivities"] == [
        {
            "playerId": "ava",
            "type": "work",
            "placementId": "opening-desk",
            "seatId": "seat-se",
            "facing": "northwest",
        }
    ]

    layout["spawnPoints"][0].update({"x": 1, "y": 1})
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorldLayoutError, match="must spawn at the seat"):
        WorldLayoutRegistry(path).build_snapshot(
            runtime_manifest_v1(), "world.opening-empty-v1"
        )


@pytest.mark.parametrize("invalid_case", ["duplicate", "out-of-bounds", "blocked"])
def test_layout_rejects_invalid_spawn_points(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    payload = json.loads(DEFAULT_LAYOUTS_PATH.read_text(encoding="utf-8"))
    spawns = payload["layouts"][0]["spawnPoints"]
    if invalid_case == "duplicate":
        spawns[1]["x"], spawns[1]["y"] = spawns[0]["x"], spawns[0]["y"]
    elif invalid_case == "out-of-bounds":
        spawns[0]["x"] = 20
    else:
        spawns[0]["x"], spawns[0]["y"] = 3, 2
    path = tmp_path / "invalid-layouts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorldLayoutError):
        WorldLayoutRegistry(path).build_snapshot(runtime_manifest())
