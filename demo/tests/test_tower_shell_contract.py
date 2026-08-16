from __future__ import annotations

import json

import pytest

from codex_v0.asset_qa import CANVAS_SIZE
from codex_v0.tower_shell_contract import (
    CONTRACT_PACK_ID,
    FIXTURE_PATH,
    LAYOUTS_PATH,
    TowerShellContractError,
    _exact_int,
    build_from_repository,
    build_tower_shell_contract,
    contract_layouts,
    first_difference,
    read_fixture,
)


def test_offline_geometry_matches_the_committed_parity_fixture() -> None:
    """The browser half asserts against this same file from `checks/`.

    Neither suite runs the other's runtime, so a one-sided geometry edit fails
    in the opposite language rather than passing quietly.
    """

    difference = first_difference(build_from_repository(), read_fixture())
    assert difference is None, difference


def test_fixture_covers_every_core_v2_world() -> None:
    layouts = json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
    expected = [layout["id"] for layout in contract_layouts(layouts)]
    fixture = read_fixture()

    assert fixture["packId"] == CONTRACT_PACK_ID
    assert [layout["id"] for layout in fixture["layouts"]] == expected
    # A new core-v2 map must be regenerated into the fixture, not silently skipped.
    assert {(layout["layout"]["columns"], layout["layout"]["rows"]) for layout in fixture["layouts"]} == {
        (14, 9),
        (20, 12),
    }


def test_contract_refuses_fractional_local_coordinates() -> None:
    assert _exact_int(504.0, "sample") == 504
    with pytest.raises(TowerShellContractError, match="integer in local scene space"):
        _exact_int(504.5, "sample")


def test_contract_rejects_a_pack_without_a_scene_shell() -> None:
    layouts = json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
    with pytest.raises(TowerShellContractError, match="no sceneShell"):
        build_tower_shell_contract(layouts, {"id": CONTRACT_PACK_ID})


def _fixture_local_points() -> list[dict[str, int]]:
    points: list[dict[str, int]] = []
    for layout in read_fixture()["layouts"]:
        points.extend(layout["corners"].values())
        for face in layout["faces"].values():
            for key in ("topEdge", "facade", "slab", "ambientOcclusion"):
                points.extend(face[key])
            for mullion in face["mullions"]:
                points.extend((mullion["top"], mullion["bottom"]))
            for band in face["sampleBandPolygons"]:
                points.extend(band["points"])
    return points


@pytest.mark.parametrize("zoom", (1.0, 1.25, 1.5, 2.0))
def test_snapping_asymmetry_stays_within_half_a_device_pixel(zoom: float) -> None:
    """Pin the known — and pre-existing — divergence between the two renderers.

    The browser snaps polygon vertices *before* the camera transform, so on the
    integer lattice the contract enforces its `snap()` is a no-op and the device
    coordinate stays exact.  The Pillow renderer rounds *after* the transform.
    The gap is therefore the fractional part the zoom introduces: never more
    than half a device pixel, and exactly zero at integer zoom.  This affects
    the whole shell — facade, slab and ambient occlusion as much as the window
    bands — and predates the band-orientation fix.

    A fractional local coordinate would break the bound, which is what makes
    `_exact_int` load-bearing rather than decorative.
    """

    width, height = CANVAS_SIZE
    camera = {"x": -37, "y": 24}
    worst = 0.0
    for point in _fixture_local_points():
        for axis, size in (("x", width), ("y", height)):
            exact = size / 2 + camera[axis] + (point[axis] - size / 2) * zoom
            worst = max(worst, abs(exact - round(exact)))

    assert worst <= 0.5
    if float(zoom).is_integer():
        assert worst == 0.0
    else:
        # Not a vacuous bound: these zooms really do land off the pixel grid.
        assert worst > 0.0


def test_first_difference_names_the_diverging_path() -> None:
    assert first_difference({"a": [1, 2]}, {"a": [1, 2]}) is None
    assert first_difference({"a": [1, 2]}, {"a": [1, 3]}) == ".a[1]: 2 != 3"
    assert first_difference({"a": 1}, {"b": 1}) == "<root>: keys ['a'] != ['b']"
    assert first_difference({"a": [1]}, {"a": [1, 2]}) == ".a: length 1 != 2"


def test_fixture_is_committed_and_canonically_formatted() -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    expected = json.dumps(
        json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    assert raw == expected, "run: .venv/bin/python -m codex_v0.tower_shell_contract regenerate"
