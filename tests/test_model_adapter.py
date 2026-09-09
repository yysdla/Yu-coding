"""ContextPrompt wiring and ModelAdapter entry-point tests."""

from __future__ import annotations

import pytest

from project_lens.workflow.context_prompt import render_context_prompt
from project_lens.workflow.model_adapter import StubModelAdapter
from tests.test_context_prompt import _pack


@pytest.mark.asyncio
async def test_stub_adapter_accepts_context_prompt_only() -> None:
    prompt = render_context_prompt(_pack())
    result = await StubModelAdapter().prepare(prompt)
    assert result.used_prompt is True
    assert result.adapter == "model_adapter.v1"
    assert result.provider == "stub.v1"
    assert result.allow_apply is False
    assert result.section_layers == ("L0", "L1", "L2", "L3", "L4", "L5")
    assert result.message_count >= 1
    assert result.usage["total_tokens"] > 0
    assert result.audit_refs()["allow_apply"] is False
