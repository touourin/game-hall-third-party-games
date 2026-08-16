from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    controller_token_hash TEXT NOT NULL,
    day_offset INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    speed REAL NOT NULL DEFAULT 1.0 CHECK (speed >= 0.1 AND speed <= 8.0),
    forced_wheel INTEGER NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    asset_release_id TEXT NULL,
    asset_pack_id TEXT NULL,
    asset_catalog_revision INTEGER NULL,
    asset_manifest_sha256 TEXT NULL,
    asset_atlas_sha256 TEXT NULL,
    world_layout_id TEXT NULL,
    world_layout_json TEXT NULL
);

CREATE TABLE IF NOT EXISTS players (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    color TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    x REAL NOT NULL,
    y REAL NOT NULL,
    spawn_x REAL NOT NULL,
    spawn_y REAL NOT NULL,
    PRIMARY KEY (run_id, id),
    UNIQUE (run_id, token_hash)
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    delta_cents INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, player_id) REFERENCES players(run_id, id) ON DELETE CASCADE,
    UNIQUE (run_id, player_id, source_key)
);

CREATE TABLE IF NOT EXISTS daily_spins (
    run_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    local_day TEXT NOT NULL,
    wheel_index INTEGER NOT NULL,
    reward_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, player_id) REFERENCES players(run_id, id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, player_id, local_day)
);

CREATE TABLE IF NOT EXISTS good_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    local_day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, sender_id) REFERENCES players(run_id, id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, recipient_id) REFERENCES players(run_id, id) ON DELETE CASCADE,
    UNIQUE (run_id, sender_id, local_day),
    CHECK (sender_id <> recipient_id)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    run_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    key TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, player_id) REFERENCES players(run_id, id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, player_id, endpoint, key)
);

CREATE INDEX IF NOT EXISTS ix_good_cards_run_day
ON good_cards(run_id, local_day, created_at);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(SCHEMA)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            migrations = {
                "asset_release_id": "TEXT NULL",
                "asset_pack_id": "TEXT NULL",
                "asset_catalog_revision": "INTEGER NULL",
                "asset_manifest_sha256": "TEXT NULL",
                "asset_atlas_sha256": "TEXT NULL",
                "world_layout_id": "TEXT NULL",
                "world_layout_json": "TEXT NULL",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE runs ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_runs_asset_manifest
                ON runs(asset_manifest_sha256)
                """
            )
            connection.commit()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
