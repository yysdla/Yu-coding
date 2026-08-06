"""ConversationSession stores: in-memory and SQLite-backed."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError

from project_lens.domain.conversation import ConversationSession
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase

logger = logging.getLogger(__name__)


class ConversationStore:
    def get(self, session_id: UUID) -> ConversationSession | None:
        raise NotImplementedError

    def get_by_binding(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        user_id: str,
        project: ProjectRef,
    ) -> ConversationSession | None:
        raise NotImplementedError

    def upsert(self, session: ConversationSession) -> ConversationSession:
        raise NotImplementedError


class InMemoryConversationStore(ConversationStore):
    def __init__(self) -> None:
        self._by_id: dict[UUID, ConversationSession] = {}
        self._by_binding: dict[tuple[str, str, str, str, str], UUID] = {}

    def get(self, session_id: UUID) -> ConversationSession | None:
        session = self._by_id.get(session_id)
        if session is None:
            return None
        if session.expires_at < datetime.now(timezone.utc):
            self._drop(session)
            return None
        return session

    def get_by_binding(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        user_id: str,
        project: ProjectRef,
    ) -> ConversationSession | None:
        key = (
            tenant_id,
            chat_id,
            user_id,
            project.tenant_id,
            project.project_id,
        )
        session_id = self._by_binding.get(key)
        if session_id is None:
            return None
        return self.get(session_id)

    def upsert(self, session: ConversationSession) -> ConversationSession:
        key = (
            session.tenant_id,
            session.chat_id,
            session.user_id,
            session.project.tenant_id,
            session.project.project_id,
        )
        self._by_id[session.session_id] = session
        self._by_binding[key] = session.session_id
        return session

    def _drop(self, session: ConversationSession) -> None:
        self._by_id.pop(session.session_id, None)
        key = (
            session.tenant_id,
            session.chat_id,
            session.user_id,
            session.project.tenant_id,
            session.project.project_id,
        )
        current = self._by_binding.get(key)
        if current == session.session_id:
            self._by_binding.pop(key, None)


class SQLiteConversationStore(ConversationStore):
    """Durable ConversationSession store. Runtime context only — never ProjectMemory."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                session_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                project_tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(tenant_id, chat_id, user_id, project_tenant_id, project_id)
            )
            """
        )

    def get(self, session_id: UUID) -> ConversationSession | None:
        row = self._database.query_one(
            "SELECT payload FROM conversation_sessions WHERE session_id = ?",
            (str(session_id),),
        )
        if row is None:
            return None
        return self._hydrate_or_drop(
            row["payload"],
            drop_session_id=str(session_id),
        )

    def get_by_binding(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        user_id: str,
        project: ProjectRef,
    ) -> ConversationSession | None:
        row = self._database.query_one(
            """
            SELECT session_id, payload FROM conversation_sessions
            WHERE tenant_id = ?
              AND chat_id = ?
              AND user_id = ?
              AND project_tenant_id = ?
              AND project_id = ?
            """,
            (
                tenant_id,
                chat_id,
                user_id,
                project.tenant_id,
                project.project_id,
            ),
        )
        if row is None:
            return None
        return self._hydrate_or_drop(
            row["payload"],
            drop_session_id=str(row["session_id"]),
        )

    def upsert(self, session: ConversationSession) -> ConversationSession:
        payload = session.model_dump_json()
        self._database.execute(
            """
            DELETE FROM conversation_sessions
            WHERE session_id = ?
               OR (
                    tenant_id = ?
                AND chat_id = ?
                AND user_id = ?
                AND project_tenant_id = ?
                AND project_id = ?
               )
            """,
            (
                str(session.session_id),
                session.tenant_id,
                session.chat_id,
                session.user_id,
                session.project.tenant_id,
                session.project.project_id,
            ),
        )
        self._database.execute(
            """
            INSERT INTO conversation_sessions (
                session_id,
                tenant_id,
                chat_id,
                user_id,
                project_tenant_id,
                project_id,
                expires_at,
                payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session.session_id),
                session.tenant_id,
                session.chat_id,
                session.user_id,
                session.project.tenant_id,
                session.project.project_id,
                session.expires_at.isoformat(),
                payload,
            ),
        )
        return session

    def _hydrate_or_drop(
        self,
        payload: str,
        *,
        drop_session_id: str | None = None,
    ) -> ConversationSession | None:
        try:
            session = ConversationSession.model_validate_json(payload)
        except (ValidationError, json.JSONDecodeError, ValueError, TypeError) as exc:
            # Corrupt rows are dropped so Feishu can recreate a clean session.
            logger.warning(
                "dropping corrupt conversation session payload: %s",
                drop_session_id or "unknown",
                exc_info=exc,
            )
            if drop_session_id:
                self._database.execute(
                    "DELETE FROM conversation_sessions WHERE session_id = ?",
                    (drop_session_id,),
                )
            return None
        if session.expires_at < datetime.now(timezone.utc):
            self._drop(session)
            return None
        return session

    def _drop(self, session: ConversationSession) -> None:
        self._database.execute(
            "DELETE FROM conversation_sessions WHERE session_id = ?",
            (str(session.session_id),),
        )
