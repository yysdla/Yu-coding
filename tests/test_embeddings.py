from project_lens.context.embeddings import EpisodeEmbeddingScorer, MemoryEmbeddingScorer, _cosine
from project_lens.context.embeddings import SQLiteEmbeddingCache
from project_lens.context.embeddings import build_embedding_provider
from project_lens.config import Settings
from project_lens.domain.memory import MemoryType, ProjectMemory
from project_lens.domain.models import AgentRun, ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class FakeProvider:
    model_version = "fake-v1"

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.casefold()
            vectors.append((1.0 if "支付" in lowered or "payment" in lowered else 0.0, 1.0 if "回调" in lowered or "callback" in lowered else 0.0))
        return tuple(vectors)


class CountingProvider(FakeProvider):
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return super().embed(texts)


def test_cosine_is_bounded_and_handles_zero_vector() -> None:
    assert _cosine((1.0, 0.0), (1.0, 0.0)) == 1.0
    assert _cosine((0.0, 0.0), (1.0, 0.0)) == 0.0
    assert _cosine((1.0,), (1.0, 0.0)) == 0.0


def test_memory_embedding_scorer_returns_ids_and_similarity() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    memories = (
        ProjectMemory(project=project, text="支付回调通过 Kafka", memory_type=MemoryType.RISK, approved_by="u"),
        ProjectMemory(project=project, text="前端按钮规范", approved_by="u"),
    )
    scores = MemoryEmbeddingScorer(FakeProvider()).score("payment callback", memories)
    assert scores[str(memories[0].id)] == 1.0
    assert scores[str(memories[1].id)] == 0.0


def test_episode_embedding_scorer_uses_title_summary_and_tools() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    payment_run = AgentRun(project=project, user_id="u", question="支付问题")
    other_run = AgentRun(project=project, user_id="u", question="前端按钮规范")
    from project_lens.context.history_memory import derive_episode

    payment_episode, _ = derive_episode(payment_run, ())
    other_episode, _ = derive_episode(other_run, ())
    scores = EpisodeEmbeddingScorer(FakeProvider()).score(
        "payment callback",
        (payment_episode, other_episode),
    )
    assert scores[str(payment_episode.id)] >= 0.7
    assert scores[str(payment_episode.id)] > scores[str(other_episode.id)]


def test_embedding_cache_reuses_vectors_for_same_model_and_content() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    memory = ProjectMemory(project=project, text="支付回调通过 Kafka", approved_by="u")
    provider = CountingProvider()
    cache = SQLiteEmbeddingCache(SQLiteDatabase(":memory:"))
    scorer = MemoryEmbeddingScorer(provider, cache=cache)

    first = scorer.score("payment callback", (memory,))
    second = scorer.score("payment callback", (memory,))

    assert first == second
    assert provider.calls == 1
    assert cache.invalidate(project=project, kind="project_memory") >= 1


def test_embedding_provider_can_reuse_common_openai_compatible_credentials() -> None:
    provider = build_embedding_provider(
        Settings(
            _env_file=None,
            embedding_provider="openai",
            embedding_model="embedding-test",
            model_openai_api_key="test-key",
        )
    )

    assert provider is not None
    assert provider.model_version == "embedding-test"
