from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from .db import Database


class IdentityError(ValueError):
    pass


@dataclass(frozen=True)
class PlayerIdentity:
    run_id: str
    player_id: str
    name: str
    color: str


class IdentityBoundary(Protocol):
    """Replace this boundary when a future game-hall SSO contract is available."""

    def authenticate(self, run_id: str, token: str) -> PlayerIdentity: ...


class RunTokenIdentityBoundary:
    """Review-only identity backed by run-scoped opaque player tokens."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def authenticate(self, run_id: str, token: str) -> PlayerIdentity:
        normalized_run = run_id.strip()
        normalized_token = token.strip()
        if not normalized_run or not normalized_token:
            raise IdentityError("缺少玩家身份凭证")
        token_hash = hashlib.sha256(normalized_token.encode("utf-8")).hexdigest()
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, id, display_name, color
                FROM players
                WHERE run_id = ? AND token_hash = ?
                """,
                (normalized_run, token_hash),
            ).fetchone()
        if row is None:
            raise IdentityError("玩家凭证无效或不属于当前验收轮次")
        return PlayerIdentity(
            run_id=str(row["run_id"]),
            player_id=str(row["id"]),
            name=str(row["display_name"]),
            color=str(row["color"]),
        )


class RunControllerBoundary:
    """Authenticate the random controller token issued for one review run."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def authenticate(self, run_id: str, token: str) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise IdentityError("缺少验收控制凭证")
        digest = hashlib.sha256(normalized_token.encode("utf-8")).hexdigest()
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM runs WHERE id = ? AND controller_token_hash = ?
                """,
                (run_id.strip(), digest),
            ).fetchone()
        if row is None:
            raise IdentityError("验收控制凭证无效或不属于当前轮次")
