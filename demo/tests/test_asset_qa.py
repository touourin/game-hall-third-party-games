from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from codex_v0.asset_qa import (
    AssetQaError,
    CONTACT_NAME,
    MID_NAME,
    OCCLUSION_NAME,
    OPENING_NAME,
    RECEIPT_NAME,
    CoreV2AssetQa,
    QaAsset,
    actor_depth,
    floor_front_edges,
    footprint_ground,
    placement_depth,
    scene_shell_from_manifest,
    sort_renderables,
    tower_shell_geometry,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent


def _save(image: Image.Image, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False, compress_level=9)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_assets(qa: CoreV2AssetQa, root: Path) -> dict[str, QaAsset]:
    assets: dict[str, QaAsset] = {}
    directions = ("southeast", "southwest", "northwest", "northeast")
    for index, slot in enumerate(qa.logical_slots):
        spec = qa.asset_specs.get(slot)
        if slot == "character.gus":
            spec = {
                "id": slot,
                "slot": slot,
                "kind": "character",
                "anchor": {"x": 12, "y": 46},
                "offset": {"x": 0, "y": 0},
                "footprint": [{"x": 0, "y": 0, "blocked": False}],
                "layer": 2,
            }
            image = Image.new("RGBA", (384, 192), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            frames = []
            for frame_index in range(64):
                x = frame_index % 16 * 24
                y = frame_index // 16 * 48
                frames.append({"x": x, "y": y, "width": 24, "height": 48})
                draw.rectangle((x + 7, y + 8, x + 17, y + 45), fill=(245, 245, 235, 255))
            animations = {}
            for row, direction in enumerate(directions):
                offset = row * 16
                animations.setdefault("idle", {})[direction] = list(range(offset, offset + 4))
                animations.setdefault("walk", {})[direction] = list(range(offset + 4, offset + 12))
                animations.setdefault("work", {})[direction] = list(range(offset + 12, offset + 16))
            metadata = {
                "slot": slot,
                "kind": "character",
                "frameWidth": 24,
                "frameHeight": 48,
                "columns": 16,
                "directionRows": list(directions),
                "frameCount": 64,
                "frames": frames,
                "animations": animations,
                "anchor": {"x": 12, "y": 46},
            }
        elif slot == "effect.good-card-heart":
            spec = {
                "id": slot,
                "slot": slot,
                "kind": "effect",
                "anchor": {"x": 12, "y": 20},
                "offset": {"x": 0, "y": 0},
                "footprint": [{"x": 0, "y": 0, "blocked": False}],
                "layer": 4,
            }
            image = Image.new("RGBA", (96, 24), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((4, 4, 20, 20), fill=(237, 128, 108, 255))
            metadata = {"slot": slot, "kind": "effect", "anchor": {"x": 12, "y": 20}}
        else:
            assert spec is not None, slot
            frame = spec["frame"]
            size = (
                (640, 360)
                if spec["kind"] == "backdrop"
                else (int(frame["width"]), int(frame["height"]))
            )
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            if spec["kind"] == "backdrop":
                draw.rectangle((0, 0, image.width - 1, image.height - 1), fill=(142, 187, 214, 255))
                draw.rectangle((0, image.height // 2, image.width - 1, image.height - 1), fill=(85, 127, 156, 255))
            elif spec["kind"] == "floor":
                draw.polygon(
                    ((image.width // 2, 0), (image.width - 1, image.height // 2), (image.width // 2, image.height - 1), (0, image.height // 2)),
                    fill=((80 + index * 7) % 220, (100 + index * 11) % 220, (120 + index * 13) % 220, 255),
                )
            else:
                draw.rectangle(
                    (1, 1, image.width - 2, image.height - 2),
                    fill=((70 + index * 17) % 220, (90 + index * 19) % 220, (110 + index * 23) % 220, 255),
                )
            metadata = {
                "slot": slot,
                "kind": spec["kind"],
                "anchor": spec.get("anchor", {"x": 0, "y": 0}),
                "footprint": spec.get("footprint", []),
            }
        path = root / f"{index:02d}-{slot.replace('.', '-')}.png"
        digest = _save(image, path)
        assets[slot] = QaAsset(
            slot=slot,
            kind=str(spec["kind"]),
            image_path=path,
            sha256=digest,
            metadata=metadata,
            spec=spec,
            provenance="synthetic-test",
        )
    return assets


def test_footprint_centroid_and_front_depth_are_not_first_cell() -> None:
    asset = {
        "footprint": [
            {"x": 0, "y": 0},
            {"x": 1, "y": 0},
            {"x": 2, "y": 0},
            {"x": 0, "y": 1},
            {"x": 1, "y": 1},
            {"x": 2, "y": 1},
        ]
    }
    placement = {"x": 3, "y": 2}

    assert footprint_ground(asset, placement) == (4.0, 2.5)
    assert placement_depth(asset, placement) == 8.0


def test_work_depth_lift_requires_matching_layout_seat() -> None:
    placements = [{"id": "desk", "depth": 8.0}]
    seats = [{"placementId": "desk", "id": "seat-nw"}]
    actor = {
        "x": 4,
        "y": 1,
        "activity": {"type": "work", "placementId": "desk", "seatId": "seat-nw"},
    }

    assert actor_depth(actor, placements, seats) == 8.0
    assert actor_depth({**actor, "activity": {**actor["activity"], "seatId": "forged"}}, placements, seats) == 5.7
    assert actor_depth({"x": 4, "y": 1}, placements, seats) == 5.7


def test_render_order_is_depth_then_layer_then_stable_input() -> None:
    items = [
        {"id": "late-stable", "depth": 4, "layer": 1},
        {"id": "front", "depth": 7, "layer": 0},
        {"id": "early-layer", "depth": 4, "layer": 0},
        {"id": "later-stable", "depth": 4, "layer": 1},
    ]

    assert [item["id"] for item in sort_renderables(items)] == [
        "early-layer",
        "late-stable",
        "later-stable",
        "front",
    ]


def test_synthetic_full_pack_outputs_are_hash_deterministic(tmp_path: Path) -> None:
    qa = CoreV2AssetQa(
        PROJECT_DIR,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "derived" / "core-v2",
    )
    assets = _synthetic_assets(qa, tmp_path / "synthetic")

    first = qa.generate_from_assets(assets)
    first_bytes = {
        entry["name"]: (qa.output_dir / entry["name"]).read_bytes()
        for entry in first["outputs"]
    }
    first_receipt = (qa.output_dir / RECEIPT_NAME).read_bytes()
    second = qa.generate_from_assets(assets)

    assert first["outputs"] == second["outputs"]
    assert first_receipt == (qa.output_dir / RECEIPT_NAME).read_bytes()
    assert all(first_bytes[name] == (qa.output_dir / name).read_bytes() for name in first_bytes)
    assert [entry["name"] for entry in first["outputs"]] == [
        CONTACT_NAME,
        OPENING_NAME,
        MID_NAME,
        OCCLUSION_NAME,
    ]
    assert len(first["inputs"]) == 29
    assert (qa.output_dir / CONTACT_NAME).is_file()
    with Image.open(qa.output_dir / CONTACT_NAME) as contact:
        assert contact.size == (2640, 3108)
    for name in (OPENING_NAME, MID_NAME, OCCLUSION_NAME):
        with Image.open(qa.output_dir / name) as scene:
            assert scene.size == (640, 360)
    receipt = json.loads(first_receipt)
    assert receipt["geometryVersion"] == 2
    assert receipt["outputs"] == first["outputs"]
    assert set(receipt["actorVisibility"]) == {
        "world.mid-growth-v3",
        "qa.desk-work-occlusion",
    }
    assert min(
        actor["visiblePercent"]
        for actor in receipt["actorVisibility"]["world.mid-growth-v3"].values()
    ) >= 30
    assert all(
        actor["visiblePercent"] == 100
        for actor in receipt["actorVisibility"]["qa.desk-work-occlusion"].values()
    )
    shell = receipt["sceneShell"]
    assert shell == first["sceneShell"]
    assert shell["enabled"] is True
    assert shell["facadeDepth"] == 512
    assert shell["slabDepth"] == 8
    assert shell["ambientOcclusionDepth"] == 2
    assert shell["windowBandPitch"] == 12
    assert shell["drawOrder"] == ["background", "facade", "floor", "objects"]
    assert shell["facadeExcludedFromAutoFit"] is True
    assert set(shell["scenes"]) == {
        "world.opening-empty-v2",
        "world.mid-growth-v3",
        "qa.desk-work-occlusion",
    }
    assert all(
        scene["coverage"]["extendsToCanvasBottom"]
        and scene["coverage"]["bottomRowCoveredPixels"] > 0
        and scene["coverage"]["facadePixels"] > 0
        for scene in shell["scenes"].values()
    )
    # No AssetLab object was constructed and no database was created as a side
    # effect of rendering synthetic material.
    assert not (tmp_path / "data" / "asset-lab.sqlite3").exists()


def test_focus_desk_occlusion_fixture_keeps_both_workers_clear(tmp_path: Path) -> None:
    qa = CoreV2AssetQa(
        PROJECT_DIR,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "out",
    )
    assets = _synthetic_assets(qa, tmp_path / "synthetic")
    layout = qa._occlusion_layout()
    placements = {
        placement["id"]: (placement["x"], placement["y"])
        for placement in layout["placements"]
    }
    spawns = {
        spawn["playerId"]: (spawn["x"], spawn["y"])
        for spawn in layout["spawnPoints"]
    }

    assert placements["qa-focus-nw"] == (11, 2)
    assert placements["qa-focus-ne"] == (15, 4)
    assert spawns["eli"] == (12, 4)
    assert spawns["faye"] == (14, 5)
    visibility = qa.actor_visibility(layout, assets)
    assert visibility["eli"]["visiblePercent"] == 100
    assert visibility["faye"]["visiblePercent"] == 100


def test_backdrop_is_fixed_in_screen_space(tmp_path: Path) -> None:
    qa = CoreV2AssetQa(PROJECT_DIR, data_dir=tmp_path / "data", output_dir=tmp_path / "out")
    assets = _synthetic_assets(qa, tmp_path / "synthetic")
    scene = qa.render_scene(qa.layouts["world.opening-empty-v2"], assets)

    # The synthetic backdrop paints the upper half light blue at exact canvas
    # coordinates.  Camera fitting the world must not move or scale this pixel.
    assert scene.getpixel((5, 5)) == (142, 187, 214, 255)


def test_scene_shell_uses_xmax_ymax_edges_and_exact_depth_contract() -> None:
    layout = {"columns": 14, "rows": 9, "origin": {"x": 280, "y": 136}}
    manifest = json.loads(
        (PROJECT_DIR / "assets" / "core-v2-pack.spec.json").read_text(
            encoding="utf-8"
        )
    )
    shell = scene_shell_from_manifest(manifest)

    assert shell is not None
    edges = floor_front_edges(layout)
    assert edges["xMax"] == [
        {"x": 504.0 - index * 16, "y": 240.0 + index * 8}
        for index in range(10)
    ]
    assert edges["yMax"] == [
        {"x": 360.0 - index * 16, "y": 312.0 - index * 8}
        for index in range(15)
    ]
    assert edges["frontCorner"] == {"x": 360.0, "y": 312.0}

    geometry = tower_shell_geometry(layout, shell)
    assert geometry["facadeDepth"] == 512
    assert geometry["slabDepth"] == 8
    assert geometry["windowBandPitch"] == 12
    for face_id in ("xMax", "yMax"):
        face = geometry[face_id]
        assert len(face["ambientOcclusion"]) == 4
        assert round(
            face["ambientOcclusion"][2]["y"] - face["topEdge"][-1]["y"]
        ) == 2
        assert round(
            face["ambientOcclusion"][3]["y"] - face["topEdge"][0]["y"]
        ) == 2
        assert all(
            next_band - band == 12
            for band, next_band in zip(face["windowBands"], face["windowBands"][1:])
        )
        assert face["windowBands"][0] >= geometry["slabDepth"]
        assert face["windowBands"][-1] + 12 <= geometry["facadeDepth"]


def test_window_bands_follow_the_eave_instead_of_the_screen_horizontal() -> None:
    layout = {"columns": 14, "rows": 9, "origin": {"x": 280, "y": 136}}
    manifest = json.loads(
        (PROJECT_DIR / "assets" / "core-v2-pack.spec.json").read_text(
            encoding="utf-8"
        )
    )
    shell = scene_shell_from_manifest(manifest)
    assert shell is not None

    geometry = tower_shell_geometry(layout, shell)
    depth = geometry["xMax"]["windowBands"][3]
    height = int(shell["windowBandPitch"]) - 3

    for face_id in ("xMax", "yMax"):
        face = geometry[face_id]
        band = CoreV2AssetQa._window_band_points(face, depth, height)
        run = band[1][0] - band[0][0]
        rise = band[1][1] - band[0][1]
        # A curtain wall's floor line is horizontal in world space, so on the
        # 2:1 grid it falls one pixel for every two it travels sideways.
        # Drawing it at rise 0 flattens the whole tower into a billboard.
        assert rise != 0
        assert abs(rise / run) == 0.5
        eave = face["topEdge"][-1]
        assert rise / run == (
            (eave["y"] - face["topEdge"][0]["y"])
            / (eave["x"] - face["topEdge"][0]["x"])
        )
        assert band[3][1] - band[0][1] == height

    # Both faces belong to one tower, so band k meets its twin exactly at the
    # shared front corner.
    x_band = CoreV2AssetQa._window_band_points(geometry["xMax"], depth, height)
    y_band = CoreV2AssetQa._window_band_points(geometry["yMax"], depth, height)
    assert x_band[1] == y_band[0]
    assert x_band[2] == y_band[3]


def test_scene_shell_renders_between_full_canvas_background_and_floor(
    tmp_path: Path,
) -> None:
    qa = CoreV2AssetQa(
        PROJECT_DIR, data_dir=tmp_path / "data", output_dir=tmp_path / "out"
    )
    assets = _synthetic_assets(qa, tmp_path / "synthetic")
    backdrop = assets["backdrop.beijing-cbd"].image()
    assert backdrop.size == (640, 360)
    layout = qa.layouts["world.opening-empty-v2"]
    placements, seats = qa._placements(layout, assets)
    actors = qa._actors(layout, placements, seats)
    camera = qa._camera(layout, assets, placements, actors)
    shell = scene_shell_from_manifest(qa.pack_spec)
    assert shell is not None
    shell_layer, _ = qa._scene_shell_layer(layout, camera, shell)
    bottom_alpha = shell_layer.getchannel("A").crop((0, 359, 640, 360))
    covered_x = [x for x, alpha in enumerate(bottom_alpha.getdata()) if alpha]
    assert covered_x

    modern = qa.render_scene(layout, assets)
    legacy = qa.render_scene(layout, assets, manifest={"geometryVersion": 1})
    sample_x = covered_x[len(covered_x) // 2]
    # The old renderer leaves the 640x360 synthetic background intact here;
    # geometry v2 paints the downward facade over it.
    assert legacy.getpixel((sample_x, 359)) == backdrop.getpixel((sample_x, 359))
    assert modern.getpixel((sample_x, 359)) != legacy.getpixel((sample_x, 359))

    # A front-edge floor diamond intersects the shell but is drawn later. Find
    # one real overlap pixel and prove modern/legacy share the floor result.
    floor_layer = Image.new("RGBA", (640, 360), (0, 0, 0, 0))
    for y in range(int(layout["rows"])):
        for x in range(int(layout["columns"])):
            qa._draw_asset(
                floor_layer,
                assets[qa._floor_asset_id(layout, x, y)],
                qa._project(x, y, layout["origin"]),
                camera,
            )
    overlap = []
    shell_alpha = shell_layer.getchannel("A")
    floor_alpha = floor_layer.getchannel("A")
    for y in range(360):
        for x in range(640):
            if shell_alpha.getpixel((x, y)) and floor_alpha.getpixel((x, y)):
                overlap.append((x, y))
    assert overlap
    floor_x, floor_y = overlap[len(overlap) // 2]
    assert modern.getpixel((floor_x, floor_y)) == legacy.getpixel((floor_x, floor_y))


def test_manifest_without_scene_shell_keeps_legacy_qa_path(tmp_path: Path) -> None:
    qa = CoreV2AssetQa(
        PROJECT_DIR, data_dir=tmp_path / "data", output_dir=tmp_path / "out"
    )
    assets = _synthetic_assets(qa, tmp_path / "synthetic")
    legacy_manifest = {"geometryVersion": 1}

    assert scene_shell_from_manifest(legacy_manifest) is None
    assert qa.scene_shell_receipt(assets, manifest=legacy_manifest) == {
        "enabled": False
    }
    first = qa.render_scene(
        qa.layouts["world.opening-empty-v2"], assets, manifest=legacy_manifest
    )
    second = qa.render_scene(
        qa.layouts["world.opening-empty-v2"], assets, manifest=legacy_manifest
    )
    assert first.tobytes() == second.tobytes()


def test_present_scene_shell_is_validated_like_runtime_manifest() -> None:
    manifest = json.loads(
        (PROJECT_DIR / "assets" / "core-v2-pack.spec.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["geometryVersion"] = 1
    with pytest.raises(AssetQaError, match="geometryVersion >= 2"):
        scene_shell_from_manifest(manifest)

    manifest["geometryVersion"] = 2
    manifest["sceneShell"]["unexpected"] = True
    with pytest.raises(AssetQaError, match="unsupported fields"):
        scene_shell_from_manifest(manifest)
