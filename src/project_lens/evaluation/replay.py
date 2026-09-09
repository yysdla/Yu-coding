"""Legacy incident replay helpers.

``RunService.execute`` is Hermes-only and raises; prefer HermesRuntimeService
or unit-level answer_metrics checks for evaluation.
"""

from __future__ import annotations

from collections.abc import Sequence

from project_lens.application.run_service import RunService
from project_lens.domain.models import ProjectRef, RunStatus
from project_lens.evaluation.answer_metrics import answer_metrics


async def replay_question(
    service: RunService,
    *,
    project: ProjectRef,
    user_id: str,
    question: str,
) -> dict[str, object]:
    run = service.create(project=project, user_id=user_id, question=question)
    completed = await service.execute(run.id)
    if completed is None:
        return {"status": "missing", "run_id": str(run.id)}
    result: dict[str, object] = {
        "status": completed.status.value,
        "run_id": str(completed.id),
        "trace_id": str(completed.trace_id),
    }
    if completed.status == RunStatus.COMPLETED and completed.answer is not None:
        result["metrics"] = answer_metrics(completed.answer)
    else:
        result["error"] = completed.error or "workflow did not complete"
    return result


async def replay_cases(
    service: RunService,
    *,
    project: ProjectRef,
    user_id: str,
    cases: Sequence[dict[str, str]],
) -> dict[str, object]:
    results = [
        await replay_question(
            service,
            project=project,
            user_id=user_id,
            question=case["question"],
        )
        for case in cases
    ]
    completed = [item for item in results if item["status"] == RunStatus.COMPLETED.value]
    return {
        "case_count": len(cases),
        "completed_count": len(completed),
        "completion_rate": len(completed) / len(cases) if cases else 0.0,
        "cases": results,
    }
