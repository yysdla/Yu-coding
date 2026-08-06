"""Phase 3.1: FeishuHermesToolLoopBridge uses HermesLoopRunner, not rule _plan()."""

from __future__ import annotations

import inspect

import pytest

from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu import hermes_tool_loop as hermes_tool_loop_mod
from project_lens.integrations.feishu.hermes_loop_runner import (
    HermesLoopRunResult,
    HermesLoopToolCall,
    extract_tool_calls_from_messages,
)
from project_lens.integrations.feishu.hermes_tool_loop import FeishuHermesToolLoopBridge
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig


class _FakeToolService:
    def list_tools(self) -> dict:
        return {"ok": True, "tools": []}

    async def call_tool(self, request):  # noqa: ANN001
        raise AssertionError("Fake bridge tests must not call ProjectAgentToolService directly")


class _FakeRunnerSelectingAuthorizedEvidence:
    """Chooses a tool the old rule planner would NOT pick for a .py-path question."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def run(
        self,
        *,
        question: str,
        project: ProjectRef,
        user_id: str,
        chat_id: str,
    ) -> HermesLoopRunResult:
        self.calls.append(
            {
                "question": question,
                "project_id": project.project_id,
                "user_id": user_id,
                "chat_id": chat_id,
            }
        )
        citation = {
            "id": "ev-auth-1",
            "kind": "code",
            "source_uri": "src/demo.py",
            "summary": "authorized evidence hit",
        }
        envelope = {
            "ok": True,
            "tool_name": "projectlens_authorized_evidence",
            "summary": "authorized inventory for the project",
            "citations": [citation],
            "evidence_refs": [citation],
            "unknowns": [],
            "audit_ref": {"allow_apply": False},
            "tool_result_id": "tr-1",
        }
        return HermesLoopRunResult(
            final_response="基于 authorized_evidence：项目可读源已列出。",
            tool_calls=(
                HermesLoopToolCall(
                    name="projectlens_authorized_evidence",
                    arguments={"limit": 5},
                    envelope=envelope,
                ),
            ),
            ok=True,
        )


class _FakeUnavailableRunner:
    def run(self, *, question: str, project: ProjectRef, user_id: str, chat_id: str) -> HermesLoopRunResult:
        del question, project, user_id, chat_id
        return HermesLoopRunResult(
            final_response="",
            ok=False,
            error="Hermes unavailable: missing API key",
        )


@pytest.mark.asyncio
async def test_bridge_uses_injected_runner_not_rule_plan() -> None:
    assert not hasattr(hermes_tool_loop_mod, "_plan")
    assert not hasattr(FeishuHermesToolLoopBridge, "_plan")
    source = inspect.getsource(hermes_tool_loop_mod)
    assert "def _plan(" not in source
    assert "_needs_graph" not in source
    assert "_FILE_PATH_RE" not in source

    fake = _FakeRunnerSelectingAuthorizedEvidence()
    bridge = FeishuHermesToolLoopBridge(
        tool_service=_FakeToolService(),  # type: ignore[arg-type]
        config=ProjectLensPluginConfig(),
        loop_runner=fake,
    )
    # Old rule planner would choose search_context + read_project_file for this text.
    question = "order_service.py create_order coupon relation?"
    project = ProjectRef(tenant_id="demo", project_id="payment")

    result = await bridge.answer(
        project=project,
        question=question,
        user_id="user-1",
        chat_id="chat-1",
    )

    assert fake.calls and fake.calls[0]["question"] == question
    assert result.ok is True
    assert bridge.last_tool_names == ("projectlens_authorized_evidence",)
    assert "projectlens_search_context" not in bridge.last_tool_names
    assert "projectlens_read_project_file" not in bridge.last_tool_names
    envelope = result.envelope
    assert envelope["ok"] is True
    assert envelope["citations"]
    assert envelope["evidence_refs"]
    assert envelope["tool_calls"]
    assert envelope["tool_calls"][0]["name"] == "projectlens_authorized_evidence"
    assert envelope["audit_ref"]["allow_apply"] is False
    assert envelope["audit_ref"]["mode"] == "feishu_hermes_llm_tool_loop"
    assert "authorized_evidence" in envelope["answer_summary"] or "authorized" in envelope[
        "answer_summary"
    ].casefold()


@pytest.mark.asyncio
async def test_bridge_reports_explicit_unavailable_without_rule_fallback() -> None:
    bridge = FeishuHermesToolLoopBridge(
        tool_service=_FakeToolService(),  # type: ignore[arg-type]
        config=ProjectLensPluginConfig(),
        loop_runner=_FakeUnavailableRunner(),
    )
    result = await bridge.answer(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        question="order_service.py create_order coupon?",
        user_id="user-1",
        chat_id="chat-1",
    )
    assert result.ok is False
    assert result.envelope["ok"] is False
    assert result.envelope["audit_ref"]["allow_apply"] is False
    assert result.envelope["audit_ref"]["hermes_ok"] is False
    assert "Hermes" in result.envelope["answer_summary"]
    assert "missing API key" in " ".join(result.envelope["unknowns"])
    assert bridge.last_tool_names == ()


def test_extract_tool_calls_from_messages_keeps_envelopes() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "projectlens_search_context",
                        "arguments": '{"query": "coupon", "limit": 3}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": json_dumps_envelope(),
        },
    ]
    calls = extract_tool_calls_from_messages(messages)
    assert len(calls) == 1
    assert calls[0].name == "projectlens_search_context"
    assert calls[0].arguments["query"] == "coupon"
    assert calls[0].envelope["ok"] is True
    assert calls[0].envelope["citations"]


def json_dumps_envelope() -> str:
    import json

    return json.dumps(
        {
            "ok": True,
            "tool_name": "projectlens_search_context",
            "summary": "hit",
            "citations": [
                {
                    "id": "c1",
                    "kind": "code",
                    "source_uri": "src/a.py",
                    "summary": "a",
                }
            ],
            "evidence_refs": [
                {
                    "id": "c1",
                    "kind": "code",
                    "source_uri": "src/a.py",
                    "summary": "a",
                }
            ],
        },
        ensure_ascii=False,
    )
