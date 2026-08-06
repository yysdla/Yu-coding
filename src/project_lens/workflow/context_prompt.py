"""Deterministic ContextPack -> model context renderer.

Future LLM calls must assemble prompts only through this module.
Does not call an LLM. Feishu adapters must not build prompts themselves.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from project_lens.domain.conversation import ConversationSummary
from project_lens.domain.models import Evidence
from project_lens.workflow.context_pack import ContextPack

DEFAULT_EVIDENCE_SNIPPET_CHARS = 280

# L0 anchors and pinned IDs must survive L1->L2 overflow compression.
NON_COMPRESSIBLE_LAYERS = (
    "L0_anchors",
    "pinned_ids",
    "tool_policy_hash",
    "allow_apply=False",
    "approved_project_memory_ids",
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ContextPromptSection(FrozenModel):
    layer: Literal["L0", "L1", "L2", "L3", "L4", "L5"]
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(default="", max_length=100_000)


class ContextPromptMessage(FrozenModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class ContextPrompt(FrozenModel):
    """Rendered model context derived exclusively from a ContextPack."""

    sections: tuple[ContextPromptSection, ...]
    messages: tuple[ContextPromptMessage, ...]
    audit_refs: dict[str, Any] = Field(default_factory=dict)

    def as_text(self) -> str:
        parts = [f"## {item.layer} {item.title}\n{item.content}".rstrip() for item in self.sections]
        return "\n\n".join(parts).strip() + "\n"


def clip_evidence_snippet(
    content: str,
    *,
    max_chars: int = DEFAULT_EVIDENCE_SNIPPET_CHARS,
) -> str:
    limit = max(16, int(max_chars))
    text = content.strip().replace("\r\n", "\n")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def compression_manifest(summary: ConversationSummary | None) -> dict[str, Any]:
    """Compact answer for: what was compressed vs what must stay."""

    if summary is None:
        return {
            "compression_cycle": 0,
            "compressed_turn_count": 0,
            "prior_traceback": False,
            "pinned_id_kinds": [],
            "non_compressible": list(NON_COMPRESSIBLE_LAYERS),
        }
    topic = summary.active_topic
    pinned = summary.pinned_ids
    pinned_kinds = [
        name
        for name, values in (
            ("evidence_ids", pinned.evidence_ids),
            ("proposal_ids", pinned.proposal_ids),
            ("file_paths", pinned.file_paths),
            ("run_ids", pinned.run_ids),
            ("trace_ids", pinned.trace_ids),
            ("memory_ids", pinned.memory_ids),
            ("incident_ids", pinned.incident_ids),
            ("commit_shas", pinned.commit_shas),
            ("doc_tokens", pinned.doc_tokens),
            ("symbol_names", pinned.symbol_names),
        )
        if values
    ]
    try:
        compressed_turns = int(topic.get("compressed_turns") or "0")
    except (TypeError, ValueError):
        compressed_turns = 0
    return {
        "compression_cycle": summary.compression_cycle,
        "compressed_turn_count": compressed_turns,
        "prior_traceback": bool(topic.get("prior_traceback")),
        "pinned_id_kinds": pinned_kinds,
        "non_compressible": list(NON_COMPRESSIBLE_LAYERS),
    }


def render_context_prompt(
    pack: ContextPack,
    *,
    evidence_snippet_chars: int = DEFAULT_EVIDENCE_SNIPPET_CHARS,
) -> ContextPrompt:
    """Render L0-L5 sections and chat messages from a ContextPack.

    Hard rules:
    - allow_apply is always rendered as False
    - evidence bodies are clipped snippets only
    - memories are approved ProjectMemory rows already on the pack
    - audit_refs never include full evidence content
    """

    if pack.anchors.allow_apply:
        raise ValueError("ContextPrompt refuses allow_apply=True")
    if pack.task_state is not None and pack.task_state.allow_apply:
        raise ValueError("ContextPrompt refuses task_state.allow_apply=True")

    sections = (
        _render_l0(pack),
        _render_l1(pack),
        _render_l2(pack),
        _render_l3(pack),
        _render_l4(pack, evidence_snippet_chars=evidence_snippet_chars),
        _render_l5(pack),
    )
    messages = _build_messages(pack, sections)
    compression = compression_manifest(pack.session_summary)
    audit = dict(pack.audit_refs())
    audit.update(
        {
            "renderer": "context_prompt.v1",
            "section_layers": [item.layer for item in sections],
            "message_roles": [item.role for item in messages],
            "evidence_snippet_chars": evidence_snippet_chars,
            "evidence_snippet_lengths": [
                len(clip_evidence_snippet(item.content, max_chars=evidence_snippet_chars))
                for item in pack.evidence
            ],
            "allow_apply": False,
            "compression_manifest": compression,
            "non_compressible": list(NON_COMPRESSIBLE_LAYERS),
            "model_saw_layers": [item.layer for item in sections],
        }
    )
    # Never leak full evidence bodies into audit.
    for key in list(audit):
        if "content" in key.lower() and key not in {
            "evidence_snippet_chars",
            "evidence_snippet_lengths",
        }:
            audit.pop(key, None)
    return ContextPrompt(sections=sections, messages=messages, audit_refs=audit)


def _render_l0(pack: ContextPack) -> ContextPromptSection:
    anchors = pack.anchors
    project = anchors.project
    provenance = pack.provenance
    lines = [
        f"tenant_id={project.tenant_id}",
        f"project_id={project.project_id}",
        f"service={project.service or ''}",
        f"environment={project.environment or ''}",
        f"access_scope={anchors.access_scope}",
        f"user_id={anchors.user_id}",
        f"run_id={anchors.run_id}",
        f"trace_id={anchors.trace_id}",
        f"session_id={anchors.session_id or ''}",
        f"channel_id={anchors.channel_id or ''}",
        f"skill={anchors.skill or ''}",
        f"policy_version={anchors.policy_version}",
        f"tool_boundary={anchors.tool_boundary}",
        f"tool_policy_hash={anchors.tool_policy_hash}",
        "allow_apply=False",
        f"access_permissions={','.join(sorted(anchors.access.permissions))}",
        f"provenance.retrieval_mode={provenance.retrieval_mode}",
        f"provenance.evidence_source={provenance.evidence_source}",
        f"provenance.memory_source={provenance.memory_source}",
        "non_compressible=" + ",".join(NON_COMPRESSIBLE_LAYERS),
    ]
    return ContextPromptSection(layer="L0", title="anchors", content="\n".join(lines))


def _render_l1(pack: ContextPack) -> ContextPromptSection:
    if not pack.recent_turns:
        content = f"current_question={pack.anchors.question}"
    else:
        lines: list[str] = []
        for index, turn in enumerate(pack.recent_turns, start=1):
            rewrite = turn.rewritten_question or ""
            lines.append(
                f"turn[{index}] user_id={turn.user_id} run_id={turn.run_id or ''} "
                f"text={turn.text}"
            )
            if rewrite:
                lines.append(f"turn[{index}] rewritten={rewrite}")
        lines.append(f"current_question={pack.anchors.question}")
        content = "\n".join(lines)
    return ContextPromptSection(layer="L1", title="recent_turns", content=content)


def _render_l2(pack: ContextPack) -> ContextPromptSection:
    summary = pack.session_summary
    if summary is None:
        manifest = compression_manifest(None)
        return ContextPromptSection(
            layer="L2",
            title="session_summary",
            content=(
                "(none)\n"
                f"compression.cycle={manifest['compression_cycle']}\n"
                f"compression.compressed_turn_count={manifest['compressed_turn_count']}\n"
                f"compression.non_compressible={','.join(manifest['non_compressible'])}"
            ),
        )
    pinned = summary.pinned_ids
    topic = " ".join(f"{key}={value}" for key, value in sorted(summary.active_topic.items()))
    manifest = compression_manifest(summary)
    lines = [
        f"active_skill={summary.active_skill or ''}",
        f"session_intent={summary.session_intent or ''}",
        f"active_topic={topic}",
        f"unknowns={'; '.join(summary.unknowns)}",
        f"next_actions={'; '.join(summary.next_actions)}",
        f"compression_cycle={summary.compression_cycle}",
        f"compression.cycle={manifest['compression_cycle']}",
        f"compression.compressed_turn_count={manifest['compressed_turn_count']}",
        f"compression.prior_traceback={manifest['prior_traceback']}",
        f"compression.pinned_id_kinds={','.join(manifest['pinned_id_kinds'])}",
        f"compression.non_compressible={','.join(manifest['non_compressible'])}",
        f"pinned.evidence_ids={','.join(pinned.evidence_ids)}",
        f"pinned.proposal_ids={','.join(pinned.proposal_ids)}",
        f"pinned.file_paths={','.join(pinned.file_paths)}",
        f"pinned.run_ids={','.join(pinned.run_ids)}",
        f"last_assistant_summary={summary.last_assistant_summary or ''}",
    ]
    return ContextPromptSection(layer="L2", title="session_summary", content="\n".join(lines))


def _render_l3(pack: ContextPack) -> ContextPromptSection:
    state = pack.task_state
    if state is None:
        return ContextPromptSection(layer="L3", title="task_state", content="(none)")
    lines = [
        f"phase={state.phase or ''}",
        f"plan={state.plan or ''}",
        f"files_read={','.join(state.files_read)}",
        f"files_changed={','.join(state.files_changed)}",
        f"patch_plan={state.patch_plan or ''}",
        f"diff_summary={state.diff_summary or ''}",
        f"test_commands={'; '.join(state.test_commands)}",
        f"test_results={'; '.join(state.test_results)}",
        f"approval_status={state.approval_status or ''}",
        "allow_apply=False",
        f"failed_attempts={'; '.join(state.failed_attempts)}",
        f"rejected_plans={'; '.join(state.rejected_plans)}",
    ]
    if state.sync_state:
        lines.append(
            "sync_state="
            + " ".join(f"{key}={value}" for key, value in sorted(state.sync_state.items()))
        )
    return ContextPromptSection(layer="L3", title="task_state", content="\n".join(lines))


def _render_l4(
    pack: ContextPack,
    *,
    evidence_snippet_chars: int,
) -> ContextPromptSection:
    if not pack.evidence:
        return ContextPromptSection(layer="L4", title="evidence", content="(none)")
    lines: list[str] = []
    for item in pack.evidence:
        lines.append(_format_evidence_line(item, max_chars=evidence_snippet_chars))
    return ContextPromptSection(layer="L4", title="evidence", content="\n".join(lines))


def _format_evidence_line(item: Evidence, *, max_chars: int) -> str:
    snippet = clip_evidence_snippet(item.content, max_chars=max_chars)
    meta_keys = sorted(str(key) for key in item.metadata)[:8]
    meta = ",".join(meta_keys)
    return (
        f"evidence_id={item.id} type={item.type.value} "
        f"source={item.source.system}:{item.source.source_id} "
        f"snippet={snippet} metadata_keys={meta}"
    )


def _render_l5(pack: ContextPack) -> ContextPromptSection:
    if not pack.memories:
        return ContextPromptSection(layer="L5", title="memories", content="(none)")
    lines: list[str] = []
    for memory in pack.memories:
        evidence_ids = ",".join(str(item) for item in memory.evidence_ids)
        lines.append(
            f"memory_id={memory.id} approved_by={memory.approved_by} "
            f"evidence_ids={evidence_ids} text={memory.text}"
        )
    return ContextPromptSection(layer="L5", title="memories", content="\n".join(lines))


def _build_messages(
    pack: ContextPack,
    sections: tuple[ContextPromptSection, ...],
) -> tuple[ContextPromptMessage, ...]:
    by_layer = {item.layer: item for item in sections}
    system_parts = [
        by_layer["L0"].content,
        "## Session Summary\n" + by_layer["L2"].content,
        "## Task State\n" + by_layer["L3"].content,
        "## Evidence\n" + by_layer["L4"].content,
        "## Approved Memories\n" + by_layer["L5"].content,
    ]
    messages: list[ContextPromptMessage] = [
        ContextPromptMessage(role="system", content="\n\n".join(system_parts).strip())
    ]
    for turn in pack.recent_turns:
        messages.append(ContextPromptMessage(role="user", content=turn.text))
        if turn.rewritten_question and turn.rewritten_question != turn.text:
            messages.append(
                ContextPromptMessage(
                    role="assistant",
                    content=f"(follow-up rewrite) {turn.rewritten_question}",
                )
            )
    messages.append(ContextPromptMessage(role="user", content=pack.anchors.question))
    return tuple(messages)
