"""Executable lexical retrieval baseline runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from project_lens.evaluation.run_context import evaluate


def load_baseline_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    retrieval = config.get("retrieval")
    if not isinstance(retrieval, dict):
        raise ValueError("baseline config must contain a retrieval object")
    if retrieval.get("mode") != "lexical" or retrieval.get("vector_enabled") is not False:
        raise ValueError("stage 0 baseline must remain lexical with vector disabled")
    return config


def run_baseline(
    project_root: Path,
    *,
    config_path: Path,
    dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Run the current BM25 replay and attach reproducibility metadata."""

    config = load_baseline_config(config_path)
    configured_dataset = config["dataset"]["path"]
    dataset = dataset_path or Path(config_path.parents[2] / configured_dataset)
    k = int(config["retrieval"]["max_results"])
    report = evaluate(project_root, dataset, k=k)
    canonical = json.dumps(config, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return {
        "schema_version": "retrieval-evaluation-report.v1",
        "baseline_config": str(config_path),
        "baseline_config_sha256": hashlib.sha256(canonical).hexdigest(),
        "retrieval": config["retrieval"],
        "dataset": str(dataset),
        "report": report,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_baseline(
                args.project_root,
                config_path=args.config,
                dataset_path=args.dataset,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
