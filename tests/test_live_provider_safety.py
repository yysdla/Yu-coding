"""Phase 3 Safe Live LLM provider safety tests (mock transport only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from project_lens.config import Settings
from project_lens.domain.models import (
    Claim,
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    SourceRef,
)
from project_lens.workflow.context_prompt import render_context_prompt
from project_lens.workflow.expression_enhancer import maybe_enhance_answer
from project_lens.workflow.llm_schema import parse_llm_expression_output
from project_lens.workflow.model_adapter import ProviderModelAdapter
from project_lens.workflow.providers import (
    OpenAIProvider,
    ProviderKind,
    ProviderRuntimeConfig,
    StubProvider,
    create_model_adapter_from_settings,
    provider_config_from_settings,
)
from project_lens.workflow.providers.transport import RecordingChatTransport
from tests.test_context_prompt import _pack


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _answer_with_evidence() -> ProjectAnswer:
    project = _project()
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="local", source_id="doc-1"),
        content="Ada owns order-service",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    claim = Claim(
        text="Ada owns order-service",
        type=ClaimType.FACT,
        evidence_ids=(evidence.id,),
        grade=EvidenceGrade.B,
    )
    return ProjectAnswer(
        project=project,
        skill="project_knowledge",
        confidence=0.7,
        status="identified",
        business_summary="负责人是 Ada",
        technical_summary="owner documented in knowledge base",
        claims=(claim,),
        evidence=(evidence,),
        unknowns=(),
        recommended_actions=(),
    )


@pytest.mark.asyncio
async def test_live_false_does_not_call_transport() -> None:
    transport = RecordingChatTransport(response={})
    provider = OpenAIProvider(
        live=False,
        api_key="sk-test",
        transport=transport,
    )
    result = await provider.complete(render_context_prompt(_pack()))
    assert result.status.value == "reserved"
    assert result.live is False
    assert transport.calls == []


@pytest.mark.asyncio
async def test_live_true_without_api_key_does_not_network() -> None:
    transport = RecordingChatTransport(response={})
    provider = OpenAIProvider(live=True, api_key=None, transport=transport)
    result = await provider.complete(render_context_prompt(_pack()))
    assert result.status.value == "reserved"
    assert result.live is False
    assert transport.calls == []


@pytest.mark.asyncio
async def test_live_true_with_mock_transport_uses_context_prompt_only() -> None:
    prompt = render_context_prompt(_pack())
    payload = {
        "business_summary": "给业务看：下单可能受 coupon 为空影响。",
        "technical_summary": "技术：optional coupon 未做空判断。",
        "product_summary": "产品摘要：下单失败风险。",
        "hypotheses": [],
        "claim_drafts": [],
        "allow_apply": False,
    }
    transport = RecordingChatTransport(
        response={
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
        }
    )
    adapter = ProviderModelAdapter(
        OpenAIProvider(
            live=True,
            api_key="sk-test",
            transport=transport,
            model_name="gpt-test",
        ),
        config=ProviderRuntimeConfig(
            kind=ProviderKind.OPENAI,
            live=True,
            model_name="gpt-test",
            fallback_to_stub=True,
        ),
        fallback_provider=StubProvider(),
        live_requested=True,
    )
    result = await adapter.prepare(prompt)
    assert result.status == "ok"
    assert result.live_effective is True
    assert result.usage["total_tokens"] == 140
    assert transport.calls
    body = transport.calls[0]["body"]
    assert body["messages"][0]["role"] == "system"
    # Prompt messages come from ContextPrompt, not free-form Feishu text assembly.
    assert any(item["role"] == "user" for item in body["messages"])
    # API key must never appear in recorded transport audit headers.
    assert transport.calls[0]["headers"]["Authorization"] == "***"
    assert "sk-test" not in str(transport.calls)


def test_schema_rejects_allow_apply_true() -> None:
    parsed = parse_llm_expression_output(
        json.dumps({"business_summary": "x", "allow_apply": True})
    )
    assert parsed is None or parsed.allow_apply is False


def test_uncited_fact_draft_is_downgraded_to_hypothesis() -> None:
    answer = _answer_with_evidence()
    from project_lens.workflow.model_adapter import ModelAdapterResult

    content = json.dumps(
        {
            "business_summary": "增强后的业务结论",
            "technical_summary": "增强后的技术结论",
            "claim_drafts": [
                {
                    "text": "完全没有证据的事实",
                    "claim_type": "fact",
                    "evidence_ids": [str(uuid4())],
                }
            ],
            "hypotheses": ["可选假设"],
            "allow_apply": False,
        },
        ensure_ascii=False,
    )
    adapter_result = ModelAdapterResult(
        adapter="model_adapter.v1",
        provider="openai.v1",
        provider_kind="openai",
        model_name="gpt-test",
        message_count=1,
        status="ok",
        live_effective=True,
        live=True,
        content=content,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    enhanced, audit = maybe_enhance_answer(answer, adapter_result)
    assert audit["enhanced"] is True
    assert audit["allow_apply"] is False
    assert audit["rejected_fact_drafts"] == 1
    assert "增强后的业务结论" in enhanced.business_summary
    assert any("缺证据" in item for item in enhanced.unknowns)
    assert all(item.text != "完全没有证据的事实" or item.type != ClaimType.FACT for item in enhanced.claims)


@pytest.mark.asyncio
async def test_live_failure_falls_back_without_crashing() -> None:
    prompt = render_context_prompt(_pack())
    transport = RecordingChatTransport(error=RuntimeError("network down"))
    adapter = ProviderModelAdapter(
        OpenAIProvider(live=True, api_key="sk-test", transport=transport),
        config=ProviderRuntimeConfig(
            kind=ProviderKind.OPENAI,
            live=True,
            fallback_to_stub=True,
            max_retries=0,
        ),
        fallback_provider=StubProvider(model_name="stub-fallback"),
        live_requested=True,
    )
    result = await adapter.prepare(prompt)
    assert result.fallback_used is True
    assert result.status == "fallback"
    assert result.provider == "stub.v1"
    assert result.allow_apply is False


def test_settings_live_requested_without_key_stays_offline() -> None:
    settings = Settings(
        model_provider="openai",
        model_live=True,
        model_openai_api_key=None,
        model_fallback_to_stub=True,
    )
    config = provider_config_from_settings(settings)
    assert config.live is True
    adapter = create_model_adapter_from_settings(settings)
    assert adapter.config.live is True


def test_expression_enhancer_skips_non_live_stub_content() -> None:
    from project_lens.workflow.model_adapter import ModelAdapterResult

    answer = _answer_with_evidence()
    adapter_result = ModelAdapterResult(
        adapter="model_adapter.v1",
        provider="stub.v1",
        provider_kind="stub",
        model_name="stub",
        message_count=1,
        status="ok",
        live_effective=False,
        live=False,
        content='{"business_summary":"should not apply"}',
    )
    enhanced, audit = maybe_enhance_answer(answer, adapter_result)
    assert audit["enhanced"] is False
    assert enhanced.business_summary == answer.business_summary


def test_workflow_import_boundary_for_live_provider() -> None:
    import inspect

    from project_lens.workflow.orchestrator import ProjectWorkflow

    source = inspect.getsource(ProjectWorkflow)
    assert "OpenAIProvider" not in source
    assert "UrllibChatTransport" not in source
    assert "maybe_enhance_answer" in source
