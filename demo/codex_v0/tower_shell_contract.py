"""The offline half of the cutaway-office-tower geometry parity contract.

``checks/tower-shell-contract.mjs`` builds the identical structure from the
browser renderer's own geometry.  Both halves are compared against the committed
fixture at ``checks/fixtures/tower-shell-geometry.json``, so neither test suite
has to reach into the other language's runtime; a one-sided edit to the geometry
shows up as a failure in the *other* suite.

Regenerate the fixture after an intentional geometry change::

    .venv/bin/python -m codex_v0.tower_shell_contract regenerate

That command builds both halves, refuses to write when they disagree, and prints
the first differing path so the divergence is named rather than merged away.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .asset_qa import CoreV2AssetQa, tower_shell_geometry


PROJECT_DIR = Path(__file__).resolve().parent.parent
FIXTURE_PATH = PROJECT_DIR / "checks" / "fixtures" / "tower-shell-geometry.json"
JS_CONTRACT_PATH = PROJECT_DIR / "checks" / "tower-shell-contract.mjs"
LAYOUTS_PATH = PROJECT_DIR / "assets" / "world-layouts.json"
PACK_SPEC_PATH = PROJECT_DIR / "assets" / "core-v2-pack.spec.json"
CONTRACT_SCHEMA_VERSION = 1
CONTRACT_PACK_ID = "core-v2"


class TowerShellContractError(ValueError):
    """Raised when the two renderers cannot be pinned to one shared geometry."""


def _exact_int(value: Any, label: str) -> int:
    """Local scene space is an integer pixel lattice.

    This is load-bearing, not cosmetic: the browser snaps polygon vertices
    *before* the camera transform while the Pillow renderer rounds *after* it.
    While every local coordinate is an integer the two can only disagree by the
    fractional part the camera zoom introduces — at most half a device pixel,
    and exactly zero at integer zoom.  A fractional local coordinate would break
    that bound, so the contract refuses to record one.
    """

    number = float(value)
    if not number.is_integer():
        raise TowerShellContractError(
            f"{label} must be an integer in local scene space, got {value!r}"
        )
    return int(number)


def _point(value: Mapping[str, Any], label: str) -> dict[str, int]:
    return {
        "x": _exact_int(value["x"], f"{label}.x"),
        "y": _exact_int(value["y"], f"{label}.y"),
    }


def _points(values: Sequence[Mapping[str, Any]], label: str) -> list[dict[str, int]]:
    return [_point(value, f"{label}[{index}]") for index, value in enumerate(values)]


def _sample_band_indices(count: int) -> list[int]:
    """First, middle and last band: the polygon is affine in depth, so three pin it."""

    if count <= 0:
        return []
    return sorted({0, count // 2, count - 1})


def _face_contract(face: Mapping[str, Any], shell: Mapping[str, Any]) -> dict[str, Any]:
    face_id = face["id"]
    height = int(shell["windowBandPitch"]) - 3
    bands = [
        _exact_int(depth, f"{face_id}.windowBands[{index}]")
        for index, depth in enumerate(face["windowBands"])
    ]
    return {
        "topEdge": _points(face["topEdge"], f"{face_id}.topEdge"),
        "facade": _points(face["facade"], f"{face_id}.facade"),
        "slab": _points(face["slab"], f"{face_id}.slab"),
        "ambientOcclusion": _points(
            face["ambientOcclusion"], f"{face_id}.ambientOcclusion"
        ),
        "mullions": [
            {
                "top": _point(mullion["top"], f"{face_id}.mullions[{index}].top"),
                "bottom": _point(mullion["bottom"], f"{face_id}.mullions[{index}].bottom"),
            }
            for index, mullion in enumerate(face["mullions"])
        ],
        "windowBands": bands,
        "bounds": {
            key: _exact_int(face["bounds"][key], f"{face_id}.bounds.{key}")
            for key in ("left", "top", "right", "bottom")
        },
        "sampleBandPolygons": [
            {
                "depth": bands[index],
                "height": height,
                "points": [
                    {"x": x, "y": y}
                    for x, y in CoreV2AssetQa._window_band_points(
                        face, bands[index], height
                    )
                ],
            }
            for index in _sample_band_indices(len(bands))
        ],
    }


def contract_layouts(layouts_document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every core-v2 world, id-ordered, so the fixture tracks real product maps."""

    return sorted(
        (
            {
                "id": layout["id"],
                "columns": layout["columns"],
                "rows": layout["rows"],
                "origin": {"x": layout["origin"]["x"], "y": layout["origin"]["y"]},
            }
            for layout in layouts_document["layouts"]
            if layout.get("requiredPackId") == CONTRACT_PACK_ID
        ),
        key=lambda layout: layout["id"],
    )


def build_tower_shell_contract(
    layouts_document: Mapping[str, Any], pack_spec: Mapping[str, Any]
) -> dict[str, Any]:
    shell = pack_spec.get("sceneShell")
    if not shell:
        raise TowerShellContractError(f"{CONTRACT_PACK_ID} pack spec has no sceneShell")
    layouts = []
    for layout in contract_layouts(layouts_document):
        geometry = tower_shell_geometry(layout, shell)
        edges = geometry["edges"]
        layouts.append(
            {
                "id": layout["id"],
                "layout": {
                    "columns": layout["columns"],
                    "rows": layout["rows"],
                    "origin": layout["origin"],
                },
                "corners": {
                    corner: _point(edges[corner], f"{layout['id']}.{corner}")
                    for corner in ("rightCorner", "frontCorner", "leftCorner")
                },
                "faces": {
                    face_id: _face_contract(geometry[face_id], shell)
                    for face_id in ("xMax", "yMax")
                },
            }
        )
    return {
        "schemaVersion": CONTRACT_SCHEMA_VERSION,
        "packId": CONTRACT_PACK_ID,
        "shell": {
            key: int(shell[key])
            for key in ("facadeDepth", "slabDepth", "windowBandPitch")
        },
        "layouts": layouts,
    }


def build_from_repository() -> dict[str, Any]:
    return build_tower_shell_contract(
        json.loads(LAYOUTS_PATH.read_text(encoding="utf-8")),
        json.loads(PACK_SPEC_PATH.read_text(encoding="utf-8")),
    )


def read_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def first_difference(left: Any, right: Any, path: str = "") -> str | None:
    """Name the first diverging path so a mismatch reads as a location, not a dump."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return f"{path or '<root>'}: keys {sorted(left)} != {sorted(right)}"
        for key in left:
            found = first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path or '<root>'}: length {len(left)} != {len(right)}"
        for index, (one, other) in enumerate(zip(left, right)):
            found = first_difference(one, other, f"{path}[{index}]")
            if found:
                return found
        return None
    if left != right:
        return f"{path or '<root>'}: {left!r} != {right!r}"
    return None


def build_browser_contract() -> dict[str, Any]:
    """Run the browser half through node. Only the generator needs both runtimes."""

    try:
        completed = subprocess.run(
            ["node", str(JS_CONTRACT_PATH)],
            capture_output=True,
            text=True,
            check=True,
            cwd=PROJECT_DIR,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the host
        raise TowerShellContractError(
            "regenerating the fixture needs node on PATH"
        ) from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - build failure
        raise TowerShellContractError(
            f"browser contract build failed:\n{exc.stderr.strip()}"
        ) from exc
    return json.loads(completed.stdout)


def regenerate() -> dict[str, Any]:
    offline = build_from_repository()
    browser = build_browser_contract()
    difference = first_difference(browser, offline)
    if difference:
        raise TowerShellContractError(
            "browser and offline geometry disagree, refusing to write the fixture.\n"
            f"first difference at {difference}"
        )
    payload = json.dumps(offline, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    changed = (
        not FIXTURE_PATH.exists()
        or FIXTURE_PATH.read_text(encoding="utf-8") != payload
    )
    if changed:
        FIXTURE_PATH.write_text(payload, encoding="utf-8")
    return {
        "fixture": str(FIXTURE_PATH.relative_to(PROJECT_DIR)),
        "changed": changed,
        "layouts": [layout["id"] for layout in offline["layouts"]],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild the tower shell geometry fixture from both renderers, "
            "refusing to write when they disagree."
        )
    )
    parser.add_argument("command", choices=("regenerate", "check"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            difference = first_difference(build_browser_contract(), build_from_repository())
            if difference:
                print(f"tower shell parity failed at {difference}")
                return 2
            print("tower shell parity holds")
            return 0
        result = regenerate()
    except TowerShellContractError as exc:
        print(f"tower shell contract failed: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONTRACT_PACK_ID",
    "CONTRACT_SCHEMA_VERSION",
    "FIXTURE_PATH",
    "TowerShellContractError",
    "build_from_repository",
    "build_tower_shell_contract",
    "contract_layouts",
    "first_difference",
    "read_fixture",
    "regenerate",
]
