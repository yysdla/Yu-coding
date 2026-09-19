"""Unified knowledge index contracts and persistence."""

from project_lens.context.knowledge_index.adapters import (
    EpisodeAdapter,
    EvidenceAdapter,
    MemoryObservationAdapter,
    ObsidianWikiAdapter,
    ProjectMemoryAdapter,
    SourceRecordAdapter,
)
from project_lens.context.knowledge_index.chunking import KnowledgeChunkBuilder
from project_lens.context.knowledge_index.embeddings import (
    EmbeddingBatchResult,
    EmbeddingGenerationError,
    KnowledgeEmbeddingService,
)
from project_lens.context.knowledge_index.models import (
    EmbeddingRecord,
    IndexRecordStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexJob,
    KnowledgeObjectKind,
    content_hash,
    stable_chunk_id,
    stable_document_id,
)
from project_lens.context.knowledge_index.store import SQLiteKnowledgeIndexStore

__all__ = [
    "EmbeddingRecord",
    "EmbeddingBatchResult",
    "EmbeddingGenerationError",
    "EpisodeAdapter",
    "EvidenceAdapter",
    "IndexRecordStatus",
    "KnowledgeChunk",
    "KnowledgeChunkBuilder",
    "KnowledgeDocument",
    "KnowledgeIndexJob",
    "KnowledgeEmbeddingService",
    "KnowledgeObjectKind",
    "MemoryObservationAdapter",
    "ObsidianWikiAdapter",
    "ProjectMemoryAdapter",
    "SQLiteKnowledgeIndexStore",
    "SourceRecordAdapter",
    "content_hash",
    "stable_chunk_id",
    "stable_document_id",
]
