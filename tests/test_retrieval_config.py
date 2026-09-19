from __future__ import annotations

import pytest

from project_lens.config import Settings
from project_lens.context.retrieval.config import (
    MAX_CANDIDATES_LIMIT,
    MAX_RESULTS_LIMIT,
    retrieval_config_from_settings,
)


def isolated_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_knowledge_retrieval_defaults_are_safe_lexical() -> None:
    config = retrieval_config_from_settings(retrieval_settings := isolated_settings())

    assert retrieval_settings.knowledge_retrieval_mode == "lexical"
    assert config.mode == "lexical"
    assert config.effective_mode == "lexical"
    assert config.vector_enabled is False
    assert config.vector_ready is False
    assert config.vector_fallback is True
    assert config.max_candidates == 200
    assert config.max_results == 8


def test_vector_mode_falls_back_until_provider_model_and_version_are_complete() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            knowledge_retrieval_mode="hybrid",
            knowledge_vector_enabled=True,
            embedding_provider="openai",
            knowledge_embedding_model="text-embedding-test",
        )
    )

    assert config.mode == "hybrid"
    assert config.vector_ready is False
    assert config.effective_mode == "lexical"


def test_complete_vector_configuration_can_select_hybrid_or_vector() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            knowledge_retrieval_mode="hybrid",
            knowledge_vector_enabled=True,
            embedding_provider="openai",
            knowledge_embedding_model="text-embedding-test",
            knowledge_embedding_version="2026-09-18",
            embedding_api_key="test-key",
        )
    )

    assert config.vector_ready is True
    assert config.effective_mode == "hybrid"
    assert config.for_query(requested_mode="vector").effective_mode == "vector"


def test_knowledge_model_reuses_common_embedding_model_when_not_overridden() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            knowledge_retrieval_mode="hybrid",
            knowledge_vector_enabled=True,
            embedding_provider="openai",
            embedding_model="shared-embedding-model",
            embedding_api_key="test-key",
            knowledge_embedding_version="shared-v1",
        )
    )

    assert config.embedding_model == "shared-embedding-model"
    assert config.vector_ready is True


def test_common_model_credentials_can_enable_openai_compatible_embeddings() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            knowledge_retrieval_mode="hybrid",
            knowledge_vector_enabled=True,
            embedding_provider="openai",
            embedding_model="shared-embedding-model",
            model_openai_api_key="test-key",
            knowledge_embedding_version="shared-v1",
        )
    )

    assert config.vector_ready is True


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported knowledge retrieval mode"):
        retrieval_config_from_settings(
            isolated_settings(knowledge_retrieval_mode="semantic")
        )


def test_limits_are_bounded_and_results_never_exceed_candidates() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            knowledge_max_candidates=MAX_CANDIDATES_LIMIT + 1,
            knowledge_max_results=MAX_RESULTS_LIMIT + 1,
        )
    )
    assert config.max_candidates == MAX_CANDIDATES_LIMIT
    assert config.max_results == MAX_RESULTS_LIMIT

    small = retrieval_config_from_settings(
        isolated_settings(knowledge_max_candidates=3, knowledge_max_results=50)
    )
    assert small.max_candidates == 3
    assert small.max_results == 3


def test_vector_mode_without_fallback_fails_closed() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            knowledge_retrieval_mode="vector",
            knowledge_vector_enabled=True,
            knowledge_vector_fallback=False,
        )
    )

    with pytest.raises(ValueError, match="configuration is incomplete"):
        _ = config.effective_mode


def test_config_factory_does_not_create_external_clients_in_test_mode() -> None:
    config = retrieval_config_from_settings(
        isolated_settings(
            test_mode=True,
            knowledge_vector_enabled=True,
            embedding_provider="openai",
            knowledge_embedding_model="text-embedding-test",
            knowledge_embedding_version="v1",
            embedding_api_key="test-key",
        )
    )

    assert config.vector_ready is True
    assert config.effective_mode == "lexical"
