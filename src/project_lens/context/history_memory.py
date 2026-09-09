"""Derive safe historical memory records from durable AgentRun events."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid5

from project_lens.domain.memory import (
    Episode,
    MemoryObservation,
    MemoryObservationKind,
)
from project_lens.domain.models import AgentRun
from project_lens.runtime.events import AgentEvent, AgentEventType


def derive_observations(
    run: AgentRun,
    events: Iterable[AgentEvent],
    *,
    max_observations: int = 48,
) -> tuple[MemoryObservation, ...]:
    """Convert event metadata into bounded, citation-safe historical observations."""

    observations: list[MemoryObservation] = []
    for event_index, event in enumerate(events):
        kind = _kind_for_event(event.type)
        if kind is None:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        tool_name = _optional_text(payload.get("tool"), max_chars=120)
        text = _observation_text(event.type, payload, tool_name)
        if not text:
            continue
        evidence_ids = _uuid_tuple(payload.get("evidence_ids"))
        observations.append(
            MemoryObservation(
                id=uuid5(run.id, f"event:{event_index}:{event.type.value}:{event.occurred_at.isoformat()}"),
                project=run.project,
                run_id=run.id,
                observed_at=_aware(event.occurred_at),
                kind=kind,
                text=text[:1_000],
                tool_name=tool_name,
                event_type=event.type.value,
                evidence_ids=evidence_ids,
                source_event_payload_keys=tuple(sorted(str(key) for key in payload if _safe_key(key))),
            )
        )
        if len(observations) >= max(1, int(max_observations)):
            break
    return tuple(observations)


def derive_episode(
    run: AgentRun,
    events: Iterable[AgentEvent],
    *,
    max_observations: int = 48,
) -> tuple[Episode, tuple[MemoryObservation, ...]]:
    """Build one historical Episode plus its derived observations."""

    event_list = tuple(events)
    observations = derive_observations(run, event_list, max_observations=max_observations)
    timestamps = [_aware(run.created_at), _aware(run.updated_at)]
    timestamps.extend(_aware(event.occurred_at) for event in event_list)
    answer = run.answer
    summary = (
        (answer.conclusion if answer else "")
        or (answer.technical_summary if answer else "")
        or (answer.business_summary if answer else "")
        or run.error
        or "未生成回答"
    )
    tool_names = tuple(sorted({item.tool_name for item in observations if item.tool_name}))
    evidence_ids = list(_evidence_ids_from_answer(answer))
    for observation in observations:
        evidence_ids.extend(observation.evidence_ids)
    deduped_evidence = tuple(dict.fromkeys(evidence_ids))
    episode = Episode(
        id=run.id,
        project=run.project,
        run_id=run.id,
        title=run.question[:240],
        summary=summary[:2_000],
        started_at=min(timestamps),
        ended_at=max(timestamps),
        status=run.status.value,
        tool_names=tool_names,
        evidence_ids=deduped_evidence[:24],
        observation_ids=tuple(item.id for item in observations),
    )
    return episode, observations


def _kind_for_event(event_type: AgentEventType) -> MemoryObservationKind | None:
    if event_type is AgentEventType.TOOL_COMPLETED:
        return MemoryObservationKind.TOOL_RESULT
    if event_type is AgentEventType.TOOL_DENIED:
        return MemoryObservationKind.TOOL_DENIED
    if event_type is AgentEventType.RUN_STATUS_CHANGED:
        return MemoryObservationKind.STATUS_CHANGE
    if event_type in {AgentEventType.RUN_COMPLETED, AgentEventType.RUN_FAILED}:
        return MemoryObservationKind.RUN_OUTCOME
    return None


def _observation_text(
    event_type: AgentEventType,
    payload: dict[str, Any],
    tool_name: str | None,
) -> str:
    if event_type is AgentEventType.TOOL_COMPLETED:
        state = "失败" if payload.get("is_error") else "完成"
        return f"工具 {tool_name or 'unknown'}{state}。"
    if event_type is AgentEventType.TOOL_DENIED:
        reason = _optional_text(payload.get("reason"), max_chars=280)
        return f"工具 {tool_name or 'unknown'} 被拒绝。" + (f"原因：{reason}" if reason else "")
    if event_type is AgentEventType.RUN_STATUS_CHANGED:
        status = _optional_text(payload.get("status"), max_chars=80)
        return f"运行状态变为 {status or 'unknown'}。"
    if event_type is AgentEventType.RUN_COMPLETED:
        return "AgentRun 已完成。"
    if event_type is AgentEventType.RUN_FAILED:
        error = _optional_text(payload.get("error"), max_chars=280)
        return "AgentRun 失败。" + (f"错误：{error}" if error else "")
    return ""


def _optional_text(value: object, *, max_chars: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\r", " ").replace("\n", " ")
    return text[:max_chars] if text else None


def _uuid_tuple(value: object) -> tuple[UUID, ...]:
    if isinstance(value, dict):
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return ()
    result: list[UUID] = []
    for item in value:
        try:
            result.append(UUID(str(item)))
        except (TypeError, ValueError, AttributeError):
            continue
    return tuple(dict.fromkeys(result))


def _evidence_ids_from_answer(answer: object) -> tuple[UUID, ...]:
    evidence = getattr(answer, "evidence", ()) if answer is not None else ()
    result: list[UUID] = []
    for item in evidence:
        value = getattr(item, "id", None)
        try:
            result.append(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            continue
    return tuple(dict.fromkeys(result))


def _safe_key(value: object) -> bool:
    key = str(value).casefold()
    return not any(marker in key for marker in ("content", "argument", "prompt", "secret", "token"))


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
