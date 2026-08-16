"""Deterministic, read-only visual QA for the unactivated ``core-v2`` pack.

The browser asset lab deliberately keeps draft art out of the active manifest.
That is the right game-runtime boundary, but it also means a reviewer needs a
way to inspect the complete office *before* accepting those drafts.  This
module resolves the frozen inherited versions from SQLite in read-only mode,
overlays the exact PNG/sidecar pairs currently in the inbox, and renders the
same isometric ground/depth conventions as ``web/scene.mjs``.

It never calls :class:`~codex_v0.asset_lab.AssetLab`: constructing AssetLab can
bootstrap packs and is therefore an inappropriate dependency for a QA command
whose strongest promise is that it cannot alter reviews or activation state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat, UnidentifiedImageError


CANVAS_SIZE = (640, 360)
TILE_WIDTH = 32
TILE_HEIGHT = 16
# Opaque spandrel strip capping each band; the rest of the pitch is glazing.
SPANDREL_HEIGHT = 2
ZOOM_STEPS = (1.0, 1.25, 1.5, 2.0)
PACK_ID = "core-v2"
OPENING_LAYOUT_ID = "world.opening-empty-v2"
MID_LAYOUT_ID = "world.mid-growth-v3"
CONTACT_NAME = "core-v2-candidates-contact.png"
OPENING_NAME = "world-opening-empty-v2-candidate.png"
MID_NAME = "world-mid-growth-v3-candidate.png"
OCCLUSION_NAME = "desk-work-occlusion-candidate.png"
RECEIPT_NAME = "qa-manifest.json"
PLAYER_NAMES = ("Ava", "Ben", "Cleo", "Drew", "Eli", "Faye", "Gus", "Hana")
FALLBACK_ACCENTS = (
    "#ED806C",
    "#75BD9F",
    "#78AABC",
    "#F1BF65",
    "#A88BC2",
    "#DC8EB0",
    "#82AE68",
    "#DC9765",
)
SCENE_SHELL_TYPE = "cutaway-office-tower"
SCENE_SHELL_KEYS = frozenset(
    {
        "version",
        "type",
        "facadeDepth",
        "slabDepth",
        "windowBandPitch",
        "colors",
    }
)
SCENE_SHELL_COLOR_KEYS = (
    "outline",
    "ambientOcclusion",
    "slab",
    "facadeLight",
    "facadeDark",
    "window",
    "mullion",
)


class AssetQaError(RuntimeError):
    """A clear, user-facing failure from the read-only QA pipeline."""


def scene_shell_from_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a validated optional scene-shell contract.

    ``sceneShell`` was added by geometry v2.  Its absence is deliberately
    valid so a frozen core-v0/core-v1 manifest still follows the exact legacy
    QA path.  When present, validation mirrors ``web/asset-manifest.mjs``
    instead of allowing the offline renderer to silently interpret malformed
    geometry differently from the browser.
    """

    if "sceneShell" not in manifest:
        return None
    raw = manifest["sceneShell"]
    if not isinstance(raw, Mapping):
        raise AssetQaError("sceneShell must be an object")
    fields = set(raw)
    missing = sorted(SCENE_SHELL_KEYS - fields)
    extra = sorted(fields - SCENE_SHELL_KEYS)
    if missing:
        raise AssetQaError("sceneShell is missing fields: " + ", ".join(missing))
    if extra:
        raise AssetQaError("sceneShell has unsupported fields: " + ", ".join(extra))
    geometry_version = manifest.get("geometryVersion")
    if (
        isinstance(geometry_version, bool)
        or not isinstance(geometry_version, int)
        or geometry_version < 2
    ):
        raise AssetQaError("sceneShell requires geometryVersion >= 2")
    if isinstance(raw["version"], bool) or raw["version"] != 1:
        raise AssetQaError("sceneShell.version must be 1")
    if raw["type"] != SCENE_SHELL_TYPE:
        raise AssetQaError(f"sceneShell.type must be {SCENE_SHELL_TYPE}")
    dimensions: dict[str, int] = {}
    for field in ("facadeDepth", "slabDepth", "windowBandPitch"):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AssetQaError(f"sceneShell.{field} must be a positive integer")
        dimensions[field] = value
    if dimensions["facadeDepth"] <= dimensions["slabDepth"]:
        raise AssetQaError("sceneShell.facadeDepth must exceed slabDepth")
    colors = raw["colors"]
    if not isinstance(colors, Mapping):
        raise AssetQaError("sceneShell.colors must be an object")
    color_fields = set(colors)
    expected_colors = set(SCENE_SHELL_COLOR_KEYS)
    missing_colors = sorted(expected_colors - color_fields)
    extra_colors = sorted(color_fields - expected_colors)
    if missing_colors:
        raise AssetQaError(
            "sceneShell.colors is missing fields: " + ", ".join(missing_colors)
        )
    if extra_colors:
        raise AssetQaError(
            "sceneShell.colors has unsupported fields: " + ", ".join(extra_colors)
        )
    normalized_colors: dict[str, str] = {}
    for field in SCENE_SHELL_COLOR_KEYS:
        value = colors[field]
        if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
            raise AssetQaError(f"sceneShell.colors.{field} must be #RRGGBB")
        try:
            int(value[1:], 16)
        except ValueError as exc:
            raise AssetQaError(
                f"sceneShell.colors.{field} must be #RRGGBB"
            ) from exc
        normalized_colors[field] = value.upper()
    return {
        "version": 1,
        "type": SCENE_SHELL_TYPE,
        **dimensions,
        "colors": normalized_colors,
    }


def floor_front_edges(layout: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact viewer-facing x=max and y=max floor boundaries.

    These are tile *edges*, not a chain of tile centres.  The extra endpoint
    on each side is what keeps the front corner continuous and gives every
    curtain-wall bay a deterministic mullion position.
    """

    try:
        columns = int(layout["columns"])
        rows = int(layout["rows"])
        origin_x = float(layout["origin"]["x"])
        origin_y = float(layout["origin"]["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssetQaError("sceneShell layout dimensions/origin are invalid") from exc
    if columns <= 0 or rows <= 0:
        raise AssetQaError("sceneShell layout dimensions must be positive")
    right = {
        "x": origin_x + columns * (TILE_WIDTH / 2),
        "y": origin_y + (columns - 1) * (TILE_HEIGHT / 2),
    }
    x_max = [
        {
            "x": right["x"] - index * (TILE_WIDTH / 2),
            "y": right["y"] + index * (TILE_HEIGHT / 2),
        }
        for index in range(rows + 1)
    ]
    front = x_max[-1]
    y_max = [
        {
            "x": front["x"] - index * (TILE_WIDTH / 2),
            "y": front["y"] - index * (TILE_HEIGHT / 2),
        }
        for index in range(columns + 1)
    ]
    return {
        "xMax": x_max,
        "yMax": y_max,
        "rightCorner": dict(x_max[0]),
        "frontCorner": dict(front),
        "leftCorner": dict(y_max[-1]),
    }


def _vertical_offset(point: Mapping[str, float], offset: float) -> dict[str, float]:
    return {"x": float(point["x"]), "y": float(point["y"]) + offset}


def _shell_face_geometry(
    face_id: str,
    top_edge: Sequence[Mapping[str, float]],
    slab_depth: int,
    facade_depth: int,
    window_band_pitch: int,
) -> dict[str, Any]:
    first = top_edge[0]
    last = top_edge[-1]
    top_min_y = min(float(point["y"]) for point in top_edge)
    bottom_max_y = max(float(point["y"]) for point in top_edge) + facade_depth
    # A curtain wall's floor lines are horizontal in world space, so on this 2:1
    # projection they run parallel to the eave, not parallel to the screen.
    # Bands are therefore depths below the eave rather than absolute screen y:
    # one depth describes the same slab on both faces, which is what makes them
    # meet exactly at the shared front corner without a phase fixup.
    first_band_depth = math.ceil(slab_depth / window_band_pitch) * window_band_pitch
    return {
        "id": face_id,
        "topEdge": [dict(point) for point in top_edge],
        "facade": [
            _vertical_offset(first, slab_depth),
            _vertical_offset(last, slab_depth),
            _vertical_offset(last, facade_depth),
            _vertical_offset(first, facade_depth),
        ],
        "slab": [
            dict(first),
            dict(last),
            _vertical_offset(last, slab_depth),
            _vertical_offset(first, slab_depth),
        ],
        "ambientOcclusion": [
            dict(first),
            dict(last),
            _vertical_offset(last, min(2, slab_depth)),
            _vertical_offset(first, min(2, slab_depth)),
        ],
        "mullions": [
            {
                "top": _vertical_offset(point, slab_depth),
                "bottom": _vertical_offset(point, facade_depth),
            }
            for point in top_edge
        ],
        "windowBands": list(
            range(
                first_band_depth,
                facade_depth - window_band_pitch + 1,
                window_band_pitch,
            )
        ),
        "bounds": {
            "left": min(float(point["x"]) for point in top_edge),
            "top": top_min_y + slab_depth,
            "right": max(float(point["x"]) for point in top_edge),
            "bottom": bottom_max_y,
        },
    }


def tower_shell_geometry(
    layout: Mapping[str, Any], shell: Mapping[str, Any]
) -> dict[str, Any]:
    """Build browser-equivalent shell geometry in untransformed scene space."""

    facade_depth = int(shell["facadeDepth"])
    slab_depth = int(shell["slabDepth"])
    window_band_pitch = int(shell["windowBandPitch"])
    if min(facade_depth, slab_depth, window_band_pitch) <= 0 or facade_depth <= slab_depth:
        raise AssetQaError("sceneShell depths and window pitch are invalid")
    edges = floor_front_edges(layout)
    return {
        "edges": edges,
        "xMax": _shell_face_geometry(
            "x-max", edges["xMax"], slab_depth, facade_depth, window_band_pitch
        ),
        "yMax": _shell_face_geometry(
            "y-max", edges["yMax"], slab_depth, facade_depth, window_band_pitch
        ),
        "facadeDepth": facade_depth,
        "slabDepth": slab_depth,
        "windowBandPitch": window_band_pitch,
    }


@dataclass(frozen=True)
class QaAsset:
    """One logical source image used to compose a draft pack preview."""

    slot: str
    kind: str
    image_path: Path
    sha256: str
    metadata: Mapping[str, Any]
    spec: Mapping[str, Any]
    provenance: str

    def image(self) -> Image.Image:
        try:
            with Image.open(self.image_path) as source:
                source.load()
                return source.convert("RGBA")
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetQaError(f"cannot decode PNG for {self.slot}: {self.image_path}") from exc


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AssetQaError(f"cannot read asset file: {path}") from exc
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssetQaError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise AssetQaError(f"{label} root must be an object: {path}")
    return value


def footprint_ground(
    asset: Mapping[str, Any], placement: Mapping[str, Any]
) -> tuple[float, float]:
    """Match ``groundPointForPlacement`` in ``web/asset-manifest.mjs``.

    Layout coordinates name the first footprint cell; the authored anchor sits
    at the centre of the complete footprint bounds.  Using the first cell here
    is the historical mistake that made all multi-cell furniture look shifted.
    """

    footprint = asset.get("footprint")
    if not isinstance(footprint, list) or not footprint:
        footprint = [{"x": 0, "y": 0}]
    try:
        xs = [float(cell["x"]) for cell in footprint]
        ys = [float(cell["y"]) for cell in footprint]
        x = float(placement["x"]) + (min(xs) + max(xs)) / 2
        y = float(placement["y"]) + (min(ys) + max(ys)) / 2
    except (KeyError, TypeError, ValueError) as exc:
        raise AssetQaError("invalid footprint or placement coordinate") from exc
    return x, y


def placement_depth(asset: Mapping[str, Any], placement: Mapping[str, Any]) -> float:
    footprint = asset.get("footprint")
    if not isinstance(footprint, list) or not footprint:
        footprint = [{"x": 0, "y": 0}]
    try:
        extent = max(float(cell["x"]) + float(cell["y"]) for cell in footprint)
        return float(placement["x"]) + float(placement["y"]) + max(0.0, extent)
    except (KeyError, TypeError, ValueError) as exc:
        raise AssetQaError("invalid footprint depth") from exc


def actor_depth(
    actor: Mapping[str, Any],
    placements: Sequence[Mapping[str, Any]],
    seats: Sequence[Mapping[str, Any]],
) -> float:
    """Lift only a layout-verified work actor to its matching desk depth."""

    x = float(actor.get("x", 0))
    y = float(actor.get("y", 0))
    base = x + y + 0.7
    activity = actor.get("activity")
    if not isinstance(activity, Mapping) or activity.get("type") != "work":
        return base
    placement_id = str(activity.get("placementId", ""))
    seat_id = str(activity.get("seatId", ""))
    matching_seat = next(
        (
            seat
            for seat in seats
            if str(seat.get("placementId", "")) == placement_id
            and str(seat.get("id", "")) == seat_id
        ),
        None,
    )
    if matching_seat is None:
        return base
    placement = next(
        (entry for entry in placements if str(entry.get("id", "")) == placement_id),
        None,
    )
    if placement is None:
        return base
    try:
        return max(base, float(placement["depth"]))
    except (KeyError, TypeError, ValueError):
        return base


def sort_renderables(items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Stable equivalent of ``sortByIsometricDepth`` in the web renderer."""

    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            float(pair[1].get("depth", 0) or 0),
            float(pair[1].get("layer", 0) or 0),
            pair[0],
        )
    )
    return [entry for _, entry in indexed]


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _font(size: int = 10) -> ImageFont.ImageFont:
    # Pillow's bundled default face avoids a host font lookup and therefore
    # keeps glyph metrics and output hashes stable across developer machines.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10 compatibility for downstream users.
        return ImageFont.load_default()


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _write_if_changed(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_file() and path.read_bytes() == data:
            return
        path.write_bytes(data)
    except OSError as exc:
        raise AssetQaError(f"cannot write QA output: {path}") from exc


def _alpha_composite_at(canvas: Image.Image, image: Image.Image, x: int, y: int) -> None:
    """Composite with clipping, including negative sprite coordinates."""

    if image.width <= 0 or image.height <= 0:
        return
    left = max(0, x)
    top = max(0, y)
    right = min(canvas.width, x + image.width)
    bottom = min(canvas.height, y + image.height)
    if right <= left or bottom <= top:
        return
    crop = image.crop((left - x, top - y, right - x, bottom - y))
    canvas.alpha_composite(crop, (left, top))


class CoreV2AssetQa:
    """Resolve and render a complete draft core-v2 pack without persistence."""

    def __init__(
        self,
        project_dir: str | Path | None = None,
        *,
        data_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self.project_dir = (
            Path(project_dir).resolve()
            if project_dir is not None
            else Path(__file__).resolve().parent.parent
        )
        self.data_dir = (
            Path(data_dir).resolve() if data_dir is not None else self.project_dir / "data"
        )
        self.output_dir = (
            Path(output_dir).resolve()
            if output_dir is not None
            else self.data_dir / "assets" / "derived" / PACK_ID
        )
        self.inbox_dir = self.data_dir / "assets" / "inbox"
        self.blobs_dir = self.data_dir / "assets" / "blobs"
        self.db_path = self.data_dir / "asset-lab.sqlite3"
        self.specs = self._load_specs()
        self.pack_spec = self.specs[PACK_ID]
        self.layouts = self._load_layouts()
        self.logical_slots = self._logical_slots(PACK_ID)
        self.editable_slots = tuple(self.pack_spec.get("requiredEditableSlots", ()))
        if len(self.logical_slots) != 29 or len(self.editable_slots) != 16:
            raise AssetQaError("core-v2 QA expects 29 logical slots and 16 editable candidates")
        self.asset_specs = self._merged_asset_specs(PACK_ID)
        self.player_accents = tuple(
            self.pack_spec.get("palette", {}).get("players", FALLBACK_ACCENTS)
        )
        if len(self.player_accents) != len(PLAYER_NAMES):
            self.player_accents = FALLBACK_ACCENTS

    def _load_specs(self) -> dict[str, dict[str, Any]]:
        assets_dir = self.project_dir / "assets"
        paths = {
            "core-v0": assets_dir / "core-pack.spec.json",
            "core-v1": assets_dir / "core-v1-pack.spec.json",
            "core-v2": assets_dir / "core-v2-pack.spec.json",
        }
        specs = {pack_id: _read_json(path, f"{pack_id} pack spec") for pack_id, path in paths.items()}
        for pack_id, spec in specs.items():
            if spec.get("id") != pack_id:
                raise AssetQaError(f"pack spec identifies the wrong pack: {pack_id}")
        return specs

    def _load_layouts(self) -> dict[str, dict[str, Any]]:
        payload = _read_json(self.project_dir / "assets" / "world-layouts.json", "world layouts")
        layouts = payload.get("layouts")
        if not isinstance(layouts, list):
            raise AssetQaError("world layouts must contain a layouts array")
        indexed = {
            str(entry.get("id")): entry
            for entry in layouts
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
        for layout_id in (OPENING_LAYOUT_ID, MID_LAYOUT_ID):
            if layout_id not in indexed:
                raise AssetQaError(f"required core-v2 layout is missing: {layout_id}")
        return indexed

    def _logical_slots(self, pack_id: str) -> tuple[str, ...]:
        spec = self.specs[pack_id]
        base_pack_id = spec.get("basePackId")
        if isinstance(base_pack_id, str) and base_pack_id:
            slots = list(self._logical_slots(base_pack_id))
        else:
            slots = [str(slot) for slot in spec.get("requiredSlots", ())]
        for slot in spec.get("overrideSlots", ()):
            if slot not in slots:
                raise AssetQaError(f"{pack_id} overrides unknown base slot: {slot}")
        for slot in spec.get("requiredNewSlots", ()):
            if slot in slots:
                raise AssetQaError(f"{pack_id} declares duplicate slot: {slot}")
            slots.append(str(slot))
        return tuple(slots)

    def _merged_asset_specs(self, pack_id: str) -> dict[str, dict[str, Any]]:
        spec = self.specs[pack_id]
        base_pack_id = spec.get("basePackId")
        merged = self._merged_asset_specs(base_pack_id) if isinstance(base_pack_id, str) else {}
        for slot, patch in spec.get("baseAssetPatches", {}).items():
            if slot not in merged or not isinstance(patch, Mapping):
                raise AssetQaError(f"{pack_id} patches unknown asset: {slot}")
            merged[slot] = _deep_merge(merged[slot], patch)
        for entry in spec.get("assets", ()):
            if not isinstance(entry, Mapping):
                continue
            slot = entry.get("slot")
            asset_id = entry.get("id")
            # The root manifest expands character/effect sheets into runtime
            # frame entries.  Logical source rows are reconstructed from their
            # selected version metadata instead.
            if isinstance(slot, str) and asset_id == slot:
                merged[slot] = copy.deepcopy(dict(entry))
        return merged

    def _inbox_candidates(self) -> dict[str, QaAsset]:
        found: dict[str, list[tuple[Path, Mapping[str, Any]]]] = {
            slot: [] for slot in self.editable_slots
        }
        if not self.inbox_dir.is_dir():
            raise AssetQaError(f"asset inbox does not exist: {self.inbox_dir}")
        for sidecar_path in sorted(self.inbox_dir.glob("*.json"), key=lambda path: path.name.casefold()):
            try:
                metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, Mapping) or metadata.get("packId") != PACK_ID:
                continue
            slot = str(metadata.get("slot", ""))
            if slot in found:
                found[slot].append((sidecar_path, dict(metadata)))
        missing = [slot for slot, entries in found.items() if not entries]
        ambiguous = {slot: [path.name for path, _ in entries] for slot, entries in found.items() if len(entries) > 1}
        if missing or ambiguous:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if ambiguous:
                details.append(
                    "ambiguous: "
                    + "; ".join(f"{slot}={names}" for slot, names in sorted(ambiguous.items()))
                )
            raise AssetQaError("core-v2 inbox candidates are incomplete (" + " | ".join(details) + ")")

        resolved: dict[str, QaAsset] = {}
        for slot in self.editable_slots:
            sidecar_path, metadata = found[slot][0]
            png_path = sidecar_path.with_suffix(".png")
            if not png_path.is_file():
                raise AssetQaError(f"candidate PNG is missing for {slot}: {png_path}")
            expected_name = metadata.get("sourceName")
            if expected_name is not None and Path(str(expected_name)).name != png_path.name:
                raise AssetQaError(f"candidate sourceName does not match sidecar for {slot}")
            spec = self.asset_specs.get(slot)
            if spec is None:
                raise AssetQaError(f"core-v2 has no render spec for candidate: {slot}")
            image = self._validated_image(png_path, slot)
            frame = spec.get("frame", {})
            expected_size = (int(frame.get("width", 0)), int(frame.get("height", 0)))
            if image.size != expected_size:
                raise AssetQaError(
                    f"candidate {slot} must be {expected_size[0]}x{expected_size[1]}, got {image.width}x{image.height}"
                )
            resolved[slot] = QaAsset(
                slot=slot,
                kind=str(spec.get("kind", metadata.get("kind", ""))),
                image_path=png_path,
                sha256=sha256_path(png_path),
                metadata=metadata,
                spec=spec,
                provenance="inbox-draft",
            )
        return resolved

    @staticmethod
    def _validated_image(path: Path, slot: str) -> Image.Image:
        try:
            with Image.open(path) as source:
                if source.format != "PNG":
                    raise AssetQaError(f"candidate is not PNG for {slot}: {path}")
                source.load()
                return source.convert("RGBA")
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetQaError(f"cannot decode PNG for {slot}: {path}") from exc

    def _inherited_assets(self) -> dict[str, QaAsset]:
        inherited_slots = tuple(slot for slot in self.logical_slots if slot not in self.editable_slots)
        if not self.db_path.is_file():
            raise AssetQaError(f"asset database is required for inherited core-v2 slots: {self.db_path}")
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                """
                SELECT pm.slot, pm.inherited, pm.source_release_id,
                       a.pack_id AS owner_pack_id, v.id AS version_id,
                       v.sha256, v.status, v.metadata_json
                  FROM pack_members pm
                  JOIN assets a ON a.id = pm.asset_id
                  LEFT JOIN versions v ON v.id = pm.version_id
                 WHERE pm.pack_id = ?
                 ORDER BY pm.ordinal, pm.slot
                """,
                (PACK_ID,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise AssetQaError(f"cannot read inherited asset selection: {self.db_path}") from exc
        finally:
            try:
                connection.close()
            except UnboundLocalError:
                pass
        indexed = {str(row["slot"]): row for row in rows}
        resolved: dict[str, QaAsset] = {}
        missing: list[str] = []
        for slot in inherited_slots:
            row = indexed.get(slot)
            if row is None or row["version_id"] is None or row["sha256"] is None:
                missing.append(slot)
                continue
            if int(row["inherited"]) != 1 or row["status"] != "accepted":
                raise AssetQaError(f"core-v2 inherited slot is not a frozen accepted selection: {slot}")
            sha = str(row["sha256"])
            blob_path = self.blobs_dir / sha[:2] / f"{sha}.png"
            if not blob_path.is_file():
                raise AssetQaError(f"inherited blob is missing for {slot}: {blob_path}")
            if sha256_path(blob_path) != sha:
                raise AssetQaError(f"inherited blob hash mismatch for {slot}: {blob_path}")
            try:
                metadata = json.loads(str(row["metadata_json"]))
            except json.JSONDecodeError as exc:
                raise AssetQaError(f"inherited metadata is invalid for {slot}") from exc
            if not isinstance(metadata, Mapping):
                raise AssetQaError(f"inherited metadata root is invalid for {slot}")
            spec = self.asset_specs.get(slot)
            if spec is None:
                if slot not in {"character.gus", "effect.good-card-heart"}:
                    raise AssetQaError(f"core-v2 has no render spec for inherited slot: {slot}")
                spec = {
                    "id": slot,
                    "slot": slot,
                    "kind": str(metadata.get("kind", slot.split(".", 1)[0])),
                    "anchor": copy.deepcopy(metadata.get("anchor", {"x": 0, "y": 0})),
                    "footprint": copy.deepcopy(metadata.get("footprint", [{"x": 0, "y": 0, "blocked": False}])),
                    "offset": {"x": 0, "y": 0},
                    "layer": 2 if slot == "character.gus" else 4,
                }
            self._validated_image(blob_path, slot)
            resolved[slot] = QaAsset(
                slot=slot,
                kind=str(spec.get("kind", metadata.get("kind", ""))),
                image_path=blob_path,
                sha256=sha,
                metadata=dict(metadata),
                spec=spec,
                provenance=f"base-release:{row['source_release_id']}",
            )
        if missing:
            raise AssetQaError("core-v2 inherited selections are incomplete: " + ", ".join(missing))
        return resolved

    def resolve_assets(self) -> dict[str, QaAsset]:
        resolved = {**self._inherited_assets(), **self._inbox_candidates()}
        missing = [slot for slot in self.logical_slots if slot not in resolved]
        if missing:
            raise AssetQaError("resolved core-v2 pack is incomplete: " + ", ".join(missing))
        return {slot: resolved[slot] for slot in self.logical_slots}

    @staticmethod
    def _draw_checker(
        canvas: Image.Image,
        box: tuple[int, int, int, int],
        *,
        cell: int = 16,
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        left, top, right, bottom = box
        colors = ("#1B252B", "#26333A")
        draw.rectangle(box, fill=colors[0])
        for y in range(top, bottom, cell):
            for x in range(left, right, cell):
                draw.rectangle(
                    (x, y, min(right - 1, x + cell - 1), min(bottom - 1, y + cell - 1)),
                    fill=colors[((x - left) // cell + (y - top) // cell) % 2],
                )

    def render_contact_sheet(self, assets: Mapping[str, QaAsset]) -> Image.Image:
        candidates = [assets[slot] for slot in self.editable_slots]
        backdrop = next((asset for asset in candidates if asset.kind == "backdrop"), None)
        regular = [asset for asset in candidates if asset is not backdrop]
        if backdrop is None or len(regular) != 15:
            raise AssetQaError("core-v2 contact sheet requires one backdrop and 15 regular candidates")

        width = 2640
        header_height = 48
        backdrop_height = 760
        cell_width = width // 3
        cell_height = 460
        height = header_height + backdrop_height + cell_height * 5
        canvas = Image.new("RGBA", (width, height), "#10171C")
        draw = ImageDraw.Draw(canvas)
        title_font = _font(22)
        label_font = _font(16)
        note_font = _font(13)
        draw.text((20, 12), "core-v2 / 16 draft candidates / native 1x + nearest 4x", font=title_font, fill="#F3F4EF")

        top = header_height
        draw.rectangle((0, top, width - 1, top + backdrop_height - 1), outline="#526169", width=2)
        draw.text((20, top + 12), backdrop.slot, font=label_font, fill="#F3F4EF")
        backdrop_image = backdrop.image()
        one_box = (20, top + 46, 20 + 660, top + 46 + 164)
        four_box = (20, top + 226, width - 20, top + 226 + 596)
        self._draw_checker(canvas, one_box)
        self._draw_checker(canvas, four_box)
        one_x = one_box[0] + (one_box[2] - one_box[0] - backdrop_image.width) // 2
        one_y = one_box[1] + (one_box[3] - one_box[1] - backdrop_image.height) // 2
        _alpha_composite_at(canvas, backdrop_image, one_x, one_y)
        four = backdrop_image.resize((backdrop_image.width * 4, backdrop_image.height * 4), Image.Resampling.NEAREST)
        four_x = four_box[0] + (four_box[2] - four_box[0] - four.width) // 2
        four_y = four_box[1] + (four_box[3] - four_box[1] - four.height) // 2
        _alpha_composite_at(canvas, four, four_x, four_y)
        draw.text((one_box[0] + 8, one_box[1] + 6), "1x", font=note_font, fill="#F1BF65")
        draw.text((four_box[0] + 8, four_box[1] + 6), "4x", font=note_font, fill="#F1BF65")

        grid_top = header_height + backdrop_height
        for index, asset in enumerate(regular):
            column = index % 3
            row = index // 3
            left = column * cell_width
            top = grid_top + row * cell_height
            draw.rectangle(
                (left, top, left + cell_width - 1, top + cell_height - 1),
                outline="#526169",
                width=2,
            )
            draw.text((left + 16, top + 12), asset.slot, font=label_font, fill="#F3F4EF")
            image = asset.image()
            one_box = (left + 16, top + 48, left + 216, top + cell_height - 16)
            four_box = (left + 232, top + 48, left + cell_width - 16, top + cell_height - 16)
            self._draw_checker(canvas, one_box)
            self._draw_checker(canvas, four_box)
            one_x = one_box[0] + (one_box[2] - one_box[0] - image.width) // 2
            one_y = one_box[1] + (one_box[3] - one_box[1] - image.height) // 2
            _alpha_composite_at(canvas, image, one_x, one_y)
            enlarged = image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST)
            four_x = four_box[0] + (four_box[2] - four_box[0] - enlarged.width) // 2
            four_y = four_box[1] + (four_box[3] - four_box[1] - enlarged.height) // 2
            _alpha_composite_at(canvas, enlarged, four_x, four_y)
            draw.text((one_box[0] + 6, one_box[1] + 4), "1x", font=note_font, fill="#F1BF65")
            draw.text((four_box[0] + 6, four_box[1] + 4), "4x", font=note_font, fill="#F1BF65")
        return canvas

    @staticmethod
    def _project(x: float, y: float, origin: Mapping[str, Any]) -> tuple[float, float]:
        return (
            float(origin["x"]) + (x - y) * TILE_WIDTH / 2,
            float(origin["y"]) + (x + y) * TILE_HEIGHT / 2,
        )

    @staticmethod
    def _screen_point(
        point: tuple[float, float], camera: Mapping[str, float]
    ) -> tuple[float, float]:
        width, height = CANVAS_SIZE
        zoom = float(camera["zoom"])
        return (
            width / 2 + float(camera["x"]) + (point[0] - width / 2) * zoom,
            height / 2 + float(camera["y"]) + (point[1] - height / 2) * zoom,
        )

    @classmethod
    def _screen_shell_geometry(
        cls,
        layout: Mapping[str, Any],
        shell: Mapping[str, Any],
        camera: Mapping[str, float],
    ) -> dict[str, Any]:
        """Apply the live canvas centre-origin transform to shell geometry."""

        local = tower_shell_geometry(layout, shell)
        # Band depths are vertical offsets, so the affine camera scales them by
        # zoom alone. Transforming them as points would fold in the pan twice.
        zoom = float(camera["zoom"])

        def point(value: Mapping[str, float]) -> dict[str, float]:
            x, y = cls._screen_point(
                (float(value["x"]), float(value["y"])), camera
            )
            return {"x": x, "y": y}

        def face(value: Mapping[str, Any]) -> dict[str, Any]:
            bounds = value["bounds"]
            left_top = point({"x": bounds["left"], "y": bounds["top"]})
            right_bottom = point({"x": bounds["right"], "y": bounds["bottom"]})
            return {
                "id": value["id"],
                "topEdge": [point(entry) for entry in value["topEdge"]],
                "facade": [point(entry) for entry in value["facade"]],
                "slab": [point(entry) for entry in value["slab"]],
                "ambientOcclusion": [
                    point(entry) for entry in value["ambientOcclusion"]
                ],
                "mullions": [
                    {"top": point(entry["top"]), "bottom": point(entry["bottom"])}
                    for entry in value["mullions"]
                ],
                "windowBands": [
                    float(band) * zoom for band in value["windowBands"]
                ],
                "bounds": {
                    "left": min(left_top["x"], right_bottom["x"]),
                    "top": min(left_top["y"], right_bottom["y"]),
                    "right": max(left_top["x"], right_bottom["x"]),
                    "bottom": max(left_top["y"], right_bottom["y"]),
                },
                "localBounds": dict(bounds),
            }

        return {
            "local": local,
            "edges": {
                "xMax": [point(entry) for entry in local["edges"]["xMax"]],
                "yMax": [point(entry) for entry in local["edges"]["yMax"]],
                "rightCorner": point(local["edges"]["rightCorner"]),
                "frontCorner": point(local["edges"]["frontCorner"]),
                "leftCorner": point(local["edges"]["leftCorner"]),
            },
            "xMax": face(local["xMax"]),
            "yMax": face(local["yMax"]),
            "facadeDepth": local["facadeDepth"],
            "slabDepth": local["slabDepth"],
            "windowBandPitch": local["windowBandPitch"],
        }

    @staticmethod
    def _rounded_points(
        points: Sequence[Mapping[str, float]],
    ) -> list[tuple[int, int]]:
        return [(round(float(point["x"])), round(float(point["y"]))) for point in points]

    @staticmethod
    def _window_band_points(
        face: Mapping[str, Any], depth: float, height: float
    ) -> list[tuple[int, int]]:
        """One curtain-wall band as a parallelogram swept from the eave.

        The face is the top edge extruded downwards, so a band is that edge at
        ``depth`` and at ``depth + height``.  Taking the two endpoints is exact
        because every top-edge point is collinear on the 2:1 grid.
        """

        first = face["topEdge"][0]
        last = face["topEdge"][-1]
        return [
            (round(float(first["x"])), round(float(first["y"]) + depth)),
            (round(float(last["x"])), round(float(last["y"]) + depth)),
            (round(float(last["x"])), round(float(last["y"]) + depth + height)),
            (round(float(first["x"])), round(float(first["y"]) + depth + height)),
        ]

    @staticmethod
    def _pixel_rectangle(
        draw: ImageDraw.ImageDraw,
        x: float,
        y: float,
        width: float,
        height: float,
        fill: str,
    ) -> None:
        left = round(x)
        top = round(y)
        pixel_width = max(1, round(width))
        pixel_height = max(1, round(height))
        draw.rectangle(
            (left, top, left + pixel_width - 1, top + pixel_height - 1),
            fill=fill,
        )

    def _draw_shell_face(
        self,
        layer: Image.Image,
        face: Mapping[str, Any],
        shell: Mapping[str, Any],
        camera: Mapping[str, float],
        *,
        base: str,
        glass: str,
        slab: str,
    ) -> None:
        colors = shell["colors"]
        zoom = float(camera["zoom"])
        outline_width = max(1, round(zoom))
        facade_points = self._rounded_points(face["facade"])
        draw = ImageDraw.Draw(layer)
        draw.polygon(
            facade_points,
            fill=base,
            outline=colors["outline"],
            width=outline_width,
        )

        # Pillow has no persistent canvas clip stack.  Draw the complete
        # window/mullion pattern on a transparent layer, multiply its alpha by
        # the facade polygon, then composite it over the base face.
        mask = Image.new("L", CANVAS_SIZE, 0)
        ImageDraw.Draw(mask).polygon(facade_points, fill=255)
        pattern = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        pattern_draw = ImageDraw.Draw(pattern)
        glass_height = max(1, int(shell["windowBandPitch"]) - 3) * zoom
        spandrel_height = SPANDREL_HEIGHT * zoom
        for band_depth in face["windowBands"]:
            pattern_draw.polygon(
                self._window_band_points(face, band_depth, spandrel_height),
                fill=colors["mullion"],
            )
            pattern_draw.polygon(
                self._window_band_points(
                    face, band_depth + spandrel_height, glass_height
                ),
                fill=glass,
            )
        for mullion in face["mullions"]:
            top = mullion["top"]
            bottom = mullion["bottom"]
            self._pixel_rectangle(
                pattern_draw,
                float(top["x"]) - zoom,
                float(top["y"]),
                2 * zoom,
                float(bottom["y"]) - float(top["y"]),
                colors["mullion"],
            )
        pattern.putalpha(ImageChops.multiply(pattern.getchannel("A"), mask))
        layer.alpha_composite(pattern)

        draw = ImageDraw.Draw(layer)
        draw.polygon(
            self._rounded_points(face["slab"]),
            fill=slab,
            outline=colors["outline"],
            width=outline_width,
        )
        draw.polygon(
            self._rounded_points(face["ambientOcclusion"]),
            fill=colors["ambientOcclusion"],
        )

    def _scene_shell_layer(
        self,
        layout: Mapping[str, Any],
        camera: Mapping[str, float],
        shell: Mapping[str, Any],
    ) -> tuple[Image.Image, dict[str, Any]]:
        geometry = self._screen_shell_geometry(layout, shell, camera)
        layer = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        colors = shell["colors"]
        # x=max turns away from the upper-left light source. y=max remains the
        # lighter face; ordering preserves their shared front corner.
        self._draw_shell_face(
            layer,
            geometry["xMax"],
            shell,
            camera,
            base=colors["facadeDark"],
            glass=colors["facadeLight"],
            slab=colors["mullion"],
        )
        self._draw_shell_face(
            layer,
            geometry["yMax"],
            shell,
            camera,
            base=colors["facadeLight"],
            glass=colors["window"],
            slab=colors["slab"],
        )
        return layer, geometry

    def _floor_asset_id(self, layout: Mapping[str, Any], x: int, y: int) -> str:
        floor = layout["floor"]
        asset_id = str(floor["defaultAssetId"])
        for region in floor.get("regions", ()):
            if (
                int(region["x"]) <= x < int(region["x"]) + int(region["width"])
                and int(region["y"]) <= y < int(region["y"]) + int(region["height"])
            ):
                asset_id = str(region["assetId"])
        border = floor.get("border")
        if isinstance(border, Mapping):
            edges = set(border.get("edges", ()))
            on_border = (
                ("north" in edges and y == 0)
                or ("east" in edges and x == int(layout["columns"]) - 1)
                or ("south" in edges and y == int(layout["rows"]) - 1)
                or ("west" in edges and x == 0)
            )
            if on_border:
                asset_id = str(border["assetId"])
        return asset_id

    def _placements(
        self, layout: Mapping[str, Any], assets: Mapping[str, QaAsset]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        placements: list[dict[str, Any]] = []
        seats: list[dict[str, Any]] = []
        for index, raw in enumerate(layout.get("placements", ())):
            asset_id = str(raw.get("assetId", ""))
            resolved = assets.get(asset_id)
            if resolved is None:
                raise AssetQaError(f"layout {layout.get('id')} references missing asset: {asset_id}")
            spec = resolved.spec
            ground_x, ground_y = footprint_ground(spec, raw)
            placement = {
                "id": str(raw.get("id", f"placement-{index}")),
                "assetId": asset_id,
                "x": int(raw["x"]),
                "y": int(raw["y"]),
                "renderX": ground_x,
                "renderY": ground_y,
                "depth": placement_depth(spec, raw),
                "layer": float(spec.get("layer", 0) or 0),
                "kind": str(spec.get("kind", resolved.kind)),
            }
            placements.append(placement)
            for point in spec.get("interactionPoints", ()):
                if not isinstance(point, Mapping) or point.get("kind") != "work-seat":
                    continue
                seats.append(
                    {
                        "id": str(point["id"]),
                        "placementId": placement["id"],
                        "assetId": asset_id,
                        "x": placement["x"] + int(point["x"]),
                        "y": placement["y"] + int(point["y"]),
                        "facing": str(point["facing"]),
                    }
                )
        return placements, seats

    def _actors(
        self,
        layout: Mapping[str, Any],
        placements: Sequence[Mapping[str, Any]],
        seats: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        activities = {
            str(entry.get("playerId", "")): entry
            for entry in layout.get("initialActivities", ())
            if isinstance(entry, Mapping)
        }
        actors: list[dict[str, Any]] = []
        for index, spawn in enumerate(layout.get("spawnPoints", ())):
            player_id = str(spawn.get("playerId", ""))
            activity_source = activities.get(player_id)
            activity: dict[str, Any] | None = None
            facing = "southeast"
            x = int(spawn["x"])
            y = int(spawn["y"])
            if activity_source is not None:
                seat = next(
                    (
                        entry
                        for entry in seats
                        if entry["placementId"] == str(activity_source.get("placementId", ""))
                        and entry["id"] == str(activity_source.get("seatId", ""))
                    ),
                    None,
                )
                if seat is None:
                    raise AssetQaError(
                        f"layout {layout.get('id')} initial work references unknown seat for {player_id}"
                    )
                if (x, y) != (seat["x"], seat["y"]):
                    raise AssetQaError(
                        f"layout {layout.get('id')} initial work spawn does not match seat for {player_id}"
                    )
                facing = seat["facing"]
                activity = {
                    "type": "work",
                    "placementId": seat["placementId"],
                    "seatId": seat["id"],
                }
            actor = {
                "id": player_id,
                "name": str(spawn.get("name") or PLAYER_NAMES[index % len(PLAYER_NAMES)]),
                "x": x,
                "y": y,
                "facing": facing,
                "activity": activity,
                "color": self.player_accents[index % len(self.player_accents)],
            }
            actor["depth"] = actor_depth(actor, placements, seats)
            actor["layer"] = 2
            actors.append(actor)
        return actors

    @staticmethod
    def _asset_visual_box(
        spec: Mapping[str, Any], point: tuple[float, float]
    ) -> tuple[float, float, float, float]:
        frame = spec.get("frame", {})
        anchor = spec.get("anchor", {})
        offset = spec.get("offset", {})
        left = point[0] + float(offset.get("x", 0)) - float(anchor.get("x", 0))
        top = point[1] + float(offset.get("y", 0)) - float(anchor.get("y", 0))
        return (
            left,
            top,
            left + float(frame.get("width", 0)),
            top + float(frame.get("height", 0)),
        )

    @staticmethod
    def _union(
        bounds: tuple[float, float, float, float] | None,
        candidate: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        if bounds is None:
            return candidate
        return (
            min(bounds[0], candidate[0]),
            min(bounds[1], candidate[1]),
            max(bounds[2], candidate[2]),
            max(bounds[3], candidate[3]),
        )

    def _camera(
        self,
        layout: Mapping[str, Any],
        assets: Mapping[str, QaAsset],
        placements: Sequence[Mapping[str, Any]],
        actors: Sequence[Mapping[str, Any]],
        *,
        padding: int = 16,
    ) -> dict[str, float]:
        origin = layout["origin"]
        bounds: tuple[float, float, float, float] | None = None
        for y in range(int(layout["rows"])):
            for x in range(int(layout["columns"])):
                point = self._project(x, y, origin)
                bounds = self._union(
                    bounds,
                    (
                        point[0] - TILE_WIDTH / 2,
                        point[1] - TILE_HEIGHT / 2,
                        point[0] + TILE_WIDTH / 2,
                        point[1] + TILE_HEIGHT / 2,
                    ),
                )
        for placement in placements:
            if placement["kind"] == "backdrop":
                continue
            point = self._project(float(placement["renderX"]), float(placement["renderY"]), origin)
            bounds = self._union(bounds, self._asset_visual_box(assets[placement["assetId"]].spec, point))
        character = assets["character.gus"]
        metadata = character.metadata
        frame_width = int(metadata.get("frameWidth", 24))
        frame_height = int(metadata.get("frameHeight", 48))
        anchor = metadata.get("anchor", {"x": 12, "y": 46})
        for actor in actors:
            point = self._project(float(actor["x"]), float(actor["y"]), origin)
            bounds = self._union(
                bounds,
                (
                    point[0] - float(anchor.get("x", 12)),
                    point[1] + 3 - float(anchor.get("y", 46)),
                    point[0] - float(anchor.get("x", 12)) + frame_width,
                    point[1] + 3 - float(anchor.get("y", 46)) + frame_height,
                ),
            )
            label_width = max(24, len(str(actor["name"])) * 7 + 12)
            bounds = self._union(
                bounds,
                (point[0] - label_width / 2, point[1] - 62, point[0] + label_width / 2, point[1] - 43),
            )
        if bounds is None:
            return {"x": 0.0, "y": 0.0, "zoom": 1.0}
        visual_width = max(1.0, bounds[2] - bounds[0])
        visual_height = max(1.0, bounds[3] - bounds[1])
        fitting = [
            zoom
            for zoom in ZOOM_STEPS
            if visual_width * zoom <= CANVAS_SIZE[0] - padding * 2
            and visual_height * zoom <= CANVAS_SIZE[1] - padding * 2
        ]
        zoom = max(fitting) if fitting else min(ZOOM_STEPS)
        center_x = (bounds[0] + bounds[2]) / 2
        center_y = (bounds[1] + bounds[3]) / 2
        return {
            "x": float(round(-(center_x - CANVAS_SIZE[0] / 2) * zoom)),
            "y": float(round(-(center_y - CANVAS_SIZE[1] / 2) * zoom)),
            "zoom": zoom,
        }

    @staticmethod
    def _scaled(image: Image.Image, zoom: float) -> Image.Image:
        if zoom == 1:
            return image
        return image.resize(
            (max(1, round(image.width * zoom)), max(1, round(image.height * zoom))),
            Image.Resampling.NEAREST,
        )

    def _draw_asset(
        self,
        canvas: Image.Image,
        asset: QaAsset,
        ground: tuple[float, float],
        camera: Mapping[str, float],
    ) -> None:
        zoom = float(camera["zoom"])
        image = self._scaled(asset.image(), zoom)
        point = self._screen_point(ground, camera)
        anchor = asset.spec.get("anchor", {})
        offset = asset.spec.get("offset", {})
        left = round(point[0] + float(offset.get("x", 0)) * zoom - float(anchor.get("x", 0)) * zoom)
        top = round(point[1] + float(offset.get("y", 0)) * zoom - float(anchor.get("y", 0)) * zoom)
        _alpha_composite_at(canvas, image, left, top)

    def _character_frame(self, asset: QaAsset, direction: str, action: str) -> Image.Image:
        sheet = asset.image()
        metadata = asset.metadata
        animations = metadata.get("animations", {})
        indices = animations.get(action, {}).get(direction, ()) if isinstance(animations, Mapping) else ()
        frame_index = int(indices[0]) if indices else 0
        frames = metadata.get("frames")
        if isinstance(frames, list) and 0 <= frame_index < len(frames):
            frame = frames[frame_index]
            box = (
                int(frame["x"]),
                int(frame["y"]),
                int(frame["x"]) + int(frame["width"]),
                int(frame["y"]) + int(frame["height"]),
            )
        else:
            width = int(metadata.get("frameWidth", min(24, sheet.width)))
            height = int(metadata.get("frameHeight", min(48, sheet.height)))
            columns = max(1, int(metadata.get("columns", max(1, sheet.width // width))))
            box = (
                (frame_index % columns) * width,
                (frame_index // columns) * height,
                (frame_index % columns + 1) * width,
                (frame_index // columns + 1) * height,
            )
        return sheet.crop(box)

    def _draw_marker(
        self, canvas: Image.Image, point: tuple[float, float], color: str, zoom: float
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        radius_x = max(7, round(9 * zoom))
        radius_y = max(3, round(4 * zoom))
        x, y = round(point[0]), round(point[1])
        draw.ellipse((x - radius_x, y - radius_y, x + radius_x, y + radius_y), fill=color, outline="#FFF8DF", width=1)

    def _draw_actor(
        self,
        canvas: Image.Image,
        actor: Mapping[str, Any],
        asset: QaAsset,
        point: tuple[float, float],
        camera: Mapping[str, float],
        *,
        draw_marker: bool,
    ) -> None:
        zoom = float(camera["zoom"])
        screen = self._screen_point(point, camera)
        if draw_marker:
            self._draw_marker(canvas, screen, str(actor["color"]), zoom)
        action = "work" if isinstance(actor.get("activity"), Mapping) else "idle"
        frame = self._scaled(self._character_frame(asset, str(actor["facing"]), action), zoom)
        anchor = asset.metadata.get("anchor", {"x": 12, "y": 46})
        left = round(screen[0] - float(anchor.get("x", 12)) * zoom)
        top = round(screen[1] + 3 * zoom - float(anchor.get("y", 46)) * zoom)
        _alpha_composite_at(canvas, frame, left, top)
        draw = ImageDraw.Draw(canvas)
        label_font = _font(max(8, round(9 * zoom)))
        label = str(actor["name"])
        box = draw.textbbox((0, 0), label, font=label_font, stroke_width=1)
        label_width = box[2] - box[0]
        label_x = max(4, min(canvas.width - label_width - 4, round(screen[0] - label_width / 2)))
        label_y = max(4, min(canvas.height - 15, round(screen[1] - 57 * zoom)))
        draw.ellipse((label_x - 8, label_y + 3, label_x - 2, label_y + 9), fill="#52C56A", outline="#0D2228")
        draw.text(
            (label_x, label_y),
            label,
            font=label_font,
            fill="#FFF8DF",
            stroke_width=2,
            stroke_fill="#0D2228",
        )

    def render_scene(
        self,
        layout: Mapping[str, Any],
        assets: Mapping[str, QaAsset],
        *,
        manifest: Mapping[str, Any] | None = None,
    ) -> Image.Image:
        placements, seats = self._placements(layout, assets)
        actors = self._actors(layout, placements, seats)
        camera = self._camera(layout, assets, placements, actors)
        shell = scene_shell_from_manifest(
            self.pack_spec if manifest is None else manifest
        )
        canvas = Image.new("RGBA", CANVAS_SIZE, "#1E2A2E")

        # Geometry v2 backdrops are screen-space images.  Their declared ground
        # equals anchor-offset, so the top-left is derived as (0, 0) without a
        # magic BACKDROP_GROUND constant and never receives camera transform.
        for placement in placements:
            if placement["kind"] != "backdrop":
                continue
            asset = assets[placement["assetId"]]
            image = asset.image()
            anchor = asset.spec.get("anchor", {})
            offset = asset.spec.get("offset", {})
            ground = (
                float(anchor.get("x", 0)) - float(offset.get("x", 0)),
                float(anchor.get("y", 0)) - float(offset.get("y", 0)),
            )
            left = round(ground[0] + float(offset.get("x", 0)) - float(anchor.get("x", 0)))
            top = round(ground[1] + float(offset.get("y", 0)) - float(anchor.get("y", 0)))
            _alpha_composite_at(canvas, image, left, top)

        # Match the browser's explicit layer contract: screen-space background
        # first, world-space facade second, then the floor and scene objects.
        # Camera fitting above never sees the 512px facade depth, so adding the
        # tower cannot shrink an otherwise valid map fit.
        if shell is not None:
            facade, _ = self._scene_shell_layer(layout, camera, shell)
            canvas.alpha_composite(facade)

        origin = layout["origin"]
        floor_items = []
        for y in range(int(layout["rows"])):
            for x in range(int(layout["columns"])):
                floor_items.append((x + y, y, x, self._floor_asset_id(layout, x, y)))
        for _, y, x, asset_id in sorted(floor_items):
            point = self._project(x, y, origin)
            self._draw_asset(canvas, assets[asset_id], point, camera)

        # Runtime draws emphasis beneath all composite furniture for a working
        # actor.  This keeps the ring from painting over the desk after the
        # actor itself is depth-lifted.
        for actor in actors:
            if isinstance(actor.get("activity"), Mapping):
                local = self._project(float(actor["x"]), float(actor["y"]), origin)
                self._draw_marker(canvas, self._screen_point(local, camera), str(actor["color"]), float(camera["zoom"]))

        renderables: list[dict[str, Any]] = []
        for placement in placements:
            if placement["kind"] != "backdrop":
                renderables.append({**placement, "renderType": "placement"})
        for actor in actors:
            renderables.append({**actor, "renderType": "actor"})
        for item in sort_renderables(renderables):
            if item["renderType"] == "placement":
                local = self._project(float(item["renderX"]), float(item["renderY"]), origin)
                self._draw_asset(canvas, assets[item["assetId"]], local, camera)
            else:
                local = self._project(float(item["x"]), float(item["y"]), origin)
                self._draw_actor(
                    canvas,
                    item,
                    assets["character.gus"],
                    local,
                    camera,
                    draw_marker=not isinstance(item.get("activity"), Mapping),
                )
        return canvas

    @staticmethod
    def _receipt_number(value: float) -> int | float:
        rounded = round(float(value), 3)
        return int(rounded) if rounded.is_integer() else rounded

    def _scene_shell_scene_metrics(
        self,
        layout: Mapping[str, Any],
        assets: Mapping[str, QaAsset],
        shell: Mapping[str, Any],
    ) -> dict[str, Any]:
        placements, seats = self._placements(layout, assets)
        actors = self._actors(layout, placements, seats)
        camera = self._camera(layout, assets, placements, actors)
        layer, geometry = self._scene_shell_layer(layout, camera, shell)
        alpha = layer.getchannel("A")
        histogram = alpha.histogram()
        facade_pixels = sum(histogram[1:])
        bottom_row = alpha.crop((0, CANVAS_SIZE[1] - 1, CANVAS_SIZE[0], CANVAS_SIZE[1]))
        bottom_row_pixels = sum(bottom_row.histogram()[1:])
        bounds = alpha.getbbox()
        local_edges = geometry["local"]["edges"]

        def point(value: Mapping[str, float]) -> dict[str, int | float]:
            return {
                "x": self._receipt_number(float(value["x"])),
                "y": self._receipt_number(float(value["y"])),
            }

        def face_bounds(face_id: str) -> dict[str, int | float]:
            return {
                key: self._receipt_number(float(value))
                for key, value in geometry[face_id]["bounds"].items()
            }

        return {
            "layoutId": str(layout.get("id", "")),
            "camera": {
                key: self._receipt_number(float(camera[key]))
                for key in ("x", "y", "zoom")
            },
            "frontEdges": {
                "xMax": {
                    "axis": "x",
                    "tileIndex": int(layout["columns"]) - 1,
                    "pointCount": len(local_edges["xMax"]),
                    "start": point(local_edges["xMax"][0]),
                    "end": point(local_edges["xMax"][-1]),
                },
                "yMax": {
                    "axis": "y",
                    "tileIndex": int(layout["rows"]) - 1,
                    "pointCount": len(local_edges["yMax"]),
                    "start": point(local_edges["yMax"][0]),
                    "end": point(local_edges["yMax"][-1]),
                },
                "frontCorner": point(local_edges["frontCorner"]),
            },
            "faceBounds": {
                "xMax": face_bounds("xMax"),
                "yMax": face_bounds("yMax"),
            },
            "coverage": {
                "facadePixels": facade_pixels,
                "canvasCoveragePercent": round(
                    facade_pixels / (CANVAS_SIZE[0] * CANVAS_SIZE[1]) * 100, 3
                ),
                "bottomRowCoveredPixels": bottom_row_pixels,
                "extendsToCanvasBottom": bool(
                    bottom_row_pixels
                    and float(geometry["xMax"]["bounds"]["bottom"])
                    >= CANVAS_SIZE[1]
                    and float(geometry["yMax"]["bounds"]["bottom"])
                    >= CANVAS_SIZE[1]
                ),
                "visibleBounds": (
                    {
                        "left": bounds[0],
                        "top": bounds[1],
                        "right": bounds[2],
                        "bottom": bounds[3],
                    }
                    if bounds is not None
                    else None
                ),
            },
        }

    def scene_shell_receipt(
        self,
        assets: Mapping[str, QaAsset],
        *,
        manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return deterministic shell geometry and canvas-coverage evidence."""

        source = self.pack_spec if manifest is None else manifest
        shell = scene_shell_from_manifest(source)
        if shell is None:
            return {"enabled": False}
        layouts = (
            self.layouts[OPENING_LAYOUT_ID],
            self.layouts[MID_LAYOUT_ID],
            self._occlusion_layout(),
        )
        return {
            "enabled": True,
            "version": int(shell["version"]),
            "type": str(shell["type"]),
            "facadeDepth": int(shell["facadeDepth"]),
            "slabDepth": int(shell["slabDepth"]),
            "ambientOcclusionDepth": min(2, int(shell["slabDepth"])),
            "windowBandPitch": int(shell["windowBandPitch"]),
            "drawOrder": ["background", "facade", "floor", "objects"],
            "facadeExcludedFromAutoFit": True,
            "canvas": {"width": CANVAS_SIZE[0], "height": CANVAS_SIZE[1]},
            "scenes": {
                str(layout["id"]): self._scene_shell_scene_metrics(
                    layout, assets, shell
                )
                for layout in layouts
            },
        }

    @staticmethod
    def _alpha_mask_at(image: Image.Image, left: int, top: int) -> Image.Image:
        """Place one sprite alpha channel on the logical QA canvas with clipping."""

        mask = Image.new("L", CANVAS_SIZE, 0)
        x0 = max(0, left)
        y0 = max(0, top)
        x1 = min(mask.width, left + image.width)
        y1 = min(mask.height, top + image.height)
        if x1 <= x0 or y1 <= y0:
            return mask
        source = image.getchannel("A").crop(
            (x0 - left, y0 - top, x1 - left, y1 - top)
        )
        mask.paste(source, (x0, y0))
        return mask

    def actor_visibility(
        self,
        layout: Mapping[str, Any],
        assets: Mapping[str, QaAsset],
    ) -> dict[str, dict[str, Any]]:
        """Measure body alpha that survives later-drawn scene placements.

        Labels and selection markers are deliberately excluded: a readable
        name must not disguise a character whose sprite is hidden by a desk.
        Partial-alpha assets attenuate contribution rather than acting as an
        all-or-nothing binary mask.
        """

        placements, seats = self._placements(layout, assets)
        actors = self._actors(layout, placements, seats)
        camera = self._camera(layout, assets, placements, actors)
        renderables = sort_renderables(
            [
                {**placement, "renderType": "placement"}
                for placement in placements
                if placement["kind"] != "backdrop"
            ]
            + [{**actor, "renderType": "actor"} for actor in actors]
        )
        character = assets["character.gus"]
        zoom = float(camera["zoom"])
        result: dict[str, dict[str, Any]] = {}
        for actor in actors:
            render_index = next(
                index
                for index, item in enumerate(renderables)
                if item["renderType"] == "actor" and item["id"] == actor["id"]
            )
            action = "work" if isinstance(actor.get("activity"), Mapping) else "idle"
            frame = self._scaled(
                self._character_frame(character, str(actor["facing"]), action),
                zoom,
            )
            local = self._project(float(actor["x"]), float(actor["y"]), layout["origin"])
            screen = self._screen_point(local, camera)
            anchor = character.metadata.get("anchor", {"x": 12, "y": 46})
            left = round(screen[0] - float(anchor.get("x", 12)) * zoom)
            top = round(
                screen[1]
                + 3 * zoom
                - float(anchor.get("y", 46)) * zoom
            )
            source_mask = self._alpha_mask_at(frame, left, top)
            visible_mask = source_mask.copy()
            source_alpha = float(ImageStat.Stat(source_mask).sum[0])
            occluders: list[dict[str, Any]] = []
            for item in renderables[render_index + 1 :]:
                if item["renderType"] != "placement":
                    continue
                asset = assets[item["assetId"]]
                image = self._scaled(asset.image(), zoom)
                point = self._screen_point(
                    self._project(
                        float(item["renderX"]),
                        float(item["renderY"]),
                        layout["origin"],
                    ),
                    camera,
                )
                item_anchor = asset.spec.get("anchor", {})
                offset = asset.spec.get("offset", {})
                item_left = round(
                    point[0]
                    + float(offset.get("x", 0)) * zoom
                    - float(item_anchor.get("x", 0)) * zoom
                )
                item_top = round(
                    point[1]
                    + float(offset.get("y", 0)) * zoom
                    - float(item_anchor.get("y", 0)) * zoom
                )
                occluder = self._alpha_mask_at(image, item_left, item_top)
                before = float(ImageStat.Stat(visible_mask).sum[0])
                visible_mask = ImageChops.multiply(
                    visible_mask, ImageChops.invert(occluder)
                )
                after = float(ImageStat.Stat(visible_mask).sum[0])
                loss_percent = (
                    (before - after) / source_alpha * 100 if source_alpha else 0
                )
                if loss_percent >= 0.5:
                    occluders.append(
                        {
                            "placementId": str(item["id"]),
                            "sourcePercentLost": round(loss_percent, 1),
                        }
                    )
            visible_alpha = float(ImageStat.Stat(visible_mask).sum[0])
            result[str(actor["id"])] = {
                "name": str(actor["name"]),
                "action": action,
                "visiblePercent": round(
                    visible_alpha / source_alpha * 100 if source_alpha else 0,
                    1,
                ),
                "occludedBy": occluders,
            }
        return result

    def _occlusion_layout(self) -> dict[str, Any]:
        placements = [
            {"id": "qa-cbd", "assetId": "backdrop.beijing-cbd", "x": 0, "y": 0},
            {"id": "qa-shared", "assetId": "furniture.desk-island", "x": 3, "y": 3},
            {"id": "qa-focus-nw", "assetId": "furniture.focus-desk-nw", "x": 11, "y": 2},
            {"id": "qa-focus-ne", "assetId": "furniture.focus-desk-ne", "x": 15, "y": 4},
        ]
        shared = {
            "seat-se": (4, 5),
            "seat-sw": (2, 4),
            "seat-nw": (4, 2),
            "seat-ne": (6, 3),
        }
        spawns = []
        activities = []
        for index, (seat_id, (x, y)) in enumerate(shared.items()):
            player_id = PLAYER_NAMES[index].lower()
            spawns.append({"playerId": player_id, "name": PLAYER_NAMES[index], "x": x, "y": y})
            activities.append(
                {"playerId": player_id, "type": "work", "placementId": "qa-shared", "seatId": seat_id}
            )
        for index, (placement_id, x, y) in enumerate(
            (("qa-focus-nw", 12, 4), ("qa-focus-ne", 14, 5)), start=4
        ):
            player_id = PLAYER_NAMES[index].lower()
            spawns.append({"playerId": player_id, "name": PLAYER_NAMES[index], "x": x, "y": y})
            activities.append(
                {"playerId": player_id, "type": "work", "placementId": placement_id, "seatId": "seat-work"}
            )
        return {
            "id": "qa.desk-work-occlusion",
            "label": "Shared and focus desk occlusion",
            "stage": "qa",
            "requiredPackId": PACK_ID,
            "columns": 18,
            "rows": 10,
            "origin": {"x": 278, "y": 102},
            "camera": {"x": 0, "y": 0, "zoom": 1},
            "spawnPoints": spawns,
            "initialActivities": activities,
            "floor": {
                "defaultAssetId": "floor.raw-concrete",
                "regions": [],
                "border": {
                    "assetId": "floor.utility-border",
                    "edges": ["north", "east", "south", "west"],
                },
            },
            "placements": placements,
        }

    def generate_from_assets(self, assets: Mapping[str, QaAsset]) -> dict[str, Any]:
        missing = [slot for slot in self.logical_slots if slot not in assets]
        if missing:
            raise AssetQaError("cannot render incomplete core-v2 assets: " + ", ".join(missing))
        images = {
            CONTACT_NAME: self.render_contact_sheet(assets),
            OPENING_NAME: self.render_scene(self.layouts[OPENING_LAYOUT_ID], assets),
            MID_NAME: self.render_scene(self.layouts[MID_LAYOUT_ID], assets),
            OCCLUSION_NAME: self.render_scene(self._occlusion_layout(), assets),
        }
        output_entries = []
        for name, image in images.items():
            data = _png_bytes(image)
            path = self.output_dir / name
            _write_if_changed(path, data)
            output_entries.append(
                {
                    "name": name,
                    "sha256": sha256_bytes(data),
                    "width": image.width,
                    "height": image.height,
                }
            )
        receipt = {
            "schemaVersion": 1,
            "packId": PACK_ID,
            "geometryVersion": int(self.pack_spec.get("geometryVersion", 0)),
            "inputs": [
                {
                    "slot": slot,
                    "sha256": assets[slot].sha256,
                    "provenance": assets[slot].provenance,
                }
                for slot in self.logical_slots
            ],
            "outputs": output_entries,
            "actorVisibility": {
                MID_LAYOUT_ID: self.actor_visibility(self.layouts[MID_LAYOUT_ID], assets),
                "qa.desk-work-occlusion": self.actor_visibility(
                    self._occlusion_layout(), assets
                ),
            },
            "sceneShell": self.scene_shell_receipt(assets),
        }
        receipt_bytes = (canonical_json(receipt) + "\n").encode("utf-8")
        _write_if_changed(self.output_dir / RECEIPT_NAME, receipt_bytes)
        return {**receipt, "outputDir": str(self.output_dir), "receipt": RECEIPT_NAME}

    def generate(self) -> dict[str, Any]:
        return self.generate_from_assets(self.resolve_assets())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render read-only core-v2 candidate contact and scene QA images."
    )
    parser.add_argument("command", choices=("render",))
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = CoreV2AssetQa(
            project_dir=args.project_dir,
            data_dir=args.data_dir,
            output_dir=args.output_dir,
        ).generate()
    except AssetQaError as exc:
        print(f"asset QA failed: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AssetQaError",
    "CoreV2AssetQa",
    "QaAsset",
    "actor_depth",
    "floor_front_edges",
    "footprint_ground",
    "placement_depth",
    "scene_shell_from_manifest",
    "sort_renderables",
    "tower_shell_geometry",
]
