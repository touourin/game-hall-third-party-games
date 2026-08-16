from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from PIL import Image

from . import character_motion
from .asset_geometry import (
    AssetGeometryError,
    bottom_visible_y,
    top_visible_y,
    wall_face_geometry_pixels,
    wall_ground_axis_pixels,
    wall_screen_slope,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PACK_SPEC = PROJECT_DIR / "assets" / "core-pack.spec.json"
CORE_V1_PACK_SPEC = PROJECT_DIR / "assets" / "core-v1-pack.spec.json"
CORE_V2_PACK_SPEC = PROJECT_DIR / "assets" / "core-v2-pack.spec.json"
PACK_SPEC_PATHS = {
    "core-v0": DEFAULT_PACK_SPEC,
    "core-v1": CORE_V1_PACK_SPEC,
    "core-v2": CORE_V2_PACK_SPEC,
}
CORE_V2_ALPHA_LEVELS = (0, 96, 128, 160, 192, 255)
DEFAULT_CHROMA_KEY = "#FF00FF"
DEFAULT_CHROMA_TOLERANCE = 52
DEFAULT_BACKDROP_FOCUS_Y = 0.55
DEFAULT_GLASS_PANE_ALPHA = 128
GLASS_PANE_SEED_LIGHTNESS = 165
GLASS_PANE_GROW_LIGHTNESS = 110
GLASS_PANE_MIN_AREA = 128
GLASS_PANE_MIN_VISIBLE_FRACTION = 0.08
GLASS_PANE_MIN_WIDTH = 8
GLASS_PANE_MAX_WIDTH = 24
GLASS_PANE_MIN_HEIGHT = 36
GLASS_PANE_MIN_SEED_COVERAGE = 0.70


class AssetNormalizationError(ValueError):
    """Raised when a generated candidate cannot become a deterministic sprite."""


def load_locked_palette(
    spec_path: Path = DEFAULT_PACK_SPEC,
    *,
    include_player_accents: bool = True,
) -> tuple[str, ...]:
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    palette = payload.get("palette", {})
    base_pack_id = payload.get("basePackId")
    if not palette and isinstance(base_pack_id, str):
        try:
            base_path = PACK_SPEC_PATHS[base_pack_id]
        except KeyError as exc:
            raise AssetNormalizationError(
                f"未知基础资产包：{base_pack_id}"
            ) from exc
        return load_locked_palette(
            base_path,
            include_player_accents=include_player_accents,
        )
    world = tuple(palette.get("world", ()))
    players = tuple(palette.get("players", ()))
    expected_world_count = 48 if payload.get("id") == "core-v2" else 32
    if len(world) != expected_world_count or len(players) != 8:
        raise AssetNormalizationError(
            f"资产包必须声明 {expected_world_count} 个世界色和 8 个玩家强调色"
        )
    colors = world + players if include_player_accents else world
    if len({color.casefold() for color in colors}) != len(colors):
        raise AssetNormalizationError("锁定调色板不能包含重复颜色")
    return colors


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.removeprefix("#")
    if len(value) != 6:
        raise AssetNormalizationError(f"无效调色板颜色：{hex_color}")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _is_key_like_pixel(
    pixel: tuple[int, int, int, int],
    key: tuple[int, int, int],
    tolerance: int,
) -> bool:
    red, green, blue, alpha = pixel
    return alpha > 0 and max(
        abs(red - key[0]),
        abs(green - key[1]),
        abs(blue - key[2]),
    ) <= tolerance


def _is_reserved_magenta_fringe(pixel: tuple[int, int, int, int]) -> bool:
    """Match dark antialias remnants produced around the reserved magenta key."""

    red, green, blue, alpha = pixel
    return bool(
        alpha > 0
        and red >= 96
        and blue >= 96
        and green <= 64
        and abs(red - blue) <= 64
        and min(red, blue) - green >= 64
    )


def _exterior_magenta_fringe_points(image: Image.Image) -> set[tuple[int, int]]:
    """Find reserved-key fringe connected to transparent exterior pixels."""

    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    fringe = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if _is_reserved_magenta_fringe(pixels[x, y])
    }
    seeds: list[tuple[int, int]] = []
    for x, y in fringe:
        if any(
            not 0 <= nx < width
            or not 0 <= ny < height
            or pixels[nx, ny][3] == 0
            for nx, ny in (
                (x - 1, y - 1),
                (x, y - 1),
                (x + 1, y - 1),
                (x - 1, y),
                (x + 1, y),
                (x - 1, y + 1),
                (x, y + 1),
                (x + 1, y + 1),
            )
        ):
            seeds.append((x, y))
    connected: set[tuple[int, int]] = set(seeds)
    queue: deque[tuple[int, int]] = deque(seeds)
    while queue:
        x, y = queue.popleft()
        for neighbour in (
            (x - 1, y - 1),
            (x, y - 1),
            (x + 1, y - 1),
            (x - 1, y),
            (x + 1, y),
            (x - 1, y + 1),
            (x, y + 1),
            (x + 1, y + 1),
        ):
            if neighbour in fringe and neighbour not in connected:
                connected.add(neighbour)
                queue.append(neighbour)
    return connected


def _reserved_chroma_counts(
    image: Image.Image,
    *,
    key_color: str = DEFAULT_CHROMA_KEY,
    tolerance: int = DEFAULT_CHROMA_TOLERANCE,
) -> dict[str, int]:
    key = _rgb(key_color.upper())
    counts = {"keyLike": 0, "magentaFringe": 0}
    for pixel in image.convert("RGBA").getdata():
        if _is_key_like_pixel(pixel, key, tolerance):
            counts["keyLike"] += 1
    counts["magentaFringe"] = len(_exterior_magenta_fringe_points(image))
    return counts


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _image_png_bytes(image: Image.Image) -> bytes:
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def quantize_rgba(image: Image.Image, colors: Iterable[str]) -> Image.Image:
    rgba = image.convert("RGBA")
    locked = tuple(colors)
    if not locked or len(locked) > 256:
        raise AssetNormalizationError("调色板必须包含 1–256 个颜色")
    palette = tuple(_rgb(color) for color in locked)
    cache: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    result = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    pixels: list[tuple[int, int, int, int]] = []
    for red, green, blue, alpha in rgba.getdata():
        if alpha == 0:
            pixels.append((0, 0, 0, 0))
            continue
        source = (red, green, blue)
        target = cache.get(source)
        if target is None:
            target = min(
                palette,
                key=lambda candidate: (
                    (source[0] - candidate[0]) ** 2
                    + (source[1] - candidate[1]) ** 2
                    + (source[2] - candidate[2]) ** 2
                ),
            )
            cache[source] = target
        pixels.append((*target, alpha))
    result.putdata(pixels)
    return result


def quantize_alpha(image: Image.Image, levels: Sequence[int]) -> Image.Image:
    rgba = image.convert("RGBA")
    locked = tuple(levels)
    if not locked or any(
        isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 255
        for level in locked
    ):
        raise AssetNormalizationError("alpha 层级必须为 0–255 的整数")
    alpha = rgba.getchannel("A")
    alpha.putdata(
        [min(locked, key=lambda target: (abs(value - target), target)) for value in alpha.getdata()]
    )
    rgba.putalpha(alpha)
    return rgba


def _visible_crop(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    bounds = rgba.getchannel("A").getbbox()
    if bounds is None:
        raise AssetNormalizationError("候选图没有任何可见像素")
    return rgba.crop(bounds)


def _ensure_transparency(image: Image.Image) -> None:
    alpha = image.convert("RGBA").getchannel("A")
    low, high = alpha.getextrema()
    if low != 0 or high == 0:
        raise AssetNormalizationError("候选图必须具有透明背景和可见主体")


def normalize_furniture(
    source: Image.Image,
    *,
    width: int = 96,
    height: int = 80,
    side_padding: int = 4,
    top_padding: int = 3,
    bottom_padding: int = 4,
    palette: Iterable[str] | None = None,
) -> Image.Image:
    _ensure_transparency(source)
    subject = _visible_crop(source)
    available_width = width - side_padding * 2
    available_height = height - top_padding - bottom_padding
    scale = min(available_width / subject.width, available_height / subject.height)
    if scale <= 0:
        raise AssetNormalizationError("目标家具画布过小")
    size = (
        max(1, round(subject.width * scale)),
        max(1, round(subject.height * scale)),
    )
    subject = subject.resize(size, Image.Resampling.NEAREST)
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = (width - subject.width) // 2
    y = height - bottom_padding - subject.height
    result.alpha_composite(subject, (x, y))
    return quantize_rgba(
        result,
        palette or load_locked_palette(include_player_accents=False),
    )


def normalize_backdrop(
    source: Image.Image,
    *,
    width: int,
    height: int,
    palette: Iterable[str] | None = None,
) -> Image.Image:
    """Nearest-neighbour cover crop for an intentionally opaque panorama."""

    rgba = source.convert("RGBA")
    if rgba.getchannel("A").getextrema()[1] == 0:
        raise AssetNormalizationError("背景候选没有任何可见像素")
    scale = max(width / rgba.width, height / rgba.height)
    resized = rgba.resize(
        (max(width, round(rgba.width * scale)), max(height, round(rgba.height * scale))),
        Image.Resampling.NEAREST,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    result = resized.crop((left, top, left + width, top + height))
    return quantize_rgba(
        result,
        palette or load_locked_palette(include_player_accents=False),
    )


def remove_chroma_background(
    source: Image.Image,
    *,
    key_color: str = DEFAULT_CHROMA_KEY,
    tolerance: int = DEFAULT_CHROMA_TOLERANCE,
    stats: dict[str, int] | None = None,
) -> Image.Image:
    """Remove every reserved key pixel after safely identifying a backdrop.

    An opaque source must expose key-like pixels on its border before any
    pixels are removed.  Once that confirms the generated backdrop, all
    key-like pixels are removed, including sealed holes inside the subject.
    Already-transparent authoring inputs remain valid and use the same global
    key removal because pure magenta is reserved by the v2 asset contract.
    """

    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 255:
        raise AssetNormalizationError("去背容差必须是 0–255 的整数")
    key = _rgb(key_color.upper())
    rgba = source.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()

    alpha_low, alpha_high = rgba.getchannel("A").getextrema()
    if alpha_high == 0:
        raise AssetNormalizationError("候选图没有任何可见像素")

    def is_key_like(x: int, y: int) -> bool:
        return _is_key_like_pixel(pixels[x, y], key, tolerance)

    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()
    for x in range(width):
        queue.append((x, 0))
        if height > 1:
            queue.append((x, height - 1))
    for y in range(1, max(1, height - 1)):
        queue.append((0, y))
        if width > 1:
            queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        point = (x, y)
        if point in visited or not is_key_like(x, y):
            continue
        visited.add(point)
        if x:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))

    already_transparent = alpha_low == 0
    if not visited and not already_transparent:
        # Opaque sources must prove that the reserved key is the backdrop;
        # otherwise a foreground magenta detail could become the only hole in
        # an image whose real background remains opaque.
        raise AssetNormalizationError(
            f"未在图像边缘找到可移除的色键 {key_color}"
        )

    # The prompt and pack reserve magenta exclusively for transparency.  A
    # second global pass is required because furniture and wall silhouettes
    # can enclose background islands that are unreachable from the border.
    key_like_removed = 0
    for y in range(height):
        for x in range(width):
            if is_key_like(x, y):
                red, green, blue, _ = pixels[x, y]
                pixels[x, y] = (red, green, blue, 0)
                key_like_removed += 1
    fringe_points = _exterior_magenta_fringe_points(rgba)
    for x, y in fringe_points:
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
    fringe_removed = len(fringe_points)
    residual = _reserved_chroma_counts(
        rgba,
        key_color=key_color,
        tolerance=tolerance,
    )
    if residual["keyLike"] or residual["magentaFringe"]:
        raise AssetNormalizationError("去背后仍存在保留品红色键或晕边")
    if stats is not None:
        stats.update(
            {
                "borderConnectedPixels": len(visited),
                "keyLikeRemoved": key_like_removed,
                "magentaFringeRemoved": fringe_removed,
                "residualPixels": 0,
            }
        )
    _ensure_transparency(rgba)
    return rgba


def _fit_native_subject(
    source: Image.Image,
    *,
    width: int,
    height: int,
    anchor_x: int,
    padding: int,
) -> tuple[Image.Image, dict[str, Any]]:
    subject = _visible_crop(source)
    if padding < 0 or padding * 2 >= min(width, height):
        raise AssetNormalizationError("原生帧留白超出目标画布")
    available_width = width - padding * 2
    available_height = height - padding * 2
    scale = min(available_width / subject.width, available_height / subject.height)
    if not math.isfinite(scale) or scale <= 0:
        raise AssetNormalizationError("原生帧缩放比例无效")
    output_size = (
        max(1, min(available_width, round(subject.width * scale))),
        max(1, min(available_height, round(subject.height * scale))),
    )
    resized = subject.resize(output_size, Image.Resampling.NEAREST)
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = max(padding, min(width - padding - resized.width, anchor_x - resized.width // 2))
    y = height - padding - resized.height
    result.alpha_composite(resized, (x, y))
    return result, {
        "mode": "subject-contain",
        "visibleSourceBounds": list(source.convert("RGBA").getchannel("A").getbbox() or ()),
        "placedBounds": [x, y, x + resized.width, y + resized.height],
        "scale": scale,
        "resampling": "nearest",
        "padding": padding,
    }


def _harden_binary_alpha(image: Image.Image) -> Image.Image:
    """Preserve the mask while removing generated soft-edge alpha levels."""

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    alpha.putdata([255 if value > 0 else 0 for value in alpha.getdata()])
    rgba.putalpha(alpha)
    return rgba


def _apply_component_glass_alpha(
    image: Image.Image,
    *,
    pane_alpha: int = DEFAULT_GLASS_PANE_ALPHA,
    pane_count: int = 4,
) -> tuple[Image.Image, dict[str, Any]]:
    """Make only broad, enclosed cool-light pane components translucent.

    The previous luminance-only rule also affected thin rail highlights.  This
    detector first hardens the authored silhouette, then finds connected
    cool-light regions and accepts only components shaped like enclosed panes.
    Every accepted pane receives one uniform alpha; frame, mullion, rail and
    highlight components remain fully opaque.
    """

    if (
        isinstance(pane_alpha, bool)
        or not isinstance(pane_alpha, int)
        or not 0 < pane_alpha < 255
    ):
        raise AssetNormalizationError("玻璃窗格 alpha 必须是 1–254 的整数")
    if isinstance(pane_count, bool) or not isinstance(pane_count, int) or pane_count <= 0:
        raise AssetNormalizationError("玻璃窗格数量必须是正整数")
    rgba = _harden_binary_alpha(image)
    width, height = rgba.size
    pixels = rgba.load()
    visible = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if pixels[x, y][3] > 0
    }
    seeds: set[tuple[int, int]] = set()
    grow_candidates: set[tuple[int, int]] = set()
    for x, y in visible:
        red, green, blue, _ = pixels[x, y]
        lightness = max(red, green, blue)
        cool_neutral = blue + 12 >= red and green + 12 >= red
        if cool_neutral and lightness >= GLASS_PANE_GROW_LIGHTNESS:
            grow_candidates.add((x, y))
            if lightness >= GLASS_PANE_SEED_LIGHTNESS:
                seeds.add((x, y))

    components: list[dict[str, Any]] = []
    remaining = set(grow_candidates)
    accepted_pixels: set[tuple[int, int]] = set()
    minimum_area = max(
        GLASS_PANE_MIN_AREA,
        math.ceil(len(visible) * GLASS_PANE_MIN_VISIBLE_FRACTION),
    )
    while remaining:
        seed = min(remaining, key=lambda point: (point[1], point[0]))
        queue: deque[tuple[int, int]] = deque([seed])
        remaining.remove(seed)
        points: list[tuple[int, int]] = []
        touches_exterior = False
        while queue:
            x, y = queue.popleft()
            points.append((x, y))
            for neighbour in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                nx, ny = neighbour
                if not 0 <= nx < width or not 0 <= ny < height:
                    touches_exterior = True
                    continue
                if neighbour not in visible:
                    touches_exterior = True
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    queue.append(neighbour)
        left = min(point[0] for point in points)
        top = min(point[1] for point in points)
        right = max(point[0] for point in points) + 1
        bottom = max(point[1] for point in points) + 1
        component_width = right - left
        component_height = bottom - top
        area = len(points)
        seed_area = sum(1 for point in points if point in seeds)
        seed_coverage = seed_area / area
        fill_ratio = area / (component_width * component_height)
        accepted = bool(
            not touches_exterior
            and seed_area > 0
            and seed_coverage >= GLASS_PANE_MIN_SEED_COVERAGE
            and area >= minimum_area
            and component_width >= GLASS_PANE_MIN_WIDTH
            and component_width <= GLASS_PANE_MAX_WIDTH
            and component_height >= GLASS_PANE_MIN_HEIGHT
        )
        if accepted:
            accepted_pixels.update(points)
        components.append(
            {
                "bounds": [left, top, right, bottom],
                "area": area,
                "seedArea": seed_area,
                "seedCoverage": round(seed_coverage, 6),
                "fillRatio": round(fill_ratio, 6),
                "touchesExterior": touches_exterior,
                "accepted": accepted,
            }
        )

    accepted_components = [
        component for component in components if component["accepted"]
    ]
    if len(accepted_components) != pane_count:
        raise AssetNormalizationError(
            f"窗墙候选识别到 {len(accepted_components)} 个宽玻璃窗格，规格要求 {pane_count} 个"
        )
    for x, y in accepted_pixels:
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, pane_alpha)
    opaque_pixels = len(visible) - len(accepted_pixels)
    if opaque_pixels <= 0:
        raise AssetNormalizationError("窗墙候选中未保留不透明结构框架")
    return rgba, {
        "mode": "enclosed-component-panes",
        "paneAlpha": pane_alpha,
        "paneCountExpected": pane_count,
        "seedPixels": len(seeds),
        "growPixels": len(grow_candidates),
        "panePixels": len(accepted_pixels),
        "opaqueStructurePixels": opaque_pixels,
        "componentCount": len(components),
        "paneComponentCount": sum(
            1 for component in components if component["accepted"]
        ),
        "thresholds": {
            "seedLightness": GLASS_PANE_SEED_LIGHTNESS,
            "growLightness": GLASS_PANE_GROW_LIGHTNESS,
            "minArea": minimum_area,
            "minVisibleFraction": GLASS_PANE_MIN_VISIBLE_FRACTION,
            "minWidth": GLASS_PANE_MIN_WIDTH,
            "maxWidth": GLASS_PANE_MAX_WIDTH,
            "minHeight": GLASS_PANE_MIN_HEIGHT,
            "minSeedCoverage": GLASS_PANE_MIN_SEED_COVERAGE,
        },
        "components": components,
    }


def _prepare_native_wall(
    source: Image.Image,
    *,
    width: int,
    height: int,
    ground_axis: Mapping[str, Any],
    wall_face_height: int,
    orientation: str,
) -> tuple[Image.Image, dict[str, Any]]:
    """Lock a generated wall face to its ground and top seam axes.

    Every visible source column is resampled independently into the declared
    face height, then placed between the full-footprint ground axis and its
    parallel top axis.  Adjacent native wall sprites therefore share both
    seams without runtime mirroring, affine repair or overlapping wide source
    silhouettes.
    """

    subject = _visible_crop(source)
    source_alpha = subject.getchannel("A")
    source_start_y = bottom_visible_y(source_alpha, 0)
    source_end_y = bottom_visible_y(source_alpha, subject.width - 1)
    source_delta_y = source_end_y - source_start_y
    try:
        target_start = {
            "x": int(ground_axis["start"]["x"]),
            "y": int(ground_axis["start"]["y"]),
        }
        target_end = {
            "x": int(ground_axis["end"]["x"]),
            "y": int(ground_axis["end"]["y"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise AssetNormalizationError("墙体 groundAxis 声明无效") from exc
    target_delta_x = target_end["x"] - target_start["x"]
    target_delta_y = target_end["y"] - target_start["y"]
    if (
        isinstance(wall_face_height, bool)
        or not isinstance(wall_face_height, int)
        or wall_face_height <= 0
    ):
        raise AssetNormalizationError("墙面高度必须是正整数")
    if target_delta_x <= 0 or not source_delta_y or not target_delta_y:
        raise AssetNormalizationError("墙体地面轴跨度无效")
    if (source_delta_y > 0) != (target_delta_y > 0):
        raise AssetNormalizationError(
            f"墙体原图地面轴方向与 {orientation} 规格不符"
        )

    target_top_start = {
        "x": target_start["x"],
        "y": target_start["y"] - wall_face_height,
    }
    target_top_end = {
        "x": target_end["x"],
        "y": target_end["y"] - wall_face_height,
    }
    if min(target_top_start["y"], target_top_end["y"]) < 0:
        raise AssetNormalizationError("墙体顶轴超出原生帧")

    output_width = target_delta_x + 1
    scale_x = target_delta_x / max(1, subject.width - 1)
    horizontally_resized = subject.resize(
        (output_width, subject.height),
        Image.Resampling.NEAREST,
    )
    resized_alpha = horizontally_resized.getchannel("A")
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    source_column_heights: list[int] = []
    for local_x in range(output_width):
        actual_top = top_visible_y(resized_alpha, local_x)
        actual_bottom = bottom_visible_y(resized_alpha, local_x)
        source_column_height = actual_bottom - actual_top + 1
        source_column_heights.append(source_column_height)
        desired_bottom = round(
            target_start["y"]
            + target_delta_y * local_x / max(1, output_width - 1)
        )
        desired_top = desired_bottom - wall_face_height
        target_x = target_start["x"] + local_x
        if (
            not 0 <= target_x < width
            or desired_top < 0
            or desired_bottom >= height
        ):
            raise AssetNormalizationError(
                "墙体按顶底轴归一化后超出原生帧；请修正规格"
            )
        source_column = horizontally_resized.crop(
            (local_x, actual_top, local_x + 1, actual_bottom + 1)
        )
        normalized_column = source_column.resize(
            (1, wall_face_height + 1),
            Image.Resampling.NEAREST,
        )
        result.alpha_composite(normalized_column, (target_x, desired_top))

    actual_geometry = wall_face_geometry_pixels(result, ground_axis)
    expected_geometry = {
        "groundAxis": {"start": target_start, "end": target_end},
        "topAxis": {"start": target_top_start, "end": target_top_end},
        "faceHeight": {"start": wall_face_height, "end": wall_face_height},
    }
    if actual_geometry != expected_geometry:
        raise AssetNormalizationError(
            f"墙体顶底轴像素 {actual_geometry} 与规格 {expected_geometry} 不符"
        )
    return result, {
        "mode": "footprint-ground-and-top-axis-lock",
        "visibleSourceBounds": list(source.getchannel("A").getbbox() or ()),
        "sourceAxis": {
            "start": {"x": 0, "y": source_start_y},
            "end": {"x": subject.width - 1, "y": source_end_y},
        },
        "groundAxis": {"start": target_start, "end": target_end},
        "topAxis": {"start": target_top_start, "end": target_top_end},
        "wallFaceHeight": wall_face_height,
        "axisSpan": {
            "x": target_delta_x,
            "y": target_delta_y,
        },
        "scale": {"x": scale_x},
        "sourceColumnHeightRange": [
            min(source_column_heights),
            max(source_column_heights),
        ],
        "resampling": "nearest",
    }


def _prepare_backdrop_full_canvas(
    source: Image.Image,
    *,
    width: int,
    height: int,
) -> tuple[Image.Image, dict[str, Any]]:
    """Produce an opaque full-canvas backdrop by native pass-through or cover.

    The transform never stretches, tiles, or invents edge rows.  A non-native
    source is resized uniformly with nearest-neighbour sampling and receives a
    deterministic centred cover crop.  Exact scale and crop values are kept in
    the preparation report so the authored panorama can be reproduced.
    """

    rgba = source.convert("RGBA")
    if rgba.getchannel("A").getextrema()[1] == 0:
        raise AssetNormalizationError("背景候选没有任何可见像素")
    if rgba.getchannel("A").getextrema() != (255, 255):
        raise AssetNormalizationError("全画布背景必须完全不透明")

    if rgba.size == (width, height):
        resized = rgba
        scale = 1.0
        mode = "full-canvas-native"
    else:
        scale = max(width / rgba.width, height / rgba.height)
        resized = rgba.resize(
            (
                max(width, round(rgba.width * scale)),
                max(height, round(rgba.height * scale)),
            ),
            Image.Resampling.NEAREST,
        )
        mode = "full-canvas-cover"
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    crop = [left, top, left + width, top + height]
    result = resized.crop(tuple(crop))
    return result, {
        "mode": mode,
        "scale": {
            "x": resized.width / rgba.width,
            "y": resized.height / rgba.height,
            "uniformRequested": scale,
        },
        "resizedSize": list(resized.size),
        "crop": crop,
        "resampling": "nearest",
    }


def _verify_native_orientation(image: Image.Image, slot: str, orientation: str | None) -> float | None:
    if not slot.startswith("structure.wall-") or orientation not in {"nw", "ne"}:
        return None
    try:
        slope = wall_screen_slope(image)
    except AssetGeometryError as exc:
        raise AssetNormalizationError(str(exc)) from exc
    expected_positive = orientation == "nw"
    if (expected_positive and slope <= 0.04) or (not expected_positive and slope >= -0.04):
        raise AssetNormalizationError(
            f"墙体 {slot} 像素斜率 {slope:.3f} 与 {orientation} 等距方向不符"
        )
    return slope


def prepare_native_candidate(
    source: Image.Image,
    slot: str,
    *,
    spec_path: Path = CORE_V2_PACK_SPEC,
    key_color: str = DEFAULT_CHROMA_KEY,
    key_tolerance: int = DEFAULT_CHROMA_TOLERANCE,
    focus_y: float = DEFAULT_BACKDROP_FOCUS_Y,
    padding: int = 1,
) -> tuple[Image.Image, dict[str, Any]]:
    """Prepare one generated source for the strict native-frame normalizer.

    Preparation is intentionally a separate, recorded boundary. ``slot``
    still rejects every non-native input and remains the only command that
    applies the final palette, alpha and sidecar contract.
    """

    spec = _load_spec(spec_path)
    if spec.get("nativeFrameRequired") is not True:
        raise AssetNormalizationError("prepare 仅用于要求原生帧的资产包")
    metadata = slot_metadata(slot, spec_path)
    width = int(metadata["frameWidth"]) * int(metadata["columns"])
    height = int(metadata["frameHeight"]) * int(metadata["rows"])
    source_rgba = source.convert("RGBA")
    source_bytes = _image_png_bytes(source_rgba)
    if metadata["kind"] == "backdrop":
        # ``focus_y`` remains an accepted compatibility argument for callers
        # of the v2 preview tool, but full-canvas backgrounds deliberately do
        # not have a focal-band control.
        _ = focus_y
        prepared, transform = _prepare_backdrop_full_canvas(
            source_rgba,
            width=width,
            height=height,
        )
    else:
        chroma_stats: dict[str, int] = {}
        keyed = remove_chroma_background(
            source_rgba,
            key_color=key_color,
            tolerance=key_tolerance,
            stats=chroma_stats,
        )
        if metadata["kind"] == "floor":
            subject = _visible_crop(keyed).resize((width, height), Image.Resampling.NEAREST)
            subject.putalpha(canonical_diamond_alpha(width, height))
            prepared = subject
            transform = {
                "mode": "canonical-floor-diamond",
                "visibleSourceBounds": list(keyed.getchannel("A").getbbox() or ()),
                "placedBounds": [0, 0, width, height],
                "resampling": "nearest",
            }
        elif slot.startswith("structure.wall-") and isinstance(
            metadata.get("groundAxis"), Mapping
        ):
            try:
                prepared, transform = _prepare_native_wall(
                    keyed,
                    width=width,
                    height=height,
                    ground_axis=metadata["groundAxis"],
                    wall_face_height=int(metadata["wallFaceHeight"]),
                    orientation=str(metadata.get("orientation", "")),
                )
            except AssetGeometryError as exc:
                raise AssetNormalizationError(str(exc)) from exc
            if slot in {"structure.wall-window-nw", "structure.wall-window-ne"}:
                prepared, pane_detection = _apply_component_glass_alpha(
                    prepared,
                    pane_alpha=int(metadata["paneAlpha"]),
                    pane_count=int(metadata["paneCount"]),
                )
                transform["glassPaneDetection"] = pane_detection
        else:
            prepared, transform = _fit_native_subject(
                keyed,
                width=width,
                height=height,
                anchor_x=int(metadata["anchor"]["x"]),
                padding=padding,
            )
    if slot not in {"structure.wall-window-nw", "structure.wall-window-ne"}:
        prepared = _harden_binary_alpha(prepared)
        transform["alphaLevels"] = [0, 255]
    slope = _verify_native_orientation(prepared, slot, metadata.get("orientation"))
    prepared_bytes = _image_png_bytes(prepared)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "packId": str(spec["id"]),
        "slot": slot,
        "sourceSha256": _sha256_bytes(source_bytes),
        "outputSha256": _sha256_bytes(prepared_bytes),
        "sourceSize": list(source_rgba.size),
        "outputSize": [width, height],
        "transform": transform,
    }
    if metadata["kind"] != "backdrop":
        report["chromaKey"] = {
            "color": key_color.upper(),
            "tolerance": key_tolerance,
            "backgroundConfirmation": "edge-connected-or-existing-alpha",
            "borderConnectedOnly": False,
            "globalKeyRemoval": True,
            "fringePolicy": "reserved-magenta-cleanup-fail-closed",
            **chroma_stats,
        }
    if slope is not None:
        report["orientationCheck"] = {
            "orientation": metadata["orientation"],
            "screenSlope": round(slope, 6),
            "passed": True,
        }
    return prepared, report


def load_preparation_report(
    path: Path,
    *,
    pack_id: str,
    slot: str,
    prepared_source: Path,
) -> dict[str, Any]:
    """Load provenance only when it describes the exact prepared input."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssetNormalizationError("原生帧准备报告无法读取") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise AssetNormalizationError("原生帧准备报告格式无效")
    if payload.get("packId") != pack_id or payload.get("slot") != slot:
        raise AssetNormalizationError("原生帧准备报告与资产包或槽位不匹配")
    try:
        actual_sha = _sha256_bytes(prepared_source.read_bytes())
    except OSError as exc:
        raise AssetNormalizationError("无法校验原生帧准备输入") from exc
    if payload.get("outputSha256") != actual_sha:
        raise AssetNormalizationError("原生帧准备报告与当前输入 SHA-256 不匹配")
    if pack_id == "core-v2" and slot.startswith("structure.wall-"):
        metadata = slot_metadata(slot, pack_spec_path(pack_id))
        ground_axis = metadata["groundAxis"]
        wall_face_height = int(metadata["wallFaceHeight"])
        expected_top_axis = {
            endpoint: {
                "x": int(ground_axis[endpoint]["x"]),
                "y": int(ground_axis[endpoint]["y"]) - wall_face_height,
            }
            for endpoint in ("start", "end")
        }
        transform = payload.get("transform")
        if (
            not isinstance(transform, Mapping)
            or transform.get("groundAxis") != ground_axis
            or transform.get("topAxis") != expected_top_axis
            or transform.get("wallFaceHeight") != wall_face_height
        ):
            raise AssetNormalizationError("原生帧准备报告的墙体顶底轴规格无效")
        try:
            with Image.open(prepared_source) as opened:
                prepared_rgba = opened.convert("RGBA")
                actual_geometry = wall_face_geometry_pixels(
                    prepared_rgba, ground_axis
                )
                actual_alpha_values = set(
                    prepared_rgba.getchannel("A").getdata()
                )
        except (OSError, AssetGeometryError) as exc:
            raise AssetNormalizationError("无法验证原生帧墙体顶底轴") from exc
        expected_geometry = {
            "groundAxis": ground_axis,
            "topAxis": expected_top_axis,
            "faceHeight": {
                "start": wall_face_height,
                "end": wall_face_height,
            },
        }
        if actual_geometry != expected_geometry:
            raise AssetNormalizationError("原生帧墙体像素与准备报告的顶底轴不匹配")
        if slot.startswith("structure.wall-window-"):
            pane_alpha = int(metadata["paneAlpha"])
            pane_count = int(metadata["paneCount"])
            pane_detection = transform.get("glassPaneDetection")
            pane_pixels = (
                pane_detection.get("panePixels")
                if isinstance(pane_detection, Mapping)
                else None
            )
            structure_pixels = (
                pane_detection.get("opaqueStructurePixels")
                if isinstance(pane_detection, Mapping)
                else None
            )
            if (
                not isinstance(pane_detection, Mapping)
                or pane_detection.get("mode") != "enclosed-component-panes"
                or pane_detection.get("paneAlpha") != pane_alpha
                or pane_detection.get("paneCountExpected") != pane_count
                or pane_detection.get("paneComponentCount") != pane_count
                or isinstance(pane_pixels, bool)
                or not isinstance(pane_pixels, int)
                or pane_pixels <= 0
                or isinstance(structure_pixels, bool)
                or not isinstance(structure_pixels, int)
                or structure_pixels <= 0
            ):
                raise AssetNormalizationError("原生帧准备报告的玻璃窗格识别无效")
            if not actual_alpha_values.issubset({0, pane_alpha, 255}) or not {
                pane_alpha,
                255,
            }.issubset(actual_alpha_values):
                raise AssetNormalizationError("原生帧玻璃窗格的 alpha 层级无效")
    return payload


def canonical_diamond_alpha(width: int = 32, height: int = 16) -> Image.Image:
    """Return the exact pixel-centre raster for a seamless 2:1 diamond tile."""

    if width <= 0 or height <= 0 or width != height * 2:
        raise AssetNormalizationError("地板菱形必须使用正尺寸 2:1 画布")
    mask = Image.new("L", (width, height), 0)
    pixels: list[int] = []
    for y in range(height):
        for x in range(width):
            # Pixel-centre form of |x|/(w/2) + |y|/(h/2) <= 1.
            inside = (
                abs(2 * x + 1 - width) * height
                + abs(2 * y + 1 - height) * width
                <= width * height
            )
            pixels.append(255 if inside else 0)
    mask.putdata(pixels)
    return mask


def normalize_floor_tile(
    source: Image.Image,
    *,
    width: int = 32,
    height: int = 16,
    palette: Iterable[str] | None = None,
) -> Image.Image:
    """Keep generated texture while replacing its silhouette with one fixed tile.

    Generated alpha is deliberately not trusted: even a one-pixel edge wobble
    opens a black hole where four isometric tiles meet. Transparent source
    samples inside the canonical diamond are deterministically filled from the
    nearest visible texture pixel before the hard 0/255 mask is applied.
    """

    _ensure_transparency(source)
    subject = _visible_crop(source).resize((width, height), Image.Resampling.NEAREST)
    mask = canonical_diamond_alpha(width, height)
    source_pixels = subject.load()
    visible = [
        (x, y, source_pixels[x, y][:3])
        for y in range(height)
        for x in range(width)
        if source_pixels[x, y][3] > 0
    ]
    if not visible:
        raise AssetNormalizationError("地板候选没有可用于填充的纹理像素")

    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    result_pixels = result.load()
    mask_pixels = mask.load()
    for y in range(height):
        for x in range(width):
            if mask_pixels[x, y] == 0:
                continue
            red, green, blue, alpha = source_pixels[x, y]
            if alpha == 0:
                _, _, (red, green, blue) = min(
                    visible,
                    key=lambda sample: (
                        (sample[0] - x) ** 2 + (sample[1] - y) ** 2,
                        sample[1],
                        sample[0],
                    ),
                )
            result_pixels[x, y] = (red, green, blue, 255)

    normalized = quantize_rgba(
        result,
        palette or load_locked_palette(include_player_accents=False),
    )
    normalized.putalpha(mask)
    _ensure_transparency(normalized)
    return normalized


def normalize_character_grid(
    source: Image.Image,
    *,
    columns: int = character_motion.SHEET_COLUMNS,
    rows: int = character_motion.SHEET_ROWS,
    frame_width: int = character_motion.FRAME_WIDTH,
    frame_height: int = character_motion.FRAME_HEIGHT,
    horizontal_padding: int = 2,
    vertical_padding: int = 2,
    palette: Iterable[str] | None = None,
) -> Image.Image:
    _ensure_transparency(source)
    rgba = source.convert("RGBA")
    if rgba.width % columns or rgba.height % rows:
        raise AssetNormalizationError(
            f"角色动作表必须能被 {columns}×{rows} 网格整除"
        )

    cell_width = rgba.width // columns
    cell_height = rgba.height // rows
    frames: list[Image.Image] = []
    for row in range(rows):
        for column in range(columns):
            cell = rgba.crop(
                (
                    column * cell_width,
                    row * cell_height,
                    (column + 1) * cell_width,
                    (row + 1) * cell_height,
                )
            )
            frames.append(_visible_crop(cell))

    largest_width = max(frame.width for frame in frames)
    largest_height = max(frame.height for frame in frames)
    scale = min(
        (frame_width - horizontal_padding * 2) / largest_width,
        (frame_height - vertical_padding * 2) / largest_height,
    )
    if scale <= 0:
        raise AssetNormalizationError("角色帧画布过小")

    sheet = Image.new(
        "RGBA",
        (columns * frame_width, rows * frame_height),
        (0, 0, 0, 0),
    )
    for index, frame in enumerate(frames):
        size = (
            max(1, round(frame.width * scale)),
            max(1, round(frame.height * scale)),
        )
        resized = frame.resize(size, Image.Resampling.NEAREST)
        column = index % columns
        row = index // columns
        x = column * frame_width + (frame_width - resized.width) // 2
        y = (row + 1) * frame_height - vertical_padding - resized.height
        sheet.alpha_composite(resized, (x, y))

    return quantize_rgba(sheet, palette or load_locked_palette())


def normalize_sprite_grid(
    source: Image.Image,
    *,
    columns: int,
    rows: int,
    frame_width: int,
    frame_height: int,
    horizontal_padding: int = 2,
    vertical_padding: int = 2,
    palette: Iterable[str] | None = None,
) -> Image.Image:
    """Normalize a regular multi-frame sheet without smoothing or frame drift."""

    _ensure_transparency(source)
    rgba = source.convert("RGBA")
    if columns <= 0 or rows <= 0 or rgba.width < columns or rgba.height < rows:
        raise AssetNormalizationError(f"动作表无法切分为 {columns}×{rows} 网格")
    frames: list[Image.Image] = []
    for row in range(rows):
        for column in range(columns):
            frames.append(
                _visible_crop(
                    rgba.crop(
                        (
                            round(column * rgba.width / columns),
                            round(row * rgba.height / rows),
                            round((column + 1) * rgba.width / columns),
                            round((row + 1) * rgba.height / rows),
                        )
                    )
                )
            )
    scale = min(
        (frame_width - horizontal_padding * 2) / max(frame.width for frame in frames),
        (frame_height - vertical_padding * 2) / max(frame.height for frame in frames),
    )
    if scale <= 0:
        raise AssetNormalizationError("目标帧画布过小")
    sheet = Image.new(
        "RGBA",
        (columns * frame_width, rows * frame_height),
        (0, 0, 0, 0),
    )
    for index, frame in enumerate(frames):
        resized = frame.resize(
            (
                max(1, round(frame.width * scale)),
                max(1, round(frame.height * scale)),
            ),
            Image.Resampling.NEAREST,
        )
        column = index % columns
        row = index // columns
        x = column * frame_width + (frame_width - resized.width) // 2
        y = (row + 1) * frame_height - vertical_padding - resized.height
        sheet.alpha_composite(resized, (x, y))
    result = quantize_rgba(
        sheet,
        palette or load_locked_palette(include_player_accents=False),
    )
    _ensure_transparency(result)
    return result


def _load_spec(spec_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssetNormalizationError(f"无法读取资产包规格：{spec_path}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise AssetNormalizationError("资产包规格版本无效")
    required = payload.get(
        "requiredEditableSlots",
        payload.get("requiredSlots", payload.get("requiredNewSlots")),
    )
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(slot, str) or not slot for slot in required)
        or len(set(required)) != len(required)
    ):
        raise AssetNormalizationError("资产包必需槽位声明无效")
    payload["normalizableSlots"] = required
    return payload


def pack_spec_path(pack_id: str) -> Path:
    try:
        return PACK_SPEC_PATHS[pack_id]
    except KeyError as exc:
        raise AssetNormalizationError(f"未知资产包：{pack_id}") from exc


def slot_metadata(
    slot: str,
    spec_path: Path = DEFAULT_PACK_SPEC,
) -> dict[str, Any]:
    """Return an AssetLab-compatible sidecar skeleton for one locked slot."""

    spec = _load_spec(spec_path)
    if slot not in spec["normalizableSlots"]:
        raise AssetNormalizationError(f"未知 {spec.get('id', 'asset pack')} 槽位：{slot}")
    assets = spec.get("assets")
    if not isinstance(assets, Sequence):
        raise AssetNormalizationError("资产包缺少 assets")

    if slot == "character.gus":
        sheets = spec.get("sheets")
        sheet = next(
            (
                value
                for value in sheets or []
                if isinstance(value, Mapping) and value.get("id") == slot
            ),
            None,
        )
        first_frame = next(
            (
                value
                for value in assets
                if isinstance(value, Mapping)
                and isinstance(value.get("id"), str)
                and value["id"].startswith("character.gus.")
            ),
            None,
        )
        if not isinstance(sheet, Mapping) or not isinstance(first_frame, Mapping):
            raise AssetNormalizationError("角色槽位规格不完整")
        frame_count = int(sheet["columns"]) * int(sheet["rows"])
        if frame_count != character_motion.FRAME_COUNT:
            raise AssetNormalizationError(
                f"角色槽位规格声明 {frame_count} 帧，与动作编译器的 "
                f"{character_motion.FRAME_COUNT} 帧不一致"
            )
        return {
            "assetId": slot,
            "packId": str(spec["id"]),
            "slot": slot,
            "kind": "character",
            "frameWidth": int(sheet["cellWidth"]),
            "frameHeight": int(sheet["cellHeight"]),
            "columns": int(sheet["columns"]),
            "rows": int(sheet["rows"]),
            "frameCount": int(sheet["columns"]) * int(sheet["rows"]),
            "anchor": dict(first_frame["anchor"]),
            # The frame index table lives in the motion compiler so the pack
            # spec, the normalizer and the web contract cannot drift apart.
            "animations": character_motion.animation_metadata(),
        }

    if slot == "effect.good-card-heart":
        frames = [
            value
            for value in assets
            if isinstance(value, Mapping)
            and isinstance(value.get("id"), str)
            and value["id"].startswith("effect.good-card-heart.")
        ]
        if len(frames) != 4:
            raise AssetNormalizationError("好人卡爱心必须声明 4 帧")
        first = frames[0]
        return {
            "assetId": slot,
            "packId": str(spec["id"]),
            "slot": slot,
            "kind": "effect",
            "frameWidth": int(first["frame"]["width"]),
            "frameHeight": int(first["frame"]["height"]),
            "columns": 4,
            "rows": 1,
            "frameCount": 4,
            "anchor": dict(first["anchor"]),
        }

    template = next(
        (
            value
            for value in assets
            if isinstance(value, Mapping) and value.get("slot") == slot
        ),
        None,
    )
    if not isinstance(template, Mapping):
        raise AssetNormalizationError(f"槽位缺少运行时模板：{slot}")
    frame = template.get("frame")
    if not isinstance(frame, Mapping):
        raise AssetNormalizationError(f"槽位帧规格无效：{slot}")
    result = {
        "assetId": slot,
        "packId": str(spec["id"]),
        "slot": slot,
        "kind": str(template["kind"]),
        "frameWidth": int(frame["width"]),
        "frameHeight": int(frame["height"]),
        "columns": 1,
        "rows": 1,
        "frameCount": 1,
        "anchor": dict(template["anchor"]),
        "footprint": [dict(cell) for cell in template["footprint"]],
    }
    if isinstance(template.get("orientation"), str):
        result["orientation"] = template["orientation"]
    if isinstance(template.get("groundAxis"), Mapping):
        result["groundAxis"] = {
            "start": dict(template["groundAxis"]["start"]),
            "end": dict(template["groundAxis"]["end"]),
        }
    wall_face_required = (
        spec.get("geometryVersion") == 2
        and slot.startswith("structure.wall-")
    )
    if wall_face_required or "wallFaceHeight" in template:
        wall_face_height = template.get("wallFaceHeight")
        if (
            isinstance(wall_face_height, bool)
            or not isinstance(wall_face_height, int)
            or wall_face_height <= 0
        ):
            raise AssetNormalizationError(f"墙面高度规格无效：{slot}")
        result["wallFaceHeight"] = wall_face_height
    pane_alpha_required = (
        spec.get("geometryVersion") == 2
        and slot.startswith("structure.wall-window-")
    )
    if pane_alpha_required or "paneAlpha" in template:
        pane_alpha = template.get("paneAlpha")
        if (
            isinstance(pane_alpha, bool)
            or not isinstance(pane_alpha, int)
            or not 0 < pane_alpha < 255
        ):
            raise AssetNormalizationError(f"玻璃窗格 alpha 规格无效：{slot}")
        result["paneAlpha"] = pane_alpha
        pane_count = template.get("paneCount")
        if (
            isinstance(pane_count, bool)
            or not isinstance(pane_count, int)
            or pane_count <= 0
        ):
            raise AssetNormalizationError(f"玻璃窗格数量规格无效：{slot}")
        result["paneCount"] = pane_count
    if isinstance(template.get("interactionPoints"), Sequence):
        result["interactionPoints"] = [
            dict(point) for point in template["interactionPoints"]
        ]
    return result


def normalize_slot(
    source: Image.Image,
    slot: str,
    *,
    spec_path: Path = DEFAULT_PACK_SPEC,
) -> Image.Image:
    """Normalize any versioned pack slot to exact runtime geometry and palette."""

    metadata = slot_metadata(slot, spec_path)
    spec = _load_spec(spec_path)
    frame_width = int(metadata["frameWidth"])
    frame_height = int(metadata["frameHeight"])
    expected_size = (
        frame_width * int(metadata["columns"]),
        frame_height * int(metadata["rows"]),
    )
    is_window = slot in {"structure.wall-window-nw", "structure.wall-window-ne"}
    if spec.get("nativeFrameRequired") is True:
        if source.size != expected_size:
            raise AssetNormalizationError(
                f"槽位 {slot} 必须以原生帧尺寸 {expected_size} 生成，不允许缩放或 cover 裁切"
            )
        residual_chroma = _reserved_chroma_counts(source)
        if residual_chroma["keyLike"] or residual_chroma["magentaFringe"]:
            raise AssetNormalizationError(
                f"槽位 {slot} 仍含保留品红色键或晕边，请先执行 prepare"
            )
        if metadata["kind"] == "backdrop":
            if source.convert("RGBA").getchannel("A").getextrema()[1] == 0:
                raise AssetNormalizationError("背景候选没有任何可见像素")
            result = quantize_rgba(
                _harden_binary_alpha(source),
                load_locked_palette(spec_path, include_player_accents=False),
            )
        elif metadata["kind"] == "floor":
            result = normalize_floor_tile(
                source,
                width=frame_width,
                height=frame_height,
                palette=load_locked_palette(
                    spec_path, include_player_accents=False
                ),
            )
        else:
            native_source = source
            if is_window:
                native_source, _ = _apply_component_glass_alpha(
                    source,
                    pane_alpha=int(metadata["paneAlpha"]),
                    pane_count=int(metadata["paneCount"]),
                )
            else:
                native_source = _harden_binary_alpha(source)
            _ensure_transparency(native_source)
            result = quantize_rgba(
                native_source,
                load_locked_palette(spec_path, include_player_accents=False),
            )
    elif slot == "character.gus":
        result = normalize_character_grid(
            source,
            columns=int(metadata["columns"]),
            rows=int(metadata["rows"]),
            frame_width=frame_width,
            frame_height=frame_height,
            palette=load_locked_palette(spec_path),
        )
    elif slot == "effect.good-card-heart":
        result = normalize_sprite_grid(
            source,
            columns=4,
            rows=1,
            frame_width=frame_width,
            frame_height=frame_height,
            palette=load_locked_palette(
                spec_path, include_player_accents=False
            ),
        )
    else:
        is_floor = metadata["kind"] == "floor"
        if metadata["kind"] == "backdrop":
            result = normalize_backdrop(
                source,
                width=frame_width,
                height=frame_height,
                palette=load_locked_palette(
                    spec_path, include_player_accents=False
                ),
            )
        elif is_floor:
            result = normalize_floor_tile(
                source,
                width=frame_width,
                height=frame_height,
                palette=load_locked_palette(
                    spec_path, include_player_accents=False
                ),
            )
        else:
            result = normalize_furniture(
                source,
                width=frame_width,
                height=frame_height,
                side_padding=max(1, frame_width // 24),
                top_padding=max(1, frame_height // 27),
                bottom_padding=max(2, frame_height // 20),
                palette=load_locked_palette(
                    spec_path, include_player_accents=False
                ),
            )
    if result.size != expected_size:
        raise AssetNormalizationError(
            f"槽位 {slot} 输出尺寸错误：{result.size}，应为 {expected_size}"
        )
    alpha_levels = spec.get("alphaLevels")
    if spec.get("nativeFrameRequired") is True:
        alpha_levels = (
            (0, int(metadata["paneAlpha"]), 255)
            if is_window
            else (0, 255)
        )
    if alpha_levels is not None:
        result = quantize_alpha(result, tuple(alpha_levels))
    if metadata["kind"] != "backdrop":
        _ensure_transparency(result)
    return result


def inspect_png(path: Path) -> dict[str, object]:
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    alpha = image.getchannel("A")
    colors = {
        (red, green, blue)
        for red, green, blue, opacity in image.getdata()
        if opacity > 0
    }
    return {
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
        "visibleBounds": alpha.getbbox(),
        "transparentCorners": all(
            image.getpixel(point)[3] == 0
            for point in (
                (0, 0),
                (image.width - 1, 0),
                (0, image.height - 1),
                (image.width - 1, image.height - 1),
            )
        ),
        "opaqueColorCount": len(colors),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize Codex pixel candidates")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("furniture", "character-grid"):
        child = subparsers.add_parser(command)
        child.add_argument("source", type=Path)
        child.add_argument("output", type=Path)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("source", type=Path)
    slot = subparsers.add_parser("slot")
    slot.add_argument("--pack", choices=tuple(PACK_SPEC_PATHS), default="core-v0")
    slot.add_argument("slot")
    slot.add_argument("source", type=Path)
    slot.add_argument("output", type=Path)
    slot.add_argument("--sidecar", type=Path)
    slot.add_argument("--job-id")
    slot.add_argument("--source-prompt")
    slot.add_argument("--preparation-report", type=Path)
    prepare = subparsers.add_parser(
        "prepare",
        help="prepare a generated source for a strict native-frame pack",
    )
    prepare.add_argument("--pack", choices=tuple(PACK_SPEC_PATHS), default="core-v2")
    prepare.add_argument("slot")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("output", type=Path)
    prepare.add_argument("--report", type=Path)
    prepare.add_argument("--key-color", default=DEFAULT_CHROMA_KEY)
    prepare.add_argument("--key-tolerance", type=int, default=DEFAULT_CHROMA_TOLERANCE)
    prepare.add_argument("--focus-y", type=float, default=DEFAULT_BACKDROP_FOCUS_Y)
    prepare.add_argument("--padding", type=int, default=1)
    return parser


def main() -> None:
    arguments = _build_parser().parse_args()
    if arguments.command == "inspect":
        print(json.dumps(inspect_png(arguments.source), ensure_ascii=False))
        return
    with Image.open(arguments.source) as opened:
        source = opened.convert("RGBA")
    preparation_report: dict[str, Any] | None = None
    if arguments.command == "furniture":
        normalized = normalize_furniture(source)
    elif arguments.command == "character-grid":
        normalized = normalize_character_grid(source)
    elif arguments.command == "prepare":
        spec_path = pack_spec_path(arguments.pack)
        normalized, preparation_report = prepare_native_candidate(
            source,
            arguments.slot,
            spec_path=spec_path,
            key_color=arguments.key_color,
            key_tolerance=arguments.key_tolerance,
            focus_y=arguments.focus_y,
            padding=arguments.padding,
        )
        preparation_report["sourceSha256"] = _sha256_bytes(
            arguments.source.read_bytes()
        )
    else:
        spec_path = pack_spec_path(arguments.pack)
        normalized = normalize_slot(source, arguments.slot, spec_path=spec_path)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(arguments.output, format="PNG", optimize=True)
    if arguments.command == "slot" and arguments.sidecar is not None:
        metadata = slot_metadata(arguments.slot, spec_path)
        metadata["sourceName"] = arguments.output.name
        if arguments.job_id:
            metadata["jobId"] = arguments.job_id
        if arguments.source_prompt:
            metadata["sourcePrompt"] = arguments.source_prompt
        if arguments.preparation_report is not None:
            metadata["preparation"] = load_preparation_report(
                arguments.preparation_report,
                pack_id=arguments.pack,
                slot=arguments.slot,
                prepared_source=arguments.source,
            )
        arguments.sidecar.parent.mkdir(parents=True, exist_ok=True)
        arguments.sidecar.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if arguments.command == "prepare" and arguments.report is not None:
        if preparation_report is None:
            raise AssetNormalizationError("原生帧准备报告缺失")
        preparation_report["outputSha256"] = _sha256_bytes(
            arguments.output.read_bytes()
        )
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(preparation_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(inspect_png(arguments.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
