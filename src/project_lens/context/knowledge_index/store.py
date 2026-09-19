"""SQLite persistence for knowledge documents, chunks, embeddings, and jobs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import UUID

from project_lens.context.knowledge_index.models import (
    EmbeddingRecord,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexJob,
    KnowledgeObjectKind,
)
from project_lens.persistence.sqlite import SQLiteDatabase


class SQLiteKnowledgeIndexStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.run_migrations(
            "knowledge_index",
            {1: _migration_v1},
        )

    def upsert_documents(self, documents: Iterable[KnowledgeDocument]) -> int:
        count = 0
        with self._database.transaction() as connection:
            for document in documents:
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, tenant_id, project_id, kind, object_id, revision,
                        content_hash, indexable, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content_hash=excluded.content_hash,
                        indexable=excluded.indexable,
                        payload=excluded.payload
                    """,
                    (
                        document.id,
                        document.tenant_id,
                        document.project_id,
                        document.kind.value,
                        document.object_id,
                        document.revision,
                        document.content_hash,
                        int(document.indexable),
                        document.model_dump_json(),
                    ),
                )
                count += 1
        return count

    def get_document(self, document_id: str) -> KnowledgeDocument | None:
        row = self._database.query_one(
            "SELECT payload FROM knowledge_documents WHERE id = ?", (document_id,)
        )
        return KnowledgeDocument.model_validate_json(row["payload"]) if row else None

    def list_documents(
        self,
        *,
        tenant_id: str,
        project_id: str,
        kind: KnowledgeObjectKind | str | None = None,
        indexable_only: bool = False,
        limit: int = 200,
    ) -> tuple[KnowledgeDocument, ...]:
        clauses = ["tenant_id = ?", "project_id = ?"]
        params: list[object] = [tenant_id, project_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(str(kind))
        if indexable_only:
            clauses.append("indexable = 1")
        params.append(max(1, min(int(limit), 1_000)))
        rows = self._database.query_all(
            f"SELECT payload FROM knowledge_documents WHERE {' AND '.join(clauses)} "
            "ORDER BY rowid DESC LIMIT ?",
            tuple(params),
        )
        return tuple(KnowledgeDocument.model_validate_json(row["payload"]) for row in rows)

    def upsert_chunks(self, chunks: Iterable[KnowledgeChunk]) -> int:
        count = 0
        with self._database.transaction() as connection:
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        chunk_id, document_id, tenant_id, project_id, object_kind,
                        object_id, revision, content_hash, chunker_version,
                        indexable, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        content_hash=excluded.content_hash,
                        chunker_version=excluded.chunker_version,
                        indexable=excluded.indexable,
                        payload=excluded.payload
                    """,
                    (
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.tenant_id,
                        chunk.project_id,
                        chunk.object_kind.value,
                        chunk.object_id,
                        chunk.revision,
                        chunk.content_hash,
                        chunk.chunker_version,
                        int(chunk.indexable),
                        chunk.model_dump_json(),
                    ),
                )
                count += 1
        return count

    def list_chunks(
        self,
        *,
        tenant_id: str,
        project_id: str,
        indexable_only: bool = False,
        limit: int = 1_000,
    ) -> tuple[KnowledgeChunk, ...]:
        condition = "AND indexable = 1" if indexable_only else ""
        rows = self._database.query_all(
            f"SELECT payload FROM knowledge_chunks WHERE tenant_id = ? AND project_id = ? "
            f"{condition} ORDER BY rowid DESC LIMIT ?",
            (tenant_id, project_id, max(1, min(int(limit), 10_000))),
        )
        return tuple(KnowledgeChunk.model_validate_json(row["payload"]) for row in rows)

    def put_embedding(self, record: EmbeddingRecord) -> None:
        self._database.execute(
            """
            INSERT INTO knowledge_embeddings (
                chunk_id, tenant_id, project_id, model_version,
                dimensions, content_hash, chunker_version, status, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                tenant_id, project_id, chunk_id, model_version,
                content_hash, chunker_version
            ) DO UPDATE SET
                dimensions=excluded.dimensions,
                status=excluded.status,
                payload=excluded.payload
            """,
            (
                record.chunk_id,
                record.tenant_id,
                record.project_id,
                record.model_version,
                record.dimensions,
                record.content_hash,
                record.chunker_version,
                record.status,
                record.model_dump_json(),
            ),
        )

    def get_embedding(
        self,
        *,
        tenant_id: str,
        project_id: str,
        chunk_id: str,
        model_version: str,
        content_hash: str,
        chunker_version: str,
    ) -> EmbeddingRecord | None:
        row = self._database.query_one(
            """
            SELECT payload FROM knowledge_embeddings
            WHERE tenant_id = ? AND project_id = ? AND chunk_id = ?
              AND model_version = ? AND content_hash = ? AND chunker_version = ?
            """,
            (tenant_id, project_id, chunk_id, model_version, content_hash, chunker_version),
        )
        return EmbeddingRecord.model_validate_json(row["payload"]) if row else None

    def enqueue_job(self, job: KnowledgeIndexJob) -> KnowledgeIndexJob:
        self._database.execute(
            """
            INSERT INTO knowledge_index_jobs (
                id, tenant_id, project_id, object_kind, object_id,
                content_hash, model_version, chunker_version, status,
                attempt_count, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                tenant_id, project_id, object_kind, object_id,
                content_hash, model_version, chunker_version
            ) DO UPDATE SET
                status=CASE WHEN knowledge_index_jobs.status = 'completed'
                    THEN knowledge_index_jobs.status ELSE excluded.status END,
                payload=CASE WHEN knowledge_index_jobs.status = 'completed'
                    THEN knowledge_index_jobs.payload ELSE excluded.payload END
            """,
            (
                str(job.id),
                job.tenant_id,
                job.project_id,
                job.object_kind.value,
                job.object_id,
                job.content_hash,
                job.model_version,
                job.chunker_version,
                job.status,
                job.attempt_count,
                job.model_dump_json(),
            ),
        )
        row = self._database.query_one(
            """
            SELECT payload FROM knowledge_index_jobs
            WHERE tenant_id = ? AND project_id = ? AND object_kind = ?
              AND object_id = ? AND content_hash = ? AND model_version = ?
              AND chunker_version = ?
            """,
            (
                job.tenant_id,
                job.project_id,
                job.object_kind.value,
                job.object_id,
                job.content_hash,
                job.model_version,
                job.chunker_version,
            ),
        )
        return KnowledgeIndexJob.model_validate_json(row["payload"]) if row else job

    def list_jobs(self, *, tenant_id: str, project_id: str, status: str | None = None) -> tuple[KnowledgeIndexJob, ...]:
        clauses = ["tenant_id = ?", "project_id = ?"]
        params: list[object] = [tenant_id, project_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        rows = self._database.query_all(
            f"SELECT payload FROM knowledge_index_jobs WHERE {' AND '.join(clauses)} ORDER BY rowid",
            tuple(params),
        )
        return tuple(KnowledgeIndexJob.model_validate_json(row["payload"]) for row in rows)


def _migration_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            object_id TEXT NOT NULL,
            revision TEXT,
            content_hash TEXT NOT NULL,
            indexable INTEGER NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE (tenant_id, project_id, kind, object_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_documents_scope
            ON knowledge_documents (tenant_id, project_id, indexable);
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            object_kind TEXT NOT NULL,
            object_id TEXT NOT NULL,
            revision TEXT,
            content_hash TEXT NOT NULL,
            chunker_version TEXT NOT NULL,
            indexable INTEGER NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_scope
            ON knowledge_chunks (tenant_id, project_id, indexable);
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
            ON knowledge_chunks (document_id);
        CREATE TABLE IF NOT EXISTS knowledge_embeddings (
            chunk_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            chunker_version TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, project_id, chunk_id,
                model_version, content_hash, chunker_version
            )
        );
        CREATE TABLE IF NOT EXISTS knowledge_index_jobs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            object_kind TEXT NOT NULL,
            object_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            model_version TEXT NOT NULL,
            chunker_version TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE (
                tenant_id, project_id, object_kind, object_id,
                content_hash, model_version, chunker_version
            )
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_status
            ON knowledge_index_jobs (tenant_id, project_id, status);
        """
    )


__all__ = ["SQLiteKnowledgeIndexStore"]
