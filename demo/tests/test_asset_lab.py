from __future__ import annotations

import io
import hashlib
import json
import sqlite3
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from codex_v0.character_motion import (
    FRAME_COUNT as CHARACTER_FRAME_COUNT,
    SHEET_COLUMNS,
    SHEET_SIZE as GUS_SHEET_SIZE,
)
from codex_v0.asset_lab import (
    AssetLab,
    AssetLabError,
    CORE_PACK_SPEC_PATH,
    CORE_SLOTS,
    CORE_V1_NEW_SLOTS,
    CORE_V1_PACK_ID,
    CORE_V1_PACK_SPEC_PATH,
    CORE_V2_EDITABLE_SLOT_NAMES,
    CORE_V2_INHERITED_SLOT_NAMES,
    CORE_V2_NEW_SLOT_NAMES,
    CORE_V2_OVERRIDE_SLOT_NAMES,
    CORE_V2_PACK_ID,
    CORE_V2_PACK_SPEC_PATH,
    CORE_V2_REQUIRED_SLOT_NAMES,
    CORE_V2_STYLE_PROFILE_ID,
    CORE_V2_WORLD_PALETTE,
    MAX_INPUT_BYTES,
    MAX_REVIEW_BATCH_ITEMS,
    PACK_ID,
    PLAYER_ACCENTS,
    STYLE_PROFILE_ID,
    WORLD_PALETTE,
)
from codex_v0.asset_normalize import slot_metadata
from codex_v0.asset_qa import CoreV2AssetQa
from codex_v0.character_motion import compile_character_motion


def png_bytes(
    size: tuple[int, int] = (8, 8),
    color: tuple[int, int, int, int] = (13, 34, 40, 255),
    *,
    compress_level: int = 6,
    transparent_corner: bool = True,
) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGBA", size, color)
    if transparent_corner:
        image.putpixel((size[0] - 1, size[1] - 1), (0, 0, 0, 0))
    image.save(output, format="PNG", compress_level=compress_level)
    return output.getvalue()


def character_sheet_bytes(*, drifting_frame: int | None = None) -> bytes:
    compiled = canonical_character()
    if drifting_frame is None:
        return compiled.png_bytes
    output = io.BytesIO()
    sheet = compiled.image.copy()
    origin_x = (drifting_frame % SHEET_COLUMNS) * 24
    origin_y = (drifting_frame // SHEET_COLUMNS) * 48
    for y in range(origin_y + 9, origin_y + 27):
        for x in range(origin_x + 3, origin_x + 22):
            sheet.putpixel((x, y), _hex_rgba(WORLD_PALETTE[10]))
    sheet.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


@lru_cache(maxsize=1)
def canonical_character():
    return compile_character_motion()


def character_metadata() -> dict[str, object]:
    metadata = deepcopy(canonical_character().metadata)
    metadata["slot"] = "character.gus"
    return metadata


def import_and_accept(
    lab: AssetLab,
    slot: str,
    revision: int,
    *,
    color_index: int,
) -> tuple[int, dict[str, object]]:
    if slot == "character.gus":
        metadata = character_metadata()
        data = canonical_character().png_bytes
    elif slot == "effect.good-card-heart":
        metadata = {
            "slot": slot,
            "frameWidth": 24,
            "frameHeight": 24,
            "columns": 4,
            "frameCount": 4,
        }
        data = png_bytes((96, 24), _hex_rgba(WORLD_PALETTE[color_index]))
    else:
        metadata = {"slot": slot, "anchor": {"x": 0, "y": 0}}
        runtime_spec = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
        template = next(asset for asset in runtime_spec["assets"] if asset["slot"] == slot)
        data = png_bytes(
            (template["frame"]["width"], template["frame"]["height"]),
            _hex_rgba(WORLD_PALETTE[color_index]),
        )
    imported = lab.import_png(data, metadata)
    assert imported["revision"] == revision + 1
    reviewed = lab.review(
        imported["asset"]["id"],
        imported["version"]["id"],
        "accepted",
        "",
        imported["revision"],
    )
    return reviewed["revision"], imported


def _hex_rgba(value: str) -> tuple[int, int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5)) + (255,)


@pytest.fixture
def lab(tmp_path: Path) -> AssetLab:
    return AssetLab(tmp_path / "data")


def test_bootstrap_seeds_schema_slots_and_runtime_palette_without_images(lab: AssetLab) -> None:
    result = lab.bootstrap()

    assert result["schemaVersion"] == 1
    assert result["revision"] == 0
    assert result["styleProfile"]["id"] == STYLE_PROFILE_ID
    assert result["styleProfile"]["worldPalette"] == list(WORLD_PALETTE)
    assert result["styleProfile"]["playerAccents"] == list(PLAYER_ACCENTS)
    assert len(result["styleProfile"]["worldPalette"]) == 32
    assert len(result["styleProfile"]["playerAccents"]) == 8
    runtime_spec = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    assert result["styleProfile"]["worldPalette"] == runtime_spec["palette"]["world"]
    assert result["styleProfile"]["playerAccents"] == runtime_spec["palette"]["players"]
    assert result["pack"]["id"] == PACK_ID
    assert [entry["slot"] for entry in result["pack"]["slots"]] == [
        entry["slot"] for entry in CORE_SLOTS
    ]
    assert "furniture.office-plant" not in result["pack"]["missingSlots"]
    assert result["pack"]["missingSlots"] == [entry["slot"] for entry in CORE_SLOTS]
    assert result["pack"]["activation"]["enabled"] is False
    assert "csrfToken" not in result
    assert list(lab.blobs_dir.rglob("*.png")) == []
    assert list(lab.derived_dir.rglob("*.png")) == []

    with sqlite3.connect(lab.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "style_profiles",
            "jobs",
            "assets",
            "versions",
            "reviews",
            "packs",
            "pack_members",
            "pack_releases",
            "settings",
        }.issubset(tables)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 0


def test_import_normalizes_rgba_and_deduplicates_canonical_content(lab: AssetLab) -> None:
    first_source = png_bytes((3, 2), (17, 34, 51, 255), compress_level=0)
    second_source = png_bytes((3, 2), (17, 34, 51, 255), compress_level=9)

    first = lab.import_png(first_source, {"slot": "furniture.desk-island"})
    second = lab.import_png(second_source, {"assetId": "furniture.desk-island"})

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert first["revision"] == second["revision"] == 1
    assert first["version"]["id"] == second["version"]["id"]
    assert first["version"]["status"] == "draft"
    assert first["version"]["warnings"][0]["code"] == "palette.outside_world_palette"
    blob = lab.blob_path(first["version"]["sha256"])
    with Image.open(blob) as image:
        assert image.mode == "RGBA"
        assert image.size == (3, 2)
    assert len(list(lab.blobs_dir.rglob("*.png"))) == 1
    # The catalog is unfiltered: it returns every declared slot, and dedup left exactly one
    # of them carrying exactly one draft.
    catalog = lab.catalog()
    assert set(catalog) == {"revision", "assets"}
    desk = next(asset for asset in catalog["assets"] if asset["slot"] == "furniture.desk-island")
    assert [version["status"] for version in desk["versions"]] == ["draft"]
    assert sum(1 for asset in catalog["assets"] if asset["versions"]) == 1


@pytest.mark.parametrize(
    ("data", "metadata", "code"),
    [
        (b"not a png", {"slot": "furniture.desk-island"}, "image.decode_failed"),
        (b"x" * (MAX_INPUT_BYTES + 1), {"slot": "furniture.desk-island"}, "image.too_large"),
        (png_bytes(), {"slot": "furniture.unknown"}, "slot.invalid"),
    ],
)
def test_import_rejects_invalid_input(
    lab: AssetLab,
    data: bytes,
    metadata: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(AssetLabError) as caught:
        lab.import_png(data, metadata)
    assert caught.value.code == code
    assert lab.bootstrap()["revision"] == 0


def test_import_rejects_dimensions_over_4096(lab: AssetLab) -> None:
    oversized = png_bytes((4097, 1))
    with pytest.raises(AssetLabError) as caught:
        lab.import_png(oversized, {"slot": "floor.raw-concrete"})
    assert caught.value.code == "image.dimensions_invalid"


@pytest.mark.parametrize(
    "data",
    [
        png_bytes(transparent_corner=False),
        png_bytes(color=(0, 0, 0, 0)),
    ],
)
def test_import_requires_transparent_and_visible_pixels(lab: AssetLab, data: bytes) -> None:
    with pytest.raises(AssetLabError) as caught:
        lab.import_png(data, {"slot": "floor.raw-concrete"})
    assert caught.value.code == "image.transparency_required"
    assert lab.bootstrap()["revision"] == 0


def test_character_requires_every_frame_and_all_twelve_directional_groups(lab: AssetLab) -> None:
    invalid = character_metadata()
    invalid["frameCount"] = CHARACTER_FRAME_COUNT - len(invalid["directionRows"])
    with pytest.raises(AssetLabError) as caught:
        lab.import_png(png_bytes(GUS_SHEET_SIZE), invalid)
    assert caught.value.code == "character.frames_invalid"

    invalid = character_metadata()
    invalid["animations"] = {
        **invalid["animations"],
        "work": {
            "southeast": [5, 6],
            "southwest": [12, 13],
            "northwest": [19, 20],
        },
    }
    with pytest.raises(AssetLabError) as caught:
        lab.import_png(png_bytes(GUS_SHEET_SIZE), invalid)
    assert caught.value.code == "character.animations_invalid"

    imported = lab.import_png(png_bytes(GUS_SHEET_SIZE), character_metadata())
    assert len(imported["version"]["metadata"]["frames"]) == CHARACTER_FRAME_COUNT
    assert set(imported["version"]["metadata"]["animations"]) == {"idle", "walk", "work"}
    assert set(imported["version"]["metadata"]["animations"]["walk"]) == {
        "southeast",
        "southwest",
        "northwest",
        "northeast",
    }
    accented = lab.import_png(
        png_bytes(GUS_SHEET_SIZE, _hex_rgba(PLAYER_ACCENTS[0])),
        character_metadata(),
    )
    warning_codes = [warning["code"] for warning in accented["version"]["warnings"]]
    assert "palette.outside_world_palette" not in warning_codes
    assert "character.motion_build_unverified" in warning_codes


def test_character_derived_consistency_does_not_create_duplicate_version(
    lab: AssetLab,
) -> None:
    data = character_sheet_bytes()
    first = lab.import_png(data, character_metadata())
    derived_metadata = first["version"]["metadata"]
    historical_fingerprint = hashlib.sha256(
        json.dumps(
            derived_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with lab._transaction() as connection:
        connection.execute(
            "UPDATE versions SET metadata_fingerprint = ? WHERE id = ?",
            (historical_fingerprint, first["version"]["id"]),
        )

    repeated = lab.import_png(data, character_metadata())
    assert repeated["deduplicated"] is True
    assert repeated["version"]["id"] == first["version"]["id"]
    assert repeated["revision"] == first["revision"]
    with sqlite3.connect(lab.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 1


def test_character_import_recomputes_motion_proof_and_blocks_tampered_pixels(
    lab: AssetLab,
) -> None:
    stable = lab.import_png(character_sheet_bytes(), character_metadata())
    assert stable["version"]["metadata"]["motionBuild"]["verified"] is True
    assert stable["version"]["metadata"]["characterConsistency"]["ok"] is True
    accepted = lab.review(
        stable["asset"]["id"],
        stable["version"]["id"],
        "accepted",
        "identity locked",
        stable["revision"],
    )
    assert accepted["version"]["status"] == "accepted"

    drifting = lab.import_png(
        character_sheet_bytes(drifting_frame=2),
        {
            **character_metadata(),
            "motionBuild": {"policy": "forged", "verified": True},
            "characterConsistency": {"ok": True},
        },
    )
    assert drifting["version"]["metadata"]["motionBuild"]["verified"] is False
    report = drifting["version"]["metadata"]["characterConsistency"]
    assert report["ok"] is False
    assert report["summary"]["failedFrames"] == 1
    assert [warning["code"] for warning in drifting["version"]["warnings"]] == [
        "character.motion_build_unverified",
        "character.identity_drift"
    ]
    with pytest.raises(AssetLabError) as caught:
        lab.review(
            drifting["asset"]["id"],
            drifting["version"]["id"],
            "accepted",
            "",
            drifting["revision"],
        )
    assert caught.value.code == "review.character_motion_unverified"
    assert caught.value.details["policy"] == "deterministic-pixel-rig-v1"


def test_review_uses_global_cas_and_supersedes_previous_acceptance(lab: AssetLab) -> None:
    first = lab.import_png(png_bytes(color=(13, 34, 40, 255)), {"slot": "furniture.desk-island"})
    with pytest.raises(AssetLabError) as caught:
        lab.review(first["asset"]["id"], first["version"]["id"], "accepted", "", 0)
    assert caught.value.code == "revision.conflict"
    assert caught.value.details == {"expectedRevision": 0, "actualRevision": 1}

    accepted = lab.review(
        first["asset"]["id"], first["version"]["id"], "accept", "looks good", 1
    )
    assert accepted["revision"] == 2
    assert accepted["version"]["status"] == "accepted"
    assert accepted["version"]["selected"] is True

    second = lab.import_png(png_bytes(color=(23, 52, 58, 255)), {"slot": "furniture.desk-island"})
    accepted_second = lab.review(
        second["asset"]["id"], second["version"]["id"], "accepted", "", second["revision"]
    )
    statuses = {version["id"]: version["status"] for version in accepted_second["asset"]["versions"]}
    assert statuses[first["version"]["id"]] == "superseded"
    assert statuses[second["version"]["id"]] == "accepted"

    third = lab.import_png(png_bytes(color=(49, 88, 79, 255)), {"slot": "furniture.desk-island"})
    with pytest.raises(AssetLabError) as caught:
        lab.review(third["asset"]["id"], third["version"]["id"], "rejected", "", third["revision"])
    assert caught.value.code == "review.note_required"
    rejected = lab.review(
        third["asset"]["id"],
        third["version"]["id"],
        "reject",
        "silhouette is unclear",
        third["revision"],
    )
    assert rejected["version"]["status"] == "rejected"
    assert rejected["asset"]["selectedVersionId"] == second["version"]["id"]


def _import_draft(lab: AssetLab, slot: str, *, color_index: int) -> dict[str, object]:
    """The import half of ``import_and_accept``, leaving the version as a draft."""

    if slot == "character.gus":
        return lab.import_png(canonical_character().png_bytes, character_metadata())
    if slot == "effect.good-card-heart":
        return lab.import_png(
            png_bytes((96, 24), _hex_rgba(WORLD_PALETTE[color_index])),
            {
                "slot": slot,
                "frameWidth": 24,
                "frameHeight": 24,
                "columns": 4,
                "frameCount": 4,
            },
        )
    runtime_spec = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    template = next(asset for asset in runtime_spec["assets"] if asset["slot"] == slot)
    return lab.import_png(
        png_bytes(
            (template["frame"]["width"], template["frame"]["height"]),
            _hex_rgba(WORLD_PALETTE[color_index]),
        ),
        {"slot": slot, "anchor": {"x": 0, "y": 0}},
    )


def _asset_revision(lab: AssetLab, asset_id: str) -> int:
    with sqlite3.connect(lab.db_path) as conn:
        return int(conn.execute("SELECT revision FROM assets WHERE id = ?", (asset_id,)).fetchone()[0])


def _batch_item(imported: dict[str, object], decision: str) -> dict[str, str]:
    return {
        "assetId": imported["asset"]["id"],
        "versionId": imported["version"]["id"],
        "decision": decision,
    }


def test_review_batch_applies_every_item_under_one_revision_bump(lab: AssetLab) -> None:
    drafts = [
        _import_draft(lab, "furniture.desk-island", color_index=0),
        _import_draft(lab, "furniture.storage-cabinet", color_index=1),
        _import_draft(lab, "furniture.meeting-table", color_index=2),
    ]
    expected = drafts[-1]["revision"]
    before = {item["asset"]["id"]: _asset_revision(lab, item["asset"]["id"]) for item in drafts}

    result = lab.review_batch([_batch_item(item, "accepted") for item in drafts], "", expected)

    # One bump for the whole batch, not one per item.
    assert result["revision"] == expected + 1
    assert len({entry["reviewId"] for entry in result["results"]}) == 3
    assert all(entry["reviewId"].startswith("review-") for entry in result["results"])
    assert len(result["assets"]) == 3
    assert [pack["id"] for pack in result["packs"]] == [PACK_ID]
    for draft in drafts:
        asset = next(item for item in result["assets"] if item["id"] == draft["asset"]["id"])
        assert asset["selectedVersionId"] == draft["version"]["id"]
        assert [v["status"] for v in asset["versions"] if v["id"] == draft["version"]["id"]] == [
            "accepted"
        ]
        assert asset["revision"] == before[asset["id"]] + 1

    with sqlite3.connect(lab.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT resulting_revision),"
            " MIN(expected_revision), MAX(resulting_revision) FROM reviews"
        ).fetchone() == (3, 1, expected, expected + 1)
    assert lab.bootstrap()["revision"] == expected + 1


def test_review_batch_is_all_or_nothing_and_reports_every_failure(lab: AssetLab) -> None:
    settled = _import_draft(lab, "furniture.desk-island", color_index=0)
    lab.review(
        settled["asset"]["id"], settled["version"]["id"], "accepted", "", settled["revision"]
    )
    drifting = lab.import_png(character_sheet_bytes(drifting_frame=2), character_metadata())
    good = _import_draft(lab, "furniture.storage-cabinet", color_index=1)
    expected = good["revision"]

    with pytest.raises(AssetLabError) as caught:
        lab.review_batch(
            [
                _batch_item(good, "accepted"),
                _batch_item(settled, "accepted"),
                _batch_item(drifting, "accepted"),
                {
                    "assetId": "asset-core-v0-does-not-exist",
                    "versionId": "version-does-not-exist",
                    "decision": "accepted",
                },
            ],
            "",
            expected,
        )

    assert caught.value.code == "review.batch_failed"
    details = caught.value.details
    assert details["itemCount"] == 4
    assert details["failureCount"] == 3
    assert [failure["code"] for failure in details["failures"]] == [
        "version.not_draft",
        "review.character_motion_unverified",
        "version.not_found",
    ]
    assert [failure["index"] for failure in details["failures"]] == [1, 2, 3]
    assert details["failures"][0]["versionId"] == settled["version"]["id"]

    # Nothing was written: revision, statuses and the audit log are all untouched.
    assert lab.bootstrap()["revision"] == expected
    catalog_versions = {
        version["id"]: version["status"]
        for asset in lab.catalog()["assets"]
        for version in asset["versions"]
    }
    assert catalog_versions[good["version"]["id"]] == "draft"
    assert catalog_versions[drifting["version"]["id"]] == "draft"
    with sqlite3.connect(lab.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1


def test_review_batch_uses_the_global_cas_and_rejects_stale_revisions(lab: AssetLab) -> None:
    first = _import_draft(lab, "furniture.desk-island", color_index=0)
    second = _import_draft(lab, "furniture.storage-cabinet", color_index=1)
    items = [_batch_item(first, "accepted"), _batch_item(second, "accepted")]

    with pytest.raises(AssetLabError) as caught:
        lab.review_batch(items, "", 1)
    assert caught.value.code == "revision.conflict"
    assert caught.value.details == {"expectedRevision": 1, "actualRevision": 2}
    with sqlite3.connect(lab.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    assert lab.bootstrap()["revision"] == 2

    assert lab.review_batch(items, "", 2)["revision"] == 3


def test_review_batch_requires_a_note_only_when_something_is_rejected(lab: AssetLab) -> None:
    first = _import_draft(lab, "furniture.desk-island", color_index=0)
    second = _import_draft(lab, "furniture.desk-island", color_index=1)
    assert first["asset"]["id"] == second["asset"]["id"]
    rejects = [_batch_item(first, "rejected"), _batch_item(second, "rejected")]
    before = _asset_revision(lab, first["asset"]["id"])

    with pytest.raises(AssetLabError) as caught:
        lab.review_batch(rejects, "   ", second["revision"])
    assert caught.value.code == "review.note_required"

    result = lab.review_batch(rejects, "silhouette needs one more pixel", second["revision"])
    assert result["revision"] == second["revision"] + 1
    assert result["note"] == "silhouette needs one more pixel"
    assert len(result["assets"]) == 1
    # One logical batch touching one asset bumps that asset exactly once, not once per item.
    assert result["assets"][0]["revision"] == before + 1
    assert {v["status"] for v in result["assets"][0]["versions"]} == {"rejected"}
    with sqlite3.connect(lab.db_path) as conn:
        assert conn.execute("SELECT DISTINCT note FROM reviews").fetchall() == [
            ("silhouette needs one more pixel",)
        ]

    accept = _import_draft(lab, "furniture.storage-cabinet", color_index=2)
    reject = _import_draft(lab, "furniture.meeting-table", color_index=3)
    mixed = lab.review_batch(
        [_batch_item(accept, "accepted"), _batch_item(reject, "reject")],
        "anchor is one pixel low",
        reject["revision"],
    )
    statuses = {
        version["id"]: version["status"]
        for asset in mixed["assets"]
        for version in asset["versions"]
    }
    assert statuses[accept["version"]["id"]] == "accepted"
    assert statuses[reject["version"]["id"]] == "rejected"


def test_review_batch_rejects_malformed_duplicate_and_conflicting_items(lab: AssetLab) -> None:
    first = _import_draft(lab, "furniture.desk-island", color_index=0)
    second = _import_draft(lab, "furniture.desk-island", color_index=1)
    revision = second["revision"]
    item = _batch_item(first, "accepted")

    cases: list[tuple[list[object], str]] = [
        ([], "review.batch_empty"),
        ([item, dict(item)], "review.batch_duplicate"),
        ([item, _batch_item(second, "accepted")], "review.batch_conflict"),
        ([{"assetId": first["asset"]["id"], "decision": "accepted"}], "review.batch_invalid"),
        (["not-an-object"], "review.batch_invalid"),
        ([{**item, "decision": "maybe"}], "review.decision_invalid"),
        ([dict(item, versionId=f"v{index}") for index in range(MAX_REVIEW_BATCH_ITEMS + 1)],
         "review.batch_too_large"),
    ]
    for items, code in cases:
        with pytest.raises(AssetLabError) as caught:
            lab.review_batch(items, "", revision)
        assert caught.value.code == code, items
    assert caught.value.details["maxItems"] == MAX_REVIEW_BATCH_ITEMS

    # Every one of these raises before the transaction opens.
    assert lab.bootstrap()["revision"] == revision
    with sqlite3.connect(lab.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_review_batch_still_works_on_core_v0_after_core_v1_is_seeded(tmp_path: Path) -> None:
    """Seeding core-v1 must not make core-v0 batch review unreachable.

    core-v1 inherits by inserting pack_members rows that point at the *core-v0* asset ids
    with inherited = 1, so any inherited check that is not scoped by pack would match every
    core-v0 asset and disable batch review for the whole base pack.
    """

    current = AssetLab(tmp_path / "frozen-data")
    revision = 0
    for color_index, entry in enumerate(CORE_SLOTS):
        revision, _ = import_and_accept(current, entry["slot"], revision, color_index=color_index)
    revision = current.activate(PACK_ID, revision)["revision"]
    current.bootstrap()
    with sqlite3.connect(current.db_path) as conn:
        members = conn.execute(
            "SELECT pack_id, inherited FROM pack_members"
            " WHERE slot = 'furniture.desk-island' ORDER BY pack_id"
        ).fetchall()
    assert members == [(PACK_ID, 0), (CORE_V1_PACK_ID, 1)]

    # A colour the slot has not seen, so import_png does not deduplicate into the
    # already-accepted version.
    fresh = _import_draft(current, "furniture.desk-island", color_index=20)
    assert fresh["deduplicated"] is False
    assert fresh["version"]["status"] == "draft"
    accepted = current.review_batch(
        [_batch_item(fresh, "accepted")], "", fresh["revision"]
    )
    assert accepted["packs"][0]["id"] == PACK_ID
    assert accepted["assets"][0]["selectedVersionId"] == fresh["version"]["id"]

    # A core-v1 local override batches normally too.
    override = current.import_png(
        canonical_character().png_bytes,
        {**character_metadata(), "packId": CORE_V1_PACK_ID},
    )
    overridden = current.review_batch(
        [_batch_item(override, "accepted")], "", override["revision"]
    )
    assert overridden["packs"][0]["id"] == CORE_V1_PACK_ID
    assert overridden["assets"][0]["selectedVersionId"] == override["version"]["id"]


def test_review_batch_refuses_a_frozen_inherited_version(tmp_path: Path) -> None:
    """The already-accepted inherited version is still unreviewable — via the draft check."""

    current = AssetLab(tmp_path / "inherited-data")
    revision = 0
    for color_index, entry in enumerate(CORE_SLOTS):
        revision, _ = import_and_accept(current, entry["slot"], revision, color_index=color_index)
    revision = current.activate(PACK_ID, revision)["revision"]
    bootstrap = current.bootstrap()
    core_v1 = next(pack for pack in bootstrap["packs"] if pack["id"] == CORE_V1_PACK_ID)
    inherited = next(
        slot for slot in core_v1["slots"] if slot["slot"] == "furniture.desk-island"
    )
    assert inherited["inherited"] is True

    catalog = current.catalog({"packId": PACK_ID})
    asset = next(
        entry for entry in catalog["assets"] if entry["slot"] == "furniture.desk-island"
    )
    with pytest.raises(AssetLabError) as caught:
        current.review_batch(
            [
                {
                    "assetId": asset["id"],
                    "versionId": asset["selectedVersionId"],
                    "decision": "accepted",
                }
            ],
            "",
            bootstrap["revision"],
        )
    assert caught.value.code == "review.batch_failed"
    assert [failure["code"] for failure in caught.value.details["failures"]] == [
        "version.not_draft"
    ]


def test_review_batch_flips_pack_readiness_once_the_final_slots_land(lab: AssetLab) -> None:
    revision = 0
    for color_index, entry in enumerate(CORE_SLOTS[:-2]):
        revision, _ = import_and_accept(lab, entry["slot"], revision, color_index=color_index)
    assert lab.bootstrap()["pack"]["activation"]["enabled"] is False

    drafts = [
        _import_draft(lab, entry["slot"], color_index=index)
        for index, entry in enumerate(CORE_SLOTS[-2:], start=len(CORE_SLOTS) - 2)
    ]

    result = lab.review_batch(
        [_batch_item(draft, "accepted") for draft in drafts], "", drafts[-1]["revision"]
    )
    assert result["packs"][0]["status"] == "ready"
    assert result["packs"][0]["activation"]["enabled"] is True


def test_single_and_batch_review_produce_identical_per_version_effects(tmp_path: Path) -> None:
    def snapshot(lab: AssetLab) -> tuple[object, ...]:
        with sqlite3.connect(lab.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return (
                [tuple(row) for row in conn.execute(
                    "SELECT asset_id, status FROM versions ORDER BY asset_id, version_number"
                )],
                [tuple(row) for row in conn.execute(
                    "SELECT pack_id, slot, version_id IS NOT NULL FROM pack_members ORDER BY slot"
                )],
                [tuple(row) for row in conn.execute("SELECT id, revision FROM assets ORDER BY id")],
                [tuple(row) for row in conn.execute("SELECT id, revision, status FROM packs ORDER BY id")],
                conn.execute("SELECT value FROM settings WHERE key = 'catalog_revision'").fetchone()[0],
                [tuple(row) for row in conn.execute(
                    "SELECT decision, note, expected_revision, resulting_revision FROM reviews"
                )],
            )

    single = AssetLab(tmp_path / "single")
    batch = AssetLab(tmp_path / "batch")
    for lab in (single, batch):
        imported = _import_draft(lab, "furniture.desk-island", color_index=0)
        if lab is single:
            lab.review(
                imported["asset"]["id"], imported["version"]["id"], "accepted", "ok", imported["revision"]
            )
        else:
            lab.review_batch([_batch_item(imported, "accepted")], "ok", imported["revision"])

    assert snapshot(single) == snapshot(batch)


def test_concurrent_reviews_allow_only_one_matching_revision(lab: AssetLab) -> None:
    first = lab.import_png(png_bytes(color=(13, 34, 40, 255)), {"slot": "furniture.desk-island"})
    second = lab.import_png(png_bytes(color=(23, 52, 58, 255)), {"slot": "furniture.desk-island"})
    expected = second["revision"]

    def accept(version_id: str) -> str:
        try:
            lab.review(first["asset"]["id"], version_id, "accepted", "", expected)
            return "accepted"
        except AssetLabError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(accept, [first["version"]["id"], second["version"]["id"]]))
    assert sorted(results) == ["accepted", "revision.conflict"]
    catalog = lab.catalog()
    statuses = [version["status"] for version in catalog["assets"][5]["versions"]]
    assert statuses.count("accepted") == 1
    assert statuses.count("draft") == 1


def test_two_first_delivery_assets_leave_pack_activation_disabled(lab: AssetLab) -> None:
    revision = 0
    revision, _ = import_and_accept(lab, "furniture.desk-island", revision, color_index=0)
    revision, _ = import_and_accept(lab, "character.gus", revision, color_index=1)

    with pytest.raises(AssetLabError) as caught:
        lab.activate(PACK_ID, revision)
    assert caught.value.code == "pack.incomplete"
    assert caught.value.details["missingSlots"] == [
        entry["slot"]
        for entry in CORE_SLOTS
        if entry["slot"] not in {"furniture.desk-island", "character.gus"}
    ]
    assert caught.value.details["invalidSlots"] == []
    bootstrap = lab.bootstrap()
    assert bootstrap["revision"] == revision
    assert bootstrap["pack"]["activation"]["enabled"] is False
    assert lab.active_manifest() is None


def test_complete_pack_builds_deterministic_512_atlas_with_two_pixel_extrusion(
    tmp_path: Path,
) -> None:
    atlas_shas: list[str] = []
    layouts: list[list[dict[str, int]]] = []
    first_lab: AssetLab | None = None
    first_manifest: dict[str, object] | None = None
    for lab_number in range(2):
        current = AssetLab(tmp_path / f"data-{lab_number}")
        revision = 0
        for color_index, entry in enumerate(CORE_SLOTS):
            revision, _ = import_and_accept(
                current,
                entry["slot"],
                revision,
                color_index=color_index,
            )
        assert current.bootstrap()["pack"]["status"] == "ready"
        activated = current.activate(PACK_ID, revision)
        manifest = activated["manifest"]
        assert activated["revision"] == revision + 1
        assert activated["pack"]["status"] == "active"
        assert manifest["atlases"][0]["width"] == 512
        assert manifest["atlases"][0]["height"] == 512
        assert manifest["atlases"][0]["padding"] == 2
        assert manifest["atlases"][0]["source"].startswith("/api/assets/derived/")
        assert manifest["characterMotion"]["identityLocked"] is True
        assert "trustedLegacyAcceptedMotion" not in manifest["characterMotion"]
        assert current.active_manifest() == manifest
        release = activated["release"]
        canonical = current.manifest_json_by_sha(release["manifestSha256"])
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == release["manifestSha256"]
        assert json.loads(canonical) == manifest
        assert release["manifestUrl"].endswith(release["manifestSha256"])
        assert activated["pack"]["activeRelease"] == release
        with sqlite3.connect(current.db_path) as connection:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE pack_releases SET manifest_json = '{}' WHERE id = ?",
                    (release["id"],),
                )
        atlas_shas.append(activated["catalog"]["atlasSha256"])
        layouts.append([asset["frame"] for asset in manifest["assets"]])
        if first_lab is None:
            first_lab = current
            first_manifest = manifest

    assert atlas_shas[0] == atlas_shas[1]
    assert layouts[0] == layouts[1]
    assert first_lab is not None and first_manifest is not None
    atlas_sha = atlas_shas[0]
    atlas_path = first_lab.derived_dir / atlas_sha[:2] / f"{atlas_sha}.png"
    first_region = first_manifest["assets"][0]["frame"]
    x, y = first_region["x"], first_region["y"]
    with Image.open(atlas_path) as atlas:
        source_pixel = atlas.getpixel((x, y))
        assert source_pixel == _hex_rgba(WORLD_PALETTE[0])
        for px in range(x - 2, x + first_region["width"] + 2):
            assert atlas.getpixel((px, y - 1)) == source_pixel
        assert atlas.getpixel((x - 2, y - 2)) == source_pixel


def test_core_v1_freezes_base_release_imports_new_slots_and_activates(
    tmp_path: Path,
) -> None:
    current = AssetLab(tmp_path / "v1-data")
    revision = 0
    for color_index, entry in enumerate(CORE_SLOTS):
        revision, _ = import_and_accept(
            current,
            entry["slot"],
            revision,
            color_index=color_index,
        )
    base_activation = current.activate(PACK_ID, revision)
    revision = base_activation["revision"]
    base_release_id = base_activation["release"]["id"]

    bootstrap = current.bootstrap()
    core_v1 = next(pack for pack in bootstrap["packs"] if pack["id"] == CORE_V1_PACK_ID)
    assert core_v1["baseReleaseId"] == base_release_id
    assert len(core_v1["slots"]) == len(CORE_SLOTS) + len(CORE_V1_NEW_SLOTS)
    assert all(slot["inherited"] for slot in core_v1["slots"][: len(CORE_SLOTS)])
    assert [slot["slot"] for slot in core_v1["slots"] if slot["overridable"]] == [
        "character.gus"
    ]
    assert core_v1["spec"]["overrideSlots"] == ["character.gus"]
    assert core_v1["missingSlots"] == [entry["slot"] for entry in CORE_V1_NEW_SLOTS]

    spec = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    templates = {entry["slot"]: entry for entry in spec["assets"]}
    for color_index, entry in enumerate(CORE_V1_NEW_SLOTS):
        slot = entry["slot"]
        frame = templates[slot]["frame"]
        metadata = slot_metadata(slot, CORE_V1_PACK_SPEC_PATH)
        data = png_bytes(
            (frame["width"], frame["height"]),
            _hex_rgba(WORLD_PALETTE[color_index % len(WORLD_PALETTE)]),
            transparent_corner=slot != "backdrop.beijing-cbd",
        )
        imported = current.import_png(data, metadata)
        reviewed = current.review(
            imported["asset"]["id"],
            imported["version"]["id"],
            "accepted",
            "",
            imported["revision"],
        )
        revision = reviewed["revision"]

    activated = current.activate(CORE_V1_PACK_ID, revision)
    manifest = activated["manifest"]
    assert manifest["id"] == CORE_V1_PACK_ID
    assert manifest["geometryVersion"] == spec["geometryVersion"] == 1
    assert manifest["baseReleaseId"] == base_release_id
    assert manifest["characterMotion"]["identityLocked"] is True
    assert "trustedLegacyAcceptedMotion" not in manifest["characterMotion"]
    assert manifest["requiredSlots"] == [entry["slot"] for entry in CORE_SLOTS] + [
        entry["slot"] for entry in CORE_V1_NEW_SLOTS
    ]
    desk = next(asset for asset in manifest["assets"] if asset["id"] == "furniture.desk-island")
    assert [point["id"] for point in desk["interactionPoints"]] == [
        "seat-se", "seat-sw", "seat-nw", "seat-ne"
    ]
    assert activated["pack"]["status"] == "active"
    assert current.active_release()["packId"] == CORE_V1_PACK_ID

    # Only Gus may leave the frozen inheritance boundary, and importing the
    # canonical rig must not mutate the already active immutable release.
    inherited_gus = next(
        asset
        for asset in current.catalog({"packId": CORE_V1_PACK_ID})["assets"]
        if asset["slot"] == "character.gus"
    )
    assert inherited_gus["inherited"] is True
    assert inherited_gus["overridable"] is True
    with pytest.raises(AssetLabError) as caught:
        current.import_png(
            png_bytes(),
            {"packId": CORE_V1_PACK_ID, "slot": "furniture.desk-island"},
        )
    assert caught.value.code == "slot.invalid"

    frozen_release = activated["release"]
    frozen_manifest = activated["manifest"]
    override_metadata = character_metadata()
    override_metadata["packId"] = CORE_V1_PACK_ID

    unverified_override = current.import_png(
        character_sheet_bytes(drifting_frame=2),
        override_metadata,
    )
    assert unverified_override["version"]["metadata"]["motionBuild"]["verified"] is False
    with pytest.raises(AssetLabError) as caught:
        current.review(
            unverified_override["asset"]["id"],
            unverified_override["version"]["id"],
            "accepted",
            "",
            unverified_override["revision"],
        )
    assert caught.value.code == "review.character_motion_unverified"
    assert current.active_release()["id"] == frozen_release["id"]

    imported_override = current.import_png(
        canonical_character().png_bytes,
        override_metadata,
    )
    assert imported_override["asset"]["id"] == "asset-core-v1-character-gus"
    assert imported_override["asset"]["ownerPackId"] == CORE_V1_PACK_ID
    assert imported_override["asset"]["inherited"] is False
    assert imported_override["version"]["metadata"]["motionBuild"]["verified"] is True
    assert current.active_release()["id"] == frozen_release["id"]

    reviewed_override = current.review(
        imported_override["asset"]["id"],
        imported_override["version"]["id"],
        "accepted",
        "canonical rig verified",
        imported_override["revision"],
    )
    assert current.active_release()["id"] == frozen_release["id"]
    replacement = current.activate(CORE_V1_PACK_ID, reviewed_override["revision"])
    assert replacement["release"]["id"] != frozen_release["id"]
    assert replacement["manifest"]["characterMotion"]["identityLocked"] is True
    assert "trustedLegacyAcceptedMotion" not in replacement["manifest"]["characterMotion"]
    assert current.manifest_by_sha(frozen_release["manifestSha256"]) == frozen_manifest

    with sqlite3.connect(current.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE packs SET base_release_id = 'release-other' WHERE id = ?",
                (CORE_V1_PACK_ID,),
            )


@pytest.mark.parametrize("geometry_version", [None, 0, 3, "1"])
def test_core_v1_runtime_spec_rejects_unknown_geometry_version(
    lab: AssetLab,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    geometry_version: object,
) -> None:
    import codex_v0.asset_lab as asset_lab_module

    extension = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    if geometry_version is None:
        extension.pop("geometryVersion", None)
    else:
        extension["geometryVersion"] = geometry_version
    invalid_spec_path = tmp_path / "invalid-core-v1-pack.spec.json"
    invalid_spec_path.write_text(json.dumps(extension), encoding="utf-8")
    monkeypatch.setattr(asset_lab_module, "CORE_V1_PACK_SPEC_PATH", invalid_spec_path)

    with pytest.raises(AssetLabError, match="does not match core-v1") as caught:
        lab._load_runtime_spec(CORE_V1_PACK_ID, base_release_id="release-base")

    assert caught.value.code == "runtime_spec.invalid"


@pytest.mark.parametrize("geometry_version", [1, 2])
def test_core_v1_runtime_spec_publishes_supported_geometry_version(
    lab: AssetLab,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    geometry_version: int,
) -> None:
    import codex_v0.asset_lab as asset_lab_module

    extension = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    extension["geometryVersion"] = geometry_version
    spec_path = tmp_path / "core-v1-pack.spec.json"
    spec_path.write_text(json.dumps(extension), encoding="utf-8")
    monkeypatch.setattr(asset_lab_module, "CORE_V1_PACK_SPEC_PATH", spec_path)

    manifest = lab._load_runtime_spec(CORE_V1_PACK_ID, base_release_id="release-base")

    assert manifest["geometryVersion"] == geometry_version


def test_only_frozen_core_v1_inheritance_can_trust_accepted_legacy_gus_motion() -> None:
    inherited = {
        "inherited": 1,
        "source_release_id": "release-base",
        "source_status": "accepted",
    }
    trust = AssetLab._trusted_legacy_accepted_motion

    assert trust(
        inherited,
        pack_id=CORE_V1_PACK_ID,
        base_release_id="release-base",
        identity_locked=False,
    ) is True
    assert trust(
        {**inherited, "source_status": "superseded"},
        pack_id=CORE_V1_PACK_ID,
        base_release_id="release-base",
        identity_locked=False,
    ) is True
    for entry, pack_id, base_release_id, identity_locked in (
        ({**inherited, "source_status": "draft"}, CORE_V1_PACK_ID, "release-base", False),
        ({**inherited, "inherited": 0}, CORE_V1_PACK_ID, "release-base", False),
        (inherited, CORE_V1_PACK_ID, "release-other", False),
        (inherited, PACK_ID, "release-base", False),
        (inherited, CORE_V1_PACK_ID, "release-base", True),
    ):
        assert trust(
            entry,
            pack_id=pack_id,
            base_release_id=base_release_id,
            identity_locked=identity_locked,
        ) is False


def test_core_v1_preserves_motion_from_pre_consistency_accepted_base_release(
    tmp_path: Path,
) -> None:
    current = AssetLab(tmp_path / "legacy-motion-data")
    revision = 0
    gus_version_id = ""
    for color_index, entry in enumerate(CORE_SLOTS):
        revision, imported = import_and_accept(
            current,
            entry["slot"],
            revision,
            color_index=color_index,
        )
        if entry["slot"] == "character.gus":
            gus_version_id = str(imported["version"]["id"])

    # Simulate the real accepted release that predates characterConsistency.
    with sqlite3.connect(current.db_path) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM versions WHERE id = ?",
            (gus_version_id,),
        ).fetchone()
        metadata = json.loads(row[0])
        metadata.pop("characterConsistency")
        metadata.pop("motionBuild")
        connection.execute(
            "UPDATE versions SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata, sort_keys=True, separators=(",", ":")), gus_version_id),
        )

    base_activation = current.activate(PACK_ID, revision)
    revision = base_activation["revision"]
    base_motion = base_activation["manifest"]["characterMotion"]
    assert base_motion["identityLocked"] is False
    assert base_motion["trustedLegacyAcceptedMotion"] is False

    spec = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    templates = {entry["slot"]: entry for entry in spec["assets"]}
    for color_index, entry in enumerate(CORE_V1_NEW_SLOTS):
        slot = entry["slot"]
        frame = templates[slot]["frame"]
        imported = current.import_png(
            png_bytes(
                (frame["width"], frame["height"]),
                _hex_rgba(WORLD_PALETTE[color_index % len(WORLD_PALETTE)]),
                transparent_corner=slot != "backdrop.beijing-cbd",
            ),
            slot_metadata(slot, CORE_V1_PACK_SPEC_PATH),
        )
        reviewed = current.review(
            imported["asset"]["id"],
            imported["version"]["id"],
            "accepted",
            "",
            imported["revision"],
        )
        revision = reviewed["revision"]

    inherited_activation = current.activate(CORE_V1_PACK_ID, revision)
    motion = inherited_activation["manifest"]["characterMotion"]
    assert motion["identityLocked"] is False
    assert motion["trustedLegacyAcceptedMotion"] is True


def test_core_v2_freezes_v1_release_gates_native_overrides_and_publishes_29_slots(
    tmp_path: Path,
) -> None:
    current = AssetLab(tmp_path / "v2-data")
    revision = 0
    for color_index, entry in enumerate(CORE_SLOTS):
        revision, _ = import_and_accept(
            current,
            entry["slot"],
            revision,
            color_index=color_index,
        )
    current.activate(PACK_ID, revision)
    revision = current.bootstrap()["revision"]

    v1_spec = json.loads(CORE_V1_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    v1_templates = {entry["slot"]: entry for entry in v1_spec["assets"]}
    for color_index, entry in enumerate(CORE_V1_NEW_SLOTS):
        slot = entry["slot"]
        frame = v1_templates[slot]["frame"]
        imported = current.import_png(
            png_bytes(
                (frame["width"], frame["height"]),
                _hex_rgba(WORLD_PALETTE[color_index % len(WORLD_PALETTE)]),
                transparent_corner=slot != "backdrop.beijing-cbd",
            ),
            slot_metadata(slot, CORE_V1_PACK_SPEC_PATH),
        )
        reviewed = current.review(
            imported["asset"]["id"],
            imported["version"]["id"],
            "accepted",
            "",
            imported["revision"],
        )
        revision = reviewed["revision"]
    v1_activation = current.activate(CORE_V1_PACK_ID, revision)
    assert "sceneShell" not in v1_activation["manifest"]
    revision = v1_activation["revision"]

    bootstrap = current.bootstrap()
    core_v2 = next(pack for pack in bootstrap["packs"] if pack["id"] == CORE_V2_PACK_ID)
    assert core_v2["baseReleaseId"] == v1_activation["release"]["id"]
    assert core_v2["styleProfileId"] == CORE_V2_STYLE_PROFILE_ID
    assert core_v2["spec"]["geometryVersion"] == 2
    assert len(core_v2["spec"]["worldPalette"]) == 48
    assert core_v2["spec"]["alphaLevels"] == [0, 96, 128, 160, 192, 255]
    assert len(core_v2["slots"]) == len(CORE_V2_REQUIRED_SLOT_NAMES) == 29
    assert [slot["slot"] for slot in core_v2["slots"] if slot["inherited"]] == list(
        CORE_V2_INHERITED_SLOT_NAMES
    )
    assert {
        slot["slot"] for slot in core_v2["slots"] if slot["overridable"]
    } == set(CORE_V2_OVERRIDE_SLOT_NAMES)
    assert all("ownerPackId" in slot for slot in core_v2["slots"])
    assert set(core_v2["missingSlots"]) == set(CORE_V2_EDITABLE_SLOT_NAMES)
    assert [scene["status"] for scene in core_v2["previewScenes"]] == [
        "pending",
        "pending",
    ]

    v2_spec = json.loads(CORE_V2_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    v2_templates = {entry["slot"]: entry for entry in v2_spec["assets"]}

    # A source with an otherwise valid sidecar cannot pass if it was not
    # generated at the declared native frame size.
    first_slot = CORE_V2_EDITABLE_SLOT_NAMES[0]
    first_metadata = slot_metadata(first_slot, CORE_V2_PACK_SPEC_PATH)
    wrong = current.import_png(
        png_bytes((641, 360), _hex_rgba(CORE_V2_WORLD_PALETTE[32]), transparent_corner=False),
        first_metadata,
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            wrong["asset"]["id"],
            wrong["version"]["id"],
            "accepted",
            "",
            wrong["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "native-frame-size" in caught.value.details["failures"]
    revision = wrong["revision"]

    backdrop_slot = "backdrop.beijing-cbd"
    backdrop_frame = v2_templates[backdrop_slot]["frame"]
    backdrop_bytes = png_bytes(
        (backdrop_frame["width"], backdrop_frame["height"]),
        _hex_rgba(CORE_V2_WORLD_PALETTE[32]),
        transparent_corner=False,
    )
    missing_provenance = current.import_png(
        backdrop_bytes,
        slot_metadata(backdrop_slot, CORE_V2_PACK_SPEC_PATH),
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            missing_provenance["asset"]["id"],
            missing_provenance["version"]["id"],
            "accepted",
            "",
            missing_provenance["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "backdrop-native-metadata" in caught.value.details["failures"]
    revision = missing_provenance["revision"]

    translucent = Image.new(
        "RGBA",
        (backdrop_frame["width"], backdrop_frame["height"]),
        (*_hex_rgba(CORE_V2_WORLD_PALETTE[32])[:3], 192),
    )
    translucent_output = io.BytesIO()
    translucent.save(translucent_output, format="PNG")
    translucent_bytes = translucent_output.getvalue()
    translucent_metadata = slot_metadata(backdrop_slot, CORE_V2_PACK_SPEC_PATH)
    translucent_metadata["preparation"] = {
        "schemaVersion": 1,
        "packId": CORE_V2_PACK_ID,
        "slot": backdrop_slot,
        "sourceSha256": hashlib.sha256(translucent_bytes).hexdigest(),
        "outputSha256": hashlib.sha256(translucent_bytes).hexdigest(),
        "sourceSize": [backdrop_frame["width"], backdrop_frame["height"]],
        "outputSize": [backdrop_frame["width"], backdrop_frame["height"]],
        "transform": {
            "mode": "full-canvas-native",
            "scale": {"x": 1.0, "y": 1.0, "uniformRequested": 1.0},
            "resizedSize": [backdrop_frame["width"], backdrop_frame["height"]],
            "crop": [0, 0, backdrop_frame["width"], backdrop_frame["height"]],
            "resampling": "nearest",
            "alphaLevels": [0, 255],
        },
    }
    translucent_import = current.import_png(translucent_bytes, translucent_metadata)
    with pytest.raises(AssetLabError) as caught:
        current.review(
            translucent_import["asset"]["id"],
            translucent_import["version"]["id"],
            "accepted",
            "",
            translucent_import["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "backdrop-opaque" in caught.value.details["failures"]
    revision = translucent_import["revision"]

    edge_slot = "furniture.focus-desk-nw"
    edge_frame = v2_templates[edge_slot]["frame"]
    edge_image = Image.new(
        "RGBA",
        (edge_frame["width"], edge_frame["height"]),
        _hex_rgba(CORE_V2_WORLD_PALETTE[32]),
    )
    edge_image.putpixel(
        (edge_frame["width"] - 1, edge_frame["height"] - 1),
        (0, 0, 0, 0),
    )
    edge_output = io.BytesIO()
    edge_image.save(edge_output, format="PNG")
    unsafe_edge = current.import_png(
        edge_output.getvalue(),
        slot_metadata(edge_slot, CORE_V2_PACK_SPEC_PATH),
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            unsafe_edge["asset"]["id"],
            unsafe_edge["version"]["id"],
            "accepted",
            "",
            unsafe_edge["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "transparent-corners" in caught.value.details["failures"]
    revision = unsafe_edge["revision"]

    soft_edge_image = Image.new(
        "RGBA",
        (edge_frame["width"], edge_frame["height"]),
        (0, 0, 0, 0),
    )
    ImageDraw.Draw(soft_edge_image).rectangle(
        (4, 4, edge_frame["width"] - 5, edge_frame["height"] - 5),
        fill=(*_hex_rgba(CORE_V2_WORLD_PALETTE[32])[:3], 192),
    )
    soft_edge_output = io.BytesIO()
    soft_edge_image.save(soft_edge_output, format="PNG")
    soft_edge = current.import_png(
        soft_edge_output.getvalue(),
        slot_metadata(edge_slot, CORE_V2_PACK_SPEC_PATH),
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            soft_edge["asset"]["id"],
            soft_edge["version"]["id"],
            "accepted",
            "",
            soft_edge["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "binary-alpha" in caught.value.details["failures"]
    revision = soft_edge["revision"]

    direction_slot = "structure.wall-solid-nw"
    direction_frame = v2_templates[direction_slot]["frame"]
    wrong_direction_image = Image.new(
        "RGBA",
        (direction_frame["width"], direction_frame["height"]),
        (0, 0, 0, 0),
    )
    ImageDraw.Draw(wrong_direction_image).polygon(
        [
            (4, 42),
            (direction_frame["width"] - 5, 10),
            (direction_frame["width"] - 5, direction_frame["height"] - 37),
            (4, direction_frame["height"] - 5),
        ],
        fill=_hex_rgba(CORE_V2_WORLD_PALETTE[33]),
    )
    wrong_direction_output = io.BytesIO()
    wrong_direction_image.save(wrong_direction_output, format="PNG")
    wrong_direction = current.import_png(
        wrong_direction_output.getvalue(),
        slot_metadata(direction_slot, CORE_V2_PACK_SPEC_PATH),
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            wrong_direction["asset"]["id"],
            wrong_direction["version"]["id"],
            "accepted",
            "",
            wrong_direction["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "orientation-pixels" in caught.value.details["failures"]
    revision = wrong_direction["revision"]

    # Matching the ground axis alone is insufficient: a short wall would
    # create a visible top seam against the next native wall sprite.
    short_wall_image = Image.new(
        "RGBA",
        (direction_frame["width"], direction_frame["height"]),
        (0, 0, 0, 0),
    )
    short_axis = v2_templates[direction_slot]["groundAxis"]
    short_start = short_axis["start"]
    short_end = short_axis["end"]
    ImageDraw.Draw(short_wall_image).polygon(
        [
            (short_start["x"], short_start["y"] - 38),
            (short_end["x"], short_end["y"] - 38),
            (short_end["x"], short_end["y"]),
            (short_start["x"], short_start["y"]),
        ],
        fill=_hex_rgba(CORE_V2_WORLD_PALETTE[33]),
    )
    short_wall_output = io.BytesIO()
    short_wall_image.save(short_wall_output, format="PNG")
    short_wall = current.import_png(
        short_wall_output.getvalue(),
        slot_metadata(direction_slot, CORE_V2_PACK_SPEC_PATH),
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            short_wall["asset"]["id"],
            short_wall["version"]["id"],
            "accepted",
            "",
            short_wall["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "wall-top-axis-pixels" in caught.value.details["failures"]
    assert "wall-face-height-pixels" in caught.value.details["failures"]
    revision = short_wall["revision"]

    one_pane_slot = "structure.wall-window-nw"
    one_pane_frame = v2_templates[one_pane_slot]["frame"]
    one_pane_axis = v2_templates[one_pane_slot]["groundAxis"]
    one_pane_height = v2_templates[one_pane_slot]["wallFaceHeight"]
    one_pane_image = Image.new(
        "RGBA",
        (one_pane_frame["width"], one_pane_frame["height"]),
        (0, 0, 0, 0),
    )
    one_start = one_pane_axis["start"]
    one_end = one_pane_axis["end"]
    ImageDraw.Draw(one_pane_image).polygon(
        [
            (one_start["x"], one_start["y"] - one_pane_height),
            (one_end["x"], one_end["y"] - one_pane_height),
            (one_end["x"], one_end["y"]),
            (one_start["x"], one_start["y"]),
        ],
        fill=(*_hex_rgba(CORE_V2_WORLD_PALETTE[34])[:3], 128),
        outline=_hex_rgba(CORE_V2_WORLD_PALETTE[33]),
        width=3,
    )
    one_pane_output = io.BytesIO()
    one_pane_image.save(one_pane_output, format="PNG")
    one_pane = current.import_png(
        one_pane_output.getvalue(),
        slot_metadata(one_pane_slot, CORE_V2_PACK_SPEC_PATH),
    )
    with pytest.raises(AssetLabError) as caught:
        current.review(
            one_pane["asset"]["id"],
            one_pane["version"]["id"],
            "accepted",
            "",
            one_pane["revision"],
        )
    assert caught.value.code == "review.asset_spec_failed"
    assert "glass-pane-components" in caught.value.details["failures"]
    revision = one_pane["revision"]

    for color_index, slot in enumerate(CORE_V2_EDITABLE_SLOT_NAMES):
        frame = v2_templates[slot]["frame"]
        base_color = _hex_rgba(CORE_V2_WORLD_PALETTE[(32 + color_index) % 48])
        if slot.startswith("structure.wall-"):
            image = Image.new(
                "RGBA", (frame["width"], frame["height"]), (0, 0, 0, 0)
            )
            axis = v2_templates[slot]["groundAxis"]
            start = axis["start"]
            end = axis["end"]
            wall_height = v2_templates[slot]["wallFaceHeight"]
            assert wall_height == 56
            points = [
                (start["x"], start["y"] - wall_height),
                (end["x"], end["y"] - wall_height),
                (end["x"], end["y"]),
                (start["x"], start["y"]),
            ]
            fill = (
                (*_hex_rgba(CORE_V2_WORLD_PALETTE[34])[:3], 128)
                if "window" in slot
                else base_color
            )
            ImageDraw.Draw(image).polygon(points, fill=fill, outline=base_color, width=3)
            if "window" in slot:
                wall_draw = ImageDraw.Draw(image)
                for numerator in (1, 2, 3):
                    fraction = numerator / 4
                    mullion_x = round(start["x"] + (end["x"] - start["x"]) * fraction)
                    mullion_top = round(
                        start["y"]
                        - wall_height
                        + (end["y"] - start["y"]) * fraction
                    )
                    mullion_bottom = round(
                        start["y"] + (end["y"] - start["y"]) * fraction
                    )
                    wall_draw.line(
                        (mullion_x, mullion_top, mullion_x, mullion_bottom),
                        fill=base_color,
                        width=2,
                    )
        else:
            image = Image.new(
                "RGBA", (frame["width"], frame["height"]), base_color
            )
        if slot != "backdrop.beijing-cbd" and not slot.startswith("structure.wall-"):
            for point in (
                (0, 0),
                (frame["width"] - 1, 0),
                (0, frame["height"] - 1),
                (frame["width"] - 1, frame["height"] - 1),
            ):
                image.putpixel(point, (0, 0, 0, 0))
        output = io.BytesIO()
        image.save(output, format="PNG")
        metadata = slot_metadata(slot, CORE_V2_PACK_SPEC_PATH)
        if slot == "backdrop.beijing-cbd":
            source_bytes = output.getvalue()
            metadata["preparation"] = {
                "schemaVersion": 1,
                "packId": CORE_V2_PACK_ID,
                "slot": slot,
                "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
                "outputSha256": hashlib.sha256(source_bytes).hexdigest(),
                "sourceSize": [frame["width"], frame["height"]],
                "outputSize": [frame["width"], frame["height"]],
                "transform": {
                    "mode": "full-canvas-native",
                    "scale": {"x": 1.0, "y": 1.0, "uniformRequested": 1.0},
                    "resizedSize": [frame["width"], frame["height"]],
                    "crop": [0, 0, frame["width"], frame["height"]],
                    "resampling": "nearest",
                    "alphaLevels": [0, 255],
                },
            }
        imported = current.import_png(output.getvalue(), metadata)
        reviewed = current.review(
            imported["asset"]["id"],
            imported["version"]["id"],
            "accepted",
            "",
            imported["revision"],
        )
        revision = reviewed["revision"]

    activated = current.activate(CORE_V2_PACK_ID, revision)
    manifest = activated["manifest"]
    assert manifest["id"] == CORE_V2_PACK_ID
    assert manifest["geometryVersion"] == 2
    assert manifest["baseReleaseId"] == v1_activation["release"]["id"]
    assert manifest["requiredSlots"] == list(CORE_V2_REQUIRED_SLOT_NAMES)
    assert manifest["palette"]["world"] == list(CORE_V2_WORLD_PALETTE)
    assert manifest["sceneShell"] == v2_spec["sceneShell"]
    assert "sceneShell" not in current.manifest_by_sha(
        v1_activation["release"]["manifestSha256"]
    )
    manifest_windows = [
        asset
        for asset in manifest["assets"]
        if asset["slot"].startswith("structure.wall-window-")
    ]
    assert len(manifest_windows) == 2
    assert all(window["paneAlpha"] == 128 for window in manifest_windows)
    assert all(window["paneCount"] == 4 for window in manifest_windows)
    assert manifest["atlases"] == [
        {
            **manifest["atlases"][0],
            "width": 1024,
            "height": 1024,
        }
    ]
    assert activated["pack"]["status"] == "active"
    assert current.active_release()["packId"] == CORE_V2_PACK_ID

    qa = CoreV2AssetQa(data_dir=current.data_dir)
    assert qa.output_dir == current.derived_dir / CORE_V2_PACK_ID
    qa.output_dir.mkdir(parents=True, exist_ok=True)
    preview_bytes = png_bytes((640, 360), _hex_rgba(CORE_V2_WORLD_PALETTE[32]))
    (qa.output_dir / "world-opening-empty-v2-candidate.png").write_bytes(preview_bytes)
    refreshed = next(
        pack for pack in current.bootstrap()["packs"] if pack["id"] == CORE_V2_PACK_ID
    )
    opening_preview = refreshed["previewScenes"][0]
    assert opening_preview["status"] == "ready"
    assert opening_preview["width"] == 640
    assert opening_preview["height"] == 360
    assert opening_preview["blobUrl"] == (
        f"/api/assets/derived/{opening_preview['sha256']}.png"
    )
    assert (
        current.derived_dir
        / opening_preview["sha256"][:2]
        / f"{opening_preview['sha256']}.png"
    ).is_file()

    with sqlite3.connect(current.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE packs SET base_release_id = 'release-other' WHERE id = ?",
                (CORE_V2_PACK_ID,),
            )


def test_core_v2_preview_rejects_path_sources_without_reading_outside_derived(
    lab: AssetLab, monkeypatch: pytest.MonkeyPatch
) -> None:
    import codex_v0.asset_lab as asset_lab_module

    outside_bytes = png_bytes((640, 360), (80, 90, 100, 255))
    outside_path = lab.assets_dir / "outside.png"
    outside_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path.write_bytes(outside_bytes)
    outside_sha = hashlib.sha256(outside_bytes).hexdigest()

    malicious_names = (
        "../outside.png",
        "..\\outside.png",
        "nested/outside.png",
        str(outside_path),
        "bad\x00.png",
    )
    specs = deepcopy(asset_lab_module.PACK_SPECS)
    specs[CORE_V2_PACK_ID]["previewScenes"] = [
        {
            "id": f"malicious-{index}",
            "label": "malicious",
            "layoutId": "world.opening-empty-v2",
            "sourceName": source_name,
        }
        for index, source_name in enumerate(malicious_names)
    ]
    monkeypatch.setattr(asset_lab_module, "PACK_SPECS", specs)

    previews = lab._preview_scene_payloads(CORE_V2_PACK_ID)

    assert [preview["status"] for preview in previews] == [
        "invalid"
    ] * len(malicious_names)
    assert outside_path.read_bytes() == outside_bytes
    assert not (
        lab.derived_dir / outside_sha[:2] / f"{outside_sha}.png"
    ).exists()


def test_atlas_packing_is_sorted_by_height_width_and_id(lab: AssetLab) -> None:
    entries = [
        {"id": "z", "image": Image.new("RGBA", (10, 20), (1, 2, 3, 255))},
        {"id": "b", "image": Image.new("RGBA", (20, 20), (1, 2, 3, 255))},
        {"id": "a", "image": Image.new("RGBA", (20, 20), (1, 2, 3, 255))},
        {"id": "tall", "image": Image.new("RGBA", (8, 30), (1, 2, 3, 255))},
    ]
    _, first_layout, _ = lab._build_atlas(entries)
    _, second_layout, _ = lab._build_atlas(list(reversed(entries)))
    assert first_layout == second_layout
    assert first_layout["tall"]["x"] < first_layout["a"]["x"]
    assert first_layout["a"]["x"] < first_layout["b"]["x"]
    assert first_layout["b"]["x"] < first_layout["z"]["x"]


def test_scan_inbox_uses_same_basename_json_and_rescans_idempotently(lab: AssetLab) -> None:
    lab.bootstrap()
    (lab.inbox_dir / "desk.png").write_bytes(png_bytes())
    (lab.inbox_dir / "desk.json").write_text(
        json.dumps(
            {
                "assetId": "furniture.desk-island",
                "displayName": "Desk candidate",
                "kind": "furniture",
                "footprint": [{"x": 0, "y": 0, "blocked": True}],
                "jobId": "generation-job-1",
                "sourcePrompt": "modern Beijing office desk",
            }
        ),
        encoding="utf-8",
    )
    (lab.inbox_dir / "orphan.png").write_bytes(png_bytes())

    first = lab.scan_inbox()
    assert first["revision"] == 1
    assert len(first["imported"]) == 1
    assert first["imported"][0]["sourceName"] == "desk.png"
    assert first["errors"][0]["sourceName"] == "orphan.png"
    assert first["errors"][0]["code"] == "inbox.sidecar_missing"
    metadata = first["imported"][0]["version"]["metadata"]
    assert metadata["jobId"] == "generation-job-1"
    assert metadata["sourcePrompt"] == "modern Beijing office desk"

    second = lab.scan_inbox()
    assert second["revision"] == 1
    assert second["imported"][0]["deduplicated"] is True
    with sqlite3.connect(lab.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'scan'").fetchone()[0] == 2


def test_blob_path_rejects_traversal_uppercase_and_missing_hash(lab: AssetLab) -> None:
    lab.bootstrap()
    for value in ("../secret", "A" * 64, "f" * 64):
        with pytest.raises(AssetLabError) as caught:
            lab.blob_path(value)
        assert caught.value.code in {"blob.invalid_sha", "blob.not_found"}
