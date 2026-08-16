"""Deterministic pixel-rig compiler for the four-direction Gus sprite sheet.

Every production pixel is drawn by this module: the head comes from the
``HEAD_PIXEL_ROWS`` maps below and the torso, arms and legs from the
``_draw_*`` routines.  ``write_default_layers`` caches those cells as
per-direction PNG strips under ``assets/gus-rig/layers``; the compiler then
reads the strips back and assembles them on an integer grid.  Reading through
disk keeps the build byte-reproducible and lets the palette, binary-alpha and
change-region gates run against the exact bytes that ship, but note that it is
a cache of this module's own drawing code, not an externally approved asset —
the compiler verifies reproducibility, not artistic sign-off.

``assets/gus-rig/reference/gus-turnaround-v2.png`` is a human-facing turnaround
reference only.  No code reads it and none of its pixels reach the game.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from dataclasses import dataclass
from itertools import accumulate
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw

if __package__:
    from . import isometric
else:
    # README documents this module's CLI as `python codex_v0/character_motion.py`,
    # which runs it as a top-level script with no package context.  Keep that
    # invocation working rather than silently changing a documented command.
    import isometric  # type: ignore[no-redef]


POLICY_ID = "deterministic-pixel-rig-v1"
# Direction order and the two screen axes belong to the camera, not to Gus —
# see codex_v0/isometric.py for why they are derived rather than written down.
DIRECTIONS = isometric.DIRECTIONS
NEAR_SIDE_SCREEN_X = isometric.NEAR_SIDE_SCREEN_X
FRAME_WIDTH = 24
FRAME_HEIGHT = 48
ANCHOR = {"x": 12, "y": 46}
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RIG_PATH = PROJECT_DIR / "assets" / "gus-rig" / "rig.json"
COMPONENT_ORDER = ("farLeg", "farArm", "torso", "nearLeg", "nearArm", "head")
LOCKED_COMPONENTS = ("head", "torso")

# Which per-frame offset each layer follows.  ``body`` carries the torso and
# both arms; ``head`` follows the body by default but may lag it by a pixel so
# the neck gets some secondary motion.  Legs follow nothing: they are authored
# in absolute frame coordinates, which is what keeps a planted foot welded to
# the ground row while the body rises and falls above it.  Offsetting the legs
# too is what turned the old walk cycle into a two-pixel hop with no support
# foot in half its frames.
OFFSET_GROUPS = ("body", "head")
COMPONENT_OFFSET_GROUP: dict[str, str | None] = {
    "farLeg": None,
    "farArm": "body",
    "torso": "body",
    "nearLeg": None,
    "nearArm": "body",
    "head": "head",
}
assert set(COMPONENT_OFFSET_GROUP) == set(COMPONENT_ORDER)


def _offset(
    x: int,
    y: int,
    *,
    head_x: int | None = None,
    head_y: int | None = None,
) -> dict[str, dict[str, int]]:
    """Build one frame's integer layer offsets; the head defaults to the body."""

    return {
        "body": {"x": x, "y": y},
        "head": {
            "x": x if head_x is None else head_x,
            "y": y if head_y is None else head_y,
        },
    }
TRANSFORM_POLICY = {
    "rotation": False,
    "scale": False,
    "interpolation": False,
    "subpixel": False,
    "runtimeMirror": False,
}

# Column order inside every direction row.  Each action's frame count is the
# length of its ``offsets`` table, and every derived constant below — sheet
# width, frame count, atlas indexes, QA comparisons — is computed from it, so
# changing an action's frame count is a single edit here.
ACTION_ORDER = ("idle", "walk", "work")
ACTION_CONSTRAINTS = {
    # Breathing.  The body rises on the inhale and the head trails it by a
    # frame in both directions, so the neck stretches and compresses instead of
    # the whole figure sliding as one rigid block.
    "idle": {
        "offsets": [
            _offset(0, 0),
            _offset(0, -1, head_y=0),
            _offset(0, -1),
            _offset(0, 0, head_y=-1),
        ],
        "mutableComponents": [],
    },
    # Two steps of four frames: contact, weight absorption, mid-stance pass,
    # heel lift.  The body drops a pixel as it takes the load and rises a pixel
    # over the straight support leg; the head declines to follow it down, which
    # is both what a real head does and what sells the weight.
    "walk": {
        "offsets": [
            _offset(0, 0),
            _offset(0, 1, head_y=0),
            _offset(0, -1),
            _offset(0, 0),
            _offset(0, 0),
            _offset(0, 1, head_y=0),
            _offset(0, -1),
            _offset(0, 0),
        ],
        "mutableComponents": ["farLeg", "farArm", "nearLeg", "nearArm"],
        "contactFrames": [0, 4],
    },
    # Seated typing: the body drops to the chair and the hands alternate, with
    # a one pixel head nod on the third frame so the two mid-poses differ.
    "work": {
        "offsets": [
            _offset(0, 3),
            _offset(0, 3),
            _offset(0, 3, head_y=4),
            _offset(0, 3),
        ],
        "mutableComponents": ["farLeg", "farArm", "nearLeg", "nearArm"],
        "mutableBetweenFrames": ["farArm", "nearArm"],
        "seatedComponents": ["farLeg", "nearLeg"],
    },
}
assert set(ACTION_ORDER) == set(ACTION_CONSTRAINTS), "ACTION_ORDER must cover every action"

ACTION_FRAME_COUNTS = {
    action: len(ACTION_CONSTRAINTS[action]["offsets"]) for action in ACTION_ORDER
}
ACTION_COLUMN_START = dict(
    zip(
        ACTION_ORDER,
        accumulate((ACTION_FRAME_COUNTS[action] for action in ACTION_ORDER), initial=0),
    )
)
SHEET_COLUMNS = sum(ACTION_FRAME_COUNTS.values())
SHEET_ROWS = len(DIRECTIONS)
SHEET_SIZE = (FRAME_WIDTH * SHEET_COLUMNS, FRAME_HEIGHT * SHEET_ROWS)
FRAME_COUNT = SHEET_COLUMNS * SHEET_ROWS

# Playback speed of the QA review GIFs only.  The shipped cadence lives in the
# pack spec; these are deliberately slow enough to inspect frame by frame.
QA_GIF_FRAME_DURATION_MS = {"idle": 420, "walk": 140, "work": 240}

INK = "#0D2228"
DEEP = "#17343A"
CLOTH_DARK = "#31584F"
CLOTH_LIGHT = "#426D61"
SKIN_DARK = "#B36C4F"
HAIR_DARK = "#C4C5B9"
SKIN_SHADE = "#C99D69"
HAIR_MID = "#D5D8CC"
HAIR_LIGHT = "#EDF0DE"
SKIN = "#F0BD91"
WHITE = "#FFF8DF"

# Legs used to be drawn in the jacket's own greens, which left the whole lower
# body a single dark mass: 40 of the 68 pixels below the torso were flat ink and
# only the two toe pixels had any contrast, so leg motion was invisible at 1x.
# These are a cooler blue-grey trouser ramp and a warm shoe ramp, both taken
# from the locked 32-colour world palette, plus the reference turnaround's ochre
# cuff accent that gives the arm swing a bright terminator.
TROUSER_DARK = "#4F666B"
TROUSER_LIGHT = "#637D81"
SHOE_SHADE = "#8D9B92"
SHOE_LIGHT = "#D0CCBD"
CUFF = "#A87838"

TRANSPARENT = (0, 0, 0, 0)

HEAD_PIXEL_COLORS = {
    "i": INK,
    "a": SKIN_DARK,
    "d": HAIR_DARK,
    "s": SKIN_SHADE,
    "m": HAIR_MID,
    "h": HAIR_LIGHT,
    "p": SKIN,
    "w": WHITE,
}
HEAD_PIXEL_ROWS = {
    "southeast": (
        "........iiiiii..........",
        ".......idmmmmmi.........",
        "......dmmmmmmmms........",
        "....ihwmmmmmmmmmhi......",
        "....imhhmmmmmmmhhhi.....",
        "...idmmmhmmmmwhdmmdi....",
        "...idmmmmmwdmmmidmmi....",
        "...idmmdmmmdmmmssmdi....",
        "...iddmdmmdpmmisiddi....",
        "...iddddddasmipapisi....",
        "...idaaddppipppipii.....",
        "....dppasppipppipi......",
        "....ipmssppipppipi......",
        ".....iiapppspppppi......",
        "......iiipppsapai.......",
        "...........pp...........",
    ),
    "southwest": (
        ".........iiiiii.........",
        ".......iimmmmmmii.......",
        "......immmmmmmmmmi......",
        ".....idmmmmmmmmmmwi.....",
        "....immhmmmmmmmmwmmi....",
        "...idmmmhmmmmmmwhmmdi...",
        "...idhhmmmmhwwmmmmmdi...",
        "...idmdmmmddmmmmmmmdi...",
        "...isdipdmsdmmmdmdddi...",
        "...isipppddsdmmdmdddi...",
        "....iipipddpidhddddsi...",
        ".....ipipppdpimsdddi....",
        ".....ipipppdppsipidi....",
        ".....ipppppppspp.i......",
        "......ippppppppsi.......",
        ".......ipappsssi........",
        ".......iiaaaa...........",
    ),
    # The two north directions walk away from the camera, so they are drawn as
    # genuine back views: hair mass, nape and neck, no face at all.  They used
    # to show 10-19% facial skin, which read as the character staring at you
    # while striding away — the single largest source of the "split figure"
    # complaint once the legs became legible.
    "northwest": (
        ".......immmmmi..........",
        ".....imhhhmmmmmi........",
        "...immhhhhhmdmmddi......",
        "...imhhhhhhhdmmddi......",
        "..immhhhhhhhdmmdddi.....",
        "..immmhhhhhmmdmdddi.....",
        "..immmmhhhmmmdmdddi.....",
        "..immmmmmmmmmdddddi.....",
        "..idmmmmmmmmmdddddi.....",
        "..idddmmmmmdddddddi.....",
        "...idddddddddddddi......",
        "...idddddddddddddi......",
        ".....idddddddddi........",
        "......idddddddi.........",
        ".......ipppppi..........",
        "........ipppi...........",
    ),
    "northeast": (
        ".........immmmmi........",
        ".......immmmmhhhmi......",
        ".....iddmmdmhhhhhmmi....",
        ".....iddmmdhhhhhhhmi....",
        "....idddmmdhhhhhhhmmi...",
        "....idddmdmmhhhhhmmmi...",
        "....idddmdmmmhhhmmmmi...",
        "....idddddmmmmmmmmmmi...",
        "....idddddmmmmmmmmmdi...",
        "....idddddddmmmmmdddi...",
        ".....idddddddddddddi....",
        ".....idddddddddddddi....",
        ".......idddddddddi......",
        "........idddddddi.......",
        ".........ipppppi........",
        "..........ipppi.........",
    ),
}

READABILITY_CONSTRAINTS = {
    "masterHeight": [35, 36],
    "headWidth": [17, 18],
    "headHeight": [16, 17],
    "visiblePixels": [465, 525],
    # The head is unchanged art and its share of the silhouette is a property of
    # the approved proportions, not of the animation.  The ceiling sits just
    # above the widest direction (southwest, whose head map is one row taller
    # than the other three) so the gate still catches a genuine bobble-head
    # without failing the proportions that were signed off.
    "headAreaRatio": [0.32, 0.49],
    "maximumInkRatio": 0.32,
    "minimumArmPixelsOutsideTorso": 16,
    "minimumDirectionAlphaXor": 24,
    # Opposite directions used to need a *larger* silhouette difference than
    # adjacent ones, which is wrong as physics: a standing figure's outline from
    # the front and from behind is nearly the same, and the old art only cleared
    # the bar because its four heads were four unrelated drawings rather than
    # one head turning.  Now that front and back share a skull, the silhouettes
    # correctly converge and the front/back read is carried by colour instead —
    # gated below by head skin ratio and torso turn, which measure facing
    # directly rather than inferring it from outline area.
    "minimumOppositeAlphaXor": 24,
    "minimumLegGapRows": 5,
    "nearFootBaseline": 45,
    "farFootBaseline": 44,
    # --- Motion kinematics ---------------------------------------------------
    # These replace the old "adjacent frames differ by at least one pixel" rule,
    # which certified a walk cycle that was airborne in half its frames and slid
    # its feet across the floor in the rest.
    #
    # Every walk frame must keep at least one foot on its own ground row: that
    # is the difference between walking and hopping.
    "walkSupportFootRows": {"nearLeg": 45, "farLeg": 44},
    # A planted foot must track backwards at exactly the distance the character
    # travels in one frame, so it stays welded to the floor.
    "walkFootTravelPerFrame": 2,
    # Articulation is measured on the limb layers alone, so a whole-body bob
    # cannot be mistaken for limb animation.  The old walk scored 269-293 raw
    # but only 110-144 once the bob was removed, and the old work cycle moved a
    # total of four pixels across the entire action.  Idle is excluded: its
    # motion lives in the body-to-head offset and is gated separately.
    "minimumArticulation": {"walk": 60, "work": 20},
    # The legs used to share the jacket's palette, which made the whole lower
    # body one dark mass.  Require real contrast below the torso.
    "minimumLegContrastPixels": 20,
    # --- Facing ---------------------------------------------------------------
    # The identity layers have to turn with the character.  None of the gates
    # above notice if they do not: the silhouette XOR checks were satisfied
    # entirely by limb geometry while the head kept showing a face and the torso
    # kept showing a chest, so a character walking away read as one walking
    # backwards toward you.  These are stated as facing semantics rather than as
    # pixel-difference totals, because four independently drawn heads differ by
    # 175-245 pixels whether or not any of them actually turns.
    "backViewDirections": ["northwest", "northeast"],
    "maximumBackViewSkinRatio": 0.06,
    "minimumFrontViewSkinRatio": 0.15,
    "minimumTorsoTurnPixels": 25,
}


class MotionRigError(ValueError):
    """Raised when a rig cannot produce a safe deterministic sheet."""


@dataclass(frozen=True)
class CompiledCharacterMotion:
    image: Image.Image
    png_bytes: bytes
    metadata: dict[str, Any]
    report: dict[str, Any]


def _project_relative(path: Path) -> str:
    """Render a path relative to the project root for provenance records."""

    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return resolved.as_posix()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rgba_sha256(image: Image.Image) -> str:
    rgba = image.convert("RGBA")
    return _sha256(rgba.tobytes())


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _rgba(hex_color: str) -> tuple[int, int, int, int]:
    value = hex_color.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4)) + (255,)


def _blank(width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT) -> Image.Image:
    return Image.new("RGBA", (width, height), TRANSPARENT)


def _draw_readable_head(direction: str) -> Image.Image:
    """Draw the compact identity head from an explicit pixel map.

    These maps are hand-authored and deliberately independent per direction.
    They preserve the asymmetrical white fringe, face/ear cues and neck while
    keeping the head at 16-17 pixels high so the body remains readable at native
    game scale.
    """

    try:
        rows = HEAD_PIXEL_ROWS[direction]
    except KeyError as exc:
        raise MotionRigError(f"unknown direction: {direction}") from exc
    image = _blank()
    for y, row in enumerate(rows, start=10):
        if len(row) != FRAME_WIDTH:
            raise MotionRigError(f"{direction} head pixel row must be 24 pixels wide")
        for x, key in enumerate(row):
            if key == ".":
                continue
            try:
                color = HEAD_PIXEL_COLORS[key]
            except KeyError as exc:
                raise MotionRigError(f"{direction} head uses unknown pixel key: {key}") from exc
            image.putpixel((x, y), _rgba(color))
    return image


def _draw_torso(direction: str) -> Image.Image:
    image = _blank()
    draw = ImageDraw.Draw(image)
    draw.polygon(
        [(8, 23), (15, 23), (19, 27), (18, 35), (16, 38),
         (8, 38), (6, 35), (5, 27)],
        fill=_rgba(INK),
    )
    draw.polygon(
        [(8, 25), (15, 25), (17, 28), (16, 35), (15, 37),
         (9, 37), (7, 35), (7, 28)],
        fill=_rgba(CLOTH_DARK),
    )
    # The lit panel sits on the body's camera-facing side, the same side the
    # near arm and leg hang from.  Both now read from NEAR_SIDE_SCREEN_X.
    if NEAR_SIDE_SCREEN_X[direction] < 0:
        draw.polygon([(8, 27), (10, 26), (10, 35), (8, 35)], fill=_rgba(CLOTH_LIGHT))
    else:
        draw.polygon([(14, 26), (16, 28), (16, 35), (14, 35)], fill=_rgba(CLOTH_LIGHT))
    if direction in {"southeast", "southwest"}:
        # Front: open collar over the chest.
        draw.polygon([(9, 24), (12, 27), (15, 24), (14, 30), (10, 30)], fill=_rgba(DEEP))
    else:
        # Back: no collar opening.  A shoulder yoke seam and a centre-back seam
        # give the reverse of the jacket its own read, so a character walking
        # away no longer shows the same chest as one walking toward you.
        draw.rectangle((8, 25, 16, 26), fill=_rgba(DEEP))
        draw.rectangle((11, 27, 12, 35), fill=_rgba(DEEP))
        draw.rectangle((9, 28, 9, 33), fill=_rgba(DEEP))
        draw.rectangle((15, 28, 15, 33), fill=_rgba(DEEP))
    if direction in {"southeast", "southwest"}:
        draw.rectangle((11, 24, 12, 26), fill=_rgba(WHITE))
    draw.rectangle((9, 35, 15, 36), fill=_rgba(DEEP))
    return image


# --- Shared limb rasterisation ----------------------------------------------


def _facing_signs(direction: str, near: bool) -> tuple[int, int]:
    """``isometric.facing_signs`` with this module's error type."""

    try:
        return isometric.facing_signs(direction, near)
    except ValueError as exc:
        raise MotionRigError(str(exc)) from exc


def _limb_rows(
    top: tuple[int, int],
    bottom: tuple[int, int],
    width_top: int,
    width_bottom: int,
) -> list[tuple[int, int, int]]:
    """Rasterise a tapering limb segment as inclusive ``(y, x0, x1)`` spans.

    Interpolating per scanline rather than filling a polygon keeps the limb an
    even width on every row, which is what lets a three-pixel limb stay readable
    at native scale instead of dissolving into stair-stepped ink.
    """

    (x0, y0), (x1, y1) = top, bottom
    span = max(1, y1 - y0)
    rows: list[tuple[int, int, int]] = []
    for y in range(y0, y1 + 1):
        ratio = (y - y0) / span
        centre = round(x0 + (x1 - x0) * ratio)
        width = max(1, round(width_top + (width_bottom - width_top) * ratio))
        left = centre - (width - 1) // 2
        rows.append((y, left, left + width - 1))
    return rows


def _paint_limb(
    image: Image.Image,
    rows: Sequence[tuple[int, int, int]],
    *,
    forward: int,
    fill: str,
    shade: str,
) -> None:
    """Fill limb spans with a trailing ink edge and a leading shade edge.

    Selective outlining — ink only on the edge the character moves away from —
    keeps a limb from being more outline than form.  The old legs spent 25 of
    their 45 pixels on a full ink border, which is why they read as a black blob.
    """

    for y, left, right in rows:
        if not 0 <= y < FRAME_HEIGHT:
            continue
        trailing = left if forward > 0 else right
        leading = right if forward > 0 else left
        for x in range(left, right + 1):
            if not 0 <= x < FRAME_WIDTH:
                continue
            if x == trailing:
                color = INK
            elif x == leading and right - left >= 2:
                color = shade
            else:
                color = fill
            image.putpixel((x, y), _rgba(color))


# --- Arms -------------------------------------------------------------------
#
# An arm pose is ``(hand_dx, hand_dy, elbow_lead)``, with ``hand_dx`` measured
# fore/aft from the shoulder in the direction the character faces.  Arms swing
# contralaterally: the near arm mirrors the far leg and vice versa, so the walk
# sequence below is the leg sequence rotated by half a cycle and scaled down —
# arms swing less than legs stride.
SHOULDER_Y = 27
ELBOW_Y = 32
WRIST_Y = 36
SHOULDER_LATERAL = 5

ARM_POSES: dict[str, tuple[int, int, int]] = {
    "back-3": (-3, 0, -1),
    "back-2": (-2, 0, -1),
    "back-1": (-1, 0, 0),
    "neutral": (0, 0, 0),
    "fwd-1": (1, 0, 0),
    "fwd-2": (2, 0, 1),
    "fwd-3": (3, -1, 1),
}
# Typing: the hands sit forward over a desk and alternate through a three
# pixel vertical travel.  The old work cycle moved four pixels in total across
# the whole sheet, which read as a stuck pixel rather than as typing.
ARM_WORK_POSES: dict[str, tuple[int, int, int]] = {
    "type-down": (5, 1, 2),
    "type-mid": (6, -1, 3),
    "type-up": (4, -3, 2),
}
# Near-limb sequences.  The far limb runs the same list rotated by half a
# cycle, which is what makes the gait contralateral and keeps both strips
# drawing from one shared variant set.
WALK_ARM_SEQUENCE = ("back-3", "back-2", "fwd-1", "fwd-2", "fwd-3", "fwd-1", "neutral", "back-1")
WORK_ARM_SEQUENCE = ("type-down", "type-mid", "type-up", "type-mid")


def _rotated(sequence: Sequence[str], offset: int) -> tuple[str, ...]:
    return tuple(sequence[offset:]) + tuple(sequence[:offset])


def _draw_arm(direction: str, near: bool, variant: str) -> Image.Image:
    image = _blank()
    pose = ARM_POSES.get(variant) or ARM_WORK_POSES.get(variant)
    if pose is None:
        raise MotionRigError(f"unknown arm variant: {variant}")
    hand_dx, hand_dy, elbow_lead = pose
    working = variant in ARM_WORK_POSES

    forward, side = _facing_signs(direction, near)
    shoulder = (12 + side * SHOULDER_LATERAL, SHOULDER_Y)
    # The arm hangs a pixel outboard of the shoulder so a swinging sleeve stays
    # outside the torso silhouette instead of vanishing into it.
    wrist = (shoulder[0] + side + forward * hand_dx, WRIST_Y + hand_dy)
    elbow = (
        round((shoulder[0] + wrist[0]) / 2) + side + forward * elbow_lead,
        ELBOW_Y if not working else ELBOW_Y + 1,
    )
    fill = CLOTH_LIGHT if near else CLOTH_DARK
    shade = CLOTH_DARK if near else DEEP
    _paint_limb(image, _limb_rows(shoulder, elbow, 5, 4), forward=forward, fill=fill, shade=shade)
    _paint_limb(image, _limb_rows(elbow, wrist, 4, 3), forward=forward, fill=fill, shade=shade)

    # An ochre cuff separates sleeve from hand.  At this size it is the single
    # most legible marker of where the arm ends, which is what makes the swing
    # readable at 1x against a same-coloured torso.
    cuff_rows = _limb_rows(wrist, wrist, 3, 3)
    _paint_limb(image, cuff_rows, forward=forward, fill=CUFF, shade=CUFF)

    skin = SKIN if near else SKIN_SHADE
    for offset in range(2):
        y = wrist[1] + 1 + offset
        if not 0 <= y < FRAME_HEIGHT:
            continue
        for x in range(wrist[0] - 1, wrist[0] + 2):
            if 0 <= x < FRAME_WIDTH:
                image.putpixel((x, y), _rgba(skin if offset == 0 else INK))
    return image


# --- Legs -------------------------------------------------------------------
#
# Legs carry no frame offset, so every pose is authored in absolute frame
# coordinates.  A pose is ``(foot_dx, foot_lift, hip_dy, knee_lead)``:
#
#   foot_dx    fore/aft position of the foot measured from the frame centre, in
#              the direction the character faces
#   foot_lift  how far the sole rides above its own ground row; 0 means planted
#   hip_dy     vertical position of the hip.  This MUST equal the body offset of
#              every frame that uses the pose, otherwise the leg tears away from
#              the pelvis.  ``_validate_compiled_frames`` checks it.
#   knee_lead  how far the knee leads the straight hip-to-ankle line
#
# The five stance poses walk the planted foot backwards WALK_STRIDE_PX //
# WALK_FRAMES_PER_STEP pixels per frame, which is exactly how far the character
# travels in one frame at the declared cadence.  That identity is the whole
# reason the foot sticks to the floor instead of skating over it.
#
# Gus is a short-legged design — 11 pixels of leg under a 36 pixel body, against
# roughly 48% for a human — so the stride is capped near 8 pixels before the
# hips read as doing the splits.  The cadence is fast to compensate; that is a
# deliberate scurry, not an accident.
WALK_STRIDE_PX = 8
WALK_FRAMES_PER_STEP = 4
WALK_FOOT_TRAVEL_PER_FRAME = WALK_STRIDE_PX // WALK_FRAMES_PER_STEP

HIP_Y = 36
NEAR_ANKLE_Y = 43  # sole occupies rows 44-45, the near foot's ground line
FAR_ANKLE_Y = 42  # one pixel higher, which is what reads as isometric depth
SHOE_WIDTH = 4
# Lateral stance width.  Both the hip and the foot sit this far off centre, so
# the legs stay parallel instead of splaying, and the negative space between
# them survives all the way down to the shoes.
LEG_LATERAL = 3

LEG_POSES: dict[str, tuple[int, int, int, int]] = {
    "neutral": (0, 0, 0, 0),
    # Stance: heel strike, weight absorption, mid-stance, heel lift, toe off.
    "stance-0": (4, 0, 0, 1),
    "stance-1": (2, 0, 1, 2),
    "stance-2": (0, 0, -1, 0),
    "stance-3": (-2, 0, 0, -1),
    "stance-4": (-4, 0, 0, -1),
    # Swing: lift the trailing foot, carry it past the body, reach forward.
    # The lift stays small on purpose — three pixels is over a quarter of this
    # character's leg length and reads as a parade march rather than a walk.
    "swing-0": (-3, 1, 1, 1),
    "swing-1": (1, 2, -1, 1),
    "swing-2": (3, 1, 0, 0),
}
WALK_LEG_SEQUENCE = ("stance-0", "stance-1", "stance-2", "stance-3", "stance-4", "swing-0", "swing-1", "swing-2")





def _paint_shoe(
    image: Image.Image,
    *,
    centre_x: int,
    ankle_y: int,
    forward: int,
    near: bool,
) -> None:
    """Draw the shoe as an upper band plus a solid sole one row below."""

    upper = SHOE_LIGHT if near else SHOE_SHADE
    left = centre_x - (SHOE_WIDTH - 1) // 2
    if forward > 0:
        left += 1
    else:
        left -= 1
    for offset in range(SHOE_WIDTH):
        x = left + offset
        if not 0 <= x < FRAME_WIDTH:
            continue
        toe = offset == (SHOE_WIDTH - 1 if forward > 0 else 0)
        for row, color in ((ankle_y + 1, SHOE_LIGHT if toe else upper), (ankle_y + 2, INK)):
            if 0 <= row < FRAME_HEIGHT:
                image.putpixel((x, row), _rgba(color))


def _draw_seated_leg(image: Image.Image, direction: str, near: bool) -> Image.Image:
    """Thigh forward to the knee, shin dropping to the floor under a desk.

    The seated pose is authored three pixels down like the rest of the seated
    body, so the shoe still lands on the character's own ground row.
    """

    forward, side = _facing_signs(direction, near)
    hip = (12 + side * 2, HIP_Y + 1)
    knee = (12 + forward * 5 + side, HIP_Y + 2)
    ankle_y = (NEAR_ANKLE_Y if near else FAR_ANKLE_Y) - 1
    ankle = (12 + forward * 4 + side, ankle_y)
    fill = TROUSER_LIGHT if near else TROUSER_DARK
    shade = TROUSER_DARK if near else DEEP
    _paint_limb(image, _limb_rows(hip, knee, 4, 3), forward=forward, fill=fill, shade=shade)
    _paint_limb(image, _limb_rows(knee, ankle, 3, 3), forward=forward, fill=fill, shade=shade)
    _paint_shoe(image, centre_x=ankle[0], ankle_y=ankle_y, forward=forward, near=near)
    return image


def _draw_leg(direction: str, near: bool, variant: str) -> Image.Image:
    image = _blank()
    if variant == "seated":
        return _draw_seated_leg(image, direction, near)
    try:
        foot_dx, foot_lift, hip_dy, knee_lead = LEG_POSES[variant]
    except KeyError as exc:
        raise MotionRigError(f"unknown leg variant: {variant}") from exc

    forward, side = _facing_signs(direction, near)
    hip = (12 + side * LEG_LATERAL, HIP_Y + hip_dy)
    ankle_y = (NEAR_ANKLE_Y if near else FAR_ANKLE_Y) - foot_lift
    ankle = (12 + forward * foot_dx + side * LEG_LATERAL, ankle_y)
    knee = (
        round((hip[0] + ankle[0]) / 2) + forward * knee_lead,
        (hip[1] + ankle_y) // 2 + 1,
    )
    fill = TROUSER_LIGHT if near else TROUSER_DARK
    shade = TROUSER_DARK if near else DEEP
    _paint_limb(image, _limb_rows(hip, knee, 5, 4), forward=forward, fill=fill, shade=shade)
    _paint_limb(image, _limb_rows(knee, ankle, 4, 4), forward=forward, fill=fill, shade=shade)
    _paint_shoe(image, centre_x=ankle[0], ankle_y=ankle_y, forward=forward, near=near)
    return image


def _component_strip(direction: str, component: str, variants: Sequence[str]) -> Image.Image:
    strip = _blank(FRAME_WIDTH * len(variants), FRAME_HEIGHT)
    for index, variant in enumerate(variants):
        if component == "head":
            cell = _draw_readable_head(direction)
        elif component == "torso":
            cell = _draw_torso(direction)
        elif component == "farArm":
            cell = _draw_arm(direction, False, variant)
        elif component == "nearArm":
            cell = _draw_arm(direction, True, variant)
        elif component == "farLeg":
            cell = _draw_leg(direction, False, variant)
        elif component == "nearLeg":
            cell = _draw_leg(direction, True, variant)
        else:
            raise MotionRigError(f"unknown component: {component}")
        strip.alpha_composite(cell, (index * FRAME_WIDTH, 0))
    return strip


def load_rig(path: Path | str = DEFAULT_RIG_PATH) -> dict[str, Any]:
    rig_path = Path(path)
    try:
        rig = json.loads(rig_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MotionRigError(f"cannot read rig: {rig_path}") from exc
    character_id = rig.get("character") if isinstance(rig, dict) else None
    if not isinstance(character_id, str) or not character_id.isascii() or not character_id.islower():
        raise MotionRigError("rig must name its character with a lowercase ascii id")
    if not isinstance(rig, dict) or rig.get("schemaVersion") != 1 or rig.get("policy") != POLICY_ID:
        raise MotionRigError("unsupported Gus motion rig")
    if tuple(rig.get("directions", [])) != DIRECTIONS:
        raise MotionRigError("rig directions must use the canonical four-row order")
    frame = rig.get("frame")
    if frame != {"width": FRAME_WIDTH, "height": FRAME_HEIGHT, "anchor": ANCHOR}:
        raise MotionRigError("rig frame and anchor must remain 24x48 at (12,46)")
    components = rig.get("components")
    component_order = rig.get("componentOrder")
    if not isinstance(components, dict) or not isinstance(component_order, list):
        raise MotionRigError("rig components are missing")
    if tuple(component_order) != COMPONENT_ORDER or set(components) != set(COMPONENT_ORDER):
        raise MotionRigError("componentOrder must keep the approved far-to-near z-order")
    if rig.get("transformPolicy") != TRANSFORM_POLICY:
        raise MotionRigError("rotation, scaling, interpolation, subpixels and runtime mirroring are forbidden")
    if rig.get("readabilityConstraints") != READABILITY_CONSTRAINTS:
        raise MotionRigError("readabilityConstraints must match the native-scale Gus silhouette policy")
    if rig.get("actionConstraints") != ACTION_CONSTRAINTS:
        raise MotionRigError("actionConstraints must match the deterministic Gus motion policy")
    allowed_regions = rig.get("allowedChangeRegions")
    dynamic_components = set(COMPONENT_ORDER) - set(LOCKED_COMPONENTS)
    if not isinstance(allowed_regions, dict) or set(allowed_regions) != dynamic_components:
        raise MotionRigError("every mutable limb requires exactly one allowed change region")
    for component, raw_region in allowed_regions.items():
        if (
            not isinstance(raw_region, list)
            or len(raw_region) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in raw_region)
        ):
            raise MotionRigError(f"{component} allowed change region must use four integers")
        left, top, right, bottom = raw_region
        if not (0 <= left < right <= FRAME_WIDTH and 0 <= top < bottom <= FRAME_HEIGHT):
            raise MotionRigError(f"{component} allowed change region is outside the full canvas")
    for component, spec in components.items():
        if not isinstance(spec, dict):
            raise MotionRigError(f"{component} component declaration is invalid")
        filename = spec.get("file")
        variants = spec.get("variants")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".png")
            or not isinstance(variants, list)
            or not variants
            or any(not isinstance(variant, str) or not variant for variant in variants)
            or len(set(variants)) != len(variants)
        ):
            raise MotionRigError(f"{component} component file or variants are invalid")
        if component in LOCKED_COMPONENTS:
            if variants != ["neutral"] or spec.get("identityLocked") is not True:
                raise MotionRigError(f"{component} must be a single identity-locked neutral layer")
        elif spec.get("identityLocked") is True:
            raise MotionRigError(f"{component} cannot be identity-locked")
    animations = rig.get("animations")
    if not isinstance(animations, dict) or {
        key: len(value) for key, value in animations.items()
    } != ACTION_FRAME_COUNTS:
        expected = ", ".join(f"{count} {action}" for action, count in ACTION_FRAME_COUNTS.items())
        raise MotionRigError(f"rig must contain exactly {expected} frames")
    used_variants = {component: set() for component in COMPONENT_ORDER}
    idle_variants: dict[str, str] | None = None
    for action, frames in animations.items():
        for frame_index, frame_spec in enumerate(frames):
            if not isinstance(frame_spec, dict):
                raise MotionRigError(f"{action}.{frame_index} must be an object")
            offsets = frame_spec.get("offsets")
            if not isinstance(offsets, dict) or set(offsets) != set(OFFSET_GROUPS):
                raise MotionRigError(
                    f"{action}.{frame_index} requires one offset per layer group: "
                    + ", ".join(OFFSET_GROUPS)
                )
            for group, offset in offsets.items():
                if not isinstance(offset, dict) or set(offset) != {"x", "y"}:
                    raise MotionRigError(f"{action}.{frame_index}.{group} needs an x/y offset")
                if any(
                    isinstance(offset[key], bool) or not isinstance(offset[key], int)
                    for key in ("x", "y")
                ):
                    raise MotionRigError(f"{action}.{frame_index}.{group} offset must use integers")
            if offsets != ACTION_CONSTRAINTS[action]["offsets"][frame_index]:
                raise MotionRigError(f"{action}.{frame_index} violates the declared layer offsets")
            variants = frame_spec.get("variants")
            if not isinstance(variants, dict) or set(variants) != set(components):
                raise MotionRigError(f"{action}.{frame_index} must select every component")
            for component, variant in variants.items():
                declared = components[component].get("variants", [])
                if variant not in declared:
                    raise MotionRigError(f"{action}.{frame_index} selects unknown {component} variant")
                used_variants[component].add(variant)
            if action == "idle":
                idle_variants = dict(variants)
            elif idle_variants is not None:
                changed_components = {
                    component
                    for component, variant in variants.items()
                    if variant != idle_variants[component]
                }
                allowed = set(ACTION_CONSTRAINTS[action]["mutableComponents"])
                if not changed_components <= allowed:
                    raise MotionRigError(f"{action}.{frame_index} changes an undeclared component")
    if idle_variants is None or set(idle_variants.values()) != {"neutral"}:
        raise MotionRigError("idle must select the neutral variant for every component")
    for component, variants in used_variants.items():
        if variants != set(components[component]["variants"]):
            raise MotionRigError(f"{component} declares unused or unreachable variants")
    work_frames = animations["work"]
    for frame_index in range(len(work_frames) - 1):
        changed_between_work_frames = {
            component
            for component in COMPONENT_ORDER
            if work_frames[frame_index]["variants"][component]
            != work_frames[frame_index + 1]["variants"][component]
        }
        if changed_between_work_frames != set(ACTION_CONSTRAINTS["work"]["mutableBetweenFrames"]):
            raise MotionRigError(
                f"work.{frame_index}->{frame_index + 1} may differ only in the declared arm layers"
            )
    for frame_spec in work_frames:
        if any(
            frame_spec["variants"][component] != "seated"
            for component in ACTION_CONSTRAINTS["work"]["seatedComponents"]
        ):
            raise MotionRigError("work requires the fixed seated leg variants")
    return rig


def write_default_layers(path: Path | str = DEFAULT_RIG_PATH) -> list[Path]:
    rig_path = Path(path)
    rig = load_rig(rig_path)
    written: list[Path] = []
    for direction in DIRECTIONS:
        direction_dir = rig_path.parent / "layers" / direction
        direction_dir.mkdir(parents=True, exist_ok=True)
        for component, spec in rig["components"].items():
            variants = tuple(spec["variants"])
            strip = _component_strip(direction, component, variants)
            destination = direction_dir / str(spec["file"])
            destination.write_bytes(_png_bytes(strip))
            written.append(destination)
    return written


def _load_component_cells(
    rig_path: Path,
    rig: Mapping[str, Any],
) -> tuple[dict[str, dict[str, dict[str, Image.Image]]], list[tuple[str, bytes]]]:
    palette = {_rgba(color)[:3] for color in rig["palette"]}
    cells: dict[str, dict[str, dict[str, Image.Image]]] = {}
    source_files: list[tuple[str, bytes]] = []
    for direction in DIRECTIONS:
        cells[direction] = {}
        for component, spec in rig["components"].items():
            variants = tuple(spec["variants"])
            source_path = rig_path.parent / "layers" / direction / str(spec["file"])
            try:
                source_bytes = source_path.read_bytes()
                source = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
                source.load()
            except (OSError, ValueError) as exc:
                raise MotionRigError(f"cannot read layer strip: {source_path}") from exc
            expected_size = (FRAME_WIDTH * len(variants), FRAME_HEIGHT)
            if source.size != expected_size:
                raise MotionRigError(f"{source_path} must be {expected_size[0]}x{expected_size[1]}")
            relative = source_path.relative_to(rig_path.parent).as_posix()
            source_files.append((relative, source_bytes))
            variant_cells: dict[str, Image.Image] = {}
            for index, variant in enumerate(variants):
                cell = source.crop((index * FRAME_WIDTH, 0, (index + 1) * FRAME_WIDTH, FRAME_HEIGHT))
                pixels = list(cell.getdata())
                alpha_values = {pixel[3] for pixel in pixels}
                if not alpha_values <= {0, 255}:
                    raise MotionRigError(f"{relative}:{variant} contains semi-transparent pixels")
                if any(pixel[3] == 0 and pixel[:3] != (0, 0, 0) for pixel in pixels):
                    raise MotionRigError(f"{relative}:{variant} contains hidden RGB in transparent pixels")
                visible_colors = {
                    pixel[:3]
                    for pixel in pixels
                    if pixel[3] == 255
                }
                if not visible_colors:
                    raise MotionRigError(f"{relative}:{variant} is empty")
                outside = sorted(visible_colors - palette)
                if outside:
                    raise MotionRigError(f"{relative}:{variant} uses colors outside the locked Gus palette")
                if component not in LOCKED_COMPONENTS:
                    allowed = rig["allowedChangeRegions"][component]
                    bbox = cell.getbbox()
                    assert bbox is not None
                    if not (
                        allowed[0] <= bbox[0]
                        and allowed[1] <= bbox[1]
                        and bbox[2] <= allowed[2]
                        and bbox[3] <= allowed[3]
                    ):
                        raise MotionRigError(
                            f"{relative}:{variant} leaves the declared {component} change region"
                        )
                variant_cells[str(variant)] = cell
            cells[direction][component] = variant_cells
    return cells, sorted(source_files)


def _shifted(layer: Image.Image, dx: int, dy: int, label: str) -> Image.Image:
    bbox = layer.getbbox()
    if bbox is None:
        raise MotionRigError(f"{label} is empty")
    shifted_bbox = (bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy)
    if shifted_bbox[0] < 0 or shifted_bbox[1] < 0 or shifted_bbox[2] > FRAME_WIDTH or shifted_bbox[3] > FRAME_HEIGHT:
        raise MotionRigError(f"{label} would be clipped by its integer offset")
    output = _blank()
    output.alpha_composite(layer, (dx, dy))
    return output


def _compose_frame(
    rig: Mapping[str, Any],
    cells: Mapping[str, Mapping[str, Mapping[str, Image.Image]]],
    direction: str,
    action: str,
    frame_index: int,
) -> Image.Image:
    frame_spec = rig["animations"][action][frame_index]
    offsets = frame_spec["offsets"]
    output = _blank()
    for component in rig["componentOrder"]:
        variant = frame_spec["variants"][component]
        layer = cells[direction][component][variant]
        group = COMPONENT_OFFSET_GROUP[component]
        dx, dy = (0, 0) if group is None else (offsets[group]["x"], offsets[group]["y"])
        output.alpha_composite(
            _shifted(layer, dx, dy, f"{direction}.{action}.{frame_index}.{component}.{variant}")
        )
    return output


def rig_animations() -> dict[str, list[dict[str, Any]]]:
    """Build the per-frame variant selection that ``rig.json`` records.

    Keeping this next to the pose tables means the walk cycle's contralateral
    pairing is expressed once — the far limb is always the near limb's sequence
    rotated by half a cycle — rather than being retyped across 64 frame specs.
    """

    half = len(WALK_LEG_SEQUENCE) // 2
    sequences = {
        "idle": {
            "nearLeg": ("neutral",) * ACTION_FRAME_COUNTS["idle"],
            "farLeg": ("neutral",) * ACTION_FRAME_COUNTS["idle"],
            "nearArm": ("neutral",) * ACTION_FRAME_COUNTS["idle"],
            "farArm": ("neutral",) * ACTION_FRAME_COUNTS["idle"],
        },
        "walk": {
            "nearLeg": tuple(WALK_LEG_SEQUENCE),
            "farLeg": _rotated(WALK_LEG_SEQUENCE, half),
            "nearArm": tuple(WALK_ARM_SEQUENCE),
            "farArm": _rotated(WALK_ARM_SEQUENCE, half),
        },
        "work": {
            "nearLeg": ("seated",) * ACTION_FRAME_COUNTS["work"],
            "farLeg": ("seated",) * ACTION_FRAME_COUNTS["work"],
            "nearArm": tuple(WORK_ARM_SEQUENCE),
            "farArm": _rotated(WORK_ARM_SEQUENCE, len(WORK_ARM_SEQUENCE) // 2),
        },
    }
    animations: dict[str, list[dict[str, Any]]] = {}
    for action in ACTION_ORDER:
        frames = []
        for index in range(ACTION_FRAME_COUNTS[action]):
            variants = {"torso": "neutral", "head": "neutral"}
            for component, sequence in sequences[action].items():
                variants[component] = sequence[index]
            frames.append(
                {
                    "name": f"{action}-{index}",
                    "offsets": json.loads(_canonical_json(ACTION_CONSTRAINTS[action]["offsets"][index])),
                    "variants": {component: variants[component] for component in COMPONENT_ORDER},
                }
            )
        animations[action] = frames
    return animations


def animation_metadata() -> dict[str, dict[str, list[int]]]:
    """Map every action/direction to its sheet frame indexes.

    This is the single source of truth for the atlas layout; the asset
    normalizer and the web contract both derive their tables from it rather
    than repeating the index arithmetic.
    """

    return {
        action: {
            direction: [
                row * SHEET_COLUMNS + ACTION_COLUMN_START[action] + index
                for index in range(ACTION_FRAME_COUNTS[action])
            ]
            for row, direction in enumerate(DIRECTIONS)
        }
        for action in ACTION_ORDER
    }


def _frame_diff(left: Image.Image, right: Image.Image) -> int:
    return sum(1 for a, b in zip(left.getdata(), right.getdata(), strict=True) if a != b)


def _limb_only_frame(
    rig: Mapping[str, Any],
    cells: Mapping[str, Mapping[str, Mapping[str, Image.Image]]],
    direction: str,
    action: str,
    frame_index: int,
) -> Image.Image:
    """Compose a frame from the four limbs alone, at their real offsets.

    Articulation has to be measured without the head and torso, otherwise a
    whole-body bob swamps it: shifting the old 470-pixel silhouette by one row
    changed 212 pixels on its own, which is how a walk cycle with almost no limb
    movement still cleared a "frames must differ" gate.
    """

    frame_spec = rig["animations"][action][frame_index]
    output = _blank()
    for component in rig["componentOrder"]:
        if component in LOCKED_COMPONENTS:
            continue
        variant = frame_spec["variants"][component]
        group = COMPONENT_OFFSET_GROUP[component]
        offset = frame_spec["offsets"][group] if group else {"x": 0, "y": 0}
        output.alpha_composite(
            _shifted(cells[direction][component][variant], offset["x"], offset["y"], component)
        )
    return output


def _foot_metrics(cell: Image.Image, baseline: int) -> tuple[bool, float]:
    """Return ``(planted, foot_centre_x)`` for one leg cell."""

    bbox = cell.getbbox()
    if bbox is None:
        raise MotionRigError("leg layer is empty")
    lowest = bbox[3] - 1
    columns = [x for x in range(FRAME_WIDTH) if cell.getpixel((x, lowest))[3] == 255]
    return lowest == baseline, (min(columns) + max(columns)) / 2


def _validate_motion(
    rig: Mapping[str, Any],
    cells: Mapping[str, Mapping[str, Mapping[str, Image.Image]]],
    direction: str,
) -> dict[str, Any]:
    """Check the kinematics that actually decide whether a gait reads as walking.

    Two properties matter and neither was previously tested: something has to be
    holding the character up in every frame, and whatever is holding them up has
    to travel backwards at exactly the speed the character travels forwards.
    """

    limits = READABILITY_CONSTRAINTS
    baselines = limits["walkSupportFootRows"]
    walk_frames = ACTION_FRAME_COUNTS["walk"]
    planted_rows: list[list[str]] = []
    stance_centres: dict[str, list[tuple[int, float]]] = {"nearLeg": [], "farLeg": []}

    for index in range(walk_frames):
        variants = rig["animations"]["walk"][index]["variants"]
        supported: list[str] = []
        for component, baseline in baselines.items():
            planted, centre = _foot_metrics(cells[direction][component][variants[component]], baseline)
            if planted:
                supported.append(component)
                stance_centres[component].append((index, centre))
        if not supported:
            raise MotionRigError(
                f"{direction}.walk.{index} has both feet off the ground — that is a hop, not a step"
            )
        planted_rows.append(supported)

    # A planted foot travels backwards in world terms, which on screen means
    # against the direction the character faces — hence the sign.
    forward, _ = _facing_signs(direction, True)
    travel = limits["walkFootTravelPerFrame"] * forward
    for component, samples in stance_centres.items():
        for (left_index, left), (right_index, right) in zip(samples, samples[1:]):
            if right_index != left_index + 1:
                continue  # a new stance phase began; the foot legitimately jumps
            if round(left - right) != travel:
                raise MotionRigError(
                    f"{direction}.walk {component} slides: the planted foot moved "
                    f"{left - right:g}px between frames {left_index} and {right_index}, "
                    f"but the character travels {travel}px"
                )

    articulation: dict[str, list[int]] = {}
    for action, minimum in limits["minimumArticulation"].items():
        count = ACTION_FRAME_COUNTS[action]
        limbs = [_limb_only_frame(rig, cells, direction, action, index) for index in range(count)]
        diffs = [_frame_diff(limbs[i], limbs[(i + 1) % count]) for i in range(count)] if count > 1 else [0]
        if count > 1 and min(diffs) < minimum:
            raise MotionRigError(
                f"{direction}.{action} barely articulates: the weakest frame pair moves "
                f"{min(diffs)} limb pixels, below the {minimum} the action requires"
            )
        articulation[action] = diffs

    # Idle animates the body and head rather than the limbs, so it is held to a
    # different standard: the head must not simply ride the body as one rigid
    # block, or the whole figure just slides up and down like a lift.
    relative_head = {
        frame["offsets"]["head"]["y"] - frame["offsets"]["body"]["y"]
        for frame in rig["animations"]["idle"]
    }
    if ACTION_FRAME_COUNTS["idle"] > 1 and len(relative_head) < 2:
        raise MotionRigError(
            f"{direction}.idle moves as a rigid block: the head keeps a constant "
            "offset from the body, so there is no breathing, only a bob"
        )

    torso_colors = {
        pixel[:3] for pixel in cells[direction]["torso"]["neutral"].getdata() if pixel[3] == 255
    }
    contrast = 0
    for component in ("nearLeg", "farLeg"):
        for pixel in cells[direction][component]["neutral"].getdata():
            if pixel[3] == 255 and pixel[:3] not in torso_colors:
                contrast += 1
    if contrast < limits["minimumLegContrastPixels"]:
        raise MotionRigError(
            f"{direction} legs reuse the torso palette ({contrast} contrasting pixels) "
            "and read as one dark mass at native scale"
        )

    skin_colors = {_rgba(color)[:3] for color in (SKIN, SKIN_SHADE, SKIN_DARK)}
    head_pixels = [p for p in cells[direction]["head"]["neutral"].getdata() if p[3] == 255]
    skin_ratio = sum(p[:3] in skin_colors for p in head_pixels) / len(head_pixels)
    back_view = direction in limits["backViewDirections"]
    if back_view and skin_ratio > limits["maximumBackViewSkinRatio"]:
        raise MotionRigError(
            f"{direction} walks away from the camera but its head still shows "
            f"{skin_ratio:.1%} facial skin — a back view must not show a face"
        )
    if not back_view and skin_ratio < limits["minimumFrontViewSkinRatio"]:
        raise MotionRigError(
            f"{direction} faces the camera but its head only shows {skin_ratio:.1%} skin"
        )

    # The near limbs and the torso's lit panel must hang off the same side of
    # the body, or the figure reads as twisted at the waist.
    def _colour_centre(image: Image.Image, color: str) -> float | None:
        target = _rgba(color)[:3]
        columns = [
            x
            for y in range(FRAME_HEIGHT)
            for x in range(FRAME_WIDTH)
            if image.getpixel((x, y))[3] == 255 and image.getpixel((x, y))[:3] == target
        ]
        return sum(columns) / len(columns) if columns else None

    torso_lit = _colour_centre(cells[direction]["torso"]["neutral"], CLOTH_LIGHT)
    arm_lit = _colour_centre(cells[direction]["nearArm"]["neutral"], CLOTH_LIGHT)
    if torso_lit is None or arm_lit is None:
        raise MotionRigError(f"{direction} torso or near arm has no lit panel to check")
    centre = FRAME_WIDTH / 2
    if (torso_lit - centre) * (arm_lit - centre) <= 0:
        raise MotionRigError(
            f"{direction} torso's lit panel (x={torso_lit:.1f}) and near arm "
            f"(x={arm_lit:.1f}) sit on opposite sides of the body"
        )

    return {
        "supportFootsPerWalkFrame": planted_rows,
        "stanceFootCentres": {key: [list(item) for item in value] for key, value in stance_centres.items()},
        "limbArticulation": articulation,
        "legContrastPixels": contrast,
        "headSkinRatio": round(skin_ratio, 4),
        "nearSideScreenX": NEAR_SIDE_SCREEN_X[direction],
    }


def _validate_compiled_frames(
    rig: Mapping[str, Any],
    frames: Mapping[str, Mapping[str, Sequence[Image.Image]]],
    cells: Mapping[str, Mapping[str, Mapping[str, Image.Image]]],
) -> dict[str, Any]:
    master_boxes: dict[str, tuple[int, int, int, int]] = {}
    head_boxes: dict[str, tuple[int, int, int, int]] = {}
    frame_diffs: dict[str, dict[str, list[int]]] = {}
    readability: dict[str, dict[str, Any]] = {}
    direction_masks: dict[str, set[int]] = {}
    motion: dict[str, dict[str, Any]] = {}
    for direction in DIRECTIONS:
        idle = frames[direction]["idle"][0]
        master_bbox = idle.getbbox()
        head_bbox = cells[direction]["head"]["neutral"].getbbox()
        if master_bbox is None or head_bbox is None:
            raise MotionRigError(f"{direction} identity master is empty")
        if master_bbox[3] != ANCHOR["y"]:
            raise MotionRigError(f"{direction} identity master must touch row 45")
        master_boxes[direction] = master_bbox
        head_boxes[direction] = head_bbox
        idle_pixels = list(idle.getdata())
        head_pixels = list(cells[direction]["head"]["neutral"].getdata())
        visible_pixels = sum(pixel[3] == 255 for pixel in idle_pixels)
        visible_head_pixels = sum(pixel[3] == 255 for pixel in head_pixels)
        ink_pixels = sum(pixel == _rgba(INK) for pixel in idle_pixels)
        master_height = master_bbox[3] - master_bbox[1]
        head_width = head_bbox[2] - head_bbox[0]
        head_height = head_bbox[3] - head_bbox[1]
        head_area_ratio = visible_head_pixels / visible_pixels
        ink_ratio = ink_pixels / visible_pixels

        def _within(value: float, bounds: Sequence[float]) -> bool:
            return bounds[0] <= value <= bounds[1]

        if not _within(master_height, READABILITY_CONSTRAINTS["masterHeight"]):
            raise MotionRigError(f"{direction} master is not readable at native scale")
        if not _within(head_width, READABILITY_CONSTRAINTS["headWidth"]):
            raise MotionRigError(f"{direction} head width violates native-scale readability")
        if not _within(head_height, READABILITY_CONSTRAINTS["headHeight"]):
            raise MotionRigError(f"{direction} head height violates native-scale readability")
        if not _within(visible_pixels, READABILITY_CONSTRAINTS["visiblePixels"]):
            raise MotionRigError(f"{direction} silhouette is too sparse or too dense")
        if not _within(head_area_ratio, READABILITY_CONSTRAINTS["headAreaRatio"]):
            raise MotionRigError(f"{direction} head-to-body area ratio is not human-readable")
        if ink_ratio > READABILITY_CONSTRAINTS["maximumInkRatio"]:
            raise MotionRigError(f"{direction} silhouette contains too much solid outline ink")

        torso_pixels = list(cells[direction]["torso"]["neutral"].getdata())
        arm_exposure: dict[str, int] = {}
        for component in ("farArm", "nearArm"):
            arm_pixels = list(cells[direction][component]["neutral"].getdata())
            exposed = sum(
                arm[3] == 255 and torso[3] == 0
                for arm, torso in zip(arm_pixels, torso_pixels, strict=True)
            )
            if exposed < READABILITY_CONSTRAINTS["minimumArmPixelsOutsideTorso"]:
                raise MotionRigError(f"{direction}.{component} disappears into the torso silhouette")
            arm_exposure[component] = exposed

        far_leg = cells[direction]["farLeg"]["neutral"]
        near_leg = cells[direction]["nearLeg"]["neutral"]
        far_bbox = far_leg.getbbox()
        near_bbox = near_leg.getbbox()
        assert far_bbox is not None and near_bbox is not None
        if far_bbox[3] - 1 != READABILITY_CONSTRAINTS["farFootBaseline"]:
            raise MotionRigError(f"{direction} far foot must stop one pixel above the ground baseline")
        if near_bbox[3] - 1 != READABILITY_CONSTRAINTS["nearFootBaseline"]:
            raise MotionRigError(f"{direction} near foot must keep the ground baseline")
        leg_gap_rows = 0
        for y in range(FRAME_HEIGHT):
            far_x = [x for x in range(FRAME_WIDTH) if far_leg.getpixel((x, y))[3] == 255]
            near_x = [x for x in range(FRAME_WIDTH) if near_leg.getpixel((x, y))[3] == 255]
            if not far_x or not near_x:
                continue
            gap = (
                min(near_x) - max(far_x) - 1
                if max(far_x) < min(near_x)
                else min(far_x) - max(near_x) - 1
                if max(near_x) < min(far_x)
                else -1
            )
            if gap >= 1:
                leg_gap_rows += 1
        if leg_gap_rows < READABILITY_CONSTRAINTS["minimumLegGapRows"]:
            raise MotionRigError(f"{direction} legs merge into a single column at native scale")

        direction_masks[direction] = {
            index for index, pixel in enumerate(idle_pixels) if pixel[3] == 255
        }
        readability[direction] = {
            "visiblePixels": visible_pixels,
            "headPixels": visible_head_pixels,
            "headAreaRatio": round(head_area_ratio, 4),
            "inkPixels": ink_pixels,
            "inkRatio": round(ink_ratio, 4),
            "armPixelsOutsideTorso": arm_exposure,
            "legGapRows": leg_gap_rows,
            "farFootBaseline": far_bbox[3] - 1,
            "nearFootBaseline": near_bbox[3] - 1,
        }
        walk = list(frames[direction]["walk"])
        work = list(frames[direction]["work"])
        walk_diffs = [_frame_diff(walk[index], walk[(index + 1) % len(walk)]) for index in range(len(walk))]
        work_diffs = [_frame_diff(work[index], work[index + 1]) for index in range(len(work) - 1)]
        if any(value == 0 for value in walk_diffs) or any(value == 0 for value in work_diffs):
            raise MotionRigError(f"{direction} contains visually identical adjacent action frames")
        for contact_index in ACTION_CONSTRAINTS["walk"]["contactFrames"]:
            bbox = walk[contact_index].getbbox()
            if bbox is None or bbox[3] != ANCHOR["y"]:
                raise MotionRigError(f"{direction}.walk.{contact_index} must keep a contact foot on row 45")
        frame_diffs[direction] = {"walk": walk_diffs, "work": work_diffs}
        motion[direction] = _validate_motion(rig, cells, direction)

    master_heights = [box[3] - box[1] for box in master_boxes.values()]
    head_widths = [box[2] - box[0] for box in head_boxes.values()]
    head_heights = [box[3] - box[1] for box in head_boxes.values()]
    if max(master_heights) - min(master_heights) > 1:
        raise MotionRigError("four identity masters differ in visible height by more than one pixel")
    if max(head_widths) - min(head_widths) > 1 or max(head_heights) - min(head_heights) > 1:
        raise MotionRigError("four identity heads differ in size by more than one pixel")
    direction_alpha_xor: dict[str, int] = {}
    for left_index, left in enumerate(DIRECTIONS):
        for right_index in range(left_index + 1, len(DIRECTIONS)):
            right = DIRECTIONS[right_index]
            difference = len(direction_masks[left] ^ direction_masks[right])
            opposite = (left_index, right_index) in {(0, 2), (1, 3)}
            minimum = (
                READABILITY_CONSTRAINTS["minimumOppositeAlphaXor"]
                if opposite
                else READABILITY_CONSTRAINTS["minimumDirectionAlphaXor"]
            )
            if difference < minimum:
                raise MotionRigError(f"{left} and {right} reuse an unreadably similar silhouette")
            direction_alpha_xor[f"{left}:{right}"] = difference
    return {
        "masterBboxes": {key: list(value) for key, value in master_boxes.items()},
        "headBboxes": {key: list(value) for key, value in head_boxes.items()},
        "frameDiffPixels": frame_diffs,
        "motion": motion,
        "commonFootBaseline": ANCHOR["y"] - 1,
        "nativeScaleReadability": readability,
        "directionAlphaXor": direction_alpha_xor,
    }


def compile_character_motion(path: Path | str = DEFAULT_RIG_PATH) -> CompiledCharacterMotion:
    rig_path = Path(path)
    rig = load_rig(rig_path)
    cells, source_files = _load_component_cells(rig_path, rig)
    sheet = _blank(*SHEET_SIZE)
    frames: dict[str, dict[str, list[Image.Image]]] = {}
    for row, direction in enumerate(DIRECTIONS):
        frames[direction] = {}
        sequence: list[tuple[str, int]] = [
            (action, index)
            for action in ACTION_ORDER
            for index in range(ACTION_FRAME_COUNTS[action])
        ]
        for column, (action, action_index) in enumerate(sequence):
            frame = _compose_frame(rig, cells, direction, action, action_index)
            frames[direction].setdefault(action, []).append(frame)
            sheet.alpha_composite(frame, (column * FRAME_WIDTH, row * FRAME_HEIGHT))

    geometry = _validate_compiled_frames(rig, frames, cells)
    alpha_values = set(sheet.getchannel("A").getdata())
    if not alpha_values <= {0, 255}:
        raise MotionRigError("compiled sheet contains semi-transparent pixels")
    rig_bytes = _canonical_json(rig).encode("utf-8")
    layer_digest = hashlib.sha256()
    for relative, source_bytes in source_files:
        layer_digest.update(relative.encode("utf-8"))
        layer_digest.update(b"\0")
        layer_digest.update(source_bytes)
        layer_digest.update(b"\0")
    png = _png_bytes(sheet)
    locked_hashes = {
        direction: {
            component: _rgba_sha256(cells[direction][component]["neutral"])
            for component in ("head", "torso")
        }
        for direction in DIRECTIONS
    }
    report = {
        "policy": POLICY_ID,
        "verified": True,
        "rigSha256": _sha256(rig_bytes),
        "layerSetSha256": layer_digest.hexdigest(),
        "rgbaSha256": _rgba_sha256(sheet),
        "pngSha256": _sha256(png),
        "frameCount": FRAME_COUNT,
        "integerTransforms": True,
        "paletteLocked": True,
        "binaryAlpha": True,
        "identityLayerReuse": True,
        "componentReuse": {
            "zOrder": list(COMPONENT_ORDER),
            "identityLocked": list(LOCKED_COMPONENTS),
            "allowedChangeRegions": rig["allowedChangeRegions"],
            "actionConstraints": rig["actionConstraints"],
            "readabilityConstraints": rig["readabilityConstraints"],
        },
        "transformPolicy": rig["transformPolicy"],
        "lockedComponentHashes": locked_hashes,
        "geometry": geometry,
        "errors": [],
    }
    character_id = rig["character"]
    metadata = {
        "kind": "character",
        "character": character_id,
        "frameWidth": FRAME_WIDTH,
        "frameHeight": FRAME_HEIGHT,
        "columns": SHEET_COLUMNS,
        "frameCount": FRAME_COUNT,
        "anchor": dict(ANCHOR),
        "offsetPx": {"x": 0, "y": 0},
        "footprint": [{"x": 0, "y": 0, "blocked": False}],
        "directionRows": list(DIRECTIONS),
        "animations": animation_metadata(),
        # Provenance must describe the rig that was actually compiled.  These
        # were frozen strings naming Gus's rig no matter what `--rig` pointed
        # at, so any other character's sidecar and release receipt would have
        # claimed it was built from Gus — a lie that reproduces perfectly.
        "jobId": f"{POLICY_ID}:{character_id}",
        "generationTool": POLICY_ID,
        "sourceRig": _project_relative(rig_path),
        "motionBuild": report,
    }
    return CompiledCharacterMotion(sheet, png, metadata, report)


def verify_character_motion(
    image: Image.Image,
    metadata: Mapping[str, Any],
    path: Path | str = DEFAULT_RIG_PATH,
) -> dict[str, Any]:
    try:
        expected = compile_character_motion(path)
    except MotionRigError as exc:
        return {
            "policy": POLICY_ID,
            "verified": False,
            "errors": [{"code": "canonical_rig_invalid", "message": str(exc)}],
        }
    observed = image.convert("RGBA")
    errors: list[dict[str, Any]] = []
    if observed.size != SHEET_SIZE:
        errors.append(
            {
                "code": "sheet_size_mismatch",
                "expected": list(SHEET_SIZE),
                "actual": list(observed.size),
            }
        )
    elif observed.tobytes() != expected.image.tobytes():
        errors.append(
            {
                "code": "sheet_pixels_mismatch",
                "expectedRgbaSha256": expected.report["rgbaSha256"],
                "actualRgbaSha256": _rgba_sha256(observed),
            }
        )
    required_metadata = {
        "frameWidth": FRAME_WIDTH,
        "frameHeight": FRAME_HEIGHT,
        "columns": SHEET_COLUMNS,
        "frameCount": FRAME_COUNT,
        "anchor": ANCHOR,
        "directionRows": list(DIRECTIONS),
        "animations": animation_metadata(),
    }
    for key, expected_value in required_metadata.items():
        if metadata.get(key) != expected_value:
            errors.append(
                {
                    "code": "metadata_mismatch",
                    "field": key,
                    "expected": expected_value,
                    "actual": metadata.get(key),
                }
            )
    return {
        **expected.report,
        "verified": not errors,
        "observedRgbaSha256": _rgba_sha256(observed),
        "errors": errors,
    }


def write_build_artifacts(
    output_path: Path,
    metadata_path: Path,
    *,
    rig_path: Path | str = DEFAULT_RIG_PATH,
    pack_id: str = "core-v1",
) -> CompiledCharacterMotion:
    compiled = compile_character_motion(rig_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(compiled.png_bytes)
    character_id = compiled.metadata["character"]
    sidecar = {
        "packId": pack_id,
        "slot": f"character.{character_id}",
        "displayName": f"{character_id.capitalize()} {POLICY_ID}",
        "metadata": compiled.metadata,
    }
    metadata_path.write_text(_canonical_json(sidecar) + "\n", encoding="utf-8")
    return compiled


def _indexed_gif_frame(frame: Image.Image, palette: Sequence[str], scale: int = 8) -> Image.Image:
    colors = [_rgba(color)[:3] for color in palette]
    color_indexes = {color: index + 1 for index, color in enumerate(colors)}
    indexed = Image.new("P", frame.size, 0)
    indexed.putpalette(
        ([0, 0, 0] + [channel for color in colors for channel in color] + [0] * 768)[:768]
    )
    indexes: list[int] = []
    for pixel in frame.convert("RGBA").getdata():
        if pixel[3] == 0:
            indexes.append(0)
        else:
            try:
                indexes.append(color_indexes[pixel[:3]])
            except KeyError as exc:
                raise MotionRigError("QA animation found a color outside the locked palette") from exc
    indexed.putdata(indexes)
    return indexed.resize((frame.width * scale, frame.height * scale), Image.Resampling.NEAREST)


def _save_animation_gif(
    destination: Path,
    frames: Sequence[Image.Image],
    palette: Sequence[str],
    *,
    duration_ms: int,
) -> None:
    indexed = [_indexed_gif_frame(frame, palette) for frame in frames]
    indexed[0].save(
        destination,
        format="GIF",
        save_all=True,
        append_images=indexed[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        transparency=0,
        optimize=False,
    )


def _qa_diff_columns() -> list[tuple[int, int]]:
    """Sheet column pairs shown side by side in the QA pixel-diff heat map.

    Each looping action compares every frame with its successor and wraps back
    to its first frame.  Two-frame actions drop the wrap because it would just
    repeat the same diff, and single-frame actions contribute nothing.
    """

    pairs: list[tuple[int, int]] = []
    for action in ACTION_ORDER:
        start = ACTION_COLUMN_START[action]
        count = ACTION_FRAME_COUNTS[action]
        span = count if count > 2 else count - 1
        pairs.extend((start + index, start + (index + 1) % count) for index in range(span))
    return pairs


def generate_qa_artifacts(
    output_dir: Path,
    *,
    rig_path: Path | str = DEFAULT_RIG_PATH,
) -> dict[str, Any]:
    """Build deterministic nearest-neighbour previews and pixel-diff evidence."""

    compiled = compile_character_motion(rig_path)
    rig = load_rig(rig_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    contact_path = output_dir / "gus-contact-sheet-8x.png"
    contact = compiled.image.resize(
        (compiled.image.width * 8, compiled.image.height * 8),
        Image.Resampling.NEAREST,
    )
    contact_path.write_bytes(_png_bytes(contact))
    written.append(contact_path)

    native_idle = _blank(FRAME_WIDTH * len(DIRECTIONS), FRAME_HEIGHT)
    for column, row in enumerate(range(len(DIRECTIONS))):
        idle = compiled.image.crop(
            (0, row * FRAME_HEIGHT, FRAME_WIDTH, (row + 1) * FRAME_HEIGHT)
        )
        native_idle.alpha_composite(idle, (column * FRAME_WIDTH, 0))
    native_path = output_dir / "gus-idle-native-1x.png"
    native_path.write_bytes(_png_bytes(native_idle))
    written.append(native_path)
    native_2x_path = output_dir / "gus-idle-native-2x.png"
    native_2x_path.write_bytes(
        _png_bytes(
            native_idle.resize(
                (native_idle.width * 2, native_idle.height * 2),
                Image.Resampling.NEAREST,
            )
        )
    )
    written.append(native_2x_path)

    diff_columns = _qa_diff_columns()
    heatmap = _blank(FRAME_WIDTH * len(diff_columns), FRAME_HEIGHT * len(DIRECTIONS))
    for row, _direction in enumerate(DIRECTIONS):
        source_frames = [
            compiled.image.crop(
                (column * FRAME_WIDTH, row * FRAME_HEIGHT, (column + 1) * FRAME_WIDTH, (row + 1) * FRAME_HEIGHT)
            )
            for column in range(SHEET_COLUMNS)
        ]
        comparisons = [
            (source_frames[left], source_frames[right]) for left, right in diff_columns
        ]
        for column, (left, right) in enumerate(comparisons):
            diff = _blank()
            diff_pixels: list[tuple[int, int, int, int]] = []
            for before, after in zip(left.getdata(), right.getdata(), strict=True):
                if before != after:
                    diff_pixels.append((255, 77, 109, 255) if before[3] != after[3] else (255, 216, 102, 255))
                elif before[3] == 255:
                    diff_pixels.append((13, 34, 40, 72))
                else:
                    diff_pixels.append(TRANSPARENT)
            diff.putdata(diff_pixels)
            heatmap.alpha_composite(diff, (column * FRAME_WIDTH, row * FRAME_HEIGHT))
    heatmap_path = output_dir / "gus-action-diff-heatmap-8x.png"
    heatmap_path.write_bytes(
        _png_bytes(
            heatmap.resize(
                (heatmap.width * 8, heatmap.height * 8),
                Image.Resampling.NEAREST,
            )
        )
    )
    written.append(heatmap_path)

    for row, direction in enumerate(DIRECTIONS):
        row_frames = [
            compiled.image.crop(
                (column * FRAME_WIDTH, row * FRAME_HEIGHT, (column + 1) * FRAME_WIDTH, (row + 1) * FRAME_HEIGHT)
            )
            for column in range(SHEET_COLUMNS)
        ]
        for action in ACTION_ORDER:
            count = ACTION_FRAME_COUNTS[action]
            if count < 2:
                continue
            start = ACTION_COLUMN_START[action]
            destination = output_dir / f"gus-{direction}-{action}-8x.gif"
            _save_animation_gif(
                destination,
                row_frames[start : start + count],
                rig["palette"],
                duration_ms=QA_GIF_FRAME_DURATION_MS[action],
            )
            written.append(destination)

    artifacts = [
        {
            "name": path.name,
            "sha256": _sha256(path.read_bytes()),
            "sizeBytes": path.stat().st_size,
        }
        for path in sorted(written)
    ]
    manifest = {
        "schemaVersion": 1,
        "policy": POLICY_ID,
        "motionBuild": compiled.report,
        "artifacts": artifacts,
    }
    manifest_path = output_dir / "qa-manifest.json"
    manifest_path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    return manifest


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig", type=Path, default=DEFAULT_RIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "init-default", help="redraw the deterministic component strips from this module"
    )
    build = subparsers.add_parser(
        "build",
        help=f"compile the canonical {SHEET_COLUMNS}x{SHEET_ROWS} sheet and sidecar",
    )
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--metadata-output", type=Path, required=True)
    build.add_argument("--pack-id", default="core-v1")
    qa = subparsers.add_parser("qa", help="generate deterministic GIF, contact-sheet and diff evidence")
    qa.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "init-default":
        written = write_default_layers(args.rig)
        print(_canonical_json({"written": [str(path) for path in written]}))
        return 0
    if args.command == "qa":
        print(_canonical_json(generate_qa_artifacts(args.output_dir, rig_path=args.rig)))
        return 0
    compiled = write_build_artifacts(
        args.output,
        args.metadata_output,
        rig_path=args.rig,
        pack_id=args.pack_id,
    )
    print(_canonical_json(compiled.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACTION_COLUMN_START",
    "ACTION_FRAME_COUNTS",
    "ACTION_ORDER",
    "ANCHOR",
    "DEFAULT_RIG_PATH",
    "DIRECTIONS",
    "FRAME_COUNT",
    "FRAME_HEIGHT",
    "FRAME_WIDTH",
    "MotionRigError",
    "POLICY_ID",
    "SHEET_COLUMNS",
    "SHEET_ROWS",
    "SHEET_SIZE",
    "animation_metadata",
    "compile_character_motion",
    "generate_qa_artifacts",
    "verify_character_motion",
    "write_build_artifacts",
    "write_default_layers",
]
