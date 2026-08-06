from datetime import datetime, timezone

from project_lens.context.engine import ContextEngine
from project_lens.context.indexing.feishu_documents import FeishuDocumentIndexer
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.context.sources.feishu_docs import FeishuDocumentRecord
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import ProjectRef


def _record(**overrides: object) -> FeishuDocumentRecord:
    payload = {
        "tenant_id": "demo",
        "project_id": "payment",
        "doc_token": "doc-token-1",
        "doc_url": "https://feishu.example/docx/doc-token-1",
        "title": "Owner and architecture",
        "content": "order-service owner is Ada. payment service depends on coupon data.",
        "revision": "r1",
        "updated_at": datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        "owner_user_id": "ou_ada",
        "access_scope": "project:payment:read",
    }
    payload.update(overrides)
    return FeishuDocumentRecord.model_validate(payload)


def test_feishu_document_becomes_evidence_with_required_provenance() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    evidence = FeishuDocumentIndexer().index([_record()], project=project)

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source.system == "feishu_doc"
    assert item.source.url == "https://feishu.example/docx/doc-token-1"
    assert item.metadata["revision"] == "r1"
    assert item.metadata["doc_token"] == "doc-token-1"
    assert item.access_scope == "project:payment:read"


def test_revision_change_produces_new_content_hash() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    first = FeishuDocumentIndexer().index([_record(revision="r1")], project=project)[0]
    second = FeishuDocumentIndexer().index([_record(revision="r2")], project=project)[0]
    assert first.content_hash != second.content_hash


def test_empty_or_unauthorized_documents_are_not_returned() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    evidence = FeishuDocumentIndexer().index(
        [
            _record(content="   "),
            _record(doc_token="secret", access_scope="project:payment:secret"),
        ],
        project=project,
    )
    index = InMemoryEvidenceIndex()
    index.add_many(evidence)
    engine = ContextEngine(index)
    bundle = engine.search(
        ContextQuery(text="owner Ada architecture", project=project, limit=5),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
    )
    assert not evidence or all(item.access_scope != "project:payment:read" for item in evidence if not item.content.strip())
    assert all(hit.evidence.access_scope == "project:payment:read" for hit in bundle.hits)
    # empty content skipped; secret scope not retrievable with read permission
    assert not any(hit.evidence.source.source_id.startswith("secret") for hit in bundle.hits)


def test_acl_mismatch_keeps_feishu_doc_out_of_retrieval() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    evidence = FeishuDocumentIndexer().index(
        [_record(content="unique-feishu-owner-signal Ada")],
        project=project,
    )
    index = InMemoryEvidenceIndex()
    index.add_many(evidence)
    engine = ContextEngine(index)
    denied = engine.search(
        ContextQuery(text="unique-feishu-owner-signal Ada", project=project),
        AccessContext(tenant_id="demo", user_id="u1", permissions=frozenset()),
    )
    allowed = engine.search(
        ContextQuery(text="unique-feishu-owner-signal Ada", project=project),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
    )
    assert not denied.hits
    assert allowed.hits
