from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from PIL import ImageDraw

from codex_v0.character_motion import (
    DIRECTIONS,
    FRAME_COUNT as CHARACTER_FRAME_COUNT,
    SHEET_COLUMNS,
    SHEET_SIZE as GUS_SHEET_SIZE,
)
from codex_v0.asset_normalize import (
    CORE_V1_PACK_SPEC,
    CORE_V2_PACK_SPEC,
    AssetNormalizationError,
    canonical_diamond_alpha,
    inspect_png,
    load_preparation_report,
    load_locked_palette,
    normalize_character_grid,
    normalize_furniture,
    normalize_slot,
    prepare_native_candidate,
    remove_chroma_background,
    slot_metadata,
    wall_face_geometry_pixels,
    wall_ground_axis_pixels,
    _apply_component_glass_alpha,
)


FLOOR_SLOTS = (
    "floor.raw-concrete",
    "floor.patched-concrete",
    "floor.light-wood",
    "floor.utility-border",
)


def test_locked_palette_is_exact_and_disjoint() -> None:
    palette = load_locked_palette()
    world = load_locked_palette(include_player_accents=False)
    assert len(palette) == 40
    assert len(set(palette)) == 40
    assert len(world) == 32
    assert set(world).issubset(palette)


def test_furniture_normalization_is_fixed_transparent_and_palette_bound(
    tmp_path: Path,
) -> None:
    source = Image.new("RGBA", (180, 120), (0, 0, 0, 0))
    for x in range(20, 160):
        for y in range(15, 110):
            source.putpixel((x, y), (123, 87, 61, 255))
    result = normalize_furniture(source)
    output = tmp_path / "furniture.png"
    result.save(output)
    inspection = inspect_png(output)
    assert result.size == (96, 80)
    assert inspection["transparentCorners"] is True
    assert inspection["opaqueColorCount"] <= 32


def test_character_grid_preserves_every_frame_and_shared_bottom_anchor() -> None:
    source = Image.new("RGBA", (SHEET_COLUMNS * 20, len(DIRECTIONS) * 40), (0, 0, 0, 0))
    for row in range(len(DIRECTIONS)):
        for column in range(SHEET_COLUMNS):
            left = column * 20 + 5
            top = row * 40 + 7 + (1 if column >= SHEET_COLUMNS - 2 else 0)
            for x in range(left, left + 10):
                for y in range(top, (row + 1) * 40 - 3):
                    source.putpixel((x, y), (41, 104, 173, 255))
    result = normalize_character_grid(source)
    assert result.size == GUS_SHEET_SIZE
    alpha = result.getchannel("A")
    for row in range(len(DIRECTIONS)):
        for column in range(SHEET_COLUMNS):
            bounds = alpha.crop(
                (
                    column * 24,
                    row * 48,
                    (column + 1) * 24,
                    (row + 1) * 48,
                )
            ).getbbox()
            assert bounds is not None
            assert bounds[3] == 46


def test_slot_normalizer_locks_runtime_geometry_and_sidecar_contract() -> None:
    static_slots = {
        "floor.raw-concrete": (32, 16),
        "floor.patched-concrete": (32, 16),
        "floor.light-wood": (32, 16),
        "floor.utility-border": (32, 16),
        "furniture.moving-box": (48, 64),
        "furniture.desk-island": (96, 80),
        "furniture.storage-cabinet": (80, 72),
        "furniture.tea-coffee-bar": (80, 72),
        "furniture.meeting-table": (128, 96),
    }
    source = Image.new("RGBA", (181, 121), (0, 0, 0, 0))
    for x in range(20, 160):
        for y in range(15, 110):
            if (x + y) % 3:
                source.putpixel((x, y), (123, 87, 61, 255))
    for slot, expected_size in static_slots.items():
        result = normalize_slot(source, slot)
        metadata = slot_metadata(slot)
        assert result.size == expected_size
        assert metadata["slot"] == metadata["assetId"] == slot
        assert metadata["frameCount"] == 1
        assert result.getchannel("A").getextrema() == (0, 255)


def test_slot_normalizer_locks_gus_and_heart_grids() -> None:
    gus = Image.new("RGBA", (SHEET_COLUMNS * 21, len(DIRECTIONS) * 39), (0, 0, 0, 0))
    for row in range(len(DIRECTIONS)):
        for column in range(SHEET_COLUMNS):
            for x in range(column * 21 + 4, column * 21 + 17):
                for y in range(row * 39 + 5, row * 39 + 35):
                    gus.putpixel((x, y), (117, 189, 159, 255))
    normalized_gus = normalize_slot(gus, "character.gus")
    gus_metadata = slot_metadata("character.gus")
    assert normalized_gus.size == GUS_SHEET_SIZE
    assert gus_metadata["frameCount"] == CHARACTER_FRAME_COUNT
    assert set(gus_metadata["animations"]) == {"idle", "walk", "work"}

    heart = Image.new("RGBA", (401, 101), (0, 0, 0, 0))
    for column in range(4):
        left = round(column * heart.width / 4) + 20
        right = round((column + 1) * heart.width / 4) - 20
        for x in range(left, right):
            for y in range(20, 80):
                heart.putpixel((x, y), (237, 128, 108, 255))
    normalized_heart = normalize_slot(heart, "effect.good-card-heart")
    assert normalized_heart.size == (96, 24)
    assert slot_metadata("effect.good-card-heart")["frameCount"] == 4


def test_core_v1_normalizer_covers_all_new_geometry_and_opaque_backdrop() -> None:
    expected = {
        "backdrop.beijing-cbd": (640, 112),
        "structure.wall-solid-nw": (96, 88),
        "structure.wall-solid-ne": (96, 88),
        "structure.wall-window-nw": (128, 96),
        "structure.wall-window-ne": (128, 96),
        "structure.wall-door-ne": (96, 88),
        "structure.corner-column": (32, 88),
        "decor.whiteboard-stand": (64, 72),
        "decor.floor-plant": (48, 64),
        "furniture.printer-station": (80, 72),
        "furniture.lounge-set": (112, 88),
    }
    opaque = Image.new("RGBA", (900, 200), (99, 123, 147, 255))
    transparent = Image.new("RGBA", (181, 131), (0, 0, 0, 0))
    for x in range(18, 164):
        for y in range(9, 124):
            transparent.putpixel((x, y), (99, 123, 147, 255))

    for slot, size in expected.items():
        source = opaque if slot == "backdrop.beijing-cbd" else transparent
        first = normalize_slot(source, slot, spec_path=CORE_V1_PACK_SPEC)
        second = normalize_slot(source, slot, spec_path=CORE_V1_PACK_SPEC)
        metadata = slot_metadata(slot, CORE_V1_PACK_SPEC)
        assert first.size == size
        assert first.tobytes() == second.tobytes()
        assert metadata["packId"] == "core-v1"
        assert metadata["slot"] == slot
        assert (metadata["frameWidth"], metadata["frameHeight"]) == size
        if slot == "backdrop.beijing-cbd":
            assert first.getchannel("A").getextrema() == (255, 255)
        else:
            assert first.getchannel("A").getextrema() == (0, 255)


def test_core_v2_normalizer_requires_native_frames_and_uses_48_color_superset() -> None:
    expected = {
        "backdrop.beijing-cbd": (640, 360),
        "floor.raw-concrete": (32, 16),
        "floor.utility-border": (32, 16),
        "structure.wall-solid-nw": (96, 88),
        "structure.wall-solid-ne": (96, 88),
        "structure.wall-window-nw": (128, 96),
        "structure.wall-window-ne": (128, 96),
        "structure.wall-door-ne": (96, 88),
        "structure.corner-column": (32, 88),
        "furniture.focus-desk-nw": (80, 80),
        "furniture.focus-desk-ne": (80, 80),
        "furniture.media-console": (112, 88),
        "furniture.prototype-bench": (112, 88),
        "furniture.low-bookcase": (80, 64),
        "furniture.entry-bench": (96, 56),
        "decor.pinboard-stand": (64, 72),
    }
    assert len(load_locked_palette(CORE_V2_PACK_SPEC, include_player_accents=False)) == 48
    assert len(load_locked_palette(CORE_V2_PACK_SPEC)) == 56

    for slot, size in expected.items():
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        if "wall-window" in slot:
            draw = ImageDraw.Draw(image)
            draw.rectangle((8, 4, size[0] - 9, size[1] - 5), fill=(45, 69, 76, 255))
            for left, right in ((13, 32), (38, 57), (63, 82), (88, 107)):
                draw.rectangle((left, 10, right, size[1] - 11), fill=(169, 209, 232, 180))
        else:
            for x in range(1, size[0] - 1):
                for y in range(1, size[1] - 1):
                    image.putpixel((x, y), (169, 209, 232, 180))
        if slot == "backdrop.beijing-cbd":
            image = Image.new("RGBA", size, (169, 209, 232, 255))
        normalized = normalize_slot(image, slot, spec_path=CORE_V2_PACK_SPEC)
        metadata = slot_metadata(slot, CORE_V2_PACK_SPEC)
        assert normalized.size == size
        assert set(normalized.getchannel("A").getdata()).issubset(
            {0, 96, 128, 160, 192, 255}
        )
        if "wall-window" in slot:
            assert set(normalized.getchannel("A").getdata()) == {0, 128, 255}
        elif slot != "backdrop.beijing-cbd":
            assert set(normalized.getchannel("A").getdata()).issubset({0, 255})
        assert metadata["packId"] == "core-v2"
        assert metadata["anchor"] == (
            {"x": 40, "y": 70} if slot.startswith("furniture.focus-desk") else metadata["anchor"]
        )
        if "wall-" in slot and not slot.endswith("column"):
            assert metadata["orientation"] in {"nw", "ne"}
            assert metadata["wallFaceHeight"] == 56
            if "window" in slot:
                assert metadata["paneAlpha"] == 128
                assert metadata["paneCount"] == 4

        wrong = Image.new("RGBA", (size[0] + 1, size[1]), (0, 0, 0, 0))
        wrong.putpixel((1, 1), (169, 209, 232, 255))
        with pytest.raises(AssetNormalizationError, match="原生帧尺寸"):
            normalize_slot(wrong, slot, spec_path=CORE_V2_PACK_SPEC)

    nw = slot_metadata("furniture.focus-desk-nw", CORE_V2_PACK_SPEC)
    ne = slot_metadata("furniture.focus-desk-ne", CORE_V2_PACK_SPEC)
    assert nw["interactionPoints"] == [
        {"id": "seat-work", "kind": "work-seat", "x": 1, "y": 2, "facing": "northwest"}
    ]
    assert ne["interactionPoints"] == [
        {"id": "seat-work", "kind": "work-seat", "x": -1, "y": 1, "facing": "northeast"}
    ]


def test_core_v2_prepare_keys_border_and_contains_subject_in_native_frame() -> None:
    source = Image.new("RGBA", (240, 180), (255, 0, 255, 255))
    draw = ImageDraw.Draw(source)
    draw.polygon(
        [(35, 105), (105, 70), (205, 120), (135, 158)],
        fill=(149, 97, 62, 255),
    )
    # Enclosed generated background must be removed after the edge confirms
    # that magenta is the reserved chroma key.
    source.putpixel((120, 112), (255, 0, 255, 255))
    source.putpixel((121, 112), (145, 15, 160, 255))

    keyed = remove_chroma_background(source)
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((120, 112))[3] == 0
    assert keyed.getpixel((121, 112))[3] == 0

    first, first_report = prepare_native_candidate(
        source,
        "furniture.focus-desk-nw",
        spec_path=CORE_V2_PACK_SPEC,
    )
    second, second_report = prepare_native_candidate(
        source,
        "furniture.focus-desk-nw",
        spec_path=CORE_V2_PACK_SPEC,
    )
    assert first.size == (80, 80)
    assert first.tobytes() == second.tobytes()
    assert first_report == second_report
    assert first_report["transform"]["mode"] == "subject-contain"
    assert first_report["chromaKey"]["color"] == "#FF00FF"
    assert first_report["chromaKey"]["tolerance"] == 52
    assert first_report["chromaKey"]["backgroundConfirmation"] == (
        "edge-connected-or-existing-alpha"
    )
    assert first_report["chromaKey"]["borderConnectedOnly"] is False
    assert first_report["chromaKey"]["globalKeyRemoval"] is True
    assert first_report["chromaKey"]["fringePolicy"] == (
        "reserved-magenta-cleanup-fail-closed"
    )
    assert first_report["chromaKey"]["magentaFringeRemoved"] == 1
    assert first_report["chromaKey"]["residualPixels"] == 0
    assert first.getchannel("A").getbbox() is not None
    assert first.getpixel((0, 0))[3] == 0


def test_chroma_preparation_accepts_an_already_transparent_non_keyed_source() -> None:
    source = Image.new("RGBA", (40, 32), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((7, 5, 31, 27), fill=(149, 97, 62, 255))

    prepared = remove_chroma_background(source)

    assert prepared.tobytes() == source.tobytes()
    assert prepared.getchannel("A").getextrema() == (0, 255)


def test_native_slot_rejects_reserved_magenta_fringe_without_prepare() -> None:
    source = Image.new("RGBA", (80, 64), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((12, 8, 67, 61), fill=(149, 97, 62, 255))
    source.putpixel((67, 20), (145, 15, 160, 255))

    with pytest.raises(AssetNormalizationError, match="品红色键或晕边"):
        normalize_slot(
            source,
            "furniture.low-bookcase",
            spec_path=CORE_V2_PACK_SPEC,
        )


@pytest.mark.parametrize(
    ("slot", "points", "expected_sign"),
    [
        (
            "structure.wall-solid-nw",
            [(20, 35), (185, 95), (185, 150), (20, 90)],
            1,
        ),
        (
            "structure.wall-solid-ne",
            [(20, 95), (185, 35), (185, 90), (20, 150)],
            -1,
        ),
    ],
)
def test_core_v2_prepare_verifies_native_wall_direction(
    slot: str,
    points: list[tuple[int, int]],
    expected_sign: int,
) -> None:
    source = Image.new("RGBA", (210, 180), (255, 0, 255, 255))
    ImageDraw.Draw(source).polygon(points, fill=(157, 178, 189, 255))
    prepared, report = prepare_native_candidate(
        source,
        slot,
        spec_path=CORE_V2_PACK_SPEC,
    )
    assert prepared.size == (96, 88)
    slope = report["orientationCheck"]["screenSlope"]
    assert slope * expected_sign > 0.04
    metadata = slot_metadata(slot, CORE_V2_PACK_SPEC)
    assert report["transform"]["mode"] == "footprint-ground-and-top-axis-lock"
    assert metadata["wallFaceHeight"] == 56
    expected_top_axis = {
        endpoint: {
            "x": metadata["groundAxis"][endpoint]["x"],
            "y": metadata["groundAxis"][endpoint]["y"] - 56,
        }
        for endpoint in ("start", "end")
    }
    assert report["transform"]["topAxis"] == expected_top_axis
    assert report["transform"]["wallFaceHeight"] == 56
    assert wall_ground_axis_pixels(prepared, metadata["groundAxis"]) == metadata[
        "groundAxis"
    ]
    assert wall_face_geometry_pixels(prepared, metadata["groundAxis"]) == {
        "groundAxis": metadata["groundAxis"],
        "topAxis": expected_top_axis,
        "faceHeight": {"start": 56, "end": 56},
    }

    wrong_slot = (
        "structure.wall-solid-ne"
        if slot.endswith("-nw")
        else "structure.wall-solid-nw"
    )
    with pytest.raises(AssetNormalizationError, match="方向.*不符"):
        prepare_native_candidate(source, wrong_slot, spec_path=CORE_V2_PACK_SPEC)


def test_core_v2_prepare_backdrop_uses_deterministic_full_canvas_cover() -> None:
    source = Image.new("RGBA", (800, 400), (142, 187, 214, 255))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 220, 799, 399), fill=(61, 96, 120, 255))
    prepared, report = prepare_native_candidate(
        source,
        "backdrop.beijing-cbd",
        spec_path=CORE_V2_PACK_SPEC,
        focus_y=0.65,
    )
    assert prepared.size == (640, 360)
    assert prepared.getchannel("A").getextrema() == (255, 255)
    assert report["transform"]["mode"] == "full-canvas-cover"
    assert report["transform"]["resizedSize"] == [720, 360]
    assert report["transform"]["crop"] == [40, 0, 680, 360]
    assert report["transform"]["scale"] == {
        "x": 0.9,
        "y": 0.9,
        "uniformRequested": 0.9,
    }
    assert "focusY" not in report["transform"]
    assert len(report["sourceSha256"]) == 64
    assert len(report["outputSha256"]) == 64


def test_core_v2_prepare_backdrop_preserves_native_full_canvas() -> None:
    source = Image.new("RGBA", (640, 360), (142, 187, 214, 255))
    prepared, report = prepare_native_candidate(
        source,
        "backdrop.beijing-cbd",
        spec_path=CORE_V2_PACK_SPEC,
    )
    assert prepared.tobytes() == source.tobytes()
    assert report["transform"] == {
        "mode": "full-canvas-native",
        "scale": {"x": 1.0, "y": 1.0, "uniformRequested": 1.0},
        "resizedSize": [640, 360],
        "crop": [0, 0, 640, 360],
        "resampling": "nearest",
        "alphaLevels": [0, 255],
    }


def test_core_v2_prepare_backdrop_rejects_transparency() -> None:
    source = Image.new("RGBA", (640, 360), (142, 187, 214, 255))
    source.putpixel((0, 0), (142, 187, 214, 0))
    with pytest.raises(AssetNormalizationError, match="完全不透明"):
        prepare_native_candidate(
            source,
            "backdrop.beijing-cbd",
            spec_path=CORE_V2_PACK_SPEC,
        )


def test_component_glass_mask_keeps_panes_uniform_and_rails_opaque() -> None:
    source = Image.new("RGBA", (128, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    frame_color = (45, 69, 76, 255)
    rail_highlight = (169, 209, 232, 180)
    draw.rectangle((5, 5, 122, 90), fill=frame_color)
    pane_bounds = ((10, 12, 31, 83), (36, 12, 57, 83), (62, 12, 83, 83), (88, 12, 109, 83))
    for left, top, right, bottom in pane_bounds:
        for y in range(top, bottom + 1):
            # A dark glass band exercises grow pixels; the remaining area is
            # a bright seed so the entire connected pane becomes alpha 128.
            lightness = 110 if y < top + 10 else 205
            color = (lightness - 24, lightness - 10, lightness, 180)
            draw.line((left, y, right, y), fill=color)
    draw.rectangle((6, 6, 121, 9), fill=rail_highlight)

    prepared, report = _apply_component_glass_alpha(
        source,
        pane_alpha=128,
        pane_count=4,
    )

    assert set(prepared.getchannel("A").getdata()) == {0, 128, 255}
    assert prepared.getpixel((10, 12))[3] == 128
    assert prepared.getpixel((20, 40))[3] == 128
    assert prepared.getpixel((60, 40))[3] == 255
    assert prepared.getpixel((20, 7))[3] == 255
    assert prepared.getpixel((0, 0))[3] == 0
    assert report["paneComponentCount"] == report["paneCountExpected"] == 4
    assert report["panePixels"] == sum(
        (right - left + 1) * (bottom - top + 1)
        for left, top, right, bottom in pane_bounds
    )
    assert all(
        component["seedCoverage"] >= 0.70
        for component in report["components"]
        if component["accepted"]
    )

    merged = source.copy()
    ImageDraw.Draw(merged).rectangle((32, 12, 35, 83), fill=(181, 195, 205, 255))
    with pytest.raises(AssetNormalizationError, match="规格要求 4 个"):
        _apply_component_glass_alpha(merged, pane_alpha=128, pane_count=4)

    seedless = source.copy()
    ImageDraw.Draw(seedless).rectangle((10, 12, 31, 83), fill=(86, 100, 110, 255))
    with pytest.raises(AssetNormalizationError, match="规格要求 4 个"):
        _apply_component_glass_alpha(seedless, pane_alpha=128, pane_count=4)


def test_core_v2_prepare_window_assigns_discrete_glass_but_keeps_frame_opaque() -> None:
    source = Image.new("RGBA", (240, 180), (255, 0, 255, 255))
    draw = ImageDraw.Draw(source)
    points = [(20, 38), (220, 104), (220, 158), (20, 92)]
    draw.polygon(points, fill=(190, 210, 220, 255), outline=(45, 69, 76, 255), width=8)
    draw.line((70, 55, 70, 109), fill=(45, 69, 76, 255), width=7)
    draw.line((120, 71, 120, 125), fill=(45, 69, 76, 255), width=7)
    draw.line((170, 88, 170, 142), fill=(45, 69, 76, 255), width=7)

    prepared, report = prepare_native_candidate(
        source,
        "structure.wall-window-nw",
        spec_path=CORE_V2_PACK_SPEC,
    )
    alpha_values = set(prepared.getchannel("A").getdata())
    assert alpha_values == {0, 128, 255}
    detection = report["transform"]["glassPaneDetection"]
    assert detection["paneAlpha"] == 128
    assert detection["paneComponentCount"] == detection["paneCountExpected"] == 4
    assert detection["panePixels"] > 0
    assert detection["opaqueStructurePixels"] > 0


def test_preparation_report_is_bound_to_exact_pack_slot_and_input(tmp_path: Path) -> None:
    source = Image.new("RGBA", (160, 120), (255, 0, 255, 255))
    ImageDraw.Draw(source).rectangle((30, 20, 130, 105), fill=(149, 97, 62, 255))
    prepared, report = prepare_native_candidate(
        source,
        "furniture.low-bookcase",
        spec_path=CORE_V2_PACK_SPEC,
    )
    prepared_path = tmp_path / "prepared.png"
    prepared.save(prepared_path, format="PNG", optimize=True)
    # The function-level report hashes canonical image bytes; the CLI records
    # the exact emitted file bytes before handing the report to `slot`.
    import hashlib

    report["outputSha256"] = hashlib.sha256(prepared_path.read_bytes()).hexdigest()
    report_path = tmp_path / "prepared.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    loaded = load_preparation_report(
        report_path,
        pack_id="core-v2",
        slot="furniture.low-bookcase",
        prepared_source=prepared_path,
    )
    assert loaded["sourceSha256"] == report["sourceSha256"]
    with pytest.raises(AssetNormalizationError, match="槽位不匹配"):
        load_preparation_report(
            report_path,
            pack_id="core-v2",
            slot="furniture.entry-bench",
            prepared_source=prepared_path,
        )
    prepared_path.write_bytes(prepared_path.read_bytes() + b"changed")
    with pytest.raises(AssetNormalizationError, match="SHA-256 不匹配"):
        load_preparation_report(
            report_path,
            pack_id="core-v2",
            slot="furniture.low-bookcase",
            prepared_source=prepared_path,
        )


def test_wall_preparation_report_rejects_top_axis_drift(tmp_path: Path) -> None:
    source = Image.new("RGBA", (210, 180), (255, 0, 255, 255))
    ImageDraw.Draw(source).polygon(
        [(20, 35), (185, 95), (185, 150), (20, 90)],
        fill=(157, 178, 189, 255),
    )
    prepared, report = prepare_native_candidate(
        source,
        "structure.wall-solid-nw",
        spec_path=CORE_V2_PACK_SPEC,
    )
    prepared_path = tmp_path / "wall.png"
    prepared.save(prepared_path, format="PNG", optimize=True)
    import hashlib

    report["outputSha256"] = hashlib.sha256(prepared_path.read_bytes()).hexdigest()
    report_path = tmp_path / "wall.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert load_preparation_report(
        report_path,
        pack_id="core-v2",
        slot="structure.wall-solid-nw",
        prepared_source=prepared_path,
    )["transform"]["wallFaceHeight"] == 56

    report["transform"]["topAxis"]["start"]["y"] += 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(AssetNormalizationError, match="顶底轴规格无效"):
        load_preparation_report(
            report_path,
            pack_id="core-v2",
            slot="structure.wall-solid-nw",
            prepared_source=prepared_path,
        )


@pytest.mark.parametrize(
    ("slot", "cell_count", "orientation"),
    [
        ("structure.wall-solid-nw", 3, "nw"),
        ("structure.wall-solid-ne", 3, "ne"),
        ("structure.wall-window-nw", 4, "nw"),
        ("structure.wall-window-ne", 4, "ne"),
        ("structure.wall-door-ne", 3, "ne"),
    ],
)
def test_core_v2_wall_ground_and_top_axes_join_at_adjacent_placement_without_overlap(
    slot: str,
    cell_count: int,
    orientation: str,
) -> None:
    metadata = slot_metadata(slot, CORE_V2_PACK_SPEC)
    axis = metadata["groundAxis"]
    anchor = metadata["anchor"]
    start = axis["start"]
    end = axis["end"]
    wall_face_height = metadata["wallFaceHeight"]
    top_start = {"x": start["x"], "y": start["y"] - wall_face_height}
    top_end = {"x": end["x"], "y": end["y"] - wall_face_height}
    assert wall_face_height == 56
    assert min(top_start["y"], top_end["y"]) >= 0
    assert start["x"] + end["x"] == anchor["x"] * 2
    assert start["y"] + end["y"] == anchor["y"] * 2
    assert end["x"] - start["x"] == cell_count * 16
    assert abs(end["y"] - start["y"]) == cell_count * 8

    if orientation == "nw":
        adjacent_ground = (cell_count * 16, cell_count * 8)
        next_start = (
            adjacent_ground[0] + start["x"] - anchor["x"],
            adjacent_ground[1] + start["y"] - anchor["y"],
        )
        current_end = (end["x"] - anchor["x"], end["y"] - anchor["y"])
        assert next_start == current_end
        next_top_start = (
            adjacent_ground[0] + top_start["x"] - anchor["x"],
            adjacent_ground[1] + top_start["y"] - anchor["y"],
        )
        current_top_end = (
            top_end["x"] - anchor["x"],
            top_end["y"] - anchor["y"],
        )
        assert next_top_start == current_top_end
    else:
        adjacent_ground = (-cell_count * 16, cell_count * 8)
        next_end = (
            adjacent_ground[0] + end["x"] - anchor["x"],
            adjacent_ground[1] + end["y"] - anchor["y"],
        )
        current_start = (
            start["x"] - anchor["x"],
            start["y"] - anchor["y"],
        )
        assert next_end == current_start
        next_top_end = (
            adjacent_ground[0] + top_end["x"] - anchor["x"],
            adjacent_ground[1] + top_end["y"] - anchor["y"],
        )
        current_top_start = (
            top_start["x"] - anchor["x"],
            top_start["y"] - anchor["y"],
        )
        assert next_top_end == current_top_start


@pytest.mark.parametrize("slot", FLOOR_SLOTS)
def test_floor_slot_uses_canonical_diamond_and_tiles_without_3x3_holes(
    slot: str,
) -> None:
    # Deliberately irregular AI-like alpha, including holes near its silhouette.
    source = Image.new("RGBA", (83, 43), (0, 0, 0, 0))
    dark = (13, 34, 40, 255)
    light = (23, 52, 58, 255)
    for y in range(4, 39):
        for x in range(7, 76):
            if (x + 2 * y) % 11 == 0 or (x in {8, 74} and y % 3 == 0):
                continue
            source.putpixel((x, y), dark if x < 42 else light)

    tile = normalize_slot(source, slot)
    expected_tile_alpha = canonical_diamond_alpha()
    assert tile.size == (32, 16)
    assert tile.getchannel("A").tobytes() == expected_tile_alpha.tobytes()
    assert [
        sum(1 for value in expected_tile_alpha.crop((0, y, 32, y + 1)).getdata() if value)
        for y in range(16)
    ] == [2, 6, 10, 14, 18, 22, 26, 30, 30, 26, 22, 18, 14, 10, 6, 2]
    visible_colors = {
        pixel[:3] for pixel in tile.getdata() if pixel[3] == 255
    }
    assert dark[:3] in visible_colors
    assert light[:3] in visible_colors

    # Real 3x3 composition using the runtime projection offsets. The union of
    # nine 32x16 diamonds must equal one solid 96x48 diamond exactly.
    composite = Image.new("RGBA", (96, 48), (0, 0, 0, 0))
    for grid_x in range(3):
        for grid_y in range(3):
            screen_x = (grid_x - grid_y) * 16 + 32
            screen_y = (grid_x + grid_y) * 8
            composite.alpha_composite(tile, (screen_x, screen_y))
    expected_union = canonical_diamond_alpha(96, 48)
    actual_alpha = composite.getchannel("A")
    assert actual_alpha.tobytes() == expected_union.tobytes()
    assert all(
        actual_alpha.getpixel(point) == 0
        for point in ((0, 0), (95, 0), (0, 47), (95, 47))
    )
