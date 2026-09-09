"""RoleView projection for ProjectLens answer envelopes.

Presentation only: reorders/summarizes an already-verified envelope.
Never invents facts. Never queries EvidenceIndex / graph / ops stores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

SUPPORTED_AUDIENCES = frozenset(
    {
        "team",
        "technical",
        "business",
        "qa",
        "manager",
        "ops",
        "evidence",
        "onboarding",
        "debug",
        "map",
    }
)

_AUDIENCE_TITLES: dict[str, str] = {
    "team": "团队协作视图",
    "technical": "技术视图",
    "business": "业务/产品视图",
    "qa": "测试视图",
    "manager": "管理/进度视图",
    "ops": "运维视图",
    "evidence": "证据视图",
    "onboarding": "新人理解视图",
    "debug": "调试视图",
    "map": "项目地图",
}


@dataclass(frozen=True)
class RoleViewPayload:
    audience: str
    title: str
    conclusion: str
    confirmed_points: tuple[str, ...]
    unknowns: tuple[str, ...]
    next_actions: tuple[str, ...]
    sources_teaser: str
    footer: str
    show_citations_detail: bool
    citation_lines: tuple[str, ...]
    inference_lines: tuple[str, ...]
    audit_line: str


def build_role_view(envelope: dict[str, Any], *, audience: str = "team") -> RoleViewPayload:
    """Build a RoleView from a verified ask envelope (or error envelope)."""

    audience_key = (audience or "team").strip().lower()
    if audience_key not in SUPPORTED_AUDIENCES:
        audience_key = "team"

    if envelope.get("ok") is False:
        return _error_payload(envelope, audience=audience_key)

    summary = str(envelope.get("answer_summary") or "").strip()
    facts = _fact_texts(envelope)
    inferences = [
        str(item).strip()
        for item in (envelope.get("inferences") or [])
        if str(item).strip()
    ]
    unknowns = [
        str(item).strip()
        for item in (envelope.get("unknowns") or [])
        if str(item).strip()
    ]
    actions = _action_titles(envelope)
    citations = _citation_lines(envelope)
    citation_count = len(_list_of_dicts(envelope.get("citations")))
    audit = envelope.get("audit_ref") if isinstance(envelope.get("audit_ref"), dict) else {}
    audit_line = _audit_line(audit, detailed=(audience_key in {"technical", "evidence", "debug"}))

    if audience_key == "technical":
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=summary or "当前没有可展示的技术结论。",
            confirmed_points=tuple(facts[:5]),
            unknowns=tuple(unknowns[:5]),
            next_actions=tuple(actions[:4]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=False,
            citation_lines=(),
            inference_lines=tuple(inferences[:4]),
            audit_line=audit_line,
        )

    if audience_key == "map":
        map_points = _map_focused_points(facts)
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=summary or "当前还没有可展示的项目地图结论。",
            confirmed_points=tuple(map_points[:5]),
            unknowns=tuple(unknowns[:4]),
            next_actions=tuple(actions[:3]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=False,
            citation_lines=(),
            inference_lines=tuple(inferences[:2]),
            audit_line=_audit_line(audit, detailed=True),
        )

    if audience_key == "business":
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=_business_conclusion(summary, facts),
            confirmed_points=tuple(_strip_paths(item) for item in facts[:3]),
            unknowns=tuple(unknowns[:4]),
            next_actions=tuple(actions[:3]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=False,
            citation_lines=(),
            inference_lines=(),
            audit_line=_audit_line(audit, detailed=False),
        )

    if audience_key == "qa":
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=summary or "当前没有可展示的测试相关结论。",
            confirmed_points=tuple(facts[:3]),
            unknowns=tuple(unknowns[:5]),
            next_actions=tuple(actions[:4]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=False,
            citation_lines=(),
            inference_lines=tuple(inferences[:2]),
            audit_line=_audit_line(audit, detailed=False),
        )

    if audience_key == "manager":
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=summary or "当前没有可展示的进度结论。",
            confirmed_points=tuple(_strip_paths(item) for item in facts[:3]),
            unknowns=tuple(unknowns[:4]),
            next_actions=tuple(actions[:3]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=False,
            citation_lines=(),
            inference_lines=(),
            audit_line=_audit_line(audit, detailed=False),
        )

    if audience_key == "onboarding":
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=summary or "当前资料还不足以给新人做完整介绍。",
            confirmed_points=tuple(_strip_paths(item) for item in facts[:4]),
            unknowns=tuple(unknowns[:4]),
            next_actions=tuple(actions[:3]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=False,
            citation_lines=(),
            inference_lines=(),
            audit_line=_audit_line(audit, detailed=False),
        )

    if audience_key in {"evidence", "debug"}:
        return RoleViewPayload(
            audience=audience_key,
            title=_AUDIENCE_TITLES[audience_key],
            conclusion=summary or "当前没有可展示的结论。",
            confirmed_points=tuple(facts[:6]),
            unknowns=tuple(unknowns[:6]),
            next_actions=tuple(actions[:4]),
            sources_teaser=_sources_teaser(citation_count, audience=audience_key),
            footer=_switch_hint(audience_key),
            show_citations_detail=True,
            citation_lines=tuple(citations[:8]),
            inference_lines=tuple(inferences[:4]),
            audit_line=audit_line,
        )

    # team default
    return RoleViewPayload(
        audience="team",
        title=_AUDIENCE_TITLES["team"],
        conclusion=summary or "当前没有可展示的结论。",
        confirmed_points=tuple(_strip_citation_ids(item) for item in facts[:3]),
        unknowns=tuple(unknowns[:4]),
        next_actions=tuple(actions[:3]),
        sources_teaser=_sources_teaser(citation_count, audience="team"),
        footer=_switch_hint("team"),
        show_citations_detail=False,
        citation_lines=(),
        inference_lines=(),
        audit_line=_audit_line(audit, detailed=False),
    )


def render_role_view_markdown(envelope: dict[str, Any], *, audience: str = "team") -> str:
    """Render RoleView Markdown for Hermes / Feishu text replies."""

    view = build_role_view(envelope, audience=audience)
    if envelope.get("ok") is False:
        return _render_error_markdown(envelope)

    lines = [f"### {view.title}", f"**结论**\n{view.conclusion}"]

    if view.confirmed_points:
        lines.append("")
        lines.append("**已确认**")
        lines.extend(f"- {item}" for item in view.confirmed_points)

    if view.inference_lines:
        lines.append("")
        lines.append("**推断**")
        lines.extend(f"- {item}" for item in view.inference_lines)

    if view.unknowns:
        lines.append("")
        lines.append("**暂不确定**")
        lines.extend(f"- {item}" for item in view.unknowns)

    if view.next_actions:
        lines.append("")
        lines.append("**建议下一步**")
        lines.extend(f"- {item}" for item in view.next_actions)

    if view.show_citations_detail and view.citation_lines:
        lines.append("")
        lines.append("**来源**")
        lines.extend(f"- {item}" for item in view.citation_lines)
    elif view.sources_teaser:
        lines.append("")
        lines.append(view.sources_teaser)

    if view.audit_line:
        lines.append("")
        lines.append(view.audit_line)

    if view.footer:
        lines.append("")
        lines.append(view.footer)

    return "\n".join(lines).strip()


def normalize_audience(raw: str | None) -> str:
    key = (raw or "team").strip().lower()
    return key if key in SUPPORTED_AUDIENCES else "team"


def is_supported_audience(raw: str | None) -> bool:
    return (raw or "").strip().lower() in SUPPORTED_AUDIENCES


def _error_payload(envelope: dict[str, Any], *, audience: str) -> RoleViewPayload:
    return RoleViewPayload(
        audience=audience,
        title="ProjectLens 请求失败",
        conclusion=str(envelope.get("message") or "ProjectLens request failed."),
        confirmed_points=(),
        unknowns=(),
        next_actions=(str(envelope.get("agent_recovery_hint") or "Check configuration and retry."),),
        sources_teaser="",
        footer="",
        show_citations_detail=False,
        citation_lines=(),
        inference_lines=(),
        audit_line=_audit_line(
            envelope.get("audit_ref") if isinstance(envelope.get("audit_ref"), dict) else {},
            detailed=False,
        ),
    )


def _render_error_markdown(envelope: dict[str, Any]) -> str:
    code = str(envelope.get("error_code") or "PROJECTLENS_ERROR")
    message = str(envelope.get("message") or "ProjectLens request failed.")
    hint = str(envelope.get("agent_recovery_hint") or "Check ProjectLens configuration and retry.")
    audit = envelope.get("audit_ref") if isinstance(envelope.get("audit_ref"), dict) else {}
    return "\n".join(
        [
            "### ProjectLens 请求失败",
            f"错误：{code}",
            f"说明：{message}",
            f"恢复建议：{hint}",
            _audit_line(audit, detailed=False),
        ]
    ).strip()


def _fact_texts(envelope: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for fact in _list_of_dicts(envelope.get("facts")):
        text = str(fact.get("text") or "").strip()
        if text:
            texts.append(text)
    return texts


def _map_focused_points(facts: list[str]) -> list[str]:
    """Prefer module/path/entrypoint facts for the map RoleView; no new facts."""

    markers = (
        "入口",
        "模块",
        "架构",
        "服务",
        "entrypoint",
        "module",
        "service",
        "src/",
        ".py",
        ".ts",
        ".go",
        "/",
        "\\",
    )
    focused = [
        _strip_citation_ids(item)
        for item in facts
        if any(marker.lower() in item.lower() for marker in markers)
    ]
    if focused:
        return focused
    return [_strip_citation_ids(item) for item in facts]


def _action_titles(envelope: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for action in _list_of_dicts(envelope.get("next_actions")):
        title = str(action.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _citation_lines(envelope: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for citation in _list_of_dicts(envelope.get("citations")):
        kind = str(citation.get("kind") or "source")
        source = str(citation.get("source_uri") or citation.get("id") or "source")
        summary = _truncate(str(citation.get("summary") or ""), 120)
        suffix = f" — {summary}" if summary else ""
        lines.append(f"{kind}: {source}{suffix}")
    return lines


def _sources_teaser(count: int, *, audience: str) -> str:
    if count <= 0:
        return "当前没有可展示的项目资料引用。"
    if audience == "team":
        return (
            f"已基于 {count} 条项目资料生成回答。"
            "查看来源可用：`/project-role evidence`（会回放同一 run，不重新调查）。"
        )
    return f"已基于 {count} 条项目资料生成回答。"


def _switch_hint(audience: str) -> str:
    if audience == "team":
        return (
            "切换视图（不重新编造事实）："
            "`/project-role technical` · `/project-role map` · `/project-role evidence`"
        )
    return "返回团队视图：`/project-role team`"


def _audit_line(audit: dict[str, Any], *, detailed: bool) -> str:
    allow_apply = bool(audit.get("allow_apply", False))
    parts = [f"allow_apply={str(allow_apply).lower()}"]
    run_id = audit.get("run_id")
    if run_id:
        parts.append(f"run_id={run_id}")
    if detailed:
        tools = audit.get("tool_names") if isinstance(audit.get("tool_names"), list) else []
        if tools:
            parts.append("tools=" + ",".join(str(item) for item in tools[:5]))
        trace_id = audit.get("trace_id")
        if trace_id:
            parts.append(f"trace_id={trace_id}")
    return "Audit: " + " | ".join(parts)


def _business_conclusion(summary: str, facts: list[str]) -> str:
    if summary:
        return _strip_paths(summary)
    if facts:
        return _strip_paths(facts[0])
    return "当前没有足够业务可读结论。"


def _strip_citation_ids(text: str) -> str:
    """Remove trailing [uuid, uuid] citation packs from fact display."""

    cleaned = text.strip()
    if "[" in cleaned and "]" in cleaned:
        head, _, tail = cleaned.rpartition(" [")
        if tail.endswith("]") and _looks_like_id_list(tail[:-1]):
            return head.strip()
    return cleaned


def _looks_like_id_list(raw: str) -> bool:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        return False
    for part in parts:
        try:
            UUID(part)
        except ValueError:
            return False
    return True


def _strip_paths(text: str) -> str:
    cleaned = _strip_citation_ids(text)
    # Soften dense path noise for business/manager/onboarding.
    return cleaned.replace("src/", "").replace("\\", "/")


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"
