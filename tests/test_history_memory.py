"""Structured historical memory derived from AgentRun events."""

from datetime import datetime, timezone
from uuid import uuid4

from project_lens.context.history_memory import derive_episode, derive_observations
from project_lens.context.history_store import InMemoryHistoryMemoryStore, SQLiteHistoryMemoryStore
from project_lens.domain.models import AgentRun, ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase
from project_lens.runtime.events import AgentEvent, AgentEventType


def _run() -> AgentRun:
    return AgentRun(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        user_id="u1",
        question="为什么支付回调延迟？",
    )


def test_events_become_safe_observations_without_raw_arguments() -> None:
    run = _run()
    event = AgentEvent(
        run_id=run.id,
        trace_id=run.trace_id,
        type=AgentEventType.TOOL_COMPLETED,
        occurred_at=datetime.now(timezone.utc),
        payload={
            "tool": "search_context",
            "is_error": False,
            "query": "Kafka lag",
            "content": "sensitive body must not be copied",
            "evidence_ids": [str(uuid4())],
        },
    )
    observations = derive_observations(run, (event,))
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind.value == "tool_result"
    assert observation.tool_name == "search_context"
    assert "sensitive body" not in observation.text
    assert "content" not in observation.source_event_payload_keys
    assert observation.evidence_ids


def test_episode_groups_observations_and_preserves_project_scope() -> None:
    run = _run()
    events = (
        AgentEvent(run.id, run.trace_id, AgentEventType.TOOL_STARTED, payload={"tool": "search_context"}),
        AgentEvent(run.id, run.trace_id, AgentEventType.TOOL_COMPLETED, payload={"tool": "search_context", "is_error": False}),
        AgentEvent(run.id, run.trace_id, AgentEventType.RUN_COMPLETED, payload={"status": "completed"}),
    )
    episode, observations = derive_episode(run, events)
    assert episode.project == run.project
    assert episode.run_id == run.id
    assert episode.tool_names == ("search_context",)
    assert episode.observation_ids == tuple(item.id for item in observations)
    assert episode.status == run.status.value


def test_history_store_persists_stable_episode_and_observations() -> None:
    database = SQLiteDatabase(":memory:")
    store = SQLiteHistoryMemoryStore(database)
    run = _run()
    events = (
        AgentEvent(
            run.id,
            run.trace_id,
            AgentEventType.TOOL_COMPLETED,
            payload={"tool": "search_context", "is_error": False},
        ),
    )
    first = store.upsert_run(run, events)
    second = store.upsert_run(run, events)
    assert first.id == second.id == run.id
    assert store.get_episode(run.id) == second
    observations = store.list_observations(run.id)
    assert len(observations) == 1
    assert observations[0].run_id == run.id


def test_history_store_search_is_project_scoped() -> None:
    database = SQLiteDatabase(":memory:")
    store = SQLiteHistoryMemoryStore(database)
    payment = _run()
    crm = payment.model_copy(
        update={
            "id": uuid4(),
            "trace_id": uuid4(),
            "project": ProjectRef(tenant_id="demo", project_id="crm"),
            "question": "CRM contact search",
        }
    )
    store.upsert_run(payment, ())
    store.upsert_run(crm, ())
    assert [item.run_id for item in store.search_episodes(payment.project, "支付回调")] == [payment.id]
    assert store.search_episodes(payment.project, "CRM") == ()


class _SynonymScorer:
    def score(self, query, candidates):
        return {str(item.id): 0.95 for item in candidates if "延迟" in query}


class _BrokenScorer:
    def score(self, query, candidates):
        raise RuntimeError("embedding backend unavailable")


def test_history_vector_scorer_can_recall_semantic_candidate() -> None:
    store = InMemoryHistoryMemoryStore(vector_scorer=_SynonymScorer())
    run = _run().model_copy(update={"question": "支付回调失败调查"})
    store.upsert_run(run, ())

    result = store.search_episodes(run.project, "延迟", limit=5)
    assert result and result[0].run_id == run.id


def test_history_vector_failure_falls_back_to_keyword_matching() -> None:
    store = InMemoryHistoryMemoryStore(vector_scorer=_BrokenScorer())
    run = _run()
    store.upsert_run(run, ())

    assert store.search_episodes(run.project, "支付回调", limit=5)
    assert store.search_episodes(run.project, "完全不存在", limit=5) == ()
