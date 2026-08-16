"""Local, framework-neutral asset catalog for the Codex v0 art workflow.

The lab intentionally owns its SQLite database and content-addressed files.  It
does not import the game service, expose HTTP routes, or write anything outside
``data_dir``.  A future FastAPI adapter can therefore wrap this class without
coupling the asset review workflow to player identity or game-state tables.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import uuid
import warnings
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from PIL import Image, ImageOps, UnidentifiedImageError

from .asset_geometry import (
    AssetGeometryError,
    wall_face_geometry_pixels,
    wall_screen_slope,
)
from .character_consistency import consistency_warning, inspect_character_consistency
from .character_motion import ACTION_ORDER as CHARACTER_ACTIONS
from .isometric import DIRECTIONS as CHARACTER_DIRECTIONS
from .character_motion import FRAME_COUNT as CHARACTER_FRAME_COUNT
from .character_motion import POLICY_ID as CHARACTER_MOTION_BUILD_POLICY
from .character_motion import verify_character_motion


SCHEMA_VERSION = 1
STYLE_PROFILE_ID = "beijing-modern-isometric-v1"
CORE_V2_STYLE_PROFILE_ID = "beijing-modern-isometric-v2"
PACK_ID = "core-v0"
CORE_V1_PACK_ID = "core-v1"
CORE_V2_PACK_ID = "core-v2"
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096
MAX_METADATA_BYTES = 256 * 1024
MAX_REVIEW_BATCH_ITEMS = 200
ATLAS_INITIAL_SIZE = 512
ATLAS_MAX_SIZE = 1024
ATLAS_PADDING = 2
CORE_V2_ALPHA_LEVELS = frozenset({0, 96, 128, 160, 192, 255})


def _alpha_component_stats(
    image: Image.Image,
    target_alpha: int,
) -> list[dict[str, Any]]:
    """Return deterministic 4-neighbour components for one exact alpha."""

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    remaining = {
        (x, y)
        for y in range(rgba.height)
        for x in range(rgba.width)
        if alpha.getpixel((x, y)) == target_alpha
    }
    components: list[dict[str, Any]] = []
    while remaining:
        seed = min(remaining, key=lambda point: (point[1], point[0]))
        remaining.remove(seed)
        queue: deque[tuple[int, int]] = deque([seed])
        points: list[tuple[int, int]] = []
        touches_transparency = False
        while queue:
            x, y = queue.popleft()
            points.append((x, y))
            for neighbour in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                nx, ny = neighbour
                if not 0 <= nx < rgba.width or not 0 <= ny < rgba.height:
                    touches_transparency = True
                    continue
                if alpha.getpixel(neighbour) == 0:
                    touches_transparency = True
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    queue.append(neighbour)
        left = min(point[0] for point in points)
        top = min(point[1] for point in points)
        right = max(point[0] for point in points) + 1
        bottom = max(point[1] for point in points) + 1
        components.append(
            {
                "bounds": [left, top, right, bottom],
                "area": len(points),
                "touchesTransparency": touches_transparency,
            }
        )
    return components
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CORE_PACK_SPEC_PATH = Path(__file__).resolve().parent.parent / "assets" / "core-pack.spec.json"
CORE_V1_PACK_SPEC_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "core-v1-pack.spec.json"
)
CORE_V2_PACK_SPEC_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "core-v2-pack.spec.json"
)


def _load_runtime_palettes() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Use the game runtime specification as the single palette authority."""

    try:
        spec = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
        world = tuple(spec["palette"]["world"])
        players = tuple(spec["palette"]["players"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"invalid runtime palette specification: {CORE_PACK_SPEC_PATH}") from exc
    color_pattern = re.compile(r"^#[0-9A-F]{6}$")
    if len(world) != 32 or len(players) != 8:
        raise RuntimeError("runtime palette must contain 32 world colors and 8 player accents")
    if any(not isinstance(color, str) or color_pattern.fullmatch(color) is None for color in world + players):
        raise RuntimeError("runtime palette colors must use uppercase #RRGGBB")
    return world, players


WORLD_PALETTE, PLAYER_ACCENTS = _load_runtime_palettes()


def _load_pack_spec_file(path: Path, pack_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid {pack_id} specification: {path}") from exc
    if not isinstance(payload, dict) or payload.get("id") != pack_id:
        raise RuntimeError(f"{pack_id} specification root must identify its pack")
    return payload


_CORE_V1_SPEC = _load_pack_spec_file(CORE_V1_PACK_SPEC_PATH, CORE_V1_PACK_ID)
_CORE_V2_SPEC = _load_pack_spec_file(CORE_V2_PACK_SPEC_PATH, CORE_V2_PACK_ID)
CORE_V2_WORLD_PALETTE = tuple(_CORE_V2_SPEC.get("palette", {}).get("world", ()))
CORE_V2_PLAYER_ACCENTS = tuple(_CORE_V2_SPEC.get("palette", {}).get("players", ()))
if (
    len(CORE_V2_WORLD_PALETTE) != 48
    or CORE_V2_WORLD_PALETTE[: len(WORLD_PALETTE)] != WORLD_PALETTE
    or CORE_V2_PLAYER_ACCENTS != PLAYER_ACCENTS
):
    raise RuntimeError("core-v2 palette must be a 48-color compatible superset")

CORE_SLOTS: tuple[dict[str, Any], ...] = (
    {"slot": "floor.raw-concrete", "kind": "floor", "displayName": "Raw Concrete"},
    {
        "slot": "floor.patched-concrete",
        "kind": "floor",
        "displayName": "Patched Concrete",
    },
    {"slot": "floor.light-wood", "kind": "floor", "displayName": "Light Wood"},
    {
        "slot": "floor.utility-border",
        "kind": "floor",
        "displayName": "Utility Border",
    },
    {
        "slot": "furniture.moving-box",
        "kind": "furniture",
        "displayName": "Moving Box",
    },
    {
        "slot": "furniture.desk-island",
        "kind": "furniture",
        "displayName": "Desk Island",
    },
    {
        "slot": "furniture.storage-cabinet",
        "kind": "furniture",
        "displayName": "Storage Cabinet",
    },
    {
        "slot": "furniture.tea-coffee-bar",
        "kind": "furniture",
        "displayName": "Tea and Coffee Bar",
    },
    {
        "slot": "furniture.meeting-table",
        "kind": "furniture",
        "displayName": "Meeting Table",
    },
    {"slot": "character.gus", "kind": "character", "displayName": "Gus"},
    {
        "slot": "effect.good-card-heart",
        "kind": "effect",
        "displayName": "Good Card Heart",
    },
)

SLOT_BY_NAME = {entry["slot"]: entry for entry in CORE_SLOTS}

def _display_name_for_slot(slot: str) -> str:
    return slot.split(".", 1)[-1].replace("-", " ").title()


def _slot_definitions(
    spec: Mapping[str, Any], slot_names: Sequence[str]
) -> tuple[dict[str, Any], ...]:
    templates = {
        str(entry.get("slot")): entry
        for entry in spec.get("assets", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("slot"), str)
    }
    definitions: list[dict[str, Any]] = []
    for slot in slot_names:
        template = templates.get(slot)
        if template is None or not isinstance(template.get("kind"), str):
            raise RuntimeError(f"core-v2 slot has no runtime template: {slot}")
        definitions.append(
            {
                "slot": slot,
                "kind": str(template["kind"]),
                "displayName": str(
                    template.get("displayName", _display_name_for_slot(slot))
                ),
            }
        )
    return tuple(definitions)


CORE_V1_NEW_SLOT_NAMES = tuple(_CORE_V1_SPEC.get("requiredNewSlots", ()))
CORE_V1_OVERRIDE_SLOT_NAMES = tuple(_CORE_V1_SPEC.get("overrideSlots", ()))
CORE_V1_NEW_SLOTS = _slot_definitions(_CORE_V1_SPEC, CORE_V1_NEW_SLOT_NAMES)

CORE_V2_OVERRIDE_SLOT_NAMES = tuple(_CORE_V2_SPEC.get("overrideSlots", ()))
CORE_V2_NEW_SLOT_NAMES = tuple(_CORE_V2_SPEC.get("requiredNewSlots", ()))
CORE_V2_EDITABLE_SLOT_NAMES = tuple(_CORE_V2_SPEC.get("requiredEditableSlots", ()))
if CORE_V2_EDITABLE_SLOT_NAMES != CORE_V2_OVERRIDE_SLOT_NAMES + CORE_V2_NEW_SLOT_NAMES:
    raise RuntimeError("core-v2 editable slots must declare overrides followed by new slots")
CORE_V2_OVERRIDE_SLOTS = _slot_definitions(_CORE_V2_SPEC, CORE_V2_OVERRIDE_SLOT_NAMES)
CORE_V2_NEW_SLOTS = _slot_definitions(_CORE_V2_SPEC, CORE_V2_NEW_SLOT_NAMES)
CORE_V2_EDITABLE_SLOTS = CORE_V2_OVERRIDE_SLOTS + CORE_V2_NEW_SLOTS

_CORE_V1_LOGICAL_SLOTS = CORE_SLOTS + CORE_V1_NEW_SLOTS
CORE_V2_INHERITED_SLOT_NAMES = tuple(
    entry["slot"]
    for entry in _CORE_V1_LOGICAL_SLOTS
    if entry["slot"] not in set(CORE_V2_OVERRIDE_SLOT_NAMES)
)
CORE_V2_REQUIRED_SLOT_NAMES = tuple(
    entry["slot"] for entry in _CORE_V1_LOGICAL_SLOTS
) + CORE_V2_NEW_SLOT_NAMES
if len(set(CORE_V2_REQUIRED_SLOT_NAMES)) != len(CORE_V2_REQUIRED_SLOT_NAMES):
    raise RuntimeError("core-v2 logical slots must be unique")

SLOTS_BY_PACK = {
    PACK_ID: CORE_SLOTS,
    CORE_V1_PACK_ID: CORE_V1_NEW_SLOTS,
    CORE_V2_PACK_ID: CORE_V2_EDITABLE_SLOTS,
}
REQUIRED_EDITABLE_SLOT_NAMES_BY_PACK = {
    pack_id: frozenset(entry["slot"] for entry in entries)
    for pack_id, entries in SLOTS_BY_PACK.items()
}
SLOT_LOOKUP_BY_PACK = {
    pack_id: {entry["slot"]: entry for entry in slots}
    for pack_id, slots in SLOTS_BY_PACK.items()
}
OVERRIDABLE_INHERITED_SLOTS_BY_PACK = {
    CORE_V1_PACK_ID: frozenset(CORE_V1_OVERRIDE_SLOT_NAMES),
    CORE_V2_PACK_ID: frozenset(CORE_V2_OVERRIDE_SLOT_NAMES),
}
EDITABLE_SLOT_LOOKUP_BY_PACK = {
    pack_id: {
        **SLOT_LOOKUP_BY_PACK[pack_id],
        **{
            slot: {
                **(
                    SLOT_BY_NAME.get(slot)
                    or SLOT_LOOKUP_BY_PACK[CORE_V1_PACK_ID].get(slot)
                    or SLOT_LOOKUP_BY_PACK[CORE_V2_PACK_ID].get(slot)
                    or {}
                )
            }
            for slot in OVERRIDABLE_INHERITED_SLOTS_BY_PACK.get(pack_id, ())
        },
    }
    for pack_id in SLOTS_BY_PACK
}
ALL_SLOT_BY_NAME = {
    entry["slot"]: entry
    for entry in CORE_SLOTS + CORE_V1_NEW_SLOTS + CORE_V2_NEW_SLOTS
}
LOGICAL_SLOTS_BY_PACK = {
    PACK_ID: CORE_SLOTS,
    CORE_V1_PACK_ID: _CORE_V1_LOGICAL_SLOTS,
    CORE_V2_PACK_ID: tuple(
        (
            SLOT_LOOKUP_BY_PACK[CORE_V2_PACK_ID].get(entry["slot"])
            or entry
        )
        for entry in _CORE_V1_LOGICAL_SLOTS
    )
    + CORE_V2_NEW_SLOTS,
}
PACK_SPEC_PATHS = {
    PACK_ID: CORE_PACK_SPEC_PATH,
    CORE_V1_PACK_ID: CORE_V1_PACK_SPEC_PATH,
    CORE_V2_PACK_ID: CORE_V2_PACK_SPEC_PATH,
}
PACK_SPECS = {
    PACK_ID: _load_pack_spec_file(CORE_PACK_SPEC_PATH, PACK_ID),
    CORE_V1_PACK_ID: _CORE_V1_SPEC,
    CORE_V2_PACK_ID: _CORE_V2_SPEC,
}
PACK_STYLE_PROFILE_IDS = {
    PACK_ID: STYLE_PROFILE_ID,
    CORE_V1_PACK_ID: STYLE_PROFILE_ID,
    CORE_V2_PACK_ID: CORE_V2_STYLE_PROFILE_ID,
}
PACK_OVERRIDE_SLOT_NAMES = {
    pack_id: tuple(spec.get("overrideSlots", ()))
    for pack_id, spec in PACK_SPECS.items()
}
PACK_NEW_SLOT_NAMES = {
    PACK_ID: tuple(entry["slot"] for entry in CORE_SLOTS),
    CORE_V1_PACK_ID: CORE_V1_NEW_SLOT_NAMES,
    CORE_V2_PACK_ID: CORE_V2_NEW_SLOT_NAMES,
}
PACK_WORLD_PALETTES = {
    PACK_ID: WORLD_PALETTE,
    CORE_V1_PACK_ID: WORLD_PALETTE,
    CORE_V2_PACK_ID: CORE_V2_WORLD_PALETTE,
}
PACK_PLAYER_ACCENTS = {
    PACK_ID: PLAYER_ACCENTS,
    CORE_V1_PACK_ID: PLAYER_ACCENTS,
    CORE_V2_PACK_ID: CORE_V2_PLAYER_ACCENTS,
}


class AssetLabError(Exception):
    """A stable domain error suitable for translation by an HTTP adapter."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "asset_lab_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AssetLabError(
            "metadata must be finite JSON data",
            code="metadata.invalid_json",
        ) from exc


def _backdrop_preparation_is_valid(
    preparation: object,
    *,
    pack_id: str,
    slot: str,
    expected_size: tuple[int, int],
) -> bool:
    """Validate the exact deterministic full-canvas transform provenance."""

    if not isinstance(preparation, Mapping):
        return False
    source_size = preparation.get("sourceSize")
    output_size = preparation.get("outputSize")
    transform = preparation.get("transform")
    if (
        preparation.get("schemaVersion") != 1
        or preparation.get("packId") != pack_id
        or preparation.get("slot") != slot
        or not isinstance(preparation.get("sourceSha256"), str)
        or SHA256_RE.fullmatch(str(preparation["sourceSha256"])) is None
        or not isinstance(preparation.get("outputSha256"), str)
        or SHA256_RE.fullmatch(str(preparation["outputSha256"])) is None
        or not isinstance(source_size, list)
        or len(source_size) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in source_size)
        or output_size != [expected_size[0], expected_size[1]]
        or not isinstance(transform, Mapping)
        or transform.get("resampling") != "nearest"
        or transform.get("alphaLevels") != [0, 255]
    ):
        return False

    source_width, source_height = source_size
    width, height = expected_size
    native = (source_width, source_height) == expected_size
    requested_scale = 1.0 if native else max(width / source_width, height / source_height)
    resized_size = (
        [width, height]
        if native
        else [
            max(width, round(source_width * requested_scale)),
            max(height, round(source_height * requested_scale)),
        ]
    )
    crop_left = (resized_size[0] - width) // 2
    crop_top = (resized_size[1] - height) // 2
    expected_crop = [crop_left, crop_top, crop_left + width, crop_top + height]
    scale = transform.get("scale")
    if not isinstance(scale, Mapping):
        return False
    expected_scale = {
        "x": resized_size[0] / source_width,
        "y": resized_size[1] / source_height,
        "uniformRequested": requested_scale,
    }
    return bool(
        transform.get("mode")
        == ("full-canvas-native" if native else "full-canvas-cover")
        and transform.get("resizedSize") == resized_size
        and transform.get("crop") == expected_crop
        and all(
            not isinstance(scale.get(key), bool)
            and isinstance(scale.get(key), (int, float))
            and math.isfinite(float(scale[key]))
            and math.isclose(float(scale[key]), expected, rel_tol=0, abs_tol=1e-12)
            for key, expected in expected_scale.items()
        )
    )


def _asset_id(slot: str, pack_id: str = PACK_ID) -> str:
    return f"asset-{pack_id}-{slot.replace('.', '-')}"


class AssetLab:
    """Persistent local asset workflow with optimistic revision checks."""

    def __init__(self, data_dir: str | os.PathLike[str] | None = None) -> None:
        project_dir = Path(__file__).resolve().parent.parent
        self.data_dir = Path(data_dir) if data_dir is not None else project_dir / "data"
        self.db_path = self.data_dir / "asset-lab.sqlite3"
        self.assets_dir = self.data_dir / "assets"
        self.inbox_dir = self.assets_dir / "inbox"
        self.blobs_dir = self.assets_dir / "blobs"
        self.derived_dir = self.assets_dir / "derived"
        self._bootstrap_lock = threading.RLock()
        self._initialized = False

    # ------------------------------------------------------------------
    # Public API

    def bootstrap(self) -> dict[str, Any]:
        """Create local storage/schema/seeds and return the review UI bootstrap."""

        self._ensure_bootstrapped()
        with self._connect() as conn:
            revision = self._catalog_revision(conn)
            packs = [
                self._pack_payload(conn, str(row["id"]))
                for row in conn.execute(
                    "SELECT id FROM packs ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at, id"
                ).fetchall()
            ]
            return {
                "schemaVersion": SCHEMA_VERSION,
                "revision": revision,
                "styleProfile": self._style_payload(conn),
                "styleProfiles": [
                    json.loads(row["spec_json"])
                    for row in conn.execute(
                        "SELECT spec_json FROM style_profiles ORDER BY created_at, id"
                    ).fetchall()
                ],
                "pack": packs[0],
                "packs": packs,
                "filters": self._filter_payload(conn),
                "limits": {
                    "maxInputBytes": MAX_INPUT_BYTES,
                    "maxImageDimension": MAX_IMAGE_DIMENSION,
                    "acceptedMimeTypes": ["image/png"],
                    "atlasInitialSize": ATLAS_INITIAL_SIZE,
                    "atlasMaxSize": ATLAS_MAX_SIZE,
                    "atlasPadding": ATLAS_PADDING,
                },
            }

    def catalog(self, filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return every asset and version in a pack.

        Narrowing is the review screen's job: it needs the whole pack for activation
        coverage and the whole version list for a draft's accepted baseline, so filtering
        here only ever forced it to ask twice.
        """

        self._ensure_bootstrapped()
        pack_id = self._resolve_pack_id(filters)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*,
                       pm.version_id AS selected_version_id
                  FROM assets a
                  JOIN pack_members pm ON pm.asset_id = a.id
                 WHERE pm.pack_id = ?
                 ORDER BY pm.ordinal, a.slot
                """,
                (pack_id,),
            ).fetchall()
            return {
                "revision": self._catalog_revision(conn),
                "assets": [self._asset_payload(conn, row["id"], pack_id) for row in rows],
            }

    def import_png(self, data: bytes, metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Decode, normalize, deduplicate, and register one PNG as a draft."""

        self._ensure_bootstrapped()
        job_id = self._create_job("import", self._safe_input_name(metadata))
        try:
            normalized_input = self._normalize_import_metadata(metadata)
            image, canonical_png = self._decode_png(
                data,
                allow_opaque=normalized_input["slot"].startswith("backdrop."),
            )
            normalized_metadata = self._normalize_asset_metadata(
                normalized_input["packId"],
                normalized_input["slot"],
                normalized_input["assetMetadata"],
                image.size,
            )
            source_metadata_json = self._source_metadata_json(normalized_metadata)
            palette_warnings = self._palette_warnings(
                image,
                normalized_input["slot"],
                normalized_input["packId"],
            )
            if normalized_input["slot"] == "character.gus":
                motion_build = verify_character_motion(image, normalized_metadata)
                normalized_metadata["motionBuild"] = motion_build
                if motion_build.get("verified") is not True:
                    palette_warnings.append(
                        {
                            "code": "character.motion_build_unverified",
                            "message": (
                                "人物动作表不是 canonical Gus 像素 Rig 的确定性编译结果；"
                                "草稿可比较，但不能被接受。"
                            ),
                            "policy": CHARACTER_MOTION_BUILD_POLICY,
                            "errors": motion_build.get("errors", []),
                        }
                    )
                consistency = inspect_character_consistency(image, normalized_metadata)
                normalized_metadata["characterConsistency"] = consistency
                warning = consistency_warning(consistency)
                if warning is not None:
                    palette_warnings.append(warning)
            blob_sha = hashlib.sha256(canonical_png).hexdigest()
            self._write_content_addressed(self.blobs_dir, blob_sha, ".png", canonical_png)
            metadata_json = _canonical_json(normalized_metadata)
            metadata_fingerprint = hashlib.sha256(
                source_metadata_json.encode("utf-8")
            ).hexdigest()
            now = _utc_now()

            with self._transaction() as conn:
                asset = self._asset_for_import(
                    conn,
                    normalized_input["packId"],
                    normalized_input["slot"],
                    now,
                )
                duplicate = conn.execute(
                    """
                    SELECT * FROM versions
                     WHERE asset_id = ? AND sha256 = ? AND metadata_fingerprint = ?
                     ORDER BY version_number DESC LIMIT 1
                    """,
                    (asset["id"], blob_sha, metadata_fingerprint),
                ).fetchone()
                if duplicate is None:
                    candidates = conn.execute(
                        """
                        SELECT * FROM versions
                         WHERE asset_id = ? AND sha256 = ?
                         ORDER BY CASE status WHEN 'accepted' THEN 0 ELSE 1 END,
                                  version_number DESC
                        """,
                        (asset["id"], blob_sha),
                    ).fetchall()
                    duplicate = next(
                        (
                            candidate
                            for candidate in candidates
                            if self._source_metadata_json(
                                json.loads(candidate["metadata_json"])
                            )
                            == source_metadata_json
                        ),
                        None,
                    )
                if duplicate is not None:
                    revision = self._catalog_revision(conn)
                    result = {
                        "jobId": job_id,
                        "deduplicated": True,
                        "revision": revision,
                        "assetId": asset["id"],
                        "versionId": duplicate["id"],
                    }
                else:
                    version_number = conn.execute(
                        "SELECT COALESCE(MAX(version_number), 0) + 1 FROM versions WHERE asset_id = ?",
                        (asset["id"],),
                    ).fetchone()[0]
                    version_id = f"version-{uuid.uuid4().hex}"
                    conn.execute(
                        """
                        INSERT INTO versions(
                            id, asset_id, version_number, sha256, width, height,
                            size_bytes, status, metadata_json, metadata_fingerprint,
                            warnings_json, created_at, reviewed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, NULL)
                        """,
                        (
                            version_id,
                            asset["id"],
                            version_number,
                            blob_sha,
                            image.width,
                            image.height,
                            len(canonical_png),
                            metadata_json,
                            metadata_fingerprint,
                            _canonical_json(palette_warnings),
                            now,
                        ),
                    )
                    conn.execute(
                        "UPDATE assets SET revision = revision + 1, updated_at = ? WHERE id = ?",
                        (now, asset["id"]),
                    )
                    conn.execute(
                        "UPDATE packs SET revision = revision + 1, updated_at = ? WHERE id = ?",
                        (now, normalized_input["packId"]),
                    )
                    revision = self._bump_catalog_revision(conn)
                    result = {
                        "jobId": job_id,
                        "deduplicated": False,
                        "revision": revision,
                        "assetId": asset["id"],
                        "versionId": version_id,
                    }

            with self._connect() as conn:
                response = {
                    "jobId": job_id,
                    "deduplicated": result["deduplicated"],
                    "revision": result["revision"],
                    "asset": self._asset_payload(
                        conn, result["assetId"], normalized_input["packId"]
                    ),
                    "version": self._version_payload_by_id(conn, result["versionId"]),
                }
            self._finish_job(job_id, "completed", response, None)
            return response
        except AssetLabError as exc:
            self._finish_job(job_id, "failed", None, exc.as_dict())
            raise
        except Exception as exc:
            error = AssetLabError("asset import failed", code="import.failed")
            self._finish_job(
                job_id,
                "failed",
                None,
                {"code": error.code, "message": str(exc)},
            )
            raise error from exc

    def scan_inbox(self, pack_id: str | None = None) -> dict[str, Any]:
        """Import sorted PNG/sidecar pairs, optionally limited by sidecar pack ID."""

        self._ensure_bootstrapped()
        if pack_id is not None and pack_id not in SLOTS_BY_PACK:
            raise AssetLabError("pack not found", code="pack.not_found")
        if pack_id is not None:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM packs WHERE id = ?", (pack_id,)
                ).fetchone()
            if exists is None:
                raise AssetLabError("pack not found", code="pack.not_found")
        job_id = self._create_job("scan", None)
        imported: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for png_path in sorted(
            (path for path in self.inbox_dir.iterdir() if path.suffix.lower() == ".png"),
            key=lambda path: path.name.casefold(),
        ):
            sidecar_path = png_path.with_suffix(".json")
            try:
                if png_path.is_symlink() or sidecar_path.is_symlink():
                    raise AssetLabError(
                        "inbox symlinks are not allowed",
                        code="inbox.symlink_rejected",
                    )
                if not sidecar_path.is_file():
                    raise AssetLabError(
                        "matching JSON sidecar is required",
                        code="inbox.sidecar_missing",
                    )
                if png_path.stat().st_size > MAX_INPUT_BYTES:
                    raise AssetLabError(
                        "PNG exceeds the 16 MiB input limit",
                        code="image.too_large",
                        details={"maxInputBytes": MAX_INPUT_BYTES},
                    )
                if sidecar_path.stat().st_size > MAX_METADATA_BYTES:
                    raise AssetLabError(
                        "sidecar metadata is too large",
                        code="metadata.too_large",
                    )
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise AssetLabError(
                        "sidecar is not valid UTF-8 JSON",
                        code="inbox.sidecar_invalid",
                    ) from exc
                if not isinstance(sidecar, Mapping):
                    raise AssetLabError(
                        "sidecar root must be a JSON object",
                        code="metadata.invalid",
                    )
                sidecar = dict(sidecar)
                sidecar_pack_id = sidecar.get("packId", PACK_ID)
                if pack_id is not None and sidecar_pack_id != pack_id:
                    continue
                sidecar.setdefault("sourceName", png_path.name)
                result = self.import_png(png_path.read_bytes(), sidecar)
                imported.append({"sourceName": png_path.name, **result})
            except AssetLabError as exc:
                errors.append({"sourceName": png_path.name, **exc.as_dict()})
            except OSError as exc:
                errors.append(
                    {
                        "sourceName": png_path.name,
                        "code": "inbox.read_failed",
                        "message": str(exc),
                        "details": {},
                    }
                )
        with self._connect() as conn:
            revision = self._catalog_revision(conn)
        response = {
            "jobId": job_id,
            "revision": revision,
            "imported": imported,
            "errors": errors,
        }
        self._finish_job(job_id, "completed", response, None)
        return response

    def _load_reviewable_version(
        self,
        conn: sqlite3.Connection,
        asset_id: str,
        version_id: str,
        decision: str,
    ) -> sqlite3.Row:
        """Resolve a draft version and enforce every acceptance gate without writing.

        Frozen inherited members need no guard here: they are ``accepted`` in their owner
        pack, so the draft check below already rejects them.
        """

        row = conn.execute(
            """
            SELECT v.*, a.pack_id, a.id AS joined_asset_id, a.slot AS joined_slot
              FROM versions v
              JOIN assets a ON a.id = v.asset_id
             WHERE v.id = ? AND a.id = ?
            """,
            (version_id, asset_id),
        ).fetchone()
        if row is None:
            raise AssetLabError("asset version not found", code="version.not_found")
        if row["status"] != "draft":
            raise AssetLabError(
                "only draft versions can be reviewed",
                code="version.not_draft",
                details={"status": row["status"]},
            )
        pack_spec = PACK_SPECS.get(str(row["pack_id"]), {})
        if decision == "accepted" and pack_spec.get("nativeFrameRequired") is True:
            failures = self._native_frame_acceptance_failures(
                row, str(row["pack_id"]), pack_spec
            )
            if failures:
                raise AssetLabError(
                    f"{row['pack_id']} 候选未通过原生帧与资产规格门禁",
                    code="review.asset_spec_failed",
                    details={"slot": row["joined_slot"], "failures": failures},
                )
        if decision == "accepted" and row["joined_slot"] == "character.gus":
            metadata = json.loads(row["metadata_json"])
            motion_build = metadata.get("motionBuild")
            if not isinstance(motion_build, Mapping) or motion_build.get("verified") is not True:
                raise AssetLabError(
                    "角色动作表不是已验证的确定性像素 Rig 编译结果",
                    code="review.character_motion_unverified",
                    details={
                        "policy": (
                            motion_build.get("policy")
                            if isinstance(motion_build, Mapping)
                            else CHARACTER_MOTION_BUILD_POLICY
                        ),
                        "errors": (
                            motion_build.get("errors", [])
                            if isinstance(motion_build, Mapping)
                            else []
                        ),
                    },
                )
            consistency = metadata.get("characterConsistency")
            if not isinstance(consistency, Mapping) or consistency.get("ok") is not True:
                summary = (
                    consistency.get("summary", {})
                    if isinstance(consistency, Mapping)
                    else {}
                )
                raise AssetLabError(
                    "角色动作表未通过身份一致性门禁；请整行修复后重新导入",
                    code="review.character_consistency_failed",
                    details={
                        "policy": (
                            consistency.get("policy")
                            if isinstance(consistency, Mapping)
                            else None
                        ),
                        "failedFrames": int(summary.get("failedFrames", 0)),
                        "failureCount": int(summary.get("failureCount", 0)),
                        "fallbackMode": "canonical-idle-bob",
                    },
                )
        return row

    def _native_frame_acceptance_failures(
        self,
        row: sqlite3.Row,
        pack_id: str,
        spec: Mapping[str, Any],
    ) -> list[str]:
        """Return deterministic spec-driven native-frame gate failures."""
        slot = str(row["joined_slot"])
        template = next(
            (
                entry
                for entry in spec.get("assets", [])
                if isinstance(entry, Mapping) and entry.get("slot") == slot
            ),
            None,
        )
        if not isinstance(template, Mapping):
            return ["runtime-template-missing"]
        try:
            metadata = json.loads(row["metadata_json"])
            warnings_payload = json.loads(row["warnings_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise AssetLabError("stored metadata is invalid", code="storage.corrupt") from exc
        expected_frame = template.get("frame", {})
        expected_size = (
            int(expected_frame.get("width", 0)),
            int(expected_frame.get("height", 0)),
        )
        failures: list[str] = []
        if (int(row["width"]), int(row["height"])) != expected_size:
            failures.append("native-frame-size")
        if metadata.get("frames") != [
            {"x": 0, "y": 0, "width": expected_size[0], "height": expected_size[1]}
        ]:
            failures.append("native-frame-grid")
        for field in ("anchor", "footprint"):
            if metadata.get(field) != template.get(field):
                failures.append(field)
        if "groundAxis" in template and metadata.get("groundAxis") != template.get(
            "groundAxis"
        ):
            failures.append("ground-axis")
        if "wallFaceHeight" in template and metadata.get(
            "wallFaceHeight"
        ) != template.get("wallFaceHeight"):
            failures.append("wall-face-height")
        if "paneAlpha" in template and metadata.get("paneAlpha") != template.get(
            "paneAlpha"
        ):
            failures.append("pane-alpha")
        if "paneCount" in template and metadata.get("paneCount") != template.get(
            "paneCount"
        ):
            failures.append("pane-count")
        anchor = metadata.get("anchor")
        if not isinstance(anchor, Mapping) or any(
            isinstance(anchor.get(axis), bool) or not isinstance(anchor.get(axis), int)
            for axis in ("x", "y")
        ):
            failures.append("integer-anchor")
        footprint = metadata.get("footprint")
        if not isinstance(footprint, list) or any(
            not isinstance(cell, Mapping)
            or isinstance(cell.get("x"), bool)
            or not isinstance(cell.get("x"), int)
            or isinstance(cell.get("y"), bool)
            or not isinstance(cell.get("y"), int)
            or not isinstance(cell.get("blocked"), bool)
            for cell in (footprint if isinstance(footprint, list) else [])
        ):
            failures.append("integer-footprint")
        if metadata.get("kind") != template.get("kind"):
            failures.append("kind")
        if metadata.get("styleProfileId") != PACK_STYLE_PROFILE_IDS[pack_id]:
            failures.append("style-profile")
        if "orientation" in template and metadata.get("orientation") != template.get("orientation"):
            failures.append("orientation")
        if "interactionPoints" in template and metadata.get("interactionPoints") != template.get(
            "interactionPoints"
        ):
            failures.append("interaction-points")
        interaction_points = metadata.get("interactionPoints")
        if interaction_points is not None and (
            not isinstance(interaction_points, list)
            or any(
                not isinstance(point, Mapping)
                or isinstance(point.get("x"), bool)
                or not isinstance(point.get("x"), int)
                or isinstance(point.get("y"), bool)
                or not isinstance(point.get("y"), int)
                for point in interaction_points
            )
        ):
            failures.append("integer-interaction-points")
        if any(
            isinstance(warning, Mapping)
            and warning.get("code") == "palette.outside_world_palette"
            for warning in warnings_payload
        ):
            failures.append("palette")
        blob = self.blob_path(str(row["sha256"]))
        try:
            with Image.open(blob) as opened:
                image = opened.convert("RGBA")
                alpha_values = set(image.getchannel("A").getdata())
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetLabError("selected blob cannot be decoded", code="storage.corrupt") from exc
        allowed_alpha_levels = frozenset(spec.get("alphaLevels", CORE_V2_ALPHA_LEVELS))
        if not alpha_values.issubset(allowed_alpha_levels):
            failures.append("alpha-levels")
        if slot == "backdrop.beijing-cbd":
            if alpha_values != {255}:
                failures.append("backdrop-opaque")
            palette_rgb = {
                tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
                for color in spec.get("palette", {}).get("world", [])
                if isinstance(color, str) and re.fullmatch(r"#[0-9A-F]{6}", color)
            }
            visible_rgb = {
                (red, green, blue)
                for red, green, blue, alpha in image.getdata()
                if alpha > 0
            }
            if not visible_rgb.issubset(palette_rgb):
                failures.append("palette")
            if not _backdrop_preparation_is_valid(
                metadata.get("preparation"),
                pack_id=pack_id,
                slot=slot,
                expected_size=expected_size,
            ):
                failures.append("backdrop-native-metadata")
        if slot != "backdrop.beijing-cbd" and any(
            image.getpixel(point)[3] != 0
            for point in (
                (0, 0),
                (image.width - 1, 0),
                (0, image.height - 1),
                (image.width - 1, image.height - 1),
            )
        ):
            failures.append("transparent-corners")
        if slot in {"structure.wall-window-nw", "structure.wall-window-ne"}:
            pane_alpha = template.get("paneAlpha")
            pane_count = template.get("paneCount")
            if (
                isinstance(pane_alpha, bool)
                or not isinstance(pane_alpha, int)
                or not 0 < pane_alpha < 255
            ):
                failures.append("pane-alpha")
                pane_alpha = 128
            if (
                isinstance(pane_count, bool)
                or not isinstance(pane_count, int)
                or pane_count <= 0
            ):
                failures.append("pane-count")
                pane_count = 4
            if pane_alpha not in alpha_values:
                failures.append("glass-alpha")
            if not alpha_values.issubset({0, pane_alpha, 255}):
                failures.append("glass-alpha-levels")
            if 255 not in alpha_values:
                failures.append("glass-mullions-opaque")
            pane_components = _alpha_component_stats(image, pane_alpha)
            visible_area = sum(
                1 for alpha_value in image.getchannel("A").getdata() if alpha_value > 0
            )
            minimum_pane_area = max(128, math.ceil(visible_area * 0.08))
            if len(pane_components) != pane_count or any(
                component["touchesTransparency"]
                or component["area"] < minimum_pane_area
                or component["bounds"][2] - component["bounds"][0] < 8
                or component["bounds"][2] - component["bounds"][0] > 24
                or component["bounds"][3] - component["bounds"][1] < 36
                for component in pane_components
            ):
                failures.append("glass-pane-components")
        elif not alpha_values.issubset({0, 255}):
            failures.append("binary-alpha")
        orientation = template.get("orientation")
        if slot.startswith("structure.wall-") and orientation in {"nw", "ne"}:
            try:
                slope = wall_screen_slope(image)
            except AssetGeometryError:
                failures.append("orientation-pixels")
            else:
                if (orientation == "nw" and slope <= 0.04) or (
                    orientation == "ne" and slope >= -0.04
                ):
                    failures.append("orientation-pixels")
            if isinstance(template.get("groundAxis"), Mapping):
                try:
                    actual_geometry = wall_face_geometry_pixels(
                        image, template["groundAxis"]
                    )
                except AssetGeometryError:
                    failures.append("ground-axis-pixels")
                    failures.append("wall-top-axis-pixels")
                    failures.append("wall-face-height-pixels")
                else:
                    if actual_geometry["groundAxis"] != template["groundAxis"]:
                        failures.append("ground-axis-pixels")
                    wall_face_height = template.get("wallFaceHeight")
                    if (
                        isinstance(wall_face_height, bool)
                        or not isinstance(wall_face_height, int)
                        or wall_face_height <= 0
                    ):
                        failures.append("wall-face-height")
                    else:
                        expected_top_axis = {
                            endpoint: {
                                "x": int(template["groundAxis"][endpoint]["x"]),
                                "y": int(template["groundAxis"][endpoint]["y"])
                                - wall_face_height,
                            }
                            for endpoint in ("start", "end")
                        }
                        expected_face_height = {
                            "start": wall_face_height,
                            "end": wall_face_height,
                        }
                        if actual_geometry["topAxis"] != expected_top_axis:
                            failures.append("wall-top-axis-pixels")
                        if actual_geometry["faceHeight"] != expected_face_height:
                            failures.append("wall-face-height-pixels")
        return failures

    @staticmethod
    def _apply_version_review(
        conn: sqlite3.Connection, row: sqlite3.Row, decision: str, now: str
    ) -> None:
        """Write one already-validated review decision; no CAS, bumps or readiness."""

        asset_id = row["joined_asset_id"]
        version_id = row["id"]
        if decision == "accepted":
            conn.execute(
                """
                UPDATE versions
                   SET status = 'superseded', reviewed_at = ?
                 WHERE asset_id = ? AND status = 'accepted' AND id <> ?
                """,
                (now, asset_id, version_id),
            )
            conn.execute(
                "UPDATE versions SET status = 'accepted', reviewed_at = ? WHERE id = ?",
                (now, version_id),
            )
            conn.execute(
                "UPDATE pack_members SET version_id = ? WHERE pack_id = ? AND asset_id = ?",
                (version_id, row["pack_id"], asset_id),
            )
        else:
            conn.execute(
                "UPDATE versions SET status = 'rejected', reviewed_at = ? WHERE id = ?",
                (now, version_id),
            )

    @staticmethod
    def _insert_review_row(
        conn: sqlite3.Connection,
        asset_id: str,
        version_id: str,
        decision: str,
        note: str,
        expected_revision: int,
        revision: int,
        now: str,
    ) -> str:
        review_id = f"review-{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO reviews(
                id, asset_id, version_id, decision, note,
                expected_revision, resulting_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                asset_id,
                version_id,
                decision,
                note,
                expected_revision,
                revision,
                now,
            ),
        )
        return review_id

    def review(
        self,
        asset_id: str,
        version_id: str,
        decision: str,
        note: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Accept or reject a draft using the global catalog revision as CAS."""

        self._ensure_bootstrapped()
        expected_revision = self._validate_expected_revision(expected_revision)
        normalized_decision = self._normalize_decision(decision)
        clean_note = self._normalize_note(note, required=normalized_decision == "rejected")
        now = _utc_now()
        with self._transaction() as conn:
            self._assert_revision(conn, expected_revision)
            row = self._load_reviewable_version(conn, asset_id, version_id, normalized_decision)
            self._apply_version_review(conn, row, normalized_decision, now)
            conn.execute(
                "UPDATE assets SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (now, asset_id),
            )
            conn.execute(
                "UPDATE packs SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (now, row["pack_id"]),
            )
            revision = self._bump_catalog_revision(conn)
            review_id = self._insert_review_row(
                conn,
                asset_id,
                version_id,
                normalized_decision,
                clean_note,
                expected_revision,
                revision,
                now,
            )
            self._refresh_pack_readiness(conn, row["pack_id"], now)

        with self._connect() as conn:
            return {
                "revision": revision,
                "reviewId": review_id,
                "asset": self._asset_payload(conn, asset_id),
                "version": self._version_payload_by_id(conn, version_id),
                "pack": self._pack_payload(conn, row["pack_id"]),
            }

    def _normalize_review_items(self, items: Any) -> list[dict[str, str]]:
        """Validate batch structure before opening a transaction."""

        if isinstance(items, Mapping) or not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            raise AssetLabError("items must be a list", code="review.batch_invalid")
        if not items:
            raise AssetLabError("batch review requires at least one item", code="review.batch_empty")
        if len(items) > MAX_REVIEW_BATCH_ITEMS:
            raise AssetLabError(
                "batch review has too many items",
                code="review.batch_too_large",
                details={"maxItems": MAX_REVIEW_BATCH_ITEMS, "itemCount": len(items)},
            )
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        accepted_assets: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                raise AssetLabError("batch item must be an object", code="review.batch_invalid")
            asset_id = item.get("assetId")
            version_id = item.get("versionId")
            for value in (asset_id, version_id):
                if not isinstance(value, str) or not value or len(value) > 128:
                    raise AssetLabError(
                        "batch item needs a non-empty assetId and versionId",
                        code="review.batch_invalid",
                    )
            decision = self._normalize_decision(item.get("decision"))
            key = (asset_id, version_id)
            if key in seen:
                raise AssetLabError(
                    "batch review lists the same version twice",
                    code="review.batch_duplicate",
                    details={"assetId": asset_id, "versionId": version_id},
                )
            seen.add(key)
            if decision == "accepted":
                # Two accepts on one asset are order-dependent: the second supersedes the
                # first. Reject the ambiguity instead of silently picking a winner.
                if asset_id in accepted_assets:
                    raise AssetLabError(
                        "one batch cannot accept two versions of the same asset",
                        code="review.batch_conflict",
                        details={"assetId": asset_id},
                    )
                accepted_assets.add(asset_id)
            normalized.append(
                {"assetId": asset_id, "versionId": version_id, "decision": decision}
            )
        return normalized

    def review_batch(
        self,
        items: Any,
        note: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Apply many draft reviews atomically under a single global-revision CAS."""

        self._ensure_bootstrapped()
        expected_revision = self._validate_expected_revision(expected_revision)
        normalized = self._normalize_review_items(items)
        clean_note = self._normalize_note(
            note, required=any(item["decision"] == "rejected" for item in normalized)
        )
        now = _utc_now()
        with self._transaction() as conn:
            self._assert_revision(conn, expected_revision)

            # Pre-validate everything first. Applying item i can never invalidate item j
            # (supersede only touches already-accepted rows, and gates read immutable
            # metadata), so this pass sees every failure the batch would ever hit.
            rows: list[sqlite3.Row] = []
            failures: list[dict[str, Any]] = []
            for index, item in enumerate(normalized):
                try:
                    rows.append(
                        self._load_reviewable_version(
                            conn,
                            item["assetId"],
                            item["versionId"],
                            item["decision"],
                        )
                    )
                except AssetLabError as error:
                    failures.append(
                        {
                            "index": index,
                            "assetId": item["assetId"],
                            "versionId": item["versionId"],
                            **error.as_dict(),
                        }
                    )
            if failures:
                raise AssetLabError(
                    "批量验收未执行：存在不可验收的条目",
                    code="review.batch_failed",
                    details={
                        "itemCount": len(normalized),
                        "failureCount": len(failures),
                        "failures": failures,
                    },
                )

            for item, row in zip(normalized, rows, strict=True):
                self._apply_version_review(conn, row, item["decision"], now)

            asset_ids = list(dict.fromkeys(str(row["joined_asset_id"]) for row in rows))
            pack_ids = list(dict.fromkeys(str(row["pack_id"]) for row in rows))
            conn.executemany(
                "UPDATE assets SET revision = revision + 1, updated_at = ? WHERE id = ?",
                [(now, asset_id) for asset_id in asset_ids],
            )
            conn.executemany(
                "UPDATE packs SET revision = revision + 1, updated_at = ? WHERE id = ?",
                [(now, pack_id) for pack_id in pack_ids],
            )
            # One bump for the whole batch: _assert_revision only tests equality, and this
            # keeps every reviews row's (expected, resulting) pair literally true.
            revision = self._bump_catalog_revision(conn)
            results = [
                {
                    "assetId": item["assetId"],
                    "versionId": item["versionId"],
                    "decision": item["decision"],
                    "reviewId": self._insert_review_row(
                        conn,
                        item["assetId"],
                        item["versionId"],
                        item["decision"],
                        clean_note,
                        expected_revision,
                        revision,
                        now,
                    ),
                }
                for item in normalized
            ]
            for pack_id in pack_ids:
                self._refresh_pack_readiness(conn, pack_id, now)

        with self._connect() as conn:
            return {
                "revision": revision,
                "note": clean_note,
                "results": results,
                "assets": [self._asset_payload(conn, asset_id) for asset_id in asset_ids],
                "packs": [self._pack_payload(conn, pack_id) for pack_id in pack_ids],
            }

    def activate(self, pack_id: str, expected_revision: int) -> dict[str, Any]:
        """Atomically publish a complete accepted pack and deterministic atlas."""

        self._ensure_bootstrapped()
        expected_revision = self._validate_expected_revision(expected_revision)
        if pack_id not in SLOTS_BY_PACK:
            raise AssetLabError("pack not found", code="pack.not_found")
        now = _utc_now()
        manifest: dict[str, Any]
        atlas_png: bytes
        atlas_sha: str
        with self._transaction() as conn:
            self._assert_revision(conn, expected_revision)
            pack = conn.execute("SELECT * FROM packs WHERE id = ?", (pack_id,)).fetchone()
            if pack is None:
                raise AssetLabError("pack not found", code="pack.not_found")
            selected = self._selected_versions(conn, pack_id)
            missing = [entry["slot"] for entry in selected if entry["version_id"] is None]
            invalid = [
                entry["slot"]
                for entry in selected
                if entry["version_id"] is not None and entry["status"] != "accepted"
            ]
            if missing or invalid:
                raise AssetLabError(
                    "all required slots need an accepted version before activation",
                    code="pack.incomplete",
                    details={"missingSlots": missing, "invalidSlots": invalid},
                )
            runtime_spec = self._load_runtime_spec(
                pack_id,
                base_release_id=(
                    str(pack["base_release_id"])
                    if pack["base_release_id"] is not None
                    else None
                ),
            )
            atlas_entries, runtime_mappings = self._runtime_entries(selected, runtime_spec)
            atlas_png, atlas_layout, atlas_size = self._build_atlas(atlas_entries)
            atlas_sha = hashlib.sha256(atlas_png).hexdigest()
            self._write_content_addressed(self.derived_dir, atlas_sha, ".png", atlas_png)
            revision = self._bump_catalog_revision(conn)
            manifest, activation_catalog = self._build_manifest(
                selected,
                runtime_spec,
                runtime_mappings,
                atlas_layout,
                atlas_sha,
                atlas_size,
                revision,
                now,
                pack_id,
            )
            manifest_json = _canonical_json(manifest)
            manifest_sha = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
            release_id = f"release-{uuid.uuid4().hex}"
            activation_catalog = {
                **activation_catalog,
                "releaseId": release_id,
                "manifestSha256": manifest_sha,
            }
            catalog_json = _canonical_json(activation_catalog)
            conn.execute(
                """
                INSERT INTO pack_releases(
                    id, pack_id, catalog_revision, manifest_sha256,
                    atlas_sha256, manifest_json, catalog_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    release_id,
                    pack_id,
                    revision,
                    manifest_sha,
                    atlas_sha,
                    manifest_json,
                    catalog_json,
                    now,
                ),
            )
            conn.execute("UPDATE packs SET status = 'draft' WHERE status = 'active' AND id <> ?", (pack_id,))
            conn.execute(
                """
                UPDATE packs
                   SET status = 'active', revision = revision + 1,
                       atlas_sha256 = ?, manifest_json = ?, catalog_json = ?,
                       active_release_id = ?, updated_at = ?, activated_at = ?
                 WHERE id = ?
                """,
                (
                    atlas_sha,
                    manifest_json,
                    catalog_json,
                    release_id,
                    now,
                    now,
                    pack_id,
                ),
            )
        manifest_path = self.derived_dir / pack_id / "manifest.json"
        self._atomic_write(manifest_path, _canonical_json(manifest).encode("utf-8"))
        if pack_id == PACK_ID:
            self._seed_core_v1_from_active()
        elif pack_id == CORE_V1_PACK_ID:
            self._seed_core_v2_from_active()
        with self._connect() as conn:
            return {
                "revision": revision,
                "pack": self._pack_payload(conn, pack_id),
                "release": self._release_payload_by_id(conn, release_id),
                "manifest": manifest,
                "catalog": activation_catalog,
            }

    def active_manifest(self) -> dict[str, Any] | None:
        """Return the active immutable manifest snapshot, if a pack is active."""

        self._ensure_bootstrapped()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT manifest_json FROM packs
                 WHERE status = 'active' AND manifest_json IS NOT NULL
                 ORDER BY activated_at DESC, id LIMIT 1
                """
            ).fetchone()
            return json.loads(row["manifest_json"]) if row else None

    def active_release(self) -> dict[str, Any] | None:
        """Return the currently active immutable release, including snapshots."""

        self._ensure_bootstrapped()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT release.*
                  FROM packs AS pack
                  JOIN pack_releases AS release ON release.id = pack.active_release_id
                 WHERE pack.status = 'active'
                 ORDER BY pack.activated_at DESC, pack.id
                 LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            manifest_json = str(row["manifest_json"])
            manifest_sha = str(row["manifest_sha256"])
            if hashlib.sha256(manifest_json.encode("utf-8")).hexdigest() != manifest_sha:
                raise AssetLabError("stored manifest hash mismatch", code="storage.corrupt")
            atlas_sha = str(row["atlas_sha256"])
            atlas_path = self.derived_dir / atlas_sha[:2] / f"{atlas_sha}.png"
            if (
                SHA256_RE.fullmatch(atlas_sha) is None
                or not atlas_path.is_file()
                or atlas_path.is_symlink()
                or hashlib.sha256(atlas_path.read_bytes()).hexdigest() != atlas_sha
            ):
                raise AssetLabError("stored atlas hash mismatch", code="storage.corrupt")
            payload = self._release_payload(row)
            payload["manifest"] = json.loads(manifest_json)
            payload["catalog"] = json.loads(row["catalog_json"])
            return payload

    def manifest_json_by_sha(self, sha: str) -> str:
        """Resolve canonical immutable manifest bytes by lowercase SHA-256."""

        self._ensure_bootstrapped()
        if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
            raise AssetLabError("invalid manifest SHA-256", code="manifest.invalid_sha")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT manifest_json FROM pack_releases
                 WHERE manifest_sha256 = ?
                 ORDER BY created_at, id LIMIT 1
                """,
                (sha,),
            ).fetchone()
        if row is None:
            raise AssetLabError("manifest not found", code="manifest.not_found")
        manifest_json = str(row["manifest_json"])
        if hashlib.sha256(manifest_json.encode("utf-8")).hexdigest() != sha:
            raise AssetLabError("stored manifest hash mismatch", code="storage.corrupt")
        return manifest_json

    def manifest_by_sha(self, sha: str) -> dict[str, Any]:
        return json.loads(self.manifest_json_by_sha(sha))

    def blob_path(self, sha: str) -> Path:
        """Resolve an existing normalized PNG by lowercase SHA-256 only."""

        self._ensure_bootstrapped()
        if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
            raise AssetLabError("invalid SHA-256", code="blob.invalid_sha")
        path = self.blobs_dir / sha[:2] / f"{sha}.png"
        if not path.is_file() or path.is_symlink():
            raise AssetLabError("blob not found", code="blob.not_found")
        return path

    # ------------------------------------------------------------------
    # Schema and persistence

    def _ensure_bootstrapped(self) -> None:
        if self._initialized:
            return
        with self._bootstrap_lock:
            if self._initialized:
                return
            for directory in (
                self.data_dir,
                self.inbox_dir,
                self.blobs_dir,
                self.derived_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS style_profiles (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        spec_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL CHECK(kind IN ('import', 'scan')),
                        status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
                        input_name TEXT,
                        result_json TEXT,
                        error_json TEXT,
                        created_at TEXT NOT NULL,
                        finished_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS packs (
                        id TEXT PRIMARY KEY,
                        style_profile_id TEXT NOT NULL REFERENCES style_profiles(id),
                        name TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('draft', 'ready', 'active')),
                        revision INTEGER NOT NULL DEFAULT 0,
                        atlas_sha256 TEXT,
                        manifest_json TEXT,
                        catalog_json TEXT,
                        active_release_id TEXT,
                        base_release_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        activated_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS assets (
                        id TEXT PRIMARY KEY,
                        pack_id TEXT NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
                        slot TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(pack_id, slot)
                    );

                    CREATE TABLE IF NOT EXISTS versions (
                        id TEXT PRIMARY KEY,
                        asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                        version_number INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        width INTEGER NOT NULL,
                        height INTEGER NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('draft', 'accepted', 'rejected', 'superseded')),
                        metadata_json TEXT NOT NULL,
                        metadata_fingerprint TEXT NOT NULL,
                        warnings_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        reviewed_at TEXT,
                        UNIQUE(asset_id, version_number),
                        UNIQUE(asset_id, sha256, metadata_fingerprint)
                    );

                    CREATE INDEX IF NOT EXISTS idx_versions_sha256 ON versions(sha256);
                    CREATE INDEX IF NOT EXISTS idx_versions_asset_status ON versions(asset_id, status);

                    CREATE TABLE IF NOT EXISTS reviews (
                        id TEXT PRIMARY KEY,
                        asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                        version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                        decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
                        note TEXT NOT NULL,
                        expected_revision INTEGER NOT NULL,
                        resulting_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS pack_members (
                        pack_id TEXT NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
                        slot TEXT NOT NULL,
                        asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                        version_id TEXT REFERENCES versions(id),
                        required INTEGER NOT NULL CHECK(required IN (0, 1)),
                        ordinal INTEGER NOT NULL,
                        inherited INTEGER NOT NULL DEFAULT 0 CHECK(inherited IN (0, 1)),
                        source_release_id TEXT,
                        PRIMARY KEY(pack_id, slot),
                        UNIQUE(pack_id, asset_id)
                    );

                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS pack_releases (
                        id TEXT PRIMARY KEY,
                        pack_id TEXT NOT NULL REFERENCES packs(id),
                        catalog_revision INTEGER NOT NULL,
                        manifest_sha256 TEXT NOT NULL,
                        atlas_sha256 TEXT NOT NULL,
                        manifest_json TEXT NOT NULL,
                        catalog_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(pack_id, catalog_revision)
                    );

                    CREATE INDEX IF NOT EXISTS idx_pack_releases_manifest_sha
                    ON pack_releases(manifest_sha256);

                    CREATE TRIGGER IF NOT EXISTS pack_releases_no_update
                    BEFORE UPDATE ON pack_releases
                    BEGIN
                        SELECT RAISE(ABORT, 'pack releases are immutable');
                    END;

                    CREATE TRIGGER IF NOT EXISTS pack_releases_no_delete
                    BEFORE DELETE ON pack_releases
                    BEGIN
                        SELECT RAISE(ABORT, 'pack releases are immutable');
                    END;
                    """
                )
                pack_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(packs)").fetchall()
                }
                if "catalog_json" not in pack_columns:
                    conn.execute("ALTER TABLE packs ADD COLUMN catalog_json TEXT")
                if "active_release_id" not in pack_columns:
                    conn.execute("ALTER TABLE packs ADD COLUMN active_release_id TEXT")
                if "base_release_id" not in pack_columns:
                    conn.execute("ALTER TABLE packs ADD COLUMN base_release_id TEXT")
                member_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(pack_members)").fetchall()
                }
                if "inherited" not in member_columns:
                    conn.execute(
                        "ALTER TABLE pack_members ADD COLUMN inherited INTEGER NOT NULL DEFAULT 0"
                    )
                if "source_release_id" not in member_columns:
                    conn.execute(
                        "ALTER TABLE pack_members ADD COLUMN source_release_id TEXT"
                    )
                conn.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS packs_base_release_immutable
                    BEFORE UPDATE OF base_release_id ON packs
                    WHEN OLD.base_release_id IS NOT NULL
                     AND NEW.base_release_id IS NOT OLD.base_release_id
                    BEGIN
                        SELECT RAISE(ABORT, 'pack base release is immutable');
                    END;
                    """
                )
            self._seed_fixed_catalog()
            self._backfill_pack_releases()
            self._seed_core_v1_from_active()
            self._seed_core_v2_from_active()
            self._initialized = True

    def _seed_fixed_catalog(self) -> None:
        now = _utc_now()
        style_spec = {
            "id": STYLE_PROFILE_ID,
            "name": "Beijing Modern Isometric",
            "description": "Present-day Beijing startup office in readable 2D pixel art.",
            "projection": {
                "type": "isometric-2-to-1",
                "tileWidth": 32,
                "tileHeight": 16,
                "verticalUnit": 8,
            },
            "rendering": {
                "format": "png",
                "colorMode": "RGBA",
                "pixelArt": True,
                "nearestNeighbor": True,
                "transparentBackground": True,
            },
            "atlas": {
                "initialSize": ATLAS_INITIAL_SIZE,
                "maxSize": ATLAS_MAX_SIZE,
                "padding": ATLAS_PADDING,
                "extrusion": ATLAS_PADDING,
            },
            "worldPalette": list(WORLD_PALETTE),
            "playerAccents": list(PLAYER_ACCENTS),
        }
        style_v2_spec = {
            **json.loads(_canonical_json(style_spec)),
            "id": CORE_V2_STYLE_PROFILE_ID,
            "name": "Beijing Modern Isometric v2",
            "description": (
                "Modern Beijing office pixel art with a cooler glass-and-sky range "
                "and restrained warm wood accents."
            ),
            "worldPalette": list(CORE_V2_WORLD_PALETTE),
            "playerAccents": list(CORE_V2_PLAYER_ACCENTS),
        }
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('catalog_revision', '0')")
            conn.execute(
                """
                INSERT OR IGNORE INTO style_profiles(id, name, spec_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (STYLE_PROFILE_ID, style_spec["name"], _canonical_json(style_spec), now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO style_profiles(id, name, spec_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    CORE_V2_STYLE_PROFILE_ID,
                    style_v2_spec["name"],
                    _canonical_json(style_v2_spec),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO packs(
                    id, style_profile_id, name, status, revision,
                    atlas_sha256, manifest_json, catalog_json, active_release_id,
                    created_at, updated_at, activated_at
                ) VALUES (?, ?, ?, 'draft', 0, NULL, NULL, NULL, NULL, ?, ?, NULL)
                """,
                (PACK_ID, STYLE_PROFILE_ID, "Core v0", now, now),
            )
            for ordinal, slot in enumerate(CORE_SLOTS):
                asset_id = _asset_id(slot["slot"])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO assets(
                        id, pack_id, slot, kind, display_name, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        asset_id,
                        PACK_ID,
                        slot["slot"],
                        slot["kind"],
                        slot["displayName"],
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pack_members(
                        pack_id, slot, asset_id, version_id, required, ordinal
                    ) VALUES (?, ?, ?, NULL, 1, ?)
                    """,
                    (PACK_ID, slot["slot"], asset_id, ordinal),
                )

    def _backfill_pack_releases(self) -> None:
        """Convert pre-release active packs without changing review selections."""

        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM packs
                 WHERE status = 'active' AND manifest_json IS NOT NULL
                   AND atlas_sha256 IS NOT NULL AND active_release_id IS NULL
                """
            ).fetchall()
            for row in rows:
                manifest_json = _canonical_json(json.loads(row["manifest_json"]))
                manifest_sha = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
                catalog = json.loads(row["catalog_json"]) if row["catalog_json"] else {}
                catalog_revision = int(catalog.get("revision", row["revision"]))
                existing = conn.execute(
                    """
                    SELECT id FROM pack_releases
                     WHERE pack_id = ? AND catalog_revision = ?
                    """,
                    (row["id"], catalog_revision),
                ).fetchone()
                release_id = (
                    str(existing["id"])
                    if existing is not None
                    else f"release-migrated-{manifest_sha[:20]}"
                )
                catalog = {
                    **catalog,
                    "releaseId": release_id,
                    "manifestSha256": manifest_sha,
                }
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pack_releases(
                        id, pack_id, catalog_revision, manifest_sha256,
                        atlas_sha256, manifest_json, catalog_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release_id,
                        row["id"],
                        catalog_revision,
                        manifest_sha,
                        row["atlas_sha256"],
                        manifest_json,
                        _canonical_json(catalog),
                        row["activated_at"] or row["updated_at"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE packs
                       SET active_release_id = ?, manifest_json = ?, catalog_json = ?
                     WHERE id = ?
                    """,
                    (release_id, manifest_json, _canonical_json(catalog), row["id"]),
                )

    def _seed_core_v1_from_active(self) -> None:
        """Create core-v1 once, pinning the then-active core-v0 release forever."""

        self._seed_extension_from_active(
            pack_id=CORE_V1_PACK_ID,
            pack_name="Core v1",
            style_profile_id=STYLE_PROFILE_ID,
            base_pack_id=PACK_ID,
            base_slots=CORE_SLOTS,
            # core-v1's Gus override is optional, so it remains inherited at
            # seed time and is materialized lazily on first import.
            override_slot_names=(),
            new_slots=CORE_V1_NEW_SLOTS,
        )

    def _seed_core_v2_from_active(self) -> None:
        """Create core-v2 once, freezing the then-active core-v1 release."""

        self._seed_extension_from_active(
            pack_id=CORE_V2_PACK_ID,
            pack_name="Core v2",
            style_profile_id=CORE_V2_STYLE_PROFILE_ID,
            base_pack_id=CORE_V1_PACK_ID,
            base_slots=_CORE_V1_LOGICAL_SLOTS,
            override_slot_names=CORE_V2_OVERRIDE_SLOT_NAMES,
            new_slots=CORE_V2_NEW_SLOTS,
        )

    def _seed_extension_from_active(
        self,
        *,
        pack_id: str,
        pack_name: str,
        style_profile_id: str,
        base_pack_id: str,
        base_slots: Sequence[Mapping[str, Any]],
        override_slot_names: Sequence[str],
        new_slots: Sequence[Mapping[str, Any]],
    ) -> None:
        """Seed one immutable-base extension using only its declarative boundary."""

        now = _utc_now()
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT base_release_id FROM packs WHERE id = ?",
                (pack_id,),
            ).fetchone()
            if existing is not None:
                if existing["base_release_id"] is None:
                    raise AssetLabError(
                        f"{pack_id} base release is missing",
                        code="storage.corrupt",
                    )
                return
            base_pack = conn.execute(
                """
                SELECT active_release_id FROM packs
                 WHERE id = ? AND status = 'active' AND active_release_id IS NOT NULL
                """,
                (base_pack_id,),
            ).fetchone()
            if base_pack is None:
                return
            base_release_id = str(base_pack["active_release_id"])
            release = conn.execute(
                "SELECT catalog_json FROM pack_releases WHERE id = ? AND pack_id = ?",
                (base_release_id, base_pack_id),
            ).fetchone()
            if release is None:
                raise AssetLabError(
                    "active core-v0 release is missing",
                    code="storage.corrupt",
                )
            try:
                release_catalog = json.loads(release["catalog_json"])
                if release_catalog.get("synthetic") is True:
                    return
                catalog_assets = {
                    str(entry["slot"]): entry
                    for entry in release_catalog["assets"]
                }
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise AssetLabError(
                    "active core-v0 catalog is invalid",
                    code="storage.corrupt",
                ) from exc
            base_slot_names = [str(entry["slot"]) for entry in base_slots]
            if set(catalog_assets) != set(base_slot_names):
                raise AssetLabError(
                    f"active {base_pack_id} catalog is incomplete",
                    code="storage.corrupt",
                )
            conn.execute(
                """
                INSERT INTO packs(
                    id, style_profile_id, name, status, revision,
                    atlas_sha256, manifest_json, catalog_json, active_release_id,
                    base_release_id, created_at, updated_at, activated_at
                ) VALUES (?, ?, ?, 'draft', 0, NULL, NULL, NULL, NULL, ?, ?, ?, NULL)
                """,
                (
                    pack_id,
                    style_profile_id,
                    pack_name,
                    base_release_id,
                    now,
                    now,
                ),
            )
            override_names = set(override_slot_names)
            local_definitions = {
                entry["slot"]: entry
                for entry in EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id].values()
            }
            for ordinal, slot in enumerate(base_slots):
                if slot["slot"] in override_names:
                    definition = local_definitions[slot["slot"]]
                    asset_id = _asset_id(slot["slot"], pack_id)
                    conn.execute(
                        """
                        INSERT INTO assets(
                            id, pack_id, slot, kind, display_name, revision,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            asset_id,
                            pack_id,
                            slot["slot"],
                            definition["kind"],
                            definition["displayName"],
                            now,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO pack_members(
                            pack_id, slot, asset_id, version_id, required, ordinal,
                            inherited, source_release_id
                        ) VALUES (?, ?, ?, NULL, 1, ?, 0, NULL)
                        """,
                        (pack_id, slot["slot"], asset_id, ordinal),
                    )
                    continue
                inherited = catalog_assets[slot["slot"]]
                asset_id = str(inherited["assetId"])
                version_id = str(inherited["versionId"])
                valid = conn.execute(
                    """
                    SELECT 1 FROM versions v JOIN assets a ON a.id = v.asset_id
                     WHERE v.id = ? AND a.id = ?
                       AND v.status IN ('accepted', 'superseded')
                    """,
                    (version_id, asset_id),
                ).fetchone()
                if valid is None:
                    raise AssetLabError(
                        f"{pack_id} inherited member does not match its base release",
                        code="storage.corrupt",
                    )
                conn.execute(
                    """
                    INSERT INTO pack_members(
                        pack_id, slot, asset_id, version_id, required, ordinal,
                        inherited, source_release_id
                    ) VALUES (?, ?, ?, ?, 1, ?, 1, ?)
                    """,
                    (
                        pack_id,
                        slot["slot"],
                        asset_id,
                        version_id,
                        ordinal,
                        base_release_id,
                    ),
                )
            for offset, slot in enumerate(new_slots, start=len(base_slots)):
                asset_id = _asset_id(slot["slot"], pack_id)
                conn.execute(
                    """
                    INSERT INTO assets(
                        id, pack_id, slot, kind, display_name, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        asset_id,
                        pack_id,
                        slot["slot"],
                        slot["kind"],
                        slot["displayName"],
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO pack_members(
                        pack_id, slot, asset_id, version_id, required, ordinal,
                        inherited, source_release_id
                    ) VALUES (?, ?, ?, NULL, 1, ?, 0, NULL)
                    """,
                    (pack_id, slot["slot"], asset_id, offset),
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _setting(conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise AssetLabError("asset lab setting is missing", code="storage.invalid")
        return str(row["value"])

    def _catalog_revision(self, conn: sqlite3.Connection) -> int:
        return int(self._setting(conn, "catalog_revision"))

    def _bump_catalog_revision(self, conn: sqlite3.Connection) -> int:
        revision = self._catalog_revision(conn) + 1
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'catalog_revision'",
            (str(revision),),
        )
        return revision

    def _assert_revision(self, conn: sqlite3.Connection, expected_revision: int) -> None:
        actual = self._catalog_revision(conn)
        if actual != expected_revision:
            raise AssetLabError(
                "catalog revision changed",
                code="revision.conflict",
                details={"expectedRevision": expected_revision, "actualRevision": actual},
            )

    def _create_job(self, kind: str, input_name: str | None) -> str:
        job_id = f"job-{uuid.uuid4().hex}"
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO jobs(id, kind, status, input_name, created_at)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (job_id, kind, input_name, _utc_now()),
            )
        return job_id

    def _finish_job(
        self,
        job_id: str,
        status: str,
        result: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE jobs
                   SET status = ?, result_json = ?, error_json = ?, finished_at = ?
                 WHERE id = ?
                """,
                (
                    status,
                    _canonical_json(result) if result is not None else None,
                    _canonical_json(error) if error is not None else None,
                    _utc_now(),
                    job_id,
                ),
            )

    # ------------------------------------------------------------------
    # Validation and normalization

    @staticmethod
    def _safe_input_name(metadata: Mapping[str, Any] | Any) -> str | None:
        if not isinstance(metadata, Mapping):
            return None
        value = metadata.get("sourceName")
        if isinstance(value, str):
            return Path(value).name[:255]
        return None

    def _normalize_import_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, Mapping):
            raise AssetLabError("metadata must be an object", code="metadata.invalid")
        raw = dict(metadata)
        pack_id = raw.pop("packId", PACK_ID)
        if pack_id not in SLOTS_BY_PACK:
            raise AssetLabError("pack not found", code="pack.not_found")
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM packs WHERE id = ?", (pack_id,)
            ).fetchone()
        if exists is None:
            raise AssetLabError("pack not found", code="pack.not_found")
        expected_style_id = PACK_STYLE_PROFILE_IDS[pack_id]
        style_id = raw.pop("styleProfileId", expected_style_id)
        if style_id != expected_style_id:
            raise AssetLabError("style profile does not match pack", code="style.mismatch")
        slot = raw.pop("slot", None)
        supplied_asset_id = raw.pop("assetId", None)
        if slot is None and isinstance(supplied_asset_id, str):
            allowed_slots = EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id]
            if supplied_asset_id in allowed_slots:
                slot = supplied_asset_id
            else:
                slot = next(
                    (
                        entry["slot"]
                        for entry in EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id].values()
                        if _asset_id(entry["slot"], pack_id) == supplied_asset_id
                    ),
                    None,
                )
        allowed_slots = EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id]
        if not isinstance(slot, str) or slot not in allowed_slots:
            raise AssetLabError(
                f"slot must be one of the {pack_id} editable slots",
                code="slot.invalid",
                details={"allowedSlots": list(allowed_slots)},
            )
        if supplied_asset_id is not None and supplied_asset_id not in (
            slot,
            _asset_id(slot, pack_id),
        ):
            raise AssetLabError("assetId and slot do not match", code="asset.mismatch")
        display_name = raw.pop("displayName", None)
        if display_name is not None:
            if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120:
                raise AssetLabError("displayName is invalid", code="metadata.invalid")
            display_name = display_name.strip()
        nested = raw.pop("metadata", {})
        if not isinstance(nested, Mapping):
            raise AssetLabError("metadata.metadata must be an object", code="metadata.invalid")
        asset_metadata = dict(nested)
        for key, value in raw.items():
            if key != "sourceName":
                asset_metadata.setdefault(key, value)
        if display_name:
            asset_metadata.setdefault("displayName", display_name)
        self._validate_json_value(asset_metadata)
        if len(_canonical_json(asset_metadata).encode("utf-8")) > MAX_METADATA_BYTES:
            raise AssetLabError("metadata is too large", code="metadata.too_large")
        return {
            "packId": pack_id,
            "styleProfileId": style_id,
            "slot": slot,
            "assetId": _asset_id(slot, pack_id),
            "assetMetadata": asset_metadata,
        }

    def _normalize_asset_metadata(
        self,
        pack_id: str,
        slot: str,
        metadata: Mapping[str, Any],
        image_size: tuple[int, int],
    ) -> dict[str, Any]:
        normalized = dict(metadata)
        width, height = image_size
        frames_value = normalized.get("frames")
        frame_width = normalized.get("frameWidth")
        frame_height = normalized.get("frameHeight")
        if frames_value is None and (frame_width is not None or frame_height is not None):
            fw = self._positive_int(frame_width, "frameWidth")
            fh = self._positive_int(frame_height, "frameHeight")
            columns = normalized.get("columns", width // fw)
            columns = self._positive_int(columns, "columns")
            available_columns = width // fw
            available_rows = height // fh
            if columns > available_columns:
                raise AssetLabError("columns exceed image width", code="frames.invalid")
            default_count = CHARACTER_FRAME_COUNT if slot == "character.gus" else columns * available_rows
            frame_count = normalized.get("frameCount", default_count)
            frame_count = self._positive_int(frame_count, "frameCount")
            if frame_count > columns * available_rows:
                raise AssetLabError("frame grid exceeds image bounds", code="frames.invalid")
            frames_value = [
                {
                    "x": (index % columns) * fw,
                    "y": (index // columns) * fh,
                    "width": fw,
                    "height": fh,
                }
                for index in range(frame_count)
            ]
            normalized["columns"] = columns
            normalized["frameCount"] = frame_count
        if frames_value is None:
            frames_value = [{"x": 0, "y": 0, "width": width, "height": height}]
        normalized["frames"] = self._normalize_frames(frames_value, width, height)
        if slot == "character.gus":
            if len(normalized["frames"]) != CHARACTER_FRAME_COUNT:
                raise AssetLabError(
                    f"character.gus requires exactly {CHARACTER_FRAME_COUNT} frames",
                    code="character.frames_invalid",
                    details={"required": CHARACTER_FRAME_COUNT, "actual": len(normalized["frames"])},
                )
            normalized["animations"] = self._normalize_character_animations(
                normalized.get("animations")
            )
        elif "animations" in normalized:
            normalized["animations"] = self._normalize_generic_animations(
                normalized["animations"], len(normalized["frames"])
            )
        normalized["slot"] = slot
        normalized["kind"] = EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id][slot]["kind"]
        normalized["styleProfileId"] = PACK_STYLE_PROFILE_IDS[pack_id]
        self._validate_json_value(normalized)
        if len(_canonical_json(normalized).encode("utf-8")) > MAX_METADATA_BYTES:
            raise AssetLabError("metadata is too large", code="metadata.too_large")
        return normalized

    @staticmethod
    def _source_metadata_json(metadata: Mapping[str, Any]) -> str:
        """Exclude deterministic inspection results from immutable source identity."""

        source_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in {"characterConsistency", "motionBuild"}
        }
        return _canonical_json(source_metadata)

    def _asset_for_import(
        self,
        conn: sqlite3.Connection,
        pack_id: str,
        slot: str,
        now: str,
    ) -> sqlite3.Row:
        asset = conn.execute(
            "SELECT * FROM assets WHERE pack_id = ? AND slot = ?",
            (pack_id, slot),
        ).fetchone()
        if asset is not None:
            return asset
        if slot not in OVERRIDABLE_INHERITED_SLOTS_BY_PACK.get(pack_id, ()):
            raise AssetLabError("asset slot does not exist", code="asset.not_found")
        member = conn.execute(
            """
            SELECT pm.inherited, pm.source_release_id
              FROM pack_members pm
             WHERE pm.pack_id = ? AND pm.slot = ?
            """,
            (pack_id, slot),
        ).fetchone()
        if member is None or not bool(member["inherited"]):
            raise AssetLabError("inherited asset override is unavailable", code="asset.not_found")
        definition = EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id][slot]
        asset_id = _asset_id(slot, pack_id)
        conn.execute(
            """
            INSERT INTO assets(
                id, pack_id, slot, kind, display_name, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                asset_id,
                pack_id,
                slot,
                definition["kind"],
                definition["displayName"],
                now,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE pack_members
               SET asset_id = ?, version_id = NULL,
                   inherited = 0, source_release_id = NULL
             WHERE pack_id = ? AND slot = ? AND inherited = 1
            """,
            (asset_id, pack_id, slot),
        )
        return conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()

    def _normalize_frames(
        self,
        frames: Any,
        image_width: int,
        image_height: int,
    ) -> list[dict[str, int]]:
        if not isinstance(frames, Sequence) or isinstance(frames, (str, bytes, bytearray)):
            raise AssetLabError("frames must be an array", code="frames.invalid")
        if not frames or len(frames) > 2048:
            raise AssetLabError("frames array length is invalid", code="frames.invalid")
        result: list[dict[str, int]] = []
        for index, frame in enumerate(frames):
            if not isinstance(frame, Mapping):
                raise AssetLabError("each frame must be an object", code="frames.invalid")
            try:
                x = frame.get("x", frame.get("left", 0))
                y = frame.get("y", frame.get("top", 0))
                width = frame.get("width", frame.get("w"))
                height = frame.get("height", frame.get("h"))
                if isinstance(x, bool) or isinstance(y, bool):
                    raise ValueError
                x = int(x)
                y = int(y)
                fw = self._positive_int(width, f"frames[{index}].width")
                fh = self._positive_int(height, f"frames[{index}].height")
            except (TypeError, ValueError) as exc:
                raise AssetLabError("frame rectangle is invalid", code="frames.invalid") from exc
            if x < 0 or y < 0 or x + fw > image_width or y + fh > image_height:
                raise AssetLabError(
                    "frame rectangle is outside the image",
                    code="frames.out_of_bounds",
                    details={"frameIndex": index},
                )
            result.append({"x": x, "y": y, "width": fw, "height": fh})
        return result

    def _normalize_character_animations(self, value: Any) -> dict[str, dict[str, list[int]]]:
        if not isinstance(value, Mapping):
            raise AssetLabError(
                "character.gus requires animations metadata",
                code="character.animations_invalid",
            )
        aliases = {
            "southeast": "southeast",
            "south-east": "southeast",
            "se": "southeast",
            "s": "southeast",
            "south": "southeast",
            "down": "southeast",
            "southwest": "southwest",
            "south-west": "southwest",
            "sw": "southwest",
            "w": "southwest",
            "west": "southwest",
            "left": "southwest",
            "northwest": "northwest",
            "north-west": "northwest",
            "nw": "northwest",
            "n": "northwest",
            "north": "northwest",
            "up": "northwest",
            "northeast": "northeast",
            "north-east": "northeast",
            "ne": "northeast",
            "e": "northeast",
            "east": "northeast",
            "right": "northeast",
        }
        required_directions = CHARACTER_DIRECTIONS
        required_actions = CHARACTER_ACTIONS
        if set(value) != set(required_actions):
            raise AssetLabError(
                "character animations must contain " + ", ".join(required_actions),
                code="character.animations_invalid",
            )
        result: dict[str, dict[str, list[int]]] = {}
        all_indexes: list[int] = []
        for action in required_actions:
            directions = value[action]
            if not isinstance(directions, Mapping):
                raise AssetLabError("animation directions must be objects", code="character.animations_invalid")
            normalized_directions: dict[str, list[int]] = {}
            for raw_direction, indexes in directions.items():
                direction = aliases.get(str(raw_direction).casefold())
                if direction is None or direction in normalized_directions:
                    raise AssetLabError("animation direction is invalid", code="character.animations_invalid")
                normalized = self._normalize_frame_indexes(indexes, CHARACTER_FRAME_COUNT)
                normalized_directions[direction] = normalized
                all_indexes.extend(normalized)
            if set(normalized_directions) != set(required_directions):
                raise AssetLabError(
                    "each character animation requires four directions",
                    code="character.animations_invalid",
                )
            result[action] = {
                direction: normalized_directions[direction] for direction in required_directions
            }
        if len(all_indexes) != CHARACTER_FRAME_COUNT or set(all_indexes) != set(range(CHARACTER_FRAME_COUNT)):
            raise AssetLabError(
                f"the {CHARACTER_FRAME_COUNT} character frames must each belong to exactly one animation direction",
                code="character.animations_invalid",
            )
        return result

    def _normalize_generic_animations(self, value: Any, frame_count: int) -> Any:
        if not isinstance(value, Mapping):
            raise AssetLabError("animations must be an object", code="animations.invalid")

        def walk(node: Any) -> Any:
            if isinstance(node, Mapping):
                return {str(key): walk(child) for key, child in node.items()}
            return self._normalize_frame_indexes(node, frame_count)

        return walk(value)

    @staticmethod
    def _normalize_frame_indexes(value: Any, frame_count: int) -> list[int]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
            raise AssetLabError("animation frames must be a non-empty array", code="animations.invalid")
        indexes: list[int] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < frame_count:
                raise AssetLabError("animation frame index is invalid", code="animations.invalid")
            indexes.append(item)
        return indexes

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AssetLabError(f"{field} must be a positive integer", code="metadata.invalid")
        return value

    def _validate_json_value(self, value: Any, depth: int = 0) -> None:
        if depth > 10:
            raise AssetLabError("metadata nesting is too deep", code="metadata.invalid")
        if value is None or isinstance(value, (bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise AssetLabError("metadata numbers must be finite", code="metadata.invalid")
            return
        if isinstance(value, str):
            if len(value) > 16_384:
                raise AssetLabError("metadata string is too long", code="metadata.invalid")
            return
        if isinstance(value, Mapping):
            if len(value) > 512:
                raise AssetLabError("metadata object has too many fields", code="metadata.invalid")
            for key, child in value.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise AssetLabError("metadata keys must be short strings", code="metadata.invalid")
                self._validate_json_value(child, depth + 1)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if len(value) > 4096:
                raise AssetLabError("metadata array is too long", code="metadata.invalid")
            for child in value:
                self._validate_json_value(child, depth + 1)
            return
        raise AssetLabError("metadata contains an unsupported value", code="metadata.invalid")

    @staticmethod
    def _validate_expected_revision(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AssetLabError("expectedRevision must be a non-negative integer", code="revision.invalid")
        return value

    @staticmethod
    def _normalize_decision(value: Any) -> str:
        aliases = {
            "accept": "accepted",
            "accepted": "accepted",
            "reject": "rejected",
            "rejected": "rejected",
        }
        normalized = aliases.get(value.casefold()) if isinstance(value, str) else None
        if normalized is None:
            raise AssetLabError(
                "decision must be accepted or rejected",
                code="review.decision_invalid",
            )
        return normalized

    @staticmethod
    def _normalize_note(value: Any, *, required: bool) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise AssetLabError("review note must be text", code="review.note_invalid")
        value = value.strip()
        if required and not value:
            raise AssetLabError("rejection requires a note", code="review.note_required")
        if len(value) > 2000:
            raise AssetLabError("review note is too long", code="review.note_invalid")
        return value

    def _resolve_pack_id(self, filters: Mapping[str, Any] | None) -> str:
        """Resolve the catalog's pack. Row/version narrowing is the client's job."""

        if filters is None:
            filters = {}
        if not isinstance(filters, Mapping):
            raise AssetLabError("filters must be an object", code="filters.invalid")
        pack_id = filters.get("packId", PACK_ID)
        if pack_id not in SLOTS_BY_PACK:
            raise AssetLabError("pack not found", code="pack.not_found")
        return pack_id

    # ------------------------------------------------------------------
    # Image handling

    def _decode_png(
        self,
        data: bytes,
        *,
        allow_opaque: bool = False,
    ) -> tuple[Image.Image, bytes]:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise AssetLabError("PNG input must be bytes", code="image.invalid")
        raw = bytes(data)
        if not raw:
            raise AssetLabError("PNG input is empty", code="image.invalid")
        if len(raw) > MAX_INPUT_BYTES:
            raise AssetLabError(
                "PNG exceeds the 16 MiB input limit",
                code="image.too_large",
                details={"maxInputBytes": MAX_INPUT_BYTES},
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.format != "PNG":
                        raise AssetLabError("only PNG images are accepted", code="image.not_png")
                    if getattr(source, "n_frames", 1) != 1:
                        raise AssetLabError("animated PNG is not supported", code="image.animated")
                    width, height = source.size
                    if (
                        width < 1
                        or height < 1
                        or width > MAX_IMAGE_DIMENSION
                        or height > MAX_IMAGE_DIMENSION
                    ):
                        raise AssetLabError(
                            "PNG dimensions exceed the supported range",
                            code="image.dimensions_invalid",
                            details={
                                "width": width,
                                "height": height,
                                "maxDimension": MAX_IMAGE_DIMENSION,
                            },
                        )
                    source.load()
                    normalized = ImageOps.exif_transpose(source).convert("RGBA")
                    alpha_low, alpha_high = normalized.getchannel("A").getextrema()
                    if alpha_high == 0 or (not allow_opaque and alpha_low != 0):
                        raise AssetLabError(
                            "PNG requires visible pixels and, except for backdrops, transparency",
                            code="image.transparency_required",
                        )
        except AssetLabError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning) as exc:
            raise AssetLabError("PNG could not be decoded", code="image.decode_failed") from exc
        output = io.BytesIO()
        normalized.save(output, format="PNG", optimize=False, compress_level=9)
        return normalized, output.getvalue()

    @staticmethod
    def _palette_warnings(
        image: Image.Image,
        slot: str,
        pack_id: str = PACK_ID,
    ) -> list[dict[str, Any]]:
        world_palette = PACK_WORLD_PALETTES[pack_id]
        player_accents = PACK_PLAYER_ACCENTS[pack_id]
        palette = world_palette + player_accents if slot == "character.gus" else world_palette
        allowed = {
            tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
            for color in palette
        }
        seen: set[tuple[int, int, int]] = set()
        outside: set[tuple[int, int, int]] = set()
        truncated = False
        for red, green, blue, alpha in image.getdata():
            if alpha == 0:
                continue
            rgb = (red, green, blue)
            seen.add(rgb)
            if rgb not in allowed and len(outside) < 64:
                outside.add(rgb)
            if len(seen) > 4096:
                truncated = True
                break
        if not outside and not truncated:
            return []
        return [
            {
                "code": "palette.outside_world_palette",
                "message": "Visible RGB colors outside the slot's fixed palette were kept as a draft warning.",
                "distinctRgbCount": len(seen),
                "countTruncated": truncated,
                "outsidePaletteSample": [
                    f"#{red:02X}{green:02X}{blue:02X}" for red, green, blue in sorted(outside)[:16]
                ],
            }
        ]

    def _write_content_addressed(
        self,
        root: Path,
        sha: str,
        suffix: str,
        content: bytes,
    ) -> Path:
        path = root / sha[:2] / f"{sha}{suffix}"
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise AssetLabError("content path is unsafe", code="storage.invalid")
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise AssetLabError("content-addressed file is corrupt", code="storage.corrupt")
            return path
        self._atomic_write(path, content)
        return path

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    # Atlas and manifest

    def _selected_versions(self, conn: sqlite3.Connection, pack_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT pm.ordinal, pm.slot, pm.required, pm.version_id,
                   pm.inherited, pm.source_release_id,
                   a.id AS asset_id, a.pack_id AS owner_pack_id,
                   a.kind, a.display_name,
                   v.version_number, v.sha256, v.width, v.height, v.size_bytes,
                   CASE WHEN pm.inherited = 1 THEN 'accepted' ELSE v.status END AS status,
                   v.status AS source_status, v.metadata_json, v.warnings_json
              FROM pack_members pm
              JOIN assets a ON a.id = pm.asset_id
              LEFT JOIN versions v ON v.id = pm.version_id
             WHERE pm.pack_id = ?
             ORDER BY pm.ordinal, pm.slot
            """,
            (pack_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _load_runtime_spec(
        self,
        pack_id: str,
        *,
        base_release_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            spec = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AssetLabError(
                "runtime asset specification cannot be read",
                code="runtime_spec.invalid",
            ) from exc
        required_slots = [entry["slot"] for entry in CORE_SLOTS]
        if (
            not isinstance(spec, dict)
            or spec.get("schemaVersion") != SCHEMA_VERSION
            or spec.get("id") != PACK_ID
            or spec.get("requiredSlots") != required_slots
            or spec.get("palette", {}).get("world") != list(WORLD_PALETTE)
            or spec.get("palette", {}).get("players") != list(PLAYER_ACCENTS)
            or spec.get("characterMotion", {}).get("policy") != "canonical-idle-v1"
            or spec.get("characterMotion", {}).get("fallback") != "canonical-idle-bob"
            or not isinstance(spec.get("characterMotion", {}).get("identityLocked"), bool)
            or not isinstance(spec.get("assets"), list)
            or not isinstance(spec.get("animations"), list)
            or not isinstance(spec.get("fixtures"), list)
            or not isinstance(spec.get("atlases"), list)
            or len(spec["atlases"]) != 1
        ):
            raise AssetLabError(
                "runtime asset specification does not match core-v0",
                code="runtime_spec.invalid",
            )
        if pack_id == PACK_ID:
            return spec
        if base_release_id is None:
            raise AssetLabError(
                "runtime asset specification is unavailable",
                code="runtime_spec.invalid",
            )
        if pack_id == CORE_V1_PACK_ID:
            try:
                extension = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise AssetLabError(
                    "core-v1 runtime asset specification cannot be read",
                    code="runtime_spec.invalid",
                ) from exc
            new_slots = [entry["slot"] for entry in CORE_V1_NEW_SLOTS]
            if (
                not isinstance(extension, dict)
                or extension.get("schemaVersion") != SCHEMA_VERSION
                or extension.get("geometryVersion") not in {1, 2}
                or extension.get("id") != CORE_V1_PACK_ID
                or extension.get("basePackId") != PACK_ID
                or extension.get("requiredNewSlots") != new_slots
                or extension.get("overrideSlots")
                != list(CORE_V1_OVERRIDE_SLOT_NAMES)
                or not isinstance(extension.get("assets"), list)
                or [entry.get("slot") for entry in extension["assets"]] != new_slots
                or not isinstance(extension.get("baseAssetPatches"), dict)
            ):
                raise AssetLabError(
                    "runtime asset specification does not match core-v1",
                    code="runtime_spec.invalid",
                )
            merged = json.loads(_canonical_json(spec))
            merged["id"] = CORE_V1_PACK_ID
            merged["geometryVersion"] = extension["geometryVersion"]
            merged["baseReleaseId"] = base_release_id
            merged["requiredSlots"] = required_slots + new_slots
            merged["atlases"][0]["id"] = CORE_V1_PACK_ID
            merged["atlases"][0]["source"] = "core-v1.png"
            for sheet in merged["sheets"]:
                sheet["atlas"] = CORE_V1_PACK_ID
            patches = extension["baseAssetPatches"]
            for asset in merged["assets"]:
                patch = patches.get(asset.get("id"))
                if isinstance(patch, dict):
                    asset.update(json.loads(_canonical_json(patch)))
            merged["assets"].extend(json.loads(_canonical_json(extension["assets"])))
            for asset in merged["assets"]:
                asset["atlas"] = CORE_V1_PACK_ID
            return merged
        if pack_id != CORE_V2_PACK_ID:
            raise AssetLabError(
                "runtime asset specification is unavailable",
                code="runtime_spec.invalid",
            )
        try:
            extension = json.loads(CORE_V2_PACK_SPEC_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AssetLabError(
                "core-v2 runtime asset specification cannot be read",
                code="runtime_spec.invalid",
            ) from exc
        extension_assets = (
            extension.get("assets", []) if isinstance(extension, Mapping) else []
        )
        wall_templates = [
            entry
            for entry in extension_assets
            if isinstance(entry, Mapping)
            and str(entry.get("slot", "")).startswith("structure.wall-")
        ]
        window_templates = [
            entry
            for entry in wall_templates
            if str(entry.get("slot", "")).startswith("structure.wall-window-")
        ]
        if (
            not isinstance(extension, dict)
            or extension.get("schemaVersion") != SCHEMA_VERSION
            or extension.get("geometryVersion") != 2
            or extension.get("id") != CORE_V2_PACK_ID
            or extension.get("basePackId") != CORE_V1_PACK_ID
            or extension.get("styleProfileId") != CORE_V2_STYLE_PROFILE_ID
            or extension.get("nativeFrameRequired") is not True
            or extension.get("sceneShell")
            != {
                "version": 1,
                "type": "cutaway-office-tower",
                "facadeDepth": 512,
                "slabDepth": 8,
                "windowBandPitch": 12,
                "colors": {
                    "outline": "#0D2228",
                    "ambientOcclusion": "#0D2228",
                    "slab": "#566169",
                    "facadeLight": "#557F9C",
                    "facadeDark": "#3D6078",
                    "window": "#729FBE",
                    "mullion": "#3B454C",
                },
            }
            or extension.get("alphaLevels") != sorted(CORE_V2_ALPHA_LEVELS)
            or extension.get("overrideSlots") != list(CORE_V2_OVERRIDE_SLOT_NAMES)
            or extension.get("requiredNewSlots") != list(CORE_V2_NEW_SLOT_NAMES)
            or extension.get("requiredEditableSlots") != list(CORE_V2_EDITABLE_SLOT_NAMES)
            or extension.get("palette", {}).get("world")
            != list(CORE_V2_WORLD_PALETTE)
            or extension.get("palette", {}).get("players")
            != list(CORE_V2_PLAYER_ACCENTS)
            or not isinstance(extension.get("assets"), list)
            or [entry.get("slot") for entry in extension["assets"]]
            != list(CORE_V2_EDITABLE_SLOT_NAMES)
            or not isinstance(extension.get("baseAssetPatches"), dict)
            or len(wall_templates) != 5
            or any(entry.get("wallFaceHeight") != 56 for entry in wall_templates)
            or len(window_templates) != 2
            or any(entry.get("paneAlpha") != 128 for entry in window_templates)
            or any(entry.get("paneCount") != 4 for entry in window_templates)
        ):
            raise AssetLabError(
                "runtime asset specification does not match core-v2",
                code="runtime_spec.invalid",
            )
        with self._connect() as conn:
            release = conn.execute(
                "SELECT manifest_json, manifest_sha256, pack_id FROM pack_releases WHERE id = ?",
                (base_release_id,),
            ).fetchone()
        if release is None or release["pack_id"] != CORE_V1_PACK_ID:
            raise AssetLabError(
                "core-v2 base release is unavailable",
                code="runtime_spec.invalid",
            )
        base_manifest_json = str(release["manifest_json"])
        if hashlib.sha256(base_manifest_json.encode("utf-8")).hexdigest() != release[
            "manifest_sha256"
        ]:
            raise AssetLabError("stored manifest hash mismatch", code="storage.corrupt")
        try:
            merged = json.loads(base_manifest_json)
        except json.JSONDecodeError as exc:
            raise AssetLabError("stored manifest is invalid", code="storage.corrupt") from exc
        if (
            merged.get("id") != CORE_V1_PACK_ID
            or merged.get("requiredSlots")
            != [entry["slot"] for entry in _CORE_V1_LOGICAL_SLOTS]
        ):
            raise AssetLabError(
                "core-v2 base release does not match core-v1",
                code="runtime_spec.invalid",
            )
        replacement_by_slot = {
            entry["slot"]: json.loads(_canonical_json(entry))
            for entry in extension["assets"]
        }
        override_names = set(CORE_V2_OVERRIDE_SLOT_NAMES)
        merged_assets = [
            asset
            for asset in merged.get("assets", [])
            if asset.get("slot") not in override_names
        ]
        merged_assets.extend(
            replacement_by_slot[slot] for slot in CORE_V2_EDITABLE_SLOT_NAMES
        )
        merged.update(
            {
                "id": CORE_V2_PACK_ID,
                "name": extension.get("name", "Core v2"),
                "geometryVersion": 2,
                "basePackId": CORE_V1_PACK_ID,
                "baseReleaseId": base_release_id,
                "styleProfileId": CORE_V2_STYLE_PROFILE_ID,
                "nativeFrameRequired": True,
                "sceneShell": json.loads(_canonical_json(extension["sceneShell"])),
                "palette": json.loads(_canonical_json(extension["palette"])),
                "requiredSlots": list(CORE_V2_REQUIRED_SLOT_NAMES),
                "overrideSlots": list(CORE_V2_OVERRIDE_SLOT_NAMES),
                "requiredNewSlots": list(CORE_V2_NEW_SLOT_NAMES),
                "requiredEditableSlots": list(CORE_V2_EDITABLE_SLOT_NAMES),
                "previewScenes": json.loads(
                    _canonical_json(extension.get("previewScenes", []))
                ),
                "assets": merged_assets,
            }
        )
        if not isinstance(merged.get("atlases"), list) or len(merged["atlases"]) != 1:
            raise AssetLabError("core-v2 base atlas is invalid", code="runtime_spec.invalid")
        merged["atlases"][0].update(
            {"id": CORE_V2_PACK_ID, "source": "core-v2.png"}
        )
        for sheet in merged.get("sheets", []):
            sheet["atlas"] = CORE_V2_PACK_ID
        for asset in merged["assets"]:
            asset["atlas"] = CORE_V2_PACK_ID
        return merged

    def _runtime_entries(
        self,
        selected: list[dict[str, Any]],
        runtime_spec: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected_by_slot = {entry["slot"]: entry for entry in selected}
        selected_slot_names = set(selected_by_slot)
        sources: dict[str, tuple[Image.Image, dict[str, Any]]] = {}
        for slot, entry in selected_by_slot.items():
            if entry["sha256"] is None or entry["metadata_json"] is None:
                raise AssetLabError("selected version has no blob metadata", code="storage.invalid")
            blob = self.blob_path(entry["sha256"])
            try:
                with Image.open(blob) as opened:
                    opened.load()
                    image = opened.convert("RGBA")
            except (OSError, UnidentifiedImageError) as exc:
                raise AssetLabError("selected blob cannot be decoded", code="storage.corrupt") from exc
            sources[slot] = (image, json.loads(entry["metadata_json"]))

        sheets = runtime_spec.get("sheets")
        if not isinstance(sheets, list) or len(sheets) != 1 or sheets[0].get("id") != "character.gus":
            raise AssetLabError(
                "runtime specification requires one character.gus sheet",
                code="runtime_spec.invalid",
            )
        gus_sheet = sheets[0]
        required_sheet_fields = ("columns", "rows", "cellWidth", "cellHeight")
        if any(not isinstance(gus_sheet.get(field), int) for field in required_sheet_fields):
            raise AssetLabError("runtime Gus sheet grid is invalid", code="runtime_spec.invalid")

        atlas_entries: list[dict[str, Any]] = []
        runtime_mappings: list[dict[str, Any]] = []
        owners_seen: set[str] = set()
        gus_sheet_added = False
        for template_asset in runtime_spec["assets"]:
            runtime_id = template_asset.get("id")
            runtime_slot = template_asset.get("slot")
            if runtime_slot in selected_slot_names and runtime_slot not in {
                "character.gus",
                "effect.good-card-heart",
            }:
                owner_slot = runtime_slot
                source_frame_index = 0
                if len(sources[owner_slot][1]["frames"]) != 1:
                    raise AssetLabError(
                        "static core assets must contain exactly one frame at activation",
                        code="pack.runtime_geometry_invalid",
                        details={"slot": owner_slot},
                    )
            elif isinstance(runtime_id, str) and runtime_id.startswith("character.gus."):
                if gus_sheet_added:
                    continue
                owner_slot = "character.gus"
                source_image, source_metadata = sources[owner_slot]
                character_assets = [
                    asset
                    for asset in runtime_spec["assets"]
                    if isinstance(asset.get("id"), str)
                    and asset["id"].startswith("character.gus.")
                ]
                expected_count = gus_sheet["columns"] * gus_sheet["rows"]
                if len(character_assets) != expected_count:
                    raise AssetLabError("runtime Gus sheet frame count is invalid", code="runtime_spec.invalid")
                sheet_image = Image.new(
                    "RGBA",
                    (
                        gus_sheet["columns"] * gus_sheet["cellWidth"],
                        gus_sheet["rows"] * gus_sheet["cellHeight"],
                    ),
                    (0, 0, 0, 0),
                )
                for sheet_index, character_asset in enumerate(character_assets):
                    character_id = character_asset["id"]
                    source_index = self._gus_source_frame_index(character_id, source_metadata)
                    source_frame = source_metadata["frames"][source_index]
                    expected_size = (
                        character_asset["frame"].get("width"),
                        character_asset["frame"].get("height"),
                    )
                    actual_size = (source_frame["width"], source_frame["height"])
                    if actual_size != expected_size:
                        raise AssetLabError(
                            "accepted Gus frame does not match the runtime geometry",
                            code="pack.runtime_geometry_invalid",
                            details={
                                "slot": owner_slot,
                                "runtimeAssetId": character_id,
                                "expectedSize": list(expected_size),
                                "actualSize": list(actual_size),
                            },
                        )
                    source_box = (
                        source_frame["x"],
                        source_frame["y"],
                        source_frame["x"] + source_frame["width"],
                        source_frame["y"] + source_frame["height"],
                    )
                    crop = source_image.crop(source_box)
                    if crop.getchannel("A").getextrema()[1] == 0:
                        raise AssetLabError(
                            "runtime frame has no visible pixels",
                            code="pack.runtime_geometry_invalid",
                            details={"slot": owner_slot, "runtimeAssetId": character_id},
                        )
                    local_frame = {
                        "x": (sheet_index % gus_sheet["columns"]) * gus_sheet["cellWidth"],
                        "y": (sheet_index // gus_sheet["columns"]) * gus_sheet["cellHeight"],
                        "width": gus_sheet["cellWidth"],
                        "height": gus_sheet["cellHeight"],
                    }
                    sheet_image.paste(crop, (local_frame["x"], local_frame["y"]))
                    runtime_mappings.append(
                        {
                            "id": character_id,
                            "slot": owner_slot,
                            "atlasEntryId": "sheet:character.gus",
                            "sourceFrameIndex": source_index,
                            "sourceFrame": source_frame,
                            "localFrame": local_frame,
                        }
                    )
                atlas_entries.append(
                    {
                        "id": "sheet:character.gus",
                        "slot": owner_slot,
                        "image": sheet_image,
                    }
                )
                owners_seen.add(owner_slot)
                gus_sheet_added = True
                continue
            elif isinstance(runtime_id, str) and runtime_id.startswith("effect.good-card-heart."):
                owner_slot = "effect.good-card-heart"
                frames = sources[owner_slot][1]["frames"]
                if len(frames) != 4:
                    raise AssetLabError(
                        "effect.good-card-heart requires four frames at activation",
                        code="pack.runtime_geometry_invalid",
                        details={"slot": owner_slot, "requiredFrames": 4, "actualFrames": len(frames)},
                    )
                try:
                    source_frame_index = int(runtime_id.rsplit(".", 1)[1])
                except (ValueError, IndexError) as exc:
                    raise AssetLabError(
                        "runtime heart frame id is invalid",
                        code="runtime_spec.invalid",
                    ) from exc
            else:
                raise AssetLabError(
                    "runtime asset cannot be mapped to a core-v0 slot",
                    code="runtime_spec.invalid",
                    details={"runtimeAssetId": runtime_id},
                )
            source_image, source_metadata = sources[owner_slot]
            frames = source_metadata["frames"]
            if not 0 <= source_frame_index < len(frames):
                raise AssetLabError(
                    "runtime frame points outside source metadata",
                    code="pack.runtime_geometry_invalid",
                    details={"slot": owner_slot, "runtimeAssetId": runtime_id},
                )
            source_frame = frames[source_frame_index]
            expected_frame = template_asset.get("frame", {})
            actual_size = (source_frame["width"], source_frame["height"])
            expected_size = (expected_frame.get("width"), expected_frame.get("height"))
            if actual_size != expected_size:
                raise AssetLabError(
                    "accepted source frame does not match the runtime geometry",
                    code="pack.runtime_geometry_invalid",
                    details={
                        "slot": owner_slot,
                        "runtimeAssetId": runtime_id,
                        "expectedSize": list(expected_size),
                        "actualSize": list(actual_size),
                    },
                )
            box = (
                source_frame["x"],
                source_frame["y"],
                source_frame["x"] + source_frame["width"],
                source_frame["y"] + source_frame["height"],
            )
            crop = source_image.crop(box)
            if crop.getchannel("A").getextrema()[1] == 0:
                raise AssetLabError(
                    "runtime frame has no visible pixels",
                    code="pack.runtime_geometry_invalid",
                    details={"slot": owner_slot, "runtimeAssetId": runtime_id},
                )
            atlas_entries.append(
                {
                    "id": runtime_id,
                    "slot": owner_slot,
                    "image": crop,
                }
            )
            runtime_mappings.append(
                {
                    "id": runtime_id,
                    "slot": owner_slot,
                    "atlasEntryId": runtime_id,
                    "sourceFrameIndex": source_frame_index,
                    "sourceFrame": source_frame,
                    "localFrame": {
                        "x": 0,
                        "y": 0,
                        "width": source_frame["width"],
                        "height": source_frame["height"],
                    },
                }
            )
            owners_seen.add(owner_slot)
        if owners_seen != selected_slot_names:
            raise AssetLabError(
                "runtime specification does not implement every selected pack slot",
                code="runtime_spec.invalid",
                details={"mappedSlots": sorted(owners_seen)},
            )
        return atlas_entries, runtime_mappings

    @staticmethod
    def _gus_source_frame_index(runtime_id: str, metadata: Mapping[str, Any]) -> int:
        parts = runtime_id.split(".")
        if len(parts) not in (4, 5) or parts[:2] != ["character", "gus"]:
            raise AssetLabError("runtime Gus frame id is invalid", code="runtime_spec.invalid")
        direction = parts[2]
        action = parts[3]
        try:
            # A bare `character.gus.<direction>.<action>` id means frame 0; any
            # other id must carry an explicit sequence number.  Never default a
            # suffixed id to 0 — that silently maps every frame of a multi-frame
            # action onto the first one instead of failing the build.
            sequence_index = int(parts[4]) if len(parts) == 5 else 0
            indexes = metadata["animations"][action][direction]
            if not 0 <= sequence_index < len(indexes):
                raise IndexError(sequence_index)
            return indexes[sequence_index]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AssetLabError(
                "Gus animation metadata cannot satisfy the runtime frame order",
                code="pack.runtime_geometry_invalid",
                details={"runtimeAssetId": runtime_id},
            ) from exc

    def _build_atlas(
        self, atlas_entries: list[dict[str, Any]]
    ) -> tuple[bytes, dict[str, dict[str, int]], int]:
        ordered_entries = sorted(
            atlas_entries,
            key=lambda entry: (
                -int(entry["image"].height),
                -int(entry["image"].width),
                str(entry["id"]),
            ),
        )
        chosen: tuple[int, dict[str, dict[str, int]]] | None = None
        for size in (ATLAS_INITIAL_SIZE, ATLAS_MAX_SIZE):
            layout = self._shelf_layout(ordered_entries, size)
            if layout is not None:
                chosen = (size, layout)
                break
        if chosen is None:
            raise AssetLabError(
                "accepted assets do not fit the maximum atlas",
                code="atlas.too_large",
                details={"maxSize": ATLAS_MAX_SIZE, "padding": ATLAS_PADDING},
            )
        size, layout = chosen
        atlas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        for entry in ordered_entries:
            rect = layout[entry["id"]]
            self._paste_extruded(atlas, entry["image"], rect["x"], rect["y"])
        output = io.BytesIO()
        atlas.save(output, format="PNG", optimize=False, compress_level=9)
        return output.getvalue(), layout, size

    @staticmethod
    def _shelf_layout(
        atlas_entries: list[dict[str, Any]], size: int
    ) -> dict[str, dict[str, int]] | None:
        x = 0
        y = 0
        row_height = 0
        layout: dict[str, dict[str, int]] = {}
        border = ATLAS_PADDING * 2
        for entry in atlas_entries:
            image = entry["image"]
            cell_width = image.width + border
            cell_height = image.height + border
            if cell_width > size or cell_height > size:
                return None
            if x and x + cell_width > size:
                x = 0
                y += row_height
                row_height = 0
            if y + cell_height > size:
                return None
            layout[entry["id"]] = {
                "x": x + ATLAS_PADDING,
                "y": y + ATLAS_PADDING,
                "width": image.width,
                "height": image.height,
            }
            x += cell_width
            row_height = max(row_height, cell_height)
        return layout

    @staticmethod
    def _paste_extruded(atlas: Image.Image, image: Image.Image, x: int, y: int) -> None:
        padding = ATLAS_PADDING
        width, height = image.size
        atlas.paste(image, (x, y))
        atlas.paste(image.crop((0, 0, width, 1)).resize((width, padding)), (x, y - padding))
        atlas.paste(
            image.crop((0, height - 1, width, height)).resize((width, padding)),
            (x, y + height),
        )
        atlas.paste(image.crop((0, 0, 1, height)).resize((padding, height)), (x - padding, y))
        atlas.paste(
            image.crop((width - 1, 0, width, height)).resize((padding, height)),
            (x + width, y),
        )
        atlas.paste(image.getpixel((0, 0)), (x - padding, y - padding, x, y))
        atlas.paste(image.getpixel((width - 1, 0)), (x + width, y - padding, x + width + padding, y))
        atlas.paste(
            image.getpixel((0, height - 1)),
            (x - padding, y + height, x, y + height + padding),
        )
        atlas.paste(
            image.getpixel((width - 1, height - 1)),
            (x + width, y + height, x + width + padding, y + height + padding),
        )

    def _build_manifest(
        self,
        selected: list[dict[str, Any]],
        runtime_spec: Mapping[str, Any],
        runtime_mappings: list[dict[str, Any]],
        layout: dict[str, dict[str, int]],
        atlas_sha: str,
        atlas_size: int,
        revision: int,
        activated_at: str,
        pack_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = json.loads(_canonical_json(runtime_spec))
        character_entry = next(
            (entry for entry in selected if entry["slot"] == "character.gus"),
            None,
        )
        character_metadata = (
            json.loads(character_entry["metadata_json"])
            if character_entry is not None and character_entry["metadata_json"] is not None
            else {}
        )
        character_consistency = character_metadata.get("characterConsistency")
        character_motion_build = character_metadata.get("motionBuild")
        character_motion = manifest.get("characterMotion")
        if not isinstance(character_motion, dict):
            raise AssetLabError(
                "runtime character motion policy is missing",
                code="runtime_spec.invalid",
            )
        character_motion["identityLocked"] = bool(
            isinstance(character_consistency, Mapping)
            and character_consistency.get("ok") is True
            and isinstance(character_motion_build, Mapping)
            and character_motion_build.get("verified") is True
        )
        if character_motion["identityLocked"]:
            # Deterministic releases describe their positive proof only.  The
            # compatibility marker remains available solely to immutable
            # releases whose inherited Gus predates the motion-build policy.
            character_motion.pop("trustedLegacyAcceptedMotion", None)
        else:
            character_motion["trustedLegacyAcceptedMotion"] = (
                self._trusted_legacy_accepted_motion(
                    character_entry,
                    pack_id=pack_id,
                    base_release_id=manifest.get("baseReleaseId"),
                    identity_locked=False,
                )
            )
        manifest["atlases"][0].update(
            {
                "source": f"/api/assets/derived/{atlas_sha}.png",
                "width": atlas_size,
                "height": atlas_size,
                "padding": ATLAS_PADDING,
            }
        )
        mapping_by_id = {entry["id"]: entry for entry in runtime_mappings}
        sheet_by_id = {sheet["id"]: sheet for sheet in manifest.get("sheets", [])}
        if "character.gus" not in sheet_by_id:
            raise AssetLabError("runtime Gus sheet is missing", code="runtime_spec.invalid")
        sheet_by_id["character.gus"]["frame"] = layout["sheet:character.gus"]
        sheet_by_id["character.gus"]["atlas"] = manifest["atlases"][0]["id"]
        for template_asset in manifest["assets"]:
            template_asset["atlas"] = manifest["atlases"][0]["id"]
            mapping = mapping_by_id[template_asset["id"]]
            atlas_region = layout[mapping["atlasEntryId"]]
            template_asset["frame"] = {
                "x": atlas_region["x"] + mapping["localFrame"]["x"],
                "y": atlas_region["y"] + mapping["localFrame"]["y"],
                "width": mapping["localFrame"]["width"],
                "height": mapping["localFrame"]["height"],
            }

        runtime_by_slot: dict[str, list[dict[str, Any]]] = {
            entry["slot"]: [] for entry in selected
        }
        for entry in runtime_mappings:
            atlas_region = layout[entry["atlasEntryId"]]
            runtime_by_slot[entry["slot"]].append(
                {
                    "id": entry["id"],
                    "sourceFrameIndex": entry["sourceFrameIndex"],
                    "sourceFrame": entry["sourceFrame"],
                    "atlasFrame": {
                        "x": atlas_region["x"] + entry["localFrame"]["x"],
                        "y": atlas_region["y"] + entry["localFrame"]["y"],
                        "width": entry["localFrame"]["width"],
                        "height": entry["localFrame"]["height"],
                    },
                }
            )
        catalog_assets = []
        for entry in selected:
            catalog_assets.append(
                {
                    "slot": entry["slot"],
                    "assetId": entry["asset_id"],
                    "versionId": entry["version_id"],
                    "versionNumber": entry["version_number"],
                    "sha256": entry["sha256"],
                    "width": entry["width"],
                    "height": entry["height"],
                    "inherited": bool(entry.get("inherited")),
                    "sourceReleaseId": entry.get("source_release_id"),
                    "runtimeAssets": runtime_by_slot[entry["slot"]],
                }
            )
        catalog = {
            "schemaVersion": SCHEMA_VERSION,
            "revision": revision,
            "styleProfileId": STYLE_PROFILE_ID,
            "packId": pack_id,
            "baseReleaseId": manifest.get("baseReleaseId"),
            "activatedAt": activated_at,
            "atlasSha256": atlas_sha,
            "assets": catalog_assets,
        }
        return manifest, catalog

    @staticmethod
    def _trusted_legacy_accepted_motion(
        character_entry: Mapping[str, Any] | None,
        *,
        pack_id: str,
        base_release_id: object,
        identity_locked: bool,
    ) -> bool:
        """Attest only the frozen, previously accepted core-v0 Gus inheritance.

        Older accepted releases predate ``characterConsistency``.  Extension
        packs are allowed to preserve those already-reviewed motion frames, but the
        exception must never apply to core-v0, new character imports, drafts,
        or an inherited member whose frozen source release does not match the
        pack's immutable base release.
        """

        if identity_locked or character_entry is None:
            return False
        return bool(
            pack_id in {CORE_V1_PACK_ID, CORE_V2_PACK_ID}
            and isinstance(base_release_id, str)
            and base_release_id
            and character_entry.get("inherited")
            and character_entry.get("source_release_id") == base_release_id
            and character_entry.get("source_status") in {"accepted", "superseded"}
        )

    # ------------------------------------------------------------------
    # Response mappers

    def _style_payload(
        self,
        conn: sqlite3.Connection,
        style_profile_id: str = STYLE_PROFILE_ID,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT spec_json FROM style_profiles WHERE id = ?",
            (style_profile_id,),
        ).fetchone()
        if row is None:
            raise AssetLabError("style profile not found", code="style.not_found")
        return json.loads(row["spec_json"])

    def _filter_payload(self, conn: sqlite3.Connection) -> dict[str, Any]:
        packs = [
            {"id": str(row["id"]), "name": str(row["name"])}
            for row in conn.execute("SELECT id, name FROM packs ORDER BY created_at, id")
        ]
        slots_by_pack = {
            pack_id: [entry["slot"] for entry in LOGICAL_SLOTS_BY_PACK[pack_id]]
            for pack_id in SLOTS_BY_PACK
            if any(pack["id"] == pack_id for pack in packs)
        }
        # The filter vocabulary the review screen builds its two dropdowns from. The pack
        # list itself is a sibling key on the bootstrap payload, not a filter.
        return {
            "slotsByPack": slots_by_pack,
            "kinds": sorted(
                {
                    entry["kind"]
                    for pack_id in slots_by_pack
                    for entry in LOGICAL_SLOTS_BY_PACK[pack_id]
                }
            ),
            "statuses": ["draft", "accepted", "rejected", "superseded"],
        }

    def _pack_payload(self, conn: sqlite3.Connection, pack_id: str) -> dict[str, Any]:
        pack = conn.execute("SELECT * FROM packs WHERE id = ?", (pack_id,)).fetchone()
        if pack is None:
            raise AssetLabError("pack not found", code="pack.not_found")
        selected = self._selected_versions(conn, pack_id)
        slots = [
            {
                "slot": entry["slot"],
                "assetId": entry["asset_id"],
                "ownerPackId": entry["owner_pack_id"],
                "kind": entry["kind"],
                "displayName": entry["display_name"],
                "required": bool(entry["required"]),
                "selectedVersionId": entry["version_id"],
                "selectedStatus": entry["status"],
                "inherited": bool(entry["inherited"]),
                "overridable": bool(
                    entry["slot"]
                    in OVERRIDABLE_INHERITED_SLOTS_BY_PACK.get(pack_id, ())
                ),
                "overrideRequired": bool(
                    entry["slot"] in PACK_OVERRIDE_SLOT_NAMES.get(pack_id, ())
                    and entry["slot"]
                    in REQUIRED_EDITABLE_SLOT_NAMES_BY_PACK.get(pack_id, ())
                ),
                "editable": entry["slot"] in EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id],
                "sourceReleaseId": entry["source_release_id"],
            }
            for entry in selected
        ]
        missing = [entry["slot"] for entry in selected if entry["version_id"] is None]
        invalid = [
            entry["slot"]
            for entry in selected
            if entry["version_id"] is not None and entry["status"] != "accepted"
        ]
        manifest = json.loads(pack["manifest_json"]) if pack["manifest_json"] else None
        activation_catalog = json.loads(pack["catalog_json"]) if pack["catalog_json"] else None
        selected_ids = [entry["version_id"] for entry in selected]
        active_ids = (
            [entry["versionId"] for entry in activation_catalog.get("assets", [])]
            if activation_catalog
            else []
        )
        active_release = (
            self._release_payload_by_id(conn, str(pack["active_release_id"]))
            if pack["active_release_id"]
            else None
        )
        return {
            "id": pack["id"],
            "styleProfileId": pack["style_profile_id"],
            "styleProfile": self._style_payload(conn, str(pack["style_profile_id"])),
            "baseReleaseId": pack["base_release_id"],
            "name": pack["name"],
            "status": pack["status"],
            "revision": pack["revision"],
            "atlasSha256": pack["atlas_sha256"],
            "activatedAt": pack["activated_at"],
            "activeRelease": active_release,
            "spec": self._pack_spec_payload(pack_id),
            "previewScenes": self._preview_scene_payloads(pack_id),
            "slots": slots,
            "requiredSlotCount": len(slots),
            "missingSlots": missing,
            "invalidSlots": invalid,
            "activation": {
                "enabled": not missing and not invalid,
                "active": manifest is not None and pack["status"] == "active",
                "hasPendingChanges": bool(manifest) and selected_ids != active_ids,
                "missingSlots": missing,
                "invalidSlots": invalid,
            },
        }

    def _pack_spec_payload(self, pack_id: str) -> dict[str, Any]:
        try:
            spec = json.loads(PACK_SPEC_PATHS[pack_id].read_text(encoding="utf-8"))
        except (KeyError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AssetLabError(
                "runtime asset specification cannot be read",
                code="runtime_spec.invalid",
            ) from exc
        return {
            "id": pack_id,
            "name": str(spec.get("name", pack_id)),
            "basePackId": spec.get("basePackId"),
            "geometryVersion": int(spec.get("geometryVersion", 0)),
            "nativeFrameRequired": bool(spec.get("nativeFrameRequired", False)),
            "alphaLevels": list(spec.get("alphaLevels", [0, 255])),
            "requiredSlots": [
                entry["slot"] for entry in LOGICAL_SLOTS_BY_PACK[pack_id]
            ],
            "editableSlots": list(EDITABLE_SLOT_LOOKUP_BY_PACK[pack_id]),
            "overrideSlots": (
                list(PACK_OVERRIDE_SLOT_NAMES[pack_id])
            ),
            "newSlots": list(PACK_NEW_SLOT_NAMES[pack_id]),
            "worldPalette": list(PACK_WORLD_PALETTES[pack_id]),
            "playerAccents": list(PACK_PLAYER_ACCENTS[pack_id]),
        }

    def _preview_scene_payloads(self, pack_id: str) -> list[dict[str, Any]]:
        scenes = PACK_SPECS[pack_id].get("previewScenes", [])
        payloads: list[dict[str, Any]] = []
        for scene in scenes:
            base = {
                "id": str(scene["id"]),
                "label": str(scene["label"]),
                "layoutId": str(scene["layoutId"]),
                "status": "pending",
                "blobUrl": None,
                "width": None,
                "height": None,
                "sha256": None,
            }
            source_name = str(scene.get("sourceName", ""))
            if not self._is_safe_preview_basename(source_name):
                payloads.append({**base, "status": "invalid"})
                continue
            source_path, source_status = self._preview_scene_source_path(
                pack_id, source_name
            )
            if source_status == "invalid":
                payloads.append({**base, "status": "invalid"})
                continue
            if source_path is None:
                payloads.append(base)
                continue
            try:
                data = source_path.read_bytes()
                with Image.open(io.BytesIO(data)) as opened:
                    if opened.format != "PNG":
                        raise OSError("not png")
                    width, height = opened.size
                    opened.verify()
                sha = hashlib.sha256(data).hexdigest()
                self._write_content_addressed(self.derived_dir, sha, ".png", data)
            except (OSError, UnidentifiedImageError):
                payloads.append({**base, "status": "invalid"})
                continue
            payloads.append(
                {
                    **base,
                    "status": "ready",
                    "blobUrl": f"/api/assets/derived/{sha}.png",
                    "width": width,
                    "height": height,
                    "sha256": sha,
                }
            )
        return payloads

    @staticmethod
    def _is_safe_preview_basename(source_name: str) -> bool:
        """Accept one PNG basename, never a path on POSIX or Windows."""

        return bool(
            source_name
            and "\x00" not in source_name
            and "/" not in source_name
            and "\\" not in source_name
            and source_name not in {".", ".."}
            and Path(source_name).name == source_name
            and Path(source_name).suffix == ".png"
        )

    def _preview_scene_source_path(
        self, pack_id: str, source_name: str
    ) -> tuple[Path | None, str]:
        """Resolve a QA preview without allowing a pack or source path escape.

        Geometry-v2 and newer packs use the producer's pack-scoped output
        directory exclusively.  Older packs may still read the historical
        derived-root location when no scoped file exists.
        """

        derived_root = self.derived_dir.resolve()
        pack_root = self.derived_dir / pack_id
        try:
            resolved_pack_root = pack_root.resolve()
            resolved_pack_root.relative_to(derived_root)
        except (OSError, RuntimeError, ValueError):
            return None, "invalid"
        if pack_root.is_symlink():
            return None, "invalid"

        candidates = [pack_root / source_name]
        geometry_version = int(PACK_SPECS[pack_id].get("geometryVersion", 0))
        if geometry_version < 2:
            candidates.append(self.derived_dir / source_name)

        for index, candidate in enumerate(candidates):
            try:
                resolved_candidate = candidate.resolve()
                resolved_candidate.relative_to(derived_root)
            except (OSError, RuntimeError, ValueError):
                return None, "invalid"
            if candidate.is_symlink():
                return None, "invalid"
            if candidate.is_file():
                return candidate, "ready"
            if candidate.exists():
                return None, "invalid"
            # A legacy root fallback is only considered when the scoped source
            # is genuinely absent, never when it is malformed.
            if index == 0:
                continue
        return None, "pending"

    def _release_payload_by_id(
        self, conn: sqlite3.Connection, release_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM pack_releases WHERE id = ?", (release_id,)
        ).fetchone()
        if row is None:
            raise AssetLabError("pack release not found", code="release.not_found")
        return self._release_payload(row)

    @staticmethod
    def _release_payload(row: sqlite3.Row) -> dict[str, Any]:
        manifest_sha = str(row["manifest_sha256"])
        atlas_sha = str(row["atlas_sha256"])
        return {
            "id": str(row["id"]),
            "packId": str(row["pack_id"]),
            "catalogRevision": int(row["catalog_revision"]),
            "manifestSha256": manifest_sha,
            "manifestUrl": f"/api/assets/manifests/{manifest_sha}",
            "atlasSha256": atlas_sha,
            "atlasUrl": f"/api/assets/derived/{atlas_sha}.png",
            "createdAt": str(row["created_at"]),
        }

    def _asset_payload(
        self,
        conn: sqlite3.Connection,
        asset_id: str,
        pack_id: str | None = None,
    ) -> dict[str, Any]:
        selected_pack_id = pack_id
        if selected_pack_id is None:
            owner = conn.execute(
                "SELECT pack_id FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
            selected_pack_id = str(owner["pack_id"]) if owner is not None else ""
        row = conn.execute(
            """
            SELECT a.*, pm.pack_id AS member_pack_id,
                   pm.version_id AS selected_version_id,
                   pm.inherited, pm.source_release_id
              FROM assets a
              JOIN pack_members pm ON pm.asset_id = a.id
             WHERE a.id = ? AND pm.pack_id = ?
            """,
            (asset_id, selected_pack_id),
        ).fetchone()
        if row is None:
            raise AssetLabError("asset not found", code="asset.not_found")
        versions = conn.execute(
            "SELECT * FROM versions WHERE asset_id = ? ORDER BY version_number DESC",
            (asset_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "packId": row["member_pack_id"],
            "ownerPackId": row["pack_id"],
            "inherited": bool(row["inherited"]),
            "overridable": bool(
                row["slot"]
                in OVERRIDABLE_INHERITED_SLOTS_BY_PACK.get(row["member_pack_id"], ())
            ),
            "overrideRequired": bool(
                row["slot"]
                in PACK_OVERRIDE_SLOT_NAMES.get(row["member_pack_id"], ())
                and row["slot"]
                in REQUIRED_EDITABLE_SLOT_NAMES_BY_PACK.get(
                    row["member_pack_id"], ()
                )
            ),
            "editable": row["slot"]
            in EDITABLE_SLOT_LOOKUP_BY_PACK[row["member_pack_id"]],
            "sourceReleaseId": row["source_release_id"],
            "slot": row["slot"],
            "kind": row["kind"],
            "displayName": row["display_name"],
            "revision": row["revision"],
            "selectedVersionId": row["selected_version_id"],
            "versions": [
                self._version_payload(version, row["selected_version_id"]) for version in versions
            ],
        }

    def _version_payload_by_id(self, conn: sqlite3.Connection, version_id: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT v.*, pm.version_id AS selected_version_id
              FROM versions v
              JOIN assets a ON a.id = v.asset_id
              JOIN pack_members pm ON pm.asset_id = a.id AND pm.pack_id = a.pack_id
             WHERE v.id = ?
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            raise AssetLabError("version not found", code="version.not_found")
        return self._version_payload(row, row["selected_version_id"])

    @staticmethod
    def _version_payload(row: sqlite3.Row, selected_version_id: str | None) -> dict[str, Any]:
        return {
            "id": row["id"],
            "assetId": row["asset_id"],
            "number": row["version_number"],
            "status": row["status"],
            "selected": row["id"] == selected_version_id,
            "sha256": row["sha256"],
            "blobUrl": f"/api/assets/blobs/{row['sha256']}",
            "width": row["width"],
            "height": row["height"],
            "sizeBytes": row["size_bytes"],
            "metadata": json.loads(row["metadata_json"]),
            "warnings": json.loads(row["warnings_json"]),
            "createdAt": row["created_at"],
            "reviewedAt": row["reviewed_at"],
        }

    def _refresh_pack_readiness(self, conn: sqlite3.Connection, pack_id: str, now: str) -> None:
        pack = conn.execute("SELECT status, manifest_json FROM packs WHERE id = ?", (pack_id,)).fetchone()
        if pack is None or pack["status"] == "active":
            return
        rows = self._selected_versions(conn, pack_id)
        ready = all(row["version_id"] is not None and row["status"] == "accepted" for row in rows)
        conn.execute(
            "UPDATE packs SET status = ?, updated_at = ? WHERE id = ?",
            ("ready" if ready else "draft", now, pack_id),
        )


__all__ = [
    "ATLAS_INITIAL_SIZE",
    "ATLAS_MAX_SIZE",
    "ATLAS_PADDING",
    "AssetLab",
    "AssetLabError",
    "CORE_SLOTS",
    "CORE_V1_NEW_SLOTS",
    "CORE_V1_PACK_ID",
    "CORE_V1_PACK_SPEC_PATH",
    "CORE_V2_EDITABLE_SLOT_NAMES",
    "CORE_V2_EDITABLE_SLOTS",
    "CORE_V2_ALPHA_LEVELS",
    "CORE_V2_INHERITED_SLOT_NAMES",
    "CORE_V2_NEW_SLOT_NAMES",
    "CORE_V2_NEW_SLOTS",
    "CORE_V2_OVERRIDE_SLOT_NAMES",
    "CORE_V2_OVERRIDE_SLOTS",
    "CORE_V2_PACK_ID",
    "CORE_V2_PACK_SPEC_PATH",
    "CORE_V2_PLAYER_ACCENTS",
    "CORE_V2_REQUIRED_SLOT_NAMES",
    "CORE_V2_STYLE_PROFILE_ID",
    "CORE_V2_WORLD_PALETTE",
    "EDITABLE_SLOT_LOOKUP_BY_PACK",
    "MAX_IMAGE_DIMENSION",
    "MAX_INPUT_BYTES",
    "MAX_REVIEW_BATCH_ITEMS",
    "PACK_ID",
    "OVERRIDABLE_INHERITED_SLOTS_BY_PACK",
    "PLAYER_ACCENTS",
    "STYLE_PROFILE_ID",
    "WORLD_PALETTE",
]
