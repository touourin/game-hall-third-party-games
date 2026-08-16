from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .pathfinding import astar


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LAYOUTS_PATH = PROJECT_DIR / "assets" / "world-layouts.json"
DEFAULT_LAYOUT_ID = "world.mid-growth-v1"
EXPECTED_PLAYER_ROSTER = (
    ("ava", "Ava"),
    ("ben", "Ben"),
    ("cleo", "Cleo"),
    ("drew", "Drew"),
    ("eli", "Eli"),
    ("faye", "Faye"),
    ("gus", "Gus"),
    ("hana", "Hana"),
)


class WorldLayoutError(ValueError):
    """Raised when a versioned world cannot be safely bound to an asset release."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class WorldLayoutRegistry:
    """Load product layouts and derive collision from a release manifest.

    The layout declares only placements. Collision is copied from the immutable
    asset manifest at run creation and stored with the run, so rendering,
    bootstrap, pathfinding, and reconnect snapshots consume one frozen value.
    """

    def __init__(self, path: str | Path = DEFAULT_LAYOUTS_PATH) -> None:
        self.path = Path(path)

    def descriptors(
        self,
        *,
        active_pack_id: str | None = None,
        creatable_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return stable map choices without binding mutable asset data."""

        result: list[dict[str, Any]] = []
        for layout in self._layouts():
            if creatable_only and layout.get("creatable", True) is False:
                continue
            required_pack_id = self._identifier(
                layout.get("requiredPackId", "core-v0"), "required pack id"
            )
            available = active_pack_id == required_pack_id
            reason = None
            if not available:
                reason = (
                    f"需要先在资产验收台激活 {required_pack_id}"
                    if active_pack_id is None
                    else f"当前激活的是 {active_pack_id}，此地图需要 {required_pack_id}"
                )
            result.append(
                {
                    "id": self._identifier(layout.get("id"), "layout id"),
                    # Generation-free name for the picker; the variant's own
                    # label stays in the frozen snapshot, where it is hashed.
                    "label": self._text(
                        layout.get("displayLabel") or layout.get("label"),
                        "layout label",
                    ),
                    "stage": self._identifier(
                        layout.get("stage", "unspecified"), "layout stage"
                    ),
                    "columns": self._positive_int(layout.get("columns"), "columns"),
                    "rows": self._positive_int(layout.get("rows"), "rows"),
                    "size": {
                        "columns": self._positive_int(layout.get("columns"), "columns"),
                        "rows": self._positive_int(layout.get("rows"), "rows"),
                    },
                    "requiredPackId": required_pack_id,
                    "available": available,
                    "reason": reason,
                }
            )
        return result

    def required_pack_id(self, layout_id: str) -> str:
        """Validate a known layout before consulting mutable pack state."""

        layout = self._layout(layout_id)
        return self._identifier(
            layout.get("requiredPackId", "core-v0"), "required pack id"
        )

    def build_snapshot(
        self,
        manifest: Mapping[str, Any],
        layout_id: str = DEFAULT_LAYOUT_ID,
    ) -> dict[str, Any]:
        layout = self._layout(layout_id)
        assets = self._manifest_assets(manifest)
        required_pack_id = self._identifier(
            layout.get("requiredPackId", "core-v0"), "required pack id"
        )
        if manifest.get("id") != required_pack_id:
            raise WorldLayoutError(
                f"world {layout_id} requires asset pack {required_pack_id}"
            )
        columns = self._positive_int(layout.get("columns"), "columns")
        rows = self._positive_int(layout.get("rows"), "rows")
        grid = manifest.get("grid")
        if not isinstance(grid, Mapping):
            raise WorldLayoutError("asset manifest grid is missing")
        tile_width = self._positive_int(grid.get("tileWidth"), "tileWidth")
        tile_height = self._positive_int(grid.get("tileHeight"), "tileHeight")
        elevation = self._positive_int(grid.get("elevation"), "elevation")

        floor = self._normalize_floor(layout.get("floor"), assets, columns, rows)
        placements_value = layout.get("placements")
        if not isinstance(placements_value, Sequence) or isinstance(
            placements_value, (str, bytes, bytearray)
        ):
            raise WorldLayoutError("world placements must be an array")

        placements: list[dict[str, Any]] = []
        placement_ids: set[str] = set()
        blocked: set[tuple[int, int]] = set()
        occupied_footprint: set[tuple[int, int]] = set()
        for raw in placements_value:
            if not isinstance(raw, Mapping):
                raise WorldLayoutError("world placement must be an object")
            placement_id = self._identifier(raw.get("id"), "placement id")
            if placement_id in placement_ids:
                raise WorldLayoutError(f"duplicate placement id: {placement_id}")
            placement_ids.add(placement_id)
            asset_id = self._identifier(raw.get("assetId"), "asset id")
            asset = assets.get(asset_id)
            if asset is None:
                raise WorldLayoutError(f"layout asset is absent from manifest: {asset_id}")
            x = self._integer(raw.get("x"), "placement x")
            y = self._integer(raw.get("y"), "placement y")
            if not 0 <= x < columns or not 0 <= y < rows:
                raise WorldLayoutError(f"placement is outside the world: {placement_id}")
            footprint = asset.get("footprint", [])
            if not isinstance(footprint, Sequence) or isinstance(
                footprint, (str, bytes, bytearray)
            ):
                raise WorldLayoutError(f"asset footprint is invalid: {asset_id}")
            for cell in footprint:
                if not isinstance(cell, Mapping):
                    raise WorldLayoutError(f"asset footprint is invalid: {asset_id}")
                absolute = (
                    x + self._integer(cell.get("x"), "footprint x"),
                    y + self._integer(cell.get("y"), "footprint y"),
                )
                if not 0 <= absolute[0] < columns or not 0 <= absolute[1] < rows:
                    raise WorldLayoutError(
                        f"asset footprint extends outside the world: {placement_id}"
                    )
                if asset.get("kind") != "backdrop" and absolute in occupied_footprint:
                    raise WorldLayoutError(
                        f"asset footprint overlaps another placement at {absolute}"
                    )
                if asset.get("kind") != "backdrop":
                    occupied_footprint.add(absolute)

            interaction_points = self._interaction_points(
                asset.get("interactionPoints", []), asset_id
            )
            placement = {"id": placement_id, "assetId": asset_id, "x": x, "y": y}
            if interaction_points:
                placement["interactionPoints"] = interaction_points
            placements.append(placement)
            collision = asset.get("collision", [])
            if not isinstance(collision, Sequence) or isinstance(
                collision, (str, bytes, bytearray)
            ):
                raise WorldLayoutError(f"asset collision is invalid: {asset_id}")
            for cell in collision:
                if not isinstance(cell, Mapping):
                    raise WorldLayoutError(f"asset collision is invalid: {asset_id}")
                absolute = (
                    x + self._integer(cell.get("x"), "collision x"),
                    y + self._integer(cell.get("y"), "collision y"),
                )
                if not 0 <= absolute[0] < columns or not 0 <= absolute[1] < rows:
                    raise WorldLayoutError(
                        f"asset collision extends outside the world: {placement_id}"
                    )
                if absolute in blocked:
                    raise WorldLayoutError(
                        f"asset collision overlaps another placement at {absolute}"
                    )
                blocked.add(absolute)

        spawn_points = self._normalize_spawn_points(
            layout.get("spawnPoints"), columns, rows, blocked
        )
        work_seats: list[dict[str, Any]] = []
        seat_cells: set[tuple[int, int]] = set()
        for placement in placements:
            for point in placement.get("interactionPoints", []):
                cell = (placement["x"] + point["x"], placement["y"] + point["y"])
                if not 0 <= cell[0] < columns or not 0 <= cell[1] < rows:
                    raise WorldLayoutError(
                        f"interaction point is outside the world: {placement['id']}/{point['id']}"
                    )
                if cell in blocked:
                    raise WorldLayoutError(
                        f"interaction point is blocked: {placement['id']}/{point['id']}"
                    )
                if cell in seat_cells:
                    raise WorldLayoutError(f"duplicate interaction cell: {cell}")
                seat_cells.add(cell)
                work_seats.append(
                    {
                        "placementId": placement["id"],
                        "seatId": point["id"],
                        "kind": point["kind"],
                        "x": cell[0],
                        "y": cell[1],
                        "facing": point["facing"],
                    }
                )

        initial_activities = self._normalize_initial_activities(
            layout.get("initialActivities", []), spawn_points, work_seats
        )

        self._validate_reachability(
            spawn_points, work_seats, columns=columns, rows=rows, blocked=blocked
        )

        snapshot: dict[str, Any] = {
            "schemaVersion": 1,
            "id": self._identifier(layout.get("id"), "layout id"),
            "label": self._text(layout.get("label"), "layout label"),
            "stage": self._identifier(
                layout.get("stage", "unspecified"), "layout stage"
            ),
            "requiredPackId": required_pack_id,
            "sourceFixtureId": self._identifier(
                layout.get("sourceFixtureId"), "source fixture id"
            ),
            "columns": columns,
            "rows": rows,
            "tileWidth": tile_width,
            "tileHeight": tile_height,
            "elevation": elevation,
            "origin": self._point(layout.get("origin"), "origin"),
            "camera": self._camera(layout.get("camera")),
            "spawnPoints": spawn_points,
            "floor": floor,
            "placements": placements,
            "blockedCells": [
                {"x": x, "y": y} for x, y in sorted(blocked, key=lambda item: (item[1], item[0]))
            ],
            "workSeats": work_seats,
            "interactionPoints": work_seats,
            "initialActivities": initial_activities,
        }
        snapshot["sha256"] = hashlib.sha256(
            canonical_json(snapshot).encode("utf-8")
        ).hexdigest()
        return snapshot

    def _interaction_points(self, raw: Any, asset_id: str) -> list[dict[str, Any]]:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            raise WorldLayoutError(f"asset interactionPoints is invalid: {asset_id}")
        result: list[dict[str, Any]] = []
        ids: set[str] = set()
        allowed_facings = {"southeast", "southwest", "northwest", "northeast"}
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise WorldLayoutError(f"asset interactionPoints is invalid: {asset_id}")
            point_id = self._identifier(entry.get("id"), "interaction id")
            if point_id in ids or entry.get("kind") != "work-seat":
                raise WorldLayoutError(f"asset interaction point is invalid: {asset_id}")
            facing = self._identifier(entry.get("facing"), "interaction facing")
            if facing not in allowed_facings:
                raise WorldLayoutError(f"interaction facing is invalid: {facing}")
            ids.add(point_id)
            result.append(
                {
                    "id": point_id,
                    "kind": "work-seat",
                    "x": self._integer(entry.get("x"), "interaction x"),
                    "y": self._integer(entry.get("y"), "interaction y"),
                    "facing": facing,
                }
            )
        return result

    def _normalize_initial_activities(
        self,
        raw: Any,
        spawn_points: Sequence[Mapping[str, Any]],
        work_seats: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Bind initial work to frozen seats without moving a player implicitly."""

        if not isinstance(raw, Sequence) or isinstance(
            raw, (str, bytes, bytearray)
        ):
            raise WorldLayoutError("initialActivities must be an array")
        spawns = {str(spawn["playerId"]): spawn for spawn in spawn_points}
        seats = {
            (str(seat["placementId"]), str(seat["seatId"])): seat
            for seat in work_seats
        }
        players_in_use: set[str] = set()
        seats_in_use: set[tuple[str, str]] = set()
        result: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise WorldLayoutError("initial activity must be an object")
            player_id = self._identifier(
                entry.get("playerId"), "initial activity player id"
            )
            if entry.get("type") != "work":
                raise WorldLayoutError("initial activity type must be work")
            placement_id = self._identifier(
                entry.get("placementId"), "initial activity placement id"
            )
            seat_id = self._identifier(
                entry.get("seatId"), "initial activity seat id"
            )
            key = (placement_id, seat_id)
            spawn = spawns.get(player_id)
            seat = seats.get(key)
            if spawn is None:
                raise WorldLayoutError(
                    f"initial activity player is absent from spawns: {player_id}"
                )
            if seat is None:
                raise WorldLayoutError(
                    f"initial activity seat does not exist: {placement_id}/{seat_id}"
                )
            if player_id in players_in_use:
                raise WorldLayoutError(
                    f"player has duplicate initial activities: {player_id}"
                )
            if key in seats_in_use:
                raise WorldLayoutError(
                    f"seat has duplicate initial activities: {placement_id}/{seat_id}"
                )
            if (int(spawn["x"]), int(spawn["y"])) != (
                int(seat["x"]),
                int(seat["y"]),
            ):
                raise WorldLayoutError(
                    f"initial work player must spawn at the seat: {player_id}"
                )
            players_in_use.add(player_id)
            seats_in_use.add(key)
            result.append(
                {
                    "playerId": player_id,
                    "type": "work",
                    "placementId": placement_id,
                    "seatId": seat_id,
                    "facing": str(seat["facing"]),
                }
            )
        return result

    @staticmethod
    def _validate_reachability(
        spawn_points: Sequence[Mapping[str, Any]],
        work_seats: Sequence[Mapping[str, Any]],
        *,
        columns: int,
        rows: int,
        blocked: set[tuple[int, int]],
    ) -> None:
        origin = (int(spawn_points[0]["x"]), int(spawn_points[0]["y"]))
        destinations = [
            (str(spawn["playerId"]), (int(spawn["x"]), int(spawn["y"])))
            for spawn in spawn_points[1:]
        ] + [
            (
                f"{seat['placementId']}/{seat['seatId']}",
                (int(seat["x"]), int(seat["y"])),
            )
            for seat in work_seats
        ]
        for identifier, destination in destinations:
            if not astar(
                origin,
                destination,
                columns=columns,
                rows=rows,
                blocked=frozenset(blocked),
            ):
                raise WorldLayoutError(f"world location is unreachable: {identifier}")

    def _normalize_spawn_points(
        self,
        raw: Any,
        columns: int,
        rows: int,
        blocked: set[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw, Sequence) or isinstance(
            raw, (str, bytes, bytearray)
        ):
            raise WorldLayoutError("world spawnPoints must be an array")
        if len(raw) != len(EXPECTED_PLAYER_ROSTER):
            raise WorldLayoutError("world must declare exactly eight spawn points")
        result: list[dict[str, Any]] = []
        player_ids: set[str] = set()
        cells: set[tuple[int, int]] = set()
        for index, (entry, expected) in enumerate(
            zip(raw, EXPECTED_PLAYER_ROSTER, strict=True)
        ):
            if not isinstance(entry, Mapping):
                raise WorldLayoutError("world spawn point must be an object")
            player_id = self._identifier(entry.get("playerId"), "spawn player id")
            name = self._text(entry.get("name"), "spawn player name")
            if (player_id, name) != expected:
                raise WorldLayoutError(
                    f"spawnPoints[{index}] must identify {expected[1]} ({expected[0]})"
                )
            x = self._integer(entry.get("x"), "spawn x")
            y = self._integer(entry.get("y"), "spawn y")
            cell = (x, y)
            if player_id in player_ids or cell in cells:
                raise WorldLayoutError("spawn player ids and cells must be unique")
            if not 0 <= x < columns or not 0 <= y < rows:
                raise WorldLayoutError(f"spawn point is outside the world: {player_id}")
            if cell in blocked:
                raise WorldLayoutError(f"spawn point is blocked by furniture: {player_id}")
            player_ids.add(player_id)
            cells.add(cell)
            result.append({"playerId": player_id, "name": name, "x": x, "y": y})
        return result

    def _layout(self, layout_id: str) -> dict[str, Any]:
        matches = [entry for entry in self._layouts() if entry.get("id") == layout_id]
        if len(matches) != 1:
            raise WorldLayoutError(f"world layout not found: {layout_id}")
        return dict(matches[0])

    def _layouts(self) -> list[Mapping[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorldLayoutError(f"world layout registry cannot be read: {self.path}") from exc
        if not isinstance(payload, Mapping) or payload.get("schemaVersion") != 1:
            raise WorldLayoutError("world layout registry schema is invalid")
        layouts = payload.get("layouts")
        if not isinstance(layouts, Sequence) or isinstance(layouts, (str, bytes, bytearray)):
            raise WorldLayoutError("world layout registry has no layouts")
        normalized = [entry for entry in layouts if isinstance(entry, Mapping)]
        if len(normalized) != len(layouts):
            raise WorldLayoutError("world layout registry contains an invalid layout")
        ids = [entry.get("id") for entry in normalized]
        if len(set(ids)) != len(ids):
            raise WorldLayoutError("world layout registry contains duplicate ids")
        return normalized

    @staticmethod
    def _manifest_assets(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        values = manifest.get("assets")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            raise WorldLayoutError("asset manifest has no assets")
        result: dict[str, Mapping[str, Any]] = {}
        for value in values:
            if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
                raise WorldLayoutError("asset manifest entry is invalid")
            if value["id"] in result:
                raise WorldLayoutError(f"duplicate manifest asset id: {value['id']}")
            result[value["id"]] = value
        return result

    def _normalize_floor(
        self,
        raw: Any,
        assets: Mapping[str, Mapping[str, Any]],
        columns: int,
        rows: int,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise WorldLayoutError("world floor is missing")
        default_asset_id = self._floor_asset(raw.get("defaultAssetId"), assets)
        regions_value = raw.get("regions", [])
        if not isinstance(regions_value, Sequence) or isinstance(
            regions_value, (str, bytes, bytearray)
        ):
            raise WorldLayoutError("floor regions must be an array")
        regions: list[dict[str, Any]] = []
        for entry in regions_value:
            if not isinstance(entry, Mapping):
                raise WorldLayoutError("floor region must be an object")
            region = {
                "assetId": self._floor_asset(entry.get("assetId"), assets),
                "x": self._integer(entry.get("x"), "floor region x"),
                "y": self._integer(entry.get("y"), "floor region y"),
                "width": self._positive_int(entry.get("width"), "floor region width"),
                "height": self._positive_int(entry.get("height"), "floor region height"),
            }
            if (
                region["x"] < 0
                or region["y"] < 0
                or region["x"] + region["width"] > columns
                or region["y"] + region["height"] > rows
            ):
                raise WorldLayoutError("floor region is outside the world")
            regions.append(region)
        border_value = raw.get("border")
        if not isinstance(border_value, Mapping):
            raise WorldLayoutError("floor border is missing")
        edges = border_value.get("edges")
        allowed_edges = {"north", "east", "south", "west"}
        if (
            not isinstance(edges, Sequence)
            or isinstance(edges, (str, bytes, bytearray))
            or not edges
            or any(edge not in allowed_edges for edge in edges)
            or len(set(edges)) != len(edges)
        ):
            raise WorldLayoutError("floor border edges are invalid")
        return {
            "defaultAssetId": default_asset_id,
            "regions": regions,
            "border": {
                "assetId": self._floor_asset(border_value.get("assetId"), assets),
                "edges": list(edges),
            },
        }

    @staticmethod
    def _floor_asset(value: Any, assets: Mapping[str, Mapping[str, Any]]) -> str:
        asset_id = WorldLayoutRegistry._identifier(value, "floor asset id")
        asset = assets.get(asset_id)
        if asset is None or asset.get("kind") != "floor":
            raise WorldLayoutError(f"floor asset is absent or not a floor: {asset_id}")
        return asset_id

    @staticmethod
    def _point(value: Any, field: str) -> dict[str, int]:
        if not isinstance(value, Mapping):
            raise WorldLayoutError(f"{field} must be a point")
        return {
            "x": WorldLayoutRegistry._integer(value.get("x"), f"{field} x"),
            "y": WorldLayoutRegistry._integer(value.get("y"), f"{field} y"),
        }

    @staticmethod
    def _camera(value: Any) -> dict[str, int | float]:
        if not isinstance(value, Mapping):
            raise WorldLayoutError("camera must be an object")
        zoom = value.get("zoom")
        if isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or zoom <= 0:
            raise WorldLayoutError("camera zoom must be positive")
        return {
            "x": WorldLayoutRegistry._integer(value.get("x"), "camera x"),
            "y": WorldLayoutRegistry._integer(value.get("y"), "camera y"),
            "zoom": zoom,
        }

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise WorldLayoutError(f"{field} must be a short identifier")
        return value

    @staticmethod
    def _text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise WorldLayoutError(f"{field} must be text")
        return value.strip()

    @staticmethod
    def _integer(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorldLayoutError(f"{field} must be an integer")
        return value

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        result = WorldLayoutRegistry._integer(value, field)
        if result <= 0:
            raise WorldLayoutError(f"{field} must be positive")
        return result


__all__ = [
    "DEFAULT_LAYOUT_ID",
    "DEFAULT_LAYOUTS_PATH",
    "EXPECTED_PLAYER_ROSTER",
    "WorldLayoutError",
    "WorldLayoutRegistry",
    "canonical_json",
]
