from __future__ import annotations

import json
import shutil

import pytest
from PIL import Image

from codex_v0.character_consistency import inspect_character_consistency
from codex_v0.character_motion import (
    _qa_diff_columns,
    ACTION_COLUMN_START,
    ACTION_FRAME_COUNTS,
    ACTION_ORDER,
    DEFAULT_RIG_PATH,
    DIRECTIONS,
    FRAME_COUNT,
    READABILITY_CONSTRAINTS,
    SHEET_COLUMNS,
    MotionRigError,
    POLICY_ID,
    SHEET_SIZE,
    compile_character_motion,
    generate_qa_artifacts,
    verify_character_motion,
)


def normalized_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {
        **metadata,
        "frames": [
            {
                "x": (index % SHEET_COLUMNS) * 24,
                "y": (index // SHEET_COLUMNS) * 48,
                "width": 24,
                "height": 48,
            }
            for index in range(FRAME_COUNT)
        ],
    }


def test_canonical_rig_compiles_byte_identically() -> None:
    first = compile_character_motion()
    second = compile_character_motion()

    assert first.png_bytes == second.png_bytes
    assert first.image.size == SHEET_SIZE
    assert first.report == second.report
    assert first.report["policy"] == POLICY_ID
    assert first.report["verified"] is True
    assert first.report["frameCount"] == FRAME_COUNT
    assert first.report["binaryAlpha"] is True
    assert first.report["paletteLocked"] is True
    assert first.report["integerTransforms"] is True
    assert first.report["transformPolicy"] == {
        "rotation": False,
        "scale": False,
        "interpolation": False,
        "subpixel": False,
        "runtimeMirror": False,
    }
    assert first.report["componentReuse"]["identityLocked"] == ["head", "torso"]
    # The compiler is the single source of truth for the gate values; this
    # asserts the report carries them through rather than restating them.
    assert first.report["componentReuse"]["readabilityConstraints"] == READABILITY_CONSTRAINTS
    assert READABILITY_CONSTRAINTS["walkSupportFootRows"] == {"nearLeg": 45, "farLeg": 44}
    assert READABILITY_CONSTRAINTS["walkFootTravelPerFrame"] == 2
    assert set(first.report["geometry"]["masterBboxes"]) == set(DIRECTIONS)
    assert set(first.report["geometry"]["nativeScaleReadability"]) == set(DIRECTIONS)
    assert set(first.image.getchannel("A").getdata()) <= {0, 255}


def test_walk_and_seated_work_keep_identity_locked() -> None:
    compiled = compile_character_motion()
    consistency = inspect_character_consistency(
        compiled.image,
        normalized_metadata(compiled.metadata),
    )

    assert consistency["ok"] is True
    # Every frame except each direction's idle master is checked as a candidate.
    assert consistency["summary"]["checkedFrames"] == len(DIRECTIONS) * (FRAME_COUNT // len(DIRECTIONS) - 1)
    assert consistency["summary"]["failedFrames"] == 0
    for direction in DIRECTIONS:
        geometry = compiled.report["geometry"]["frameDiffPixels"][direction]
        assert all(value > 0 for value in geometry["walk"])
        assert all(value > 0 for value in geometry["work"])
        motion = compiled.report["geometry"]["motion"][direction]
        # Something has to hold the character up in every single walk frame.
        assert all(motion["supportFootsPerWalkFrame"])
        assert len(motion["supportFootsPerWalkFrame"]) == ACTION_FRAME_COUNTS["walk"]
        # Both feet are down only on the two contact frames.
        assert [len(feet) for feet in motion["supportFootsPerWalkFrame"]] == [2, 1, 1, 1, 2, 1, 1, 1]
        # Work used to change four pixels across the whole action.
        assert min(motion["limbArticulation"]["work"]) >= READABILITY_CONSTRAINTS["minimumArticulation"]["work"]
        assert min(motion["limbArticulation"]["walk"]) >= READABILITY_CONSTRAINTS["minimumArticulation"]["walk"]
        assert motion["legContrastPixels"] >= READABILITY_CONSTRAINTS["minimumLegContrastPixels"]
    assert all(
        bbox[-1] == 46
        for bbox in compiled.report["geometry"]["masterBboxes"].values()
    )
    for report in compiled.report["geometry"]["nativeScaleReadability"].values():
        assert report["headAreaRatio"] <= READABILITY_CONSTRAINTS["headAreaRatio"][1]
        assert report["inkRatio"] <= 0.32
        assert min(report["armPixelsOutsideTorso"].values()) >= 16
        assert report["legGapRows"] >= 5
        assert report["farFootBaseline"] == 44
        assert report["nearFootBaseline"] == 45
    direction_diffs = compiled.report["geometry"]["directionAlphaXor"]
    assert min(direction_diffs.values()) >= 24
    assert direction_diffs["southeast:northwest"] >= 40
    assert direction_diffs["southwest:northeast"] >= 40


def test_work_diffs_stay_inside_shifted_arm_regions_and_heads_are_reused() -> None:
    compiled = compile_character_motion()
    rig = json.loads(DEFAULT_RIG_PATH.read_text(encoding="utf-8"))
    for row, direction in enumerate(DIRECTIONS):
        work_column = ACTION_COLUMN_START["work"]
        work_a = compiled.image.crop((work_column * 24, row * 48, (work_column + 1) * 24, (row + 1) * 48))
        work_b = compiled.image.crop(((work_column + 1) * 24, row * 48, (work_column + 2) * 24, (row + 1) * 48))
        changed = [
            (index % 24, index // 24)
            for index, (left, right) in enumerate(zip(work_a.getdata(), work_b.getdata(), strict=True))
            if left != right
        ]
        arm_regions = [rig["allowedChangeRegions"][component] for component in ("farArm", "nearArm")]
        assert changed
        assert all(
            any(x0 <= x < x1 and y0 + 3 <= y < y1 + 3 for x0, y0, x1, y1 in arm_regions)
            for x, y in changed
        )

        head_strip = Image.open(
            DEFAULT_RIG_PATH.parent / "layers" / direction / rig["components"]["head"]["file"]
        ).convert("RGBA")
        head_offsets = [
            frame["offsets"]["head"]
            for action in ACTION_ORDER
            for frame in rig["animations"][action]
        ]
        for column, offset in enumerate(head_offsets):
            frame = compiled.image.crop((column * 24, row * 48, (column + 1) * 24, (row + 1) * 48))
            for y in range(48):
                for x in range(24):
                    pixel = head_strip.getpixel((x, y))
                    if pixel[3] == 255:
                        assert frame.getpixel((x + offset["x"], y + offset["y"])) == pixel


def test_verifier_rejects_even_one_changed_pixel() -> None:
    compiled = compile_character_motion()
    changed = compiled.image.copy()
    changed.putpixel((0, 0), (13, 34, 40, 255))

    report = verify_character_motion(changed, compiled.metadata)

    assert report["verified"] is False
    assert report["errors"][0]["code"] == "sheet_pixels_mismatch"


def test_verifier_rejects_metadata_reordering() -> None:
    compiled = compile_character_motion()
    metadata = json.loads(json.dumps(compiled.metadata))
    metadata["animations"]["walk"]["southeast"] = [2, 1, 3, 4]

    report = verify_character_motion(compiled.image, metadata)

    assert report["verified"] is False
    assert any(error["code"] == "metadata_mismatch" for error in report["errors"])


def test_compiler_rejects_sem_transparent_layer(tmp_path) -> None:
    copied = tmp_path / "gus-rig"
    shutil.copytree(DEFAULT_RIG_PATH.parent, copied)
    layer_path = copied / "layers" / "southeast" / "head.png"
    layer = Image.open(layer_path).convert("RGBA")
    layer.putpixel((8, 9), (237, 240, 222, 128))
    layer.save(layer_path, format="PNG", optimize=False, compress_level=9)

    with pytest.raises(MotionRigError, match="semi-transparent"):
        compile_character_motion(copied / "rig.json")


def test_compiler_rejects_pixels_outside_declared_component_region(tmp_path) -> None:
    copied = tmp_path / "gus-rig"
    shutil.copytree(DEFAULT_RIG_PATH.parent, copied)
    layer_path = copied / "layers" / "southeast" / "near-arm.png"
    layer = Image.open(layer_path).convert("RGBA")
    layer.putpixel((0, 0), (13, 34, 40, 255))
    layer.save(layer_path, format="PNG", optimize=False, compress_level=9)

    with pytest.raises(MotionRigError, match="leaves the declared nearArm change region"):
        compile_character_motion(copied / "rig.json")


def test_compiler_rejects_unreadable_head_and_merged_legs(tmp_path) -> None:
    copied = tmp_path / "gus-rig"
    shutil.copytree(DEFAULT_RIG_PATH.parent, copied)
    head_path = copied / "layers" / "southeast" / "head.png"
    head = Image.open(head_path).convert("RGBA")
    head.putpixel((10, 9), (237, 240, 222, 255))
    head.save(head_path, format="PNG", optimize=False, compress_level=9)
    with pytest.raises(
        MotionRigError,
        match="master is not readable at native scale|head height violates native-scale readability",
    ):
        compile_character_motion(copied / "rig.json")

    shutil.rmtree(copied)
    shutil.copytree(DEFAULT_RIG_PATH.parent, copied)
    far_leg_path = copied / "layers" / "southeast" / "far-leg.png"
    far_leg = Image.open(far_leg_path).convert("RGBA")
    for y in range(35, 43):
        for x in (12, 13):
            far_leg.putpixel((x, y), (13, 34, 40, 255))
    far_leg.save(far_leg_path, format="PNG", optimize=False, compress_level=9)
    with pytest.raises(MotionRigError, match="legs merge into a single column"):
        compile_character_motion(copied / "rig.json")


def test_compiler_rejects_clipped_locked_layer_and_runtime_mirroring(tmp_path) -> None:
    copied = tmp_path / "gus-rig"
    shutil.copytree(DEFAULT_RIG_PATH.parent, copied)
    head_path = copied / "layers" / "southeast" / "head.png"
    head = Image.open(head_path).convert("RGBA")
    head.putpixel((0, 47), (13, 34, 40, 255))
    head.save(head_path, format="PNG", optimize=False, compress_level=9)
    with pytest.raises(MotionRigError, match="would be clipped"):
        compile_character_motion(copied / "rig.json")

    shutil.rmtree(copied)
    shutil.copytree(DEFAULT_RIG_PATH.parent, copied)
    rig_path = copied / "rig.json"
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    rig["transformPolicy"]["runtimeMirror"] = True
    rig_path.write_text(json.dumps(rig), encoding="utf-8")
    with pytest.raises(MotionRigError, match="runtime mirroring"):
        compile_character_motion(rig_path)


def test_qa_artifacts_are_deterministic_nearest_neighbour_evidence(tmp_path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = generate_qa_artifacts(first_dir)
    second = generate_qa_artifacts(second_dir)

    assert first == second
    looping_actions = [action for action in ACTION_ORDER if ACTION_FRAME_COUNTS[action] > 1]
    assert len(first["artifacts"]) == 4 + len(DIRECTIONS) * len(looping_actions)
    assert {entry["name"] for entry in first["artifacts"]} == {
        "gus-contact-sheet-8x.png",
        "gus-action-diff-heatmap-8x.png",
        "gus-idle-native-1x.png",
        "gus-idle-native-2x.png",
        *{
            f"gus-{direction}-{action}-8x.gif"
            for direction in DIRECTIONS
            for action in looping_actions
        },
    }
    with Image.open(first_dir / "gus-contact-sheet-8x.png") as contact:
        assert contact.size == (24 * SHEET_COLUMNS * 8, 48 * len(DIRECTIONS) * 8)
    with Image.open(first_dir / "gus-action-diff-heatmap-8x.png") as heatmap:
        assert heatmap.size == (24 * len(_qa_diff_columns()) * 8, 48 * len(DIRECTIONS) * 8)
    with Image.open(first_dir / "gus-idle-native-1x.png") as native:
        assert native.size == (24 * 4, 48)
    with Image.open(first_dir / "gus-idle-native-2x.png") as native_2x:
        assert native_2x.size == (24 * 4 * 2, 48 * 2)
    with Image.open(first_dir / "gus-southeast-walk-8x.gif") as walk:
        assert walk.size == (24 * 8, 48 * 8)
        assert walk.n_frames == ACTION_FRAME_COUNTS["walk"]
    with Image.open(first_dir / "gus-southeast-work-8x.gif") as work:
        assert work.n_frames == ACTION_FRAME_COUNTS["work"]
