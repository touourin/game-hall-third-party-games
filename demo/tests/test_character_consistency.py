from __future__ import annotations

from PIL import Image

from codex_v0.character_consistency import inspect_character_consistency
from codex_v0.character_motion import (
    DIRECTIONS,
    FRAME_COUNT as CHARACTER_FRAME_COUNT,
    SHEET_COLUMNS,
    SHEET_SIZE as GUS_SHEET_SIZE,
    animation_metadata,
)


def character_metadata() -> dict[str, object]:
    frames = [
        {
            "x": (index % SHEET_COLUMNS) * 24,
            "y": (index // SHEET_COLUMNS) * 48,
            "width": 24,
            "height": 48,
        }
        for index in range(CHARACTER_FRAME_COUNT)
    ]
    return {"frames": frames, "animations": animation_metadata()}


def identity_locked_sheet(*, drifting_frame: int | None = None) -> Image.Image:
    sheet = Image.new("RGBA", GUS_SHEET_SIZE, (0, 0, 0, 0))
    for index in range(CHARACTER_FRAME_COUNT):
        frame = Image.new("RGBA", (24, 48), (0, 0, 0, 0))
        head_left, head_right = (5, 20) if index == drifting_frame else (7, 18)
        head_color = (168, 120, 56, 255) if index == drifting_frame else (213, 216, 204, 255)
        for y in range(7, 22):
            for x in range(head_left, head_right):
                frame.putpixel((x, y), head_color)
        for y in range(22, 35):
            for x in range(8, 17):
                frame.putpixel((x, y), (49, 88, 79, 255))
        for y in range(35, 45):
            for x in range(9, 16):
                frame.putpixel((x, y), (13, 34, 40, 255))
        sheet.alpha_composite(frame, ((index % SHEET_COLUMNS) * 24, (index // SHEET_COLUMNS) * 48))
    return sheet


def test_identity_locked_character_passes_every_action_frame() -> None:
    report = inspect_character_consistency(identity_locked_sheet(), character_metadata())

    assert report["ok"] is True
    # Each direction's idle master is the reference, not a candidate.
    assert report["summary"] == {
        "checkedFrames": CHARACTER_FRAME_COUNT - len(DIRECTIONS),
        "failedFrames": 0,
        "failureCount": 0,
        "minimumHeadScore": 1.0,
        "minimumCoreScore": 1.0,
    }


def test_changed_head_shape_is_a_hard_identity_failure() -> None:
    report = inspect_character_consistency(
        identity_locked_sheet(drifting_frame=2),
        character_metadata(),
    )

    assert report["ok"] is False
    frame = next(entry for entry in report["frames"] if entry["frameIndex"] == 2)
    assert frame["passed"] is False
    assert "head_identity_drift" in frame["failures"]
    assert report["summary"]["failedFrames"] == 1
