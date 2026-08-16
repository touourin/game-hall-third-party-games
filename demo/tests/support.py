from __future__ import annotations

import hashlib
import io
import json

from PIL import Image

from codex_v0.asset_lab import (
    CORE_V1_PACK_ID,
    CORE_V2_PACK_ID,
    CORE_V2_STYLE_PROFILE_ID,
    STYLE_PROFILE_ID,
)


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(output, format="PNG")
    return output.getvalue()


def install_test_core_v1_release(app) -> dict:
    """Install a hash-valid synthetic release without reviewing real art."""

    lab = app.state.asset_lab
    manifest = lab._load_runtime_spec(
        CORE_V1_PACK_ID,
        base_release_id="release-test-core-v0",
    )
    manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_sha = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    atlas = _png_bytes()
    atlas_sha = hashlib.sha256(atlas).hexdigest()
    lab._write_content_addressed(lab.derived_dir, atlas_sha, ".png", atlas)
    catalog = {
        "schemaVersion": 1,
        "revision": 1,
        "packId": CORE_V1_PACK_ID,
        "baseReleaseId": "release-test-core-v0",
        "synthetic": True,
        "assets": [],
    }
    catalog_json = json.dumps(catalog, sort_keys=True, separators=(",", ":"))
    with lab._transaction() as connection:
        connection.execute("UPDATE packs SET status = 'draft'")
        connection.execute(
            """
            INSERT OR IGNORE INTO packs(
                id, style_profile_id, name, status, revision,
                atlas_sha256, manifest_json, catalog_json, active_release_id,
                base_release_id, created_at, updated_at, activated_at
            ) VALUES (?, ?, 'Core v1 test', 'draft', 0, NULL, NULL, NULL, NULL,
                      'release-test-core-v0', '2026-08-10T00:00:00Z',
                      '2026-08-10T00:00:00Z', NULL)
            """,
            (CORE_V1_PACK_ID, STYLE_PROFILE_ID),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO pack_releases(
                id, pack_id, catalog_revision, manifest_sha256,
                atlas_sha256, manifest_json, catalog_json, created_at
            ) VALUES ('release-test-core-v1', ?, 1, ?, ?, ?, ?, '2026-08-10T00:00:00Z')
            """,
            (CORE_V1_PACK_ID, manifest_sha, atlas_sha, manifest_json, catalog_json),
        )
        connection.execute(
            """
            UPDATE packs
               SET status = 'active', active_release_id = 'release-test-core-v1',
                   atlas_sha256 = ?, manifest_json = ?, catalog_json = ?,
                   activated_at = '2026-08-10T00:00:00Z'
             WHERE id = ?
            """,
            (atlas_sha, manifest_json, catalog_json, CORE_V1_PACK_ID),
        )
    return manifest


def install_test_core_v2_release(app) -> dict:
    """Install a hash-valid core-v2 release bound to the synthetic v1 base."""

    install_test_core_v1_release(app)
    lab = app.state.asset_lab
    manifest = lab._load_runtime_spec(
        CORE_V2_PACK_ID,
        base_release_id="release-test-core-v1",
    )
    manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_sha = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    atlas = _png_bytes()
    atlas_sha = hashlib.sha256(atlas).hexdigest()
    lab._write_content_addressed(lab.derived_dir, atlas_sha, ".png", atlas)
    catalog = {
        "schemaVersion": 1,
        "revision": 2,
        "packId": CORE_V2_PACK_ID,
        "baseReleaseId": "release-test-core-v1",
        "assets": [],
    }
    catalog_json = json.dumps(catalog, sort_keys=True, separators=(",", ":"))
    with lab._transaction() as connection:
        connection.execute("UPDATE packs SET status = 'draft'")
        connection.execute(
            """
            INSERT OR IGNORE INTO packs(
                id, style_profile_id, name, status, revision,
                atlas_sha256, manifest_json, catalog_json, active_release_id,
                base_release_id, created_at, updated_at, activated_at
            ) VALUES (?, ?, 'Core v2 test', 'draft', 0, NULL, NULL, NULL, NULL,
                      'release-test-core-v1', '2026-08-11T00:00:00Z',
                      '2026-08-11T00:00:00Z', NULL)
            """,
            (CORE_V2_PACK_ID, CORE_V2_STYLE_PROFILE_ID),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO pack_releases(
                id, pack_id, catalog_revision, manifest_sha256,
                atlas_sha256, manifest_json, catalog_json, created_at
            ) VALUES ('release-test-core-v2', ?, 2, ?, ?, ?, ?, '2026-08-11T00:00:00Z')
            """,
            (CORE_V2_PACK_ID, manifest_sha, atlas_sha, manifest_json, catalog_json),
        )
        connection.execute(
            """
            UPDATE packs
               SET status = 'active', active_release_id = 'release-test-core-v2',
                   atlas_sha256 = ?, manifest_json = ?, catalog_json = ?,
                   activated_at = '2026-08-11T00:00:00Z'
             WHERE id = ?
            """,
            (atlas_sha, manifest_json, catalog_json, CORE_V2_PACK_ID),
        )
    return manifest
