"""Shared pixel-geometry inspection for authored isometric assets."""

from __future__ import annotations

from typing import Any, Mapping

from PIL import Image


class AssetGeometryError(ValueError):
    """Raised when visible pixels cannot satisfy declared native geometry."""


def bottom_visible_y(alpha: Image.Image, x: int) -> int:
    if not 0 <= x < alpha.width:
        raise AssetGeometryError("墙体地面轴 x 坐标超出帧")
    for y in range(alpha.height - 1, -1, -1):
        if alpha.getpixel((x, y)) > 0:
            return y
    raise AssetGeometryError("墙体地面轴端点所在列没有可见像素")


def top_visible_y(alpha: Image.Image, x: int) -> int:
    if not 0 <= x < alpha.width:
        raise AssetGeometryError("墙体顶轴 x 坐标超出帧")
    for y in range(alpha.height):
        if alpha.getpixel((x, y)) > 0:
            return y
    raise AssetGeometryError("墙体顶轴端点所在列没有可见像素")


def wall_ground_axis_pixels(
    image: Image.Image,
    ground_axis: Mapping[str, Any],
) -> dict[str, dict[str, int]]:
    """Inspect the visible lower endpoint pixels of a declared wall axis."""

    try:
        start_x = int(ground_axis["start"]["x"])
        end_x = int(ground_axis["end"]["x"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssetGeometryError("墙体 groundAxis 声明无效") from exc
    alpha = image.convert("RGBA").getchannel("A")
    return {
        "start": {"x": start_x, "y": bottom_visible_y(alpha, start_x)},
        "end": {"x": end_x, "y": bottom_visible_y(alpha, end_x)},
    }


def wall_face_geometry_pixels(
    image: Image.Image,
    ground_axis: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect matching top and ground endpoints for a native wall face."""

    ground = wall_ground_axis_pixels(image, ground_axis)
    alpha = image.convert("RGBA").getchannel("A")
    top = {
        "start": {
            "x": ground["start"]["x"],
            "y": top_visible_y(alpha, ground["start"]["x"]),
        },
        "end": {
            "x": ground["end"]["x"],
            "y": top_visible_y(alpha, ground["end"]["x"]),
        },
    }
    return {
        "groundAxis": ground,
        "topAxis": top,
        "faceHeight": {
            "start": ground["start"]["y"] - top["start"]["y"],
            "end": ground["end"]["y"] - top["end"]["y"],
        },
    }


def wall_screen_slope(image: Image.Image) -> float:
    """Return a robust image-space slope for a visible wall silhouette."""

    alpha = image.convert("RGBA").getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None:
        raise AssetGeometryError("墙体候选没有可见像素")
    left, top, right, bottom = bounds
    strip_width = max(1, (right - left) // 12)
    samples: list[tuple[float, float]] = []
    alpha_pixels = alpha.load()
    for start in range(left, right, strip_width):
        end = min(right, start + strip_width)
        points = [
            (x, y)
            for x in range(start, end)
            for y in range(top, bottom)
            if alpha_pixels[x, y] > 0
        ]
        if points:
            samples.append(
                (
                    sum(point[0] for point in points) / len(points),
                    sum(point[1] for point in points) / len(points),
                )
            )
    if len(samples) < 3:
        raise AssetGeometryError("墙体候选无法验证等距方向")
    mean_x = sum(point[0] for point in samples) / len(samples)
    mean_y = sum(point[1] for point in samples) / len(samples)
    denominator = sum((point[0] - mean_x) ** 2 for point in samples)
    if denominator == 0:
        raise AssetGeometryError("墙体候选无法验证等距方向")
    return sum(
        (point[0] - mean_x) * (point[1] - mean_y) for point in samples
    ) / denominator


__all__ = [
    "AssetGeometryError",
    "bottom_visible_y",
    "top_visible_y",
    "wall_face_geometry_pixels",
    "wall_ground_axis_pixels",
    "wall_screen_slope",
]
