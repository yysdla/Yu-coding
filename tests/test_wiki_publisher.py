from datetime import datetime, timezone

import pytest

from project_lens.application.wiki_compiler import (
    InMemoryWikiDraftStore,
    WikiDraftStatus,
    WikiDraftWorkflowService,
    WikiPageDraft,
    WikiPageType,
)
from project_lens.integrations.feishu.wiki_publisher import FeishuWikiPublisher


def _draft(status: str = "review_required") -> WikiPageDraft:
    return WikiPageDraft(
        tenant_id="demo",
        project_id="payment",
        page_type=WikiPageType.REQUIREMENTS,
        title="Payment / requirements",
        status=status,
        content="# requirements",
        source_keys=("demo:payment:req:1",),
        generated_at=datetime.now(timezone.utc),
    )


class RecordingPublisher:
    def __init__(self, uri: str = "recording://ok") -> None:
        self.uri = uri
        self.calls = 0

    def publish(self, draft: WikiPageDraft) -> str:
        self.calls += 1
        return self.uri


def test_review_requires_reviewable_state_and_returns_uri() -> None:
    store = InMemoryWikiDraftStore()
    store.put_many((_draft(),))
    publisher = RecordingPublisher()
    result = WikiDraftWorkflowService(store, publisher).review(
        tenant_id="demo", project_id="payment", page_type=WikiPageType.REQUIREMENTS, approved=True
    )
    assert result.draft.status == WikiDraftStatus.PUBLISHED.value
    assert result.published_uri == "recording://ok"
    assert publisher.calls == 1


def test_rejected_review_does_not_publish() -> None:
    store = InMemoryWikiDraftStore()
    store.put_many((_draft(),))
    publisher = RecordingPublisher()
    result = WikiDraftWorkflowService(store, publisher).review(
        tenant_id="demo", project_id="payment", page_type=WikiPageType.REQUIREMENTS, approved=False
    )
    assert result.draft.status == WikiDraftStatus.REJECTED.value
    assert result.published_uri is None
    assert publisher.calls == 0


def test_feishu_publisher_uses_read_write_boundary(monkeypatch) -> None:
    class Token:
        def get(self):
            return "tenant-token"

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, **kwargs):
            self.calls.append(kwargs)
            return 200, {"code": 0, "data": {"node": {"url": "https://feishu/wiki/node"}}}

    transport = Transport()
    monkeypatch.setattr(
        "project_lens.integrations.feishu.wiki_publisher.assert_external_calls_allowed",
        lambda service: None,
    )
    publisher = FeishuWikiPublisher(
        token_provider=Token(), space_id="spc-1", parent_node_token="nod-1", transport=transport
    )
    uri = publisher.publish(_draft())
    assert uri == "https://feishu/wiki/node"
    assert transport.calls[0]["method"] == "POST"
    assert "source_keys" in transport.calls[0]["body"].decode()
