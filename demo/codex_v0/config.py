from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


@dataclass(frozen=True)
class Settings:
    database_path: Path = PROJECT_DIR / "data" / "codex-v0.sqlite3"
    web_dir: Path = PROJECT_DIR / "web"
    admin_token: str = "codex-v0-review"
    timezone_name: str = "Asia/Taipei"
    world_columns: int = 20
    world_rows: int = 12
    tile_width: int = 32
    tile_height: int = 16
    simulation_hz: int = 20
    broadcast_hz: int = 10
    movement_tiles_per_second: float = 2.2
    position_flush_seconds: float = 3.0
    movement_action_interval_seconds: float = 0.05

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=Path(
                os.environ.get(
                    "CODEX_V0_DATABASE_PATH",
                    str(PROJECT_DIR / "data" / "codex-v0.sqlite3"),
                )
            ).expanduser(),
            web_dir=Path(
                os.environ.get("CODEX_V0_WEB_DIR", str(PROJECT_DIR / "web"))
            ).expanduser(),
            admin_token=os.environ.get(
                "CODEX_V0_ADMIN_TOKEN", "codex-v0-review"
            ),
            timezone_name=os.environ.get("CODEX_V0_TIMEZONE", "Asia/Taipei"),
        )
