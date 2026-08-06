"""Run the deterministic local ProjectLens context benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_lens.context.bootstrap import LocalContextSources, build_local_context_engine
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import ProjectRef
from project_lens.evaluation.retrieval import mean_reciprocal_rank, recall_at_k


def evaluate(project_root: Path, dataset_path: Path, *, k: int = 5) -> dict:
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    access_scope = "project:payment:read"
    engine, index = build_local_context_engine(
        LocalContextSources(
            repository_root=project_root / "src",
            documents_root=project_root / "knowledge",
            incidents_file=project_root / "knowledge" / "incidents.json",
        ),
        project=project,
        access_scope=access_scope,
    )
    access = AccessContext(
        tenant_id="demo",
        user_id="evaluator",
        permissions=frozenset({access_scope}),
    )
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    reciprocal_cases = []
    per_case = []
    recalls = []
    for case in cases:
        bundle = engine.search(
            ContextQuery(text=case["query"], project=project, limit=k),
            access,
        )
        retrieved = [hit.evidence.source.source_id for hit in bundle.hits]
        relevant = set(case["relevant_sources"])
        recall = recall_at_k(retrieved, relevant, k)
        recalls.append(recall)
        reciprocal_cases.append((retrieved, relevant))
        per_case.append(
            {
                "id": case["id"],
                "retrieved": retrieved,
                "relevant": sorted(relevant),
                "recall_at_k": recall,
            }
        )
    return {
        "case_count": len(cases),
        "indexed_evidence": len(index),
        "k": k,
        "recall_at_k": sum(recalls) / len(recalls) if recalls else 0.0,
        "mrr": mean_reciprocal_rank(reciprocal_cases),
        "cases": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.project_root, args.dataset, k=args.k), indent=2))


if __name__ == "__main__":
    main()

