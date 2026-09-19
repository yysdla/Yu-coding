"""CLI for local deterministic retrieval evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_lens.config import Settings
from project_lens.context.bootstrap import LocalContextSources, build_local_context_engine
from project_lens.context.retrieval.config import retrieval_config_from_settings
from project_lens.context.retrieval.vector import EvidenceVectorRetriever
from project_lens.evaluation.release_report import build_retrieval_report, write_report
from project_lens.evaluation.answer_evaluator import evaluate_answer_citations
from project_lens.evaluation.retrieval_cases import load_retrieval_cases
from project_lens.evaluation.retrieval_runner import run_retrieval_modes
from project_lens.evaluation.security_evaluator import evaluate_retrieval_security
from project_lens.evaluation.temporal_evaluator import evaluate_temporal_retrieval


class DeterministicProvider:
    """Local provider for pipeline smoke tests; not a quality benchmark."""

    model_version = "deterministic-eval-v1"

    def embed(self, texts):
        return tuple(
            (1.0, 0.0)
            if any(token in text.casefold() for token in ("payment", "支付", "coupon"))
            else (0.0, 1.0)
            for text in texts
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--project-id", default="payment")
    parser.add_argument("--tenant-id", default="demo")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--answers",
        type=Path,
        help="JSON/JSONL answer replay records; omitted means answer evaluation is not run",
    )
    parser.add_argument("--deterministic-vector", action="store_true")
    args = parser.parse_args()

    cases = load_retrieval_cases(args.dataset)
    project_root = args.project_root
    project = {
        "tenant_id": args.tenant_id,
        "project_id": args.project_id,
    }

    from project_lens.domain.models import ProjectRef

    def typed_factory(mode: str):
        settings = Settings(
            _env_file=None,
            knowledge_retrieval_mode=mode,
            knowledge_vector_enabled=args.deterministic_vector,
            embedding_provider="fake" if args.deterministic_vector else "none",
            knowledge_embedding_model="deterministic-eval"
            if args.deterministic_vector
            else None,
            knowledge_embedding_version="deterministic-eval-v1"
            if args.deterministic_vector
            else None,
        )
        vector = (
            EvidenceVectorRetriever(DeterministicProvider())
            if args.deterministic_vector
            else None
        )
        engine, _ = build_local_context_engine(
            LocalContextSources(
                repository_root=project_root / "examples" / "payment_service" / "src",
                documents_root=project_root / "examples" / "payment_service" / "knowledge",
                incidents_file=(
                    project_root / "examples" / "payment_service" / "knowledge" / "incidents.json"
                ),
                tasks_file=(
                    project_root / "examples" / "payment_service" / "knowledge" / "tasks.json"
                ),
                releases_file=(
                    project_root / "examples" / "payment_service" / "knowledge" / "releases.json"
                ),
            ),
            project=ProjectRef(**project),
            access_scope=f"project:{args.project_id}:read",
            retrieval_config=retrieval_config_from_settings(settings),
            vector_retriever=vector,
        )
        return engine

    runs = run_retrieval_modes(cases, engine_factory=typed_factory, limit=10)
    security = evaluate_retrieval_security(cases, runs["hybrid"].cases)
    temporal = evaluate_temporal_retrieval(cases, runs["hybrid"])
    answer_metrics = None
    answer_failures = None
    if args.answers:
        answers = _load_records(args.answers)
        answer_evaluation = evaluate_answer_citations(cases, answers)
        answer_metrics = answer_evaluation.metrics
        answer_failures = list(answer_evaluation.failures)
    report = build_retrieval_report(
        project_root=project_root,
        dataset_path=args.dataset,
        runs=runs,
        security=security,
        gates_path=project_root / "evaluation" / "configs" / "gates.json",
        retrieval_config={
            "modes": ["lexical", "vector", "hybrid"],
            "deterministic_vector": args.deterministic_vector,
        },
        answer_metrics=answer_metrics,
        temporal_metrics=temporal.metrics,
        answer_failures=answer_failures,
        temporal_failures=list(temporal.failures),
    )
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _load_records(path: Path) -> list[dict]:
    if path.suffix.casefold() in {".jsonl", ".ndjson"}:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("answers", payload.get("records"))
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("answer replay must be a JSON array or JSONL objects")
    return payload


if __name__ == "__main__":
    main()
