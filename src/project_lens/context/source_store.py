"""Immutable source snapshot stores used by synchronization and authority checks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from project_lens.context.source_records import SourceRecord
from project_lens.persistence.sqlite import SQLiteDatabase


class SourceRecordStore(Protocol):
    def put(self, record: SourceRecord) -> bool: ...

    def get(
        self, tenant_id: str, project_id: str, source_id: str, revision: str
    ) -> SourceRecord | None: ...

    def all(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        include_revoked: bool = False,
    ) -> tuple[SourceRecord, ...]: ...

    def revoke(self, tenant_id: str, project_id: str, source_id: str) -> int: ...


class InMemorySourceRecordStore:
    """Append-only-by-revision store; duplicate snapshots are ignored."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, str], SourceRecord] = {}

    def put(self, record: SourceRecord) -> bool:
        if record.key in self._items:
            return False
        self._items[record.key] = record
        return True

    def put_many(self, records: Iterable[SourceRecord]) -> int:
        return sum(1 for record in records if self.put(record))

    def get(
        self, tenant_id: str, project_id: str, source_id: str, revision: str
    ) -> SourceRecord | None:
        return self._items.get((tenant_id, project_id, source_id, revision))

    def all(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        include_revoked: bool = False,
    ) -> tuple[SourceRecord, ...]:
        values = self._items.values()
        if tenant_id is not None:
            values = (item for item in values if item.tenant_id == tenant_id)
        if project_id is not None:
            values = (item for item in values if item.project_id == project_id)
        if not include_revoked:
            values = (item for item in values if not item.revoked)
        return tuple(values)

    def revoke(self, tenant_id: str, project_id: str, source_id: str) -> int:
        changed = 0
        for key, item in list(self._items.items()):
            if (
                item.tenant_id == tenant_id
                and item.project_id == project_id
                and item.source_id == source_id
                and not item.revoked
            ):
                self._items[key] = item.model_copy(update={"revoked": True})
                changed += 1
        return changed


class SQLiteSourceRecordStore:
    """Durable append-only source snapshot store backed by the project database."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._db = database
        self._migrate_legacy_schema()
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS source_records (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                revision TEXT NOT NULL,
                payload TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, project_id, source_id, revision)
            )
            """
        )

    def _migrate_legacy_schema(self) -> None:
        """Add tenant scoping without dropping immutable legacy snapshots."""

        existing = self._db.query_all("PRAGMA table_info(source_records)")
        if not existing:
            return
        columns = {str(row["name"]) for row in existing}
        if "tenant_id" in columns:
            return

        # Older builds keyed snapshots by project/source/revision only. Preserve
        # their payloads in a replacement table, using the payload tenant when
        # it exists and a visibly legacy tenant otherwise.
        self._db.execute("ALTER TABLE source_records RENAME TO source_records_legacy")
        self._db.execute(
            """
            CREATE TABLE source_records (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                revision TEXT NOT NULL,
                payload TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, project_id, source_id, revision)
            )
            """
        )
        rows = self._db.query_all(
            "SELECT project_id, source_id, revision, payload, revoked FROM source_records_legacy"
        )
        for row in rows:
            record = SourceRecord.model_validate_json(row["payload"])
            tenant_id = record.tenant_id or "legacy"
            migrated = record.model_copy(
                update={
                    "tenant_id": tenant_id,
                    "project_id": str(row["project_id"]),
                    "source_id": str(row["source_id"]),
                    "revision": str(row["revision"]),
                    "revoked": bool(row["revoked"]),
                }
            )
            self._db.execute(
                "INSERT OR IGNORE INTO source_records "
                "(tenant_id, project_id, source_id, revision, payload, revoked) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    migrated.tenant_id,
                    migrated.project_id,
                    migrated.source_id,
                    migrated.revision,
                    migrated.model_dump_json(),
                    int(migrated.revoked),
                ),
            )
        self._db.execute("DROP TABLE source_records_legacy")

    def put(self, record: SourceRecord) -> bool:
        existing = self.get(record.tenant_id, record.project_id, record.source_id, record.revision)
        if existing is not None:
            return False
        self._db.execute(
            "INSERT INTO source_records (tenant_id, project_id, source_id, revision, payload, revoked) VALUES (?, ?, ?, ?, ?, ?)",
            (record.tenant_id, record.project_id, record.source_id, record.revision, record.model_dump_json(), int(record.revoked)),
        )
        return True

    def get(
        self, tenant_id: str, project_id: str, source_id: str, revision: str
    ) -> SourceRecord | None:
        row = self._db.query_one(
            "SELECT payload FROM source_records WHERE tenant_id=? AND project_id=? AND source_id=? AND revision=?",
            (tenant_id, project_id, source_id, revision),
        )
        return SourceRecord.model_validate_json(row["payload"]) if row else None

    def all(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        include_revoked: bool = False,
    ) -> tuple[SourceRecord, ...]:
        query = "SELECT payload FROM source_records"
        args: tuple[object, ...] = ()
        clauses: list[str] = []
        if tenant_id is not None:
            clauses.append("tenant_id=?")
            args += (tenant_id,)
        if project_id is not None:
            clauses.append("project_id=?")
            args += (project_id,)
        if not include_revoked:
            clauses.append("revoked=0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = self._db.query_all(query, args)
        return tuple(SourceRecord.model_validate_json(row["payload"]) for row in rows)

    def revoke(self, tenant_id: str, project_id: str, source_id: str) -> int:
        rows = self._db.query_all(
            "SELECT payload FROM source_records WHERE tenant_id=? AND project_id=? AND source_id=? AND revoked=0",
            (tenant_id, project_id, source_id),
        )
        for row in rows:
            record = SourceRecord.model_validate_json(row["payload"]).model_copy(update={"revoked": True})
            self._db.execute(
                "UPDATE source_records SET payload=?, revoked=1 WHERE tenant_id=? AND project_id=? AND source_id=? AND revision=?",
                (record.model_dump_json(), tenant_id, project_id, source_id, record.revision),
            )
        return len(rows)


__all__ = ["InMemorySourceRecordStore", "SQLiteSourceRecordStore", "SourceRecordStore"]
