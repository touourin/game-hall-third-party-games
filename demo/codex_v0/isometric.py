"""Isometric projection conventions shared by every directional asset.

These are properties of the camera, not of any one character.  They lived in
``character_motion`` next to Gus's art, where a second character could easily
have restated them wrong — and getting them wrong has already cost this project
twice.  The first core-v1 wall PNGs shipped with their NW/NE faces swapped
relative to the footprint axis and were compensated for at runtime rather than
fixed at the source; the compensation table is still in ``web/scene.mjs``.  The
second time, the character's near-limb side was derived from the wrong axis and
contradicted the torso's own lit panel on two of the four directions.

Both axes below are *derived* from the projection rather than hand-written, so
they cannot drift from it:

    screen_x = (world_x - world_y) * TILE_WIDTH  / 2
    screen_y = (world_x + world_y) * TILE_HEIGHT / 2      (screen y grows downward)

``forward`` is the screen-x direction the character travels.  ``near side`` is
the screen-x direction of the body side facing the camera, which is the lateral
world axis whose projection sits *lower* on screen.  They are not the same axis:
walking southeast moves right across the screen while the camera-facing side of
the body is on the left.
"""

from __future__ import annotations

from typing import Mapping


# Must match TILE_WIDTH / TILE_HEIGHT in web/scene.mjs.
TILE_WIDTH = 32
TILE_HEIGHT = 16

# Canonical row order for every four-direction sheet.
DIRECTIONS = ("southeast", "southwest", "northwest", "northeast")

# The world-space step each direction name denotes.  This mirrors
# `directionForMotion` in web/scene.mjs, which maps a world delta back to a name.
DIRECTION_WORLD_STEP: Mapping[str, tuple[int, int]] = {
    "southeast": (1, 0),
    "southwest": (0, 1),
    "northwest": (-1, 0),
    "northeast": (0, -1),
}


def project(world_x: float, world_y: float) -> tuple[float, float]:
    """Project a world delta onto screen axes; screen y grows downward."""

    return (
        (world_x - world_y) * TILE_WIDTH / 2,
        (world_x + world_y) * TILE_HEIGHT / 2,
    )


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _forward_screen_x(direction: str) -> int:
    return _sign(project(*DIRECTION_WORLD_STEP[direction])[0])


def _near_side_screen_x(direction: str) -> int:
    """Screen-x of the body side that faces the camera.

    The two lateral world axes are the facing vector rotated a quarter turn each
    way.  Whichever projects lower on screen is nearer the camera; its screen-x
    sign is what limbs and the lit torso panel key off.
    """

    world_x, world_y = DIRECTION_WORLD_STEP[direction]
    laterals = ((world_y, -world_x), (-world_y, world_x))
    nearest = max(laterals, key=lambda lateral: project(*lateral)[1])
    return _sign(project(*nearest)[0])


FORWARD_SCREEN_X: Mapping[str, int] = {
    direction: _forward_screen_x(direction) for direction in DIRECTIONS
}
NEAR_SIDE_SCREEN_X: Mapping[str, int] = {
    direction: _near_side_screen_x(direction) for direction in DIRECTIONS
}

# The two axes group the directions differently.  If this ever stops holding,
# the projection changed and every directional asset needs revisiting.
assert FORWARD_SCREEN_X == {
    "southeast": 1,
    "southwest": -1,
    "northwest": -1,
    "northeast": 1,
}
assert NEAR_SIDE_SCREEN_X == {
    "southeast": -1,
    "southwest": 1,
    "northwest": -1,
    "northeast": 1,
}


def facing_signs(direction: str, near: bool) -> tuple[int, int]:
    """Return ``(forward, side)`` for a limb on a given direction.

    ``forward`` is the screen-x the character walks toward; ``side`` is the
    screen-x of the body side this limb hangs from.
    """

    try:
        forward = FORWARD_SCREEN_X[direction]
        near_side = NEAR_SIDE_SCREEN_X[direction]
    except KeyError as exc:
        raise ValueError(f"unknown direction: {direction}") from exc
    return forward, (near_side if near else -near_side)


__all__ = [
    "DIRECTIONS",
    "DIRECTION_WORLD_STEP",
    "FORWARD_SCREEN_X",
    "NEAR_SIDE_SCREEN_X",
    "TILE_HEIGHT",
    "TILE_WIDTH",
    "facing_signs",
    "project",
]
