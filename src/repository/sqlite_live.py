"""Live run state — local SQLite persistence for development."""

from __future__ import annotations

from src.repository.sqlite import SqliteBacktestRepository


class SqliteLiveRepository(SqliteBacktestRepository):
    """Persistent repository for live/paper runs on a laptop."""

    pass
