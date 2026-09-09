"""Project context indexing and evidence retrieval."""

from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle, TimeRange
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.context.source_records import FactType, SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore, SQLiteSourceRecordStore

__all__ = [
    "AccessContext",
    "ContextEngine",
    "ContextQuery",
    "EvidenceBundle",
    "InMemoryEvidenceIndex",
    "InMemorySourceRecordStore",
    "SQLiteSourceRecordStore",
    "FactType",
    "SourceRecord",
    "SourceType",
    "TimeRange",
]
