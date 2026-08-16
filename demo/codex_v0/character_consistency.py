"""Deterministic identity checks for the 28-frame Gus sprite sheet.

Image models are good at proposing motion, but they are not an authority for
character identity.  This module treats each directional idle cell as the
canonical identity reference and verifies that the head and central torso stay
stable after a small integer translation.  Limbs may move; hair, face, and the
clothing core may not be reinvented from frame to frame.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image

from .isometric import DIRECTIONS


POLICY_ID = "canonical-idle-v1"
FALLBACK_MODE = "canonical-idle-bob"

ACTION_LIMITS: dict[str, dict[str, float | int]] = {
    # idle frame 0 is the identity master itself and is never compared against
    # itself; any further idle frames are a breathing loop, so they get the
    # tightest tolerances of all three actions.
    "idle": {
        "headScore": 0.86,
        "coreScore": 0.74,
        "minimumAreaRatio": 0.92,
        "maximumAreaRatio": 1.08,
        "maximumShift": 1,
    },
    "walk": {
        "headScore": 0.72,
        "coreScore": 0.56,
        "minimumAreaRatio": 0.72,
        "maximumAreaRatio": 1.32,
        "maximumShift": 2,
    },
    "work": {
        "headScore": 0.64,
        "coreScore": 0.42,
        "minimumAreaRatio": 0.64,
        "maximumAreaRatio": 1.46,
        "maximumShift": 3,
    },
}


def _visible_area(image: Image.Image) -> int:
    return sum(1 for value in image.getchannel("A").getdata() if value > 0)


def _visible_bounds(image: Image.Image) -> tuple[int, int, int, int] | None:
    return image.getchannel("A").getbbox()


def _canonical_master_report(
    direction: str,
    frame_index: int,
    image: Image.Image,
    *,
    declared_head_bounds: Sequence[int] | None = None,
) -> dict[str, Any] | None:
    bounds = _visible_bounds(image)
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    subject_height = bottom - top
    if (
        declared_head_bounds is not None
        and len(declared_head_bounds) == 4
        and all(isinstance(value, int) and not isinstance(value, bool) for value in declared_head_bounds)
        and 0 <= declared_head_bounds[0] < declared_head_bounds[2] <= image.width
        and 0 <= declared_head_bounds[1] < declared_head_bounds[3] <= image.height
    ):
        head_width = declared_head_bounds[2] - declared_head_bounds[0]
        head_height = declared_head_bounds[3] - declared_head_bounds[1]
    else:
        head_bottom = min(bottom, top + max(1, round(subject_height * 0.55)))
        head_bounds = image.crop((0, top, image.width, head_bottom)).getchannel("A").getbbox()
        if head_bounds is None:
            return None
        head_width = head_bounds[2] - head_bounds[0]
        head_height = head_bounds[3] - head_bounds[1]
    return {
        "direction": direction,
        "frameIndex": frame_index,
        "bounds": list(bounds),
        "width": right - left,
        "height": bottom - top,
        "footBaseline": bottom - 1,
        "headWidth": head_width,
        "headHeight": head_height,
    }


def _crop_frame(image: Image.Image, frame: Mapping[str, Any]) -> Image.Image:
    x = int(frame["x"])
    y = int(frame["y"])
    width = int(frame["width"])
    height = int(frame["height"])
    return image.crop((x, y, x + width, y + height)).convert("RGBA")


def _comparison_region(
    reference: Image.Image,
    *,
    vertical_start: float,
    vertical_end: float,
    horizontal_inset: float,
) -> tuple[int, int, int, int] | None:
    bounds = reference.getchannel("A").getbbox()
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    subject_width = max(1, right - left)
    subject_height = max(1, bottom - top)
    inset = round(subject_width * horizontal_inset)
    return (
        max(0, left + inset - 1),
        max(0, top + round(subject_height * vertical_start) - 1),
        min(reference.width, right - inset + 1),
        min(reference.height, top + round(subject_height * vertical_end) + 1),
    )


def _region_similarity(
    reference: Image.Image,
    candidate: Image.Image,
    region: tuple[int, int, int, int],
    *,
    maximum_shift: int,
) -> dict[str, Any]:
    reference_pixels = reference.load()
    candidate_pixels = candidate.load()
    best: dict[str, Any] | None = None
    left, top, right, bottom = region

    for shift_y in range(-maximum_shift, maximum_shift + 1):
        for shift_x in range(-maximum_shift, maximum_shift + 1):
            union = 0
            intersection = 0
            exact = 0
            for y in range(top, bottom):
                for x in range(left, right):
                    reference_pixel = reference_pixels[x, y]
                    source_x = x - shift_x
                    source_y = y - shift_y
                    candidate_pixel = (
                        candidate_pixels[source_x, source_y]
                        if 0 <= source_x < candidate.width and 0 <= source_y < candidate.height
                        else (0, 0, 0, 0)
                    )
                    reference_visible = reference_pixel[3] > 0
                    candidate_visible = candidate_pixel[3] > 0
                    if reference_visible or candidate_visible:
                        union += 1
                    if reference_visible and candidate_visible:
                        intersection += 1
                        if reference_pixel == candidate_pixel:
                            exact += 1

            alpha_iou = intersection / union if union else 0.0
            exact_ratio = exact / union if union else 0.0
            score = alpha_iou * 0.35 + exact_ratio * 0.65
            result = {
                "score": score,
                "alphaIou": alpha_iou,
                "exactRatio": exact_ratio,
                "shift": {"x": shift_x, "y": shift_y},
            }
            candidate_key = (
                score,
                exact_ratio,
                alpha_iou,
                -(abs(shift_x) + abs(shift_y)),
                -abs(shift_y),
                -abs(shift_x),
            )
            best_key = (
                best["score"],
                best["exactRatio"],
                best["alphaIou"],
                -(abs(best["shift"]["x"]) + abs(best["shift"]["y"])),
                -abs(best["shift"]["y"]),
                -abs(best["shift"]["x"]),
            ) if best is not None else None
            if best_key is None or candidate_key > best_key:
                best = result

    assert best is not None
    return best


def _rounded(value: float) -> float:
    return round(float(value), 4)


def inspect_character_consistency(
    image: Image.Image,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a JSON-safe hard-gate report for one four-direction sheet."""

    rgba = image.convert("RGBA")
    frames_value = metadata.get("frames")
    animations_value = metadata.get("animations")
    if not isinstance(frames_value, Sequence) or isinstance(frames_value, (str, bytes)):
        raise ValueError("character consistency requires normalized frames metadata")
    if not isinstance(animations_value, Mapping):
        raise ValueError("character consistency requires normalized animations metadata")

    frames = [_crop_frame(rgba, frame) for frame in frames_value]
    motion_build = metadata.get("motionBuild")
    declared_head_boxes = (
        motion_build.get("geometry", {}).get("headBboxes", {})
        if isinstance(motion_build, Mapping)
        and isinstance(motion_build.get("geometry"), Mapping)
        and isinstance(motion_build.get("geometry", {}).get("headBboxes"), Mapping)
        else {}
    )
    frame_reports: list[dict[str, Any]] = []
    canonical_reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for direction in DIRECTIONS:
        idle_indexes = animations_value.get("idle", {}).get(direction, [])
        # The first idle frame is the identity master for the whole direction;
        # any further idle frames are ordinary candidates checked against it.
        if not isinstance(idle_indexes, Sequence) or len(idle_indexes) < 1:
            failures.append(
                {
                    "code": "canonical_idle_missing",
                    "direction": direction,
                    "message": "每个方向至少需要一张 idle 身份母版",
                }
            )
            continue
        idle_index = int(idle_indexes[0])
        if not 0 <= idle_index < len(frames):
            failures.append(
                {
                    "code": "canonical_idle_missing",
                    "direction": direction,
                    "frameIndex": idle_index,
                    "message": "idle 身份母版索引越界",
                }
            )
            continue

        reference = frames[idle_index]
        reference_area = _visible_area(reference)
        head_region = _comparison_region(
            reference,
            vertical_start=0.0,
            vertical_end=0.55,
            horizontal_inset=0.0,
        )
        core_region = _comparison_region(
            reference,
            vertical_start=0.45,
            vertical_end=0.82,
            horizontal_inset=0.18,
        )
        if reference_area == 0 or head_region is None or core_region is None:
            failures.append(
                {
                    "code": "canonical_idle_empty",
                    "direction": direction,
                    "frameIndex": idle_index,
                    "message": "idle 身份母版没有可见主体",
                }
            )
            continue
        raw_head_bounds = declared_head_boxes.get(direction)
        canonical_report = _canonical_master_report(
            direction,
            idle_index,
            reference,
            declared_head_bounds=(
                raw_head_bounds
                if isinstance(raw_head_bounds, Sequence)
                and not isinstance(raw_head_bounds, (str, bytes))
                else None
            ),
        )
        if canonical_report is None:
            failures.append(
                {
                    "code": "canonical_idle_empty",
                    "direction": direction,
                    "frameIndex": idle_index,
                    "message": "idle 身份母版无法解析头身轮廓",
                }
            )
            continue
        canonical_reports.append(canonical_report)

        for action, limits in ACTION_LIMITS.items():
            indexes = animations_value.get(action, {}).get(direction, [])
            if not isinstance(indexes, Sequence):
                continue
            for action_frame, raw_index in enumerate(indexes):
                frame_index = int(raw_index)
                if frame_index == idle_index:
                    # The identity master is the reference, not a candidate.
                    continue
                frame_failures: list[str] = []
                if not 0 <= frame_index < len(frames):
                    failures.append(
                        {
                            "code": "animation_frame_missing",
                            "direction": direction,
                            "action": action,
                            "frameIndex": frame_index,
                            "message": "动作帧索引越界",
                        }
                    )
                    continue

                candidate = frames[frame_index]
                candidate_area = _visible_area(candidate)
                if candidate_area == 0:
                    failures.append(
                        {
                            "code": "animation_frame_empty",
                            "direction": direction,
                            "action": action,
                            "frameIndex": frame_index,
                            "message": "动作帧没有可见主体",
                        }
                    )
                    continue

                maximum_shift = int(limits["maximumShift"])
                head = _region_similarity(
                    reference,
                    candidate,
                    head_region,
                    maximum_shift=maximum_shift,
                )
                core = _region_similarity(
                    reference,
                    candidate,
                    core_region,
                    maximum_shift=maximum_shift,
                )
                area_ratio = candidate_area / reference_area
                if head["score"] < float(limits["headScore"]):
                    frame_failures.append("head_identity_drift")
                if core["score"] < float(limits["coreScore"]):
                    frame_failures.append("torso_identity_drift")
                if not float(limits["minimumAreaRatio"]) <= area_ratio <= float(
                    limits["maximumAreaRatio"]
                ):
                    frame_failures.append("body_area_jump")
                if head["shift"] != core["shift"]:
                    relative_shift = max(
                        abs(int(head["shift"][axis]) - int(core["shift"][axis]))
                        for axis in ("x", "y")
                    )
                    if relative_shift > 1:
                        frame_failures.append("head_torso_detached")

                report = {
                    "direction": direction,
                    "action": action,
                    "actionFrame": action_frame,
                    "frameIndex": frame_index,
                    "canonicalFrameIndex": idle_index,
                    "headScore": _rounded(head["score"]),
                    "headAlphaIou": _rounded(head["alphaIou"]),
                    "headExactRatio": _rounded(head["exactRatio"]),
                    "headShift": head["shift"],
                    "coreScore": _rounded(core["score"]),
                    "coreAlphaIou": _rounded(core["alphaIou"]),
                    "coreExactRatio": _rounded(core["exactRatio"]),
                    "coreShift": core["shift"],
                    "areaRatio": _rounded(area_ratio),
                    "passed": not frame_failures,
                    "failures": frame_failures,
                }
                frame_reports.append(report)
                for code in frame_failures:
                    failures.append(
                        {
                            "code": code,
                            "direction": direction,
                            "action": action,
                            "actionFrame": action_frame,
                            "frameIndex": frame_index,
                        }
                    )

    if len(canonical_reports) == len(DIRECTIONS):
        anchor = metadata.get("anchor")
        expected_baseline = (
            int(anchor["y"]) - 1
            if isinstance(anchor, Mapping) and isinstance(anchor.get("y"), int)
            else canonical_reports[0]["footBaseline"]
        )
        for report in canonical_reports:
            if report["footBaseline"] != expected_baseline:
                failures.append(
                    {
                        "code": "canonical_baseline_drift",
                        "direction": report["direction"],
                        "frameIndex": report["frameIndex"],
                        "expected": expected_baseline,
                        "actual": report["footBaseline"],
                    }
                )
        for field, code in (
            ("height", "canonical_body_height_drift"),
            ("headWidth", "canonical_head_size_drift"),
            ("headHeight", "canonical_head_size_drift"),
        ):
            values = [int(report[field]) for report in canonical_reports]
            if max(values) - min(values) > 1:
                failures.append(
                    {
                        "code": code,
                        "field": field,
                        "minimum": min(values),
                        "maximum": max(values),
                    }
                )

    minimum_head = min((entry["headScore"] for entry in frame_reports), default=0.0)
    minimum_core = min((entry["coreScore"] for entry in frame_reports), default=0.0)
    failed_frames = sum(not entry["passed"] for entry in frame_reports)
    return {
        "schemaVersion": 1,
        "policy": POLICY_ID,
        "ok": not failures,
        "fallbackMode": FALLBACK_MODE,
        "thresholds": ACTION_LIMITS,
        "summary": {
            "checkedFrames": len(frame_reports),
            "failedFrames": failed_frames,
            "failureCount": len(failures),
            "minimumHeadScore": _rounded(minimum_head),
            "minimumCoreScore": _rounded(minimum_core),
        },
        "canonicalMasters": canonical_reports,
        "frames": frame_reports,
        "failures": failures,
    }


def consistency_warning(report: Mapping[str, Any]) -> dict[str, Any] | None:
    if report.get("ok") is True:
        return None
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    return {
        "code": "character.identity_drift",
        "severity": "error",
        "message": (
            "角色动作表未通过身份锁；完整 walk/work 不能被接受，运行时将使用对应方向的 idle 安全帧。"
        ),
        "failedFrames": int(summary.get("failedFrames", 0)),
        "failureCount": int(summary.get("failureCount", 0)),
        "minimumHeadScore": float(summary.get("minimumHeadScore", 0.0)),
        "minimumCoreScore": float(summary.get("minimumCoreScore", 0.0)),
    }


__all__ = [
    "ACTION_LIMITS",
    "DIRECTIONS",
    "FALLBACK_MODE",
    "POLICY_ID",
    "consistency_warning",
    "inspect_character_consistency",
]
