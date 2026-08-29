from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


class DatabaseValidationError(RuntimeError):
    """Raised when PostgreSQL is reachable but not ready for the runtime."""


def expected_alembic_head(alembic_ini_path: str = "alembic.ini") -> str:
    cfg = Config(alembic_ini_path)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if len(heads) != 1:
        raise DatabaseValidationError(f"expected exactly one Alembic head, found {heads!r}")
    return heads[0]


def validate_database_ready(engine: Engine, *, expected_revision: str | None = None) -> str:
    expected = expected_revision or expected_alembic_head()
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
            actual = connection.execute(
                text("select version_num from public.alembic_version")
            ).scalar_one_or_none()
    except Exception as exc:
        raise DatabaseValidationError(f"PostgreSQL/Alembic validation failed: {exc}") from exc

    if actual != expected:
        raise DatabaseValidationError(
            f"database is not at Alembic head: expected {expected}, found {actual}"
        )
    return str(actual)
