from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from codex_v0.config import Settings
from codex_v0.main import create_app
from codex_v0.asset_lab import (
    CORE_PACK_SPEC_PATH,
    CORE_V1_PACK_ID,
    CORE_V1_PACK_SPEC_PATH,
    CORE_V2_PACK_ID,
    CORE_V2_REQUIRED_SLOT_NAMES,
    MAX_REVIEW_BATCH_ITEMS,
    STYLE_PROFILE_ID,
)
from codex_v0.asset_normalize import slot_metadata
from tests.support import install_test_core_v2_release


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def transparent_desk_png() -> bytes:
    image = Image.new("RGBA", (96, 80), (0, 0, 0, 0))
    for x in range(4, 92):
        for y in range(10, 76):
            image.putpixel((x, y), (132, 111, 90, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def desk_metadata() -> dict:
    return {
        "packId": "core-v0",
        "slot": "furniture.desk-island",
        "displayName": "Route test desk",
        "metadata": {
            "frameWidth": 96,
            "frameHeight": 80,
            "columns": 1,
            "frameCount": 1,
            "anchor": {"x": 48, "y": 64},
            "footprint": [
                {"x": x, "y": y, "blocked": True}
                for y in range(2)
                for x in range(3)
            ],
            "jobId": "route-test",
        },
    }


def test_asset_page_loopback_csrf_import_review_and_activation_gate(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "game.sqlite3",
            web_dir=WEB_DIR,
            admin_token="test-admin",
        )
    )
    with TestClient(app, client=("127.0.0.1", 50100)) as client:
        assert client.get("/assets").status_code == 200
        bootstrap = client.get("/api/assets/bootstrap")
        assert bootstrap.status_code == 200
        bootstrap_data = bootstrap.json()
        csrf = bootstrap_data["csrfToken"]
        assert len(bootstrap_data["styleProfile"]["worldPalette"]) == 32
        assert len(bootstrap_data["styleProfile"]["playerAccents"]) == 8
        assert bootstrap_data["pack"]["activation"]["enabled"] is False

        payload = {
            "files": {"png": ("desk.png", transparent_desk_png(), "image/png")},
            "data": {"metadata": json.dumps(desk_metadata())},
        }
        assert client.post("/api/assets/import", **payload).status_code == 403
        wrong_origin = client.post(
            "/api/assets/import",
            headers={"X-CSRF-Token": csrf, "Origin": "https://example.invalid"},
            **payload,
        )
        assert wrong_origin.status_code == 403

        imported = client.post(
            "/api/assets/import",
            headers={"X-CSRF-Token": csrf},
            **payload,
        )
        assert imported.status_code == 200, imported.text
        imported_data = imported.json()
        assert imported_data["deduplicated"] is False
        assert imported_data["version"]["status"] == "draft"

        duplicate = client.post(
            "/api/assets/import",
            headers={"X-CSRF-Token": csrf},
            **payload,
        ).json()
        assert duplicate["deduplicated"] is True
        assert duplicate["version"]["id"] == imported_data["version"]["id"]

        catalog = client.get("/api/assets/catalog").json()
        desk = next(
            asset
            for asset in catalog["assets"]
            if asset["slot"] == "furniture.desk-island"
        )
        blob = client.get(desk["versions"][0]["blobUrl"])
        assert blob.status_code == 200
        assert blob.headers["x-content-type-options"] == "nosniff"
        assert "immutable" in blob.headers["cache-control"]

        reviewed = client.post(
            f"/api/assets/{desk['id']}/versions/{desk['versions'][0]['id']}/review",
            headers={"X-CSRF-Token": csrf},
            json={
                "decision": "accepted",
                "note": "共享桌岛锚点与占地通过",
                "expectedRevision": imported_data["revision"],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["version"]["status"] == "accepted"

        conflict = client.post(
            "/api/assets/packs/core-v0/activate",
            headers={"X-CSRF-Token": csrf},
            json={"expectedRevision": imported_data["revision"]},
        )
        assert conflict.status_code == 409
        current_revision = reviewed.json()["revision"]
        incomplete = client.post(
            "/api/assets/packs/core-v0/activate",
            headers={"X-CSRF-Token": csrf},
            json={"expectedRevision": current_revision},
        )
        assert incomplete.status_code == 422
        assert incomplete.json()["error"] == "pack.incomplete"
        assert client.get("/api/assets/active/manifest").status_code == 404
        assert client.get("/api/assets/blobs/not-a-sha").status_code == 404


def test_core_v2_asset_api_contract_is_pack_driven_and_manifest_is_immutable(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR)
    )
    with TestClient(app, client=("127.0.0.1", 50109)) as client:
        manifest = install_test_core_v2_release(app)
        bootstrap = client.get("/api/assets/bootstrap").json()
        core_v2 = next(
            pack for pack in bootstrap["packs"] if pack["id"] == CORE_V2_PACK_ID
        )
        assert core_v2["spec"]["geometryVersion"] == 2
        assert core_v2["spec"]["nativeFrameRequired"] is True
        assert core_v2["spec"]["requiredSlots"] == list(CORE_V2_REQUIRED_SLOT_NAMES)
        assert len(core_v2["spec"]["worldPalette"]) == 48
        assert [scene["layoutId"] for scene in core_v2["previewScenes"]] == [
            "world.opening-empty-v2",
            "world.mid-growth-v3",
        ]
        assert bootstrap["filters"]["slotsByPack"][CORE_V2_PACK_ID] == list(
            CORE_V2_REQUIRED_SLOT_NAMES
        )
        assert set(bootstrap["filters"]) == {"slotsByPack", "kinds", "statuses"}
        # An unknown pack must 404 rather than silently serve core-v0 under its name.
        unknown = client.get("/api/assets/catalog?packId=unknown-pack")
        assert unknown.status_code == 404
        assert unknown.json()["error"] == "pack.not_found"
        assert manifest["requiredSlots"] == list(CORE_V2_REQUIRED_SLOT_NAMES)
        assert len(manifest["palette"]["world"]) == 48
        active = client.get("/api/assets/active/manifest")
        assert active.status_code == 200
        assert active.json()["id"] == CORE_V2_PACK_ID
        release = core_v2["activeRelease"]
        immutable = client.get(release["manifestUrl"])
        assert immutable.status_code == 200
        assert immutable.headers["etag"] == f'"{release["manifestSha256"]}"'


def cabinet_metadata() -> dict:
    return {
        "packId": "core-v0",
        "slot": "furniture.storage-cabinet",
        "displayName": "Route test cabinet",
        "metadata": {"anchor": {"x": 0, "y": 0}, "jobId": "route-test"},
    }


def _import_two_drafts(client: TestClient, csrf: str) -> tuple[list[dict], int]:
    """Import a desk and a cabinet, returning batch items plus the current revision."""

    imports = []
    for metadata, png in (
        (desk_metadata(), transparent_desk_png()),
        (cabinet_metadata(), storage_cabinet_png()),
    ):
        response = client.post(
            "/api/assets/import",
            headers={"X-CSRF-Token": csrf},
            files={"png": ("asset.png", png, "image/png")},
            data={"metadata": json.dumps(metadata)},
        )
        assert response.status_code == 200, response.text
        imports.append(response.json())
    items = [
        {
            "assetId": entry["asset"]["id"],
            "versionId": entry["version"]["id"],
            "decision": "accepted",
        }
        for entry in imports
    ]
    return items, imports[-1]["revision"]


def storage_cabinet_png() -> bytes:
    runtime_spec = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
    frame = next(
        asset for asset in runtime_spec["assets"] if asset["slot"] == "furniture.storage-cabinet"
    )["frame"]
    image = Image.new("RGBA", (frame["width"], frame["height"]), (94, 120, 132, 255))
    image.putpixel((frame["width"] - 1, frame["height"] - 1), (0, 0, 0, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_batch_review_route_requires_loopback_csrf_and_applies_atomically(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR))
    with TestClient(app, client=("127.0.0.1", 50105)) as client:
        csrf = client.get("/api/assets/bootstrap").json()["csrfToken"]
        items, revision = _import_two_drafts(client, csrf)
        body = {"items": items, "note": "", "expectedRevision": revision}

        assert client.post("/api/assets/reviews/batch", json=body).status_code == 403
        wrong_origin = client.post(
            "/api/assets/reviews/batch",
            headers={"X-CSRF-Token": csrf, "Origin": "https://example.invalid"},
            json=body,
        )
        assert wrong_origin.status_code == 403

        stale = client.post(
            "/api/assets/reviews/batch",
            headers={"X-CSRF-Token": csrf},
            json={**body, "expectedRevision": revision - 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"] == "revision.conflict"

        accepted = client.post(
            "/api/assets/reviews/batch",
            headers={"X-CSRF-Token": csrf},
            json=body,
        )
        assert accepted.status_code == 200, accepted.text
        data = accepted.json()
        assert data["ok"] is True
        assert data["revision"] == revision + 1
        assert len(data["results"]) == 2
        assert len(data["assets"]) == 2

        catalog = client.get("/api/assets/catalog").json()
        statuses = {
            version["id"]: version["status"]
            for asset in catalog["assets"]
            for version in asset["versions"]
        }
        assert [statuses[item["versionId"]] for item in items] == ["accepted", "accepted"]


def test_batch_review_route_reports_every_failure_as_422_and_changes_nothing(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR))
    with TestClient(app, client=("127.0.0.1", 50106)) as client:
        csrf = client.get("/api/assets/bootstrap").json()["csrfToken"]
        items, revision = _import_two_drafts(client, csrf)

        failed = client.post(
            "/api/assets/reviews/batch",
            headers={"X-CSRF-Token": csrf},
            json={
                "items": [
                    items[0],
                    {
                        "assetId": "asset-core-v0-missing",
                        "versionId": "version-missing",
                        "decision": "accepted",
                    },
                ],
                "note": "",
                "expectedRevision": revision,
            },
        )
        # A batch whose only failure is not_found is still 422: the top-level code is
        # review.batch_failed, and a partial 404 is meaningless for a batch.
        assert failed.status_code == 422, failed.text
        body = failed.json()
        assert body["error"] == "review.batch_failed"
        assert body["details"]["itemCount"] == 2
        assert body["details"]["failureCount"] == 1
        assert body["details"]["failures"][0]["code"] == "version.not_found"
        assert body["details"]["failures"][0]["index"] == 1

        catalog = client.get("/api/assets/catalog").json()
        assert catalog["revision"] == revision
        statuses = {
            version["id"]: version["status"]
            for asset in catalog["assets"]
            for version in asset["versions"]
        }
        assert statuses[items[0]["versionId"]] == "draft"


def test_batch_review_route_enforces_pydantic_item_limits(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR))
    with TestClient(app, client=("127.0.0.1", 50107)) as client:
        csrf = client.get("/api/assets/bootstrap").json()["csrfToken"]
        headers = {"X-CSRF-Token": csrf}
        # These are FastAPI RequestValidationErrors carrying the default {"detail": [...]}
        # envelope, not the {ok, error, detail, details} AssetLabError envelope, so only
        # the status code is contractual here.
        empty = client.post(
            "/api/assets/reviews/batch",
            headers=headers,
            json={"items": [], "note": "", "expectedRevision": 0},
        )
        assert empty.status_code == 422
        oversized = client.post(
            "/api/assets/reviews/batch",
            headers=headers,
            json={
                "items": [
                    {"assetId": "a", "versionId": f"v{index}", "decision": "accepted"}
                    for index in range(MAX_REVIEW_BATCH_ITEMS + 1)
                ],
                "note": "",
                "expectedRevision": 0,
            },
        )
        assert oversized.status_code == 422


def test_asset_api_rejects_non_loopback_client(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR)
    )
    with TestClient(app, client=("192.0.2.10", 50101)) as client:
        assert client.get("/api/assets/bootstrap").status_code == 403
        assert client.get("/api/assets/catalog").status_code == 403
        assert client.post(
            "/api/assets/reviews/batch",
            json={"items": [{"assetId": "a", "versionId": "v", "decision": "accepted"}],
                  "note": "", "expectedRevision": 0},
        ).status_code == 403


def test_inbox_scan_filters_sidecars_by_known_pack(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR)
    )
    with TestClient(app, client=("127.0.0.1", 50104)) as client:
        csrf = client.get("/api/assets/bootstrap").json()["csrfToken"]
        inbox = app.state.asset_lab.inbox_dir
        with app.state.asset_lab._transaction() as connection:
            connection.execute(
                """
                INSERT INTO packs(
                    id, style_profile_id, name, status, revision,
                    created_at, updated_at
                ) VALUES (?, ?, 'Core v1 scan test', 'draft', 0,
                          '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')
                """,
                (CORE_V1_PACK_ID, STYLE_PROFILE_ID),
            )
            connection.execute(
                """
                INSERT INTO assets(
                    id, pack_id, slot, kind, display_name, revision,
                    created_at, updated_at
                ) VALUES (
                    'asset-core-v1-decor-floor-plant', ?, 'decor.floor-plant',
                    'decor', 'Floor Plant', 0,
                    '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z'
                )
                """,
                (CORE_V1_PACK_ID,),
            )
            connection.execute(
                """
                INSERT INTO pack_members(
                    pack_id, slot, asset_id, version_id, required, ordinal
                ) VALUES (
                    ?, 'decor.floor-plant',
                    'asset-core-v1-decor-floor-plant', NULL, 1, 0
                )
                """,
                (CORE_V1_PACK_ID,),
            )

        (inbox / "core-v0-desk.png").write_bytes(transparent_desk_png())
        (inbox / "core-v0-desk.json").write_text(
            json.dumps(desk_metadata()), encoding="utf-8"
        )

        plant_metadata = slot_metadata(
            "decor.floor-plant", CORE_V1_PACK_SPEC_PATH
        )
        plant_image = Image.new("RGBA", (48, 64), (52, 77, 63, 255))
        plant_image.putpixel((47, 63), (0, 0, 0, 0))
        plant_output = io.BytesIO()
        plant_image.save(plant_output, format="PNG")
        (inbox / "core-v1-plant.png").write_bytes(plant_output.getvalue())
        (inbox / "core-v1-plant.json").write_text(
            json.dumps(plant_metadata), encoding="utf-8"
        )

        headers = {"X-CSRF-Token": csrf}
        v1_scan = client.post(
            "/api/assets/inbox/scan?packId=core-v1", headers=headers
        )
        assert v1_scan.status_code == 200, v1_scan.text
        assert [entry["sourceName"] for entry in v1_scan.json()["imported"]] == [
            "core-v1-plant.png"
        ], v1_scan.json()
        assert v1_scan.json()["imported"][0]["asset"]["packId"] == "core-v1"

        with app.state.asset_lab._connect() as connection:
            version_counts = {
                row["pack_id"]: row["version_count"]
                for row in connection.execute(
                    """
                    SELECT a.pack_id, COUNT(v.id) AS version_count
                      FROM assets a
                      LEFT JOIN versions v ON v.asset_id = a.id
                     GROUP BY a.pack_id
                    """
                ).fetchall()
            }
        assert version_counts == {"core-v0": 0, "core-v1": 1}

        v0_scan = client.post(
            "/api/assets/inbox/scan?packId=core-v0", headers=headers
        )
        assert v0_scan.status_code == 200, v0_scan.text
        assert [entry["sourceName"] for entry in v0_scan.json()["imported"]] == [
            "core-v0-desk.png"
        ]
        assert v0_scan.json()["imported"][0]["asset"]["packId"] == "core-v0"

        with app.state.asset_lab._connect() as connection:
            version_counts = {
                row["pack_id"]: row["version_count"]
                for row in connection.execute(
                    """
                    SELECT a.pack_id, COUNT(v.id) AS version_count
                      FROM assets a
                      LEFT JOIN versions v ON v.asset_id = a.id
                     GROUP BY a.pack_id
                    """
                ).fetchall()
            }
        assert version_counts == {"core-v0": 1, "core-v1": 1}

        unknown = client.post(
            "/api/assets/inbox/scan?packId=unknown-pack", headers=headers
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"] == "pack.not_found"


def test_immutable_manifest_route_returns_exact_hashed_bytes(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR)
    )
    with TestClient(app, client=("127.0.0.1", 50102)) as client:
        manifest_bytes = b'{"id":"core-v0","schemaVersion":1}'
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        atlas_sha = "a" * 64
        with app.state.asset_lab._transaction() as connection:
            connection.execute(
                """
                INSERT INTO pack_releases(
                    id, pack_id, catalog_revision, manifest_sha256,
                    atlas_sha256, manifest_json, catalog_json, created_at
                ) VALUES ('release-route', 'core-v0', 1, ?, ?, ?, '{}', ?)
                """,
                (manifest_sha, atlas_sha, manifest_bytes.decode(), "2026-08-09T00:00:00Z"),
            )

        response = client.get(f"/api/assets/manifests/{manifest_sha}")
        assert response.status_code == 200
        assert response.content == manifest_bytes
        assert response.headers["x-manifest-sha256"] == manifest_sha
        assert response.headers["etag"] == f'"{manifest_sha}"'
        assert "immutable" in response.headers["cache-control"]
        assert client.get("/api/assets/manifests/not-a-hash").status_code == 404


def test_active_release_is_frozen_into_new_run_bootstrap(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_path=tmp_path / "game.sqlite3", web_dir=WEB_DIR)
    )
    with TestClient(app, client=("127.0.0.1", 50103)) as client:
        manifest = json.loads(CORE_PACK_SPEC_PATH.read_text(encoding="utf-8"))
        manifest_json = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        manifest_sha = hashlib.sha256(manifest_json.encode()).hexdigest()
        atlas = transparent_desk_png()
        atlas_sha = hashlib.sha256(atlas).hexdigest()
        app.state.asset_lab._write_content_addressed(
            app.state.asset_lab.derived_dir, atlas_sha, ".png", atlas
        )
        with app.state.asset_lab._transaction() as connection:
            connection.execute(
                """
                INSERT INTO pack_releases(
                    id, pack_id, catalog_revision, manifest_sha256,
                    atlas_sha256, manifest_json, catalog_json, created_at
                ) VALUES ('release-integration', 'core-v0', 12, ?, ?, ?, ?, ?)
                """,
                (
                    manifest_sha,
                    atlas_sha,
                    manifest_json,
                    '{"assets":[],"revision":12}',
                    "2026-08-09T00:00:00Z",
                ),
            )
            connection.execute(
                """
                UPDATE packs
                   SET status = 'active', active_release_id = 'release-integration',
                       atlas_sha256 = ?, manifest_json = ?,
                       catalog_json = '{"assets":[],"revision":12}',
                       activated_at = '2026-08-09T00:00:00Z'
                 WHERE id = 'core-v0'
                """,
                (atlas_sha, manifest_json),
            )

        created = client.post(
            "/api/review/runs",
            json={"label": "bound", "layoutId": "world.mid-growth-v1"},
        ).json()
        binding = created["run"]["assetPack"]
        assert binding == {
            "releaseId": "release-integration",
            "packId": "core-v0",
            "catalogRevision": 12,
            "manifestSha256": manifest_sha,
            "manifestUrl": f"/api/assets/manifests/{manifest_sha}",
            "atlasSha256": atlas_sha,
            "atlasUrl": f"/api/assets/derived/{atlas_sha}.png",
        }
        player = created["players"][0]
        bootstrap = client.get(
            f"/api/bootstrap?run={created['run']['id']}",
            headers={"Authorization": f"Bearer {player['token']}"},
        ).json()
        assert bootstrap["assetPack"] == binding
        assert bootstrap["world"]["layout"]["id"] == "world.mid-growth-v1"
        assert bootstrap["world"]["layout"] == bootstrap["run"]["worldLayout"]
        assert bootstrap["world"]["blockedCells"] == [
            {"x": x, "y": y}
            for x, y in sorted(
                (cell["x"], cell["y"])
                for cell in bootstrap["world"]["layout"]["blockedCells"]
            )
        ]
