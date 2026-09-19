from __future__ import annotations

from project_lens.context.knowledge_index import (
    KnowledgeChunkBuilder,
    KnowledgeDocument,
    KnowledgeObjectKind,
)


def _document(text: str, **changes: object) -> KnowledgeDocument:
    return KnowledgeDocument(
        id="doc-1",
        tenant_id="t1",
        project_id="p1",
        kind=KnowledgeObjectKind.SOURCE_RECORD,
        object_id="source-1",
        source_system="docs",
        source_id="source-1",
        revision="r1",
        title="需求",
        text=text,
        access_scope="project:p1:read",
        content_hash="a" * 64,
        **changes,
    )


def test_chunk_builder_preserves_markdown_heading_path_and_provenance() -> None:
    document = _document(
        "# 订单\n\n## 回调\n\n支付回调需要幂等。\n\n"
        "英文 callback must be idempotent.",
        metadata={"authority": "requirement"},
    )
    chunks = KnowledgeChunkBuilder(max_chars=80, overlap_chars=10).build(document)

    assert chunks
    assert any(chunk.title_path == ("订单", "回调") for chunk in chunks)
    assert chunks[0].metadata["document_id"] == "doc-1"
    assert chunks[0].metadata["revision"] == "r1"
    assert chunks[0].metadata["derived"] is False


def test_chunk_ids_are_stable_and_content_changes_only_change_content_ids() -> None:
    builder = KnowledgeChunkBuilder(max_chars=30, overlap_chars=5)
    first = builder.build(_document("第一段内容。\n\n第二段内容。"))
    again = builder.build(_document("第一段内容。\n\n第二段内容。"))
    changed = builder.build(_document("第一段内容已修改。\n\n第二段内容。"))

    assert [item.chunk_id for item in first] == [item.chunk_id for item in again]
    assert [item.chunk_id for item in first] != [item.chunk_id for item in changed]


def test_non_indexable_statuses_are_excluded() -> None:
    builder = KnowledgeChunkBuilder()
    for status in ("pending", "rejected", "revoked", "expired"):
        assert builder.build(_document("不可检索", status=status)) == ()
    assert builder.build(_document("不可检索", indexable=False)) == ()


def test_long_mixed_language_text_is_bounded_and_has_overlap() -> None:
    text = "中文支付回调 " * 80 + " English callback " * 80
    chunks = KnowledgeChunkBuilder(max_chars=100, overlap_chars=20).build(_document(text))

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 100 for chunk in chunks)
    assert all(chunk.chunk_count == len(chunks) for chunk in chunks)
    assert chunks[0].text[-20:] in chunks[1].text
