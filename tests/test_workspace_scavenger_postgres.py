from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.acquisition.workspace import WorkspaceManager, WorkspacePolicy
from astra_indexator.application import JobCoordinator
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )


@pytest.fixture(scope="module")
def database_url() -> str:
    with PostgresContainer("postgres:16") as postgres:
        url = _psycopg_url(postgres.get_connection_url())
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url


def _claim(engine):
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                access_zone_code="0600",
                knowledge_type="TECHNICAL",
                source_uri="seaweed://documents/source.txt",
                source_file_name="source.txt",
            ),
        )
        session.commit()
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(
            session, worker_id="scavenger-worker", lease_seconds=60
        )
        assert claimed is not None
        session.commit()
        return claimed


def test_live_attempt_workspace_is_never_age_deleted(database_url: str, tmp_path: Path) -> None:
    engine = create_engine(database_url)
    claimed = _claim(engine)
    manager = WorkspaceManager(WorkspacePolicy(tmp_path, 1, 0, 1024, 1))
    root = manager.attempt_root(claimed.token.job_id, claimed.token.attempt_id)
    root.mkdir(parents=True)
    (root / "source.validated").write_text("x")
    old = time.time() - 3600
    os.utime(root, (old, old))
    with Session(engine) as session:
        assert manager.scavenge(session, now_epoch=time.time()) == []
    assert root.exists()


def test_expired_attempt_workspace_is_scavenged(database_url: str, tmp_path: Path) -> None:
    engine = create_engine(database_url)
    claimed = _claim(engine)
    manager = WorkspaceManager(WorkspacePolicy(tmp_path, 1, 0, 1024, 1))
    root = manager.attempt_root(claimed.token.job_id, claimed.token.attempt_id)
    root.mkdir(parents=True)
    (root / "source.part").write_text("x")
    old = time.time() - 3600
    os.utime(root, (old, old))
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job SET lease_until=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": claimed.token.job_id},
        )
    with Session(engine) as session:
        deleted = manager.scavenge(session, now_epoch=time.time())
    assert root in deleted
    assert not root.exists()
