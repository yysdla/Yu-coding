"""API JSON and Feishu cards share the same audience projection."""

from __future__ import annotations

from project_lens.application.answer_envelope import project_answer_to_envelope
from project_lens.domain.models import AgentRun, RunStatus
from project_lens.integrations.feishu.cards import render_answer_card
from project_lens.project_space.policies import RoleKind, effective_scope_to_audit_dict
from tests.test_role_aware_answering import _answer, _scope


def _run(role: RoleKind) -> AgentRun:
    scope = _scope(role)
    return AgentRun(
        project=scope.project,
        user_id=scope.actor_id,
        channel_id=scope.chat_id,
        question="RQ-204 的需求变更会影响哪些地方？当前风险是什么？",
        runtime_access=effective_scope_to_audit_dict(scope),
        status=RunStatus.COMPLETED,
        answer=_answer(),
    )


def _card_text(card: dict[str, object]) -> str:
    return "\n".join(
        str(item.get("content") or "")
        for item in card.get("elements", [])  # type: ignore[union-attr]
        if isinstance(item, dict)
    )


def test_same_answer_has_same_facts_and_citations_across_roles() -> None:
    answer = _answer()
    envelopes = {
        role: project_answer_to_envelope(run=_run(role), answer=answer)
        for role in (
            RoleKind.DEVELOPER,
            RoleKind.PRODUCT,
            RoleKind.QA,
            RoleKind.MANAGER,
        )
    }
    baseline = envelopes[RoleKind.DEVELOPER]
    for envelope in envelopes.values():
        assert envelope["project"] == baseline["project"]
        assert envelope["facts"] == baseline["facts"]
        assert envelope["citations"] == baseline["citations"]
        assert envelope["unknowns"] == baseline["unknowns"]

    titles = {
        envelope["audience_view"]["title"]  # type: ignore[index]
        for envelope in envelopes.values()
    }
    assert titles == {"技术视图", "业务/产品视图", "测试视图", "管理/进度视图"}


def test_feishu_card_uses_current_actor_role_automatically() -> None:
    answer = _answer()
    developer = _card_text(render_answer_card(_run(RoleKind.DEVELOPER), answer))
    product = _card_text(render_answer_card(_run(RoleKind.PRODUCT), answer))
    qa = _card_text(render_answer_card(_run(RoleKind.QA), answer))
    manager = _card_text(render_answer_card(_run(RoleKind.MANAGER), answer))

    assert "技术视图" in developer and "相关模块 / 文件 / 接口" in developer
    assert "业务/产品视图" in product and "需求范围 / 当前进度" in product
    assert "测试视图" in qa and "影响场景 / 回归范围" in qa
    assert "管理/进度视图" in manager and "里程碑 / 截止时间" in manager
    assert len({developer, product, qa, manager}) == 4


def test_api_and_card_use_the_same_audience_sections() -> None:
    answer = _answer()
    run = _run(RoleKind.QA)
    envelope = project_answer_to_envelope(run=run, answer=answer)
    card_text = _card_text(render_answer_card(run, answer))
    view = envelope["audience_view"]
    assert isinstance(view, dict)
    for section in view["sections"]:
        assert section["title"] in card_text
        for item in section["items"]:
            assert item in card_text

