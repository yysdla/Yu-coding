import json

import pytest

from project_lens.evaluation.retrieval_cases import (
    dataset_fingerprint,
    load_retrieval_cases,
)


def test_retrieval_cases_accept_legacy_relevant_sources(tmp_path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "one",
                    "tenant_id": "t1",
                    "project_id": "p1",
                    "query": "where",
                    "relevant_sources": ["doc#1"],
                }
            ]
        ),
        encoding="utf-8",
    )
    cases = load_retrieval_cases(path)
    assert cases[0].relevant_source_keys == ("doc#1",)
    assert len(dataset_fingerprint(path)) == 64


def test_retrieval_cases_reject_duplicates_and_overlap(tmp_path) -> None:
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        '{"id":"x","tenant_id":"t","project_id":"p","query":"q","relevant_source_keys":["s"]}\n'
        '{"id":"x","tenant_id":"t","project_id":"p","query":"q","relevant_source_keys":["s"]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_retrieval_cases(duplicate)

    overlap = tmp_path / "overlap.json"
    overlap.write_text(
        json.dumps(
            [
                {
                    "id": "x",
                    "tenant_id": "t",
                    "project_id": "p",
                    "query": "q",
                    "relevant_object_ids": ["same"],
                    "forbidden_object_ids": ["same"],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="overlap"):
        load_retrieval_cases(overlap)
