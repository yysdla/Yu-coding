"""Phase D curated harness replay sets.

Each set answers: if the answer is wrong, can we replay the conversation path?
"""

from __future__ import annotations

from dataclasses import dataclass

from project_lens.evaluation.harness_replay import ReplayStep

TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


@dataclass(frozen=True)
class HarnessReplaySet:
    name: str
    description: str
    steps: tuple[ReplayStep, ...]
    expect_skills: tuple[str, ...] = ()
    expect_followup_rewrite: bool = False
    require_compression_cycle: bool = False
    expect_authorized_retrieval: bool = False


PHASE_D_REPLAY_SETS: dict[str, HarnessReplaySet] = {
    "project_intro": HarnessReplaySet(
        name="project_intro",
        description="介绍一下这个项目 — Evidence-backed project intro path",
        steps=(ReplayStep(question="介绍一下这个项目"),),
        expect_skills=("project_knowledge", "architecture"),
        expect_authorized_retrieval=True,
    ),
    "knowledge_gap": HarnessReplaySet(
        name="knowledge_gap",
        description="知识库缺什么 — gap report with evidence trail",
        steps=(ReplayStep(question="知识库缺什么"),),
        expect_skills=("project_knowledge", "architecture"),
        expect_authorized_retrieval=True,
    ),
    "graph_path": HarnessReplaySet(
        name="graph_path",
        description="项目地图 — architecture navigation with graph paths",
        # Use Feishu rewrite text so skill routing hits architecture (含「架构/依赖」).
        steps=(
            ReplayStep(
                question="请解释这个项目的架构、服务、入口、依赖和风险，形成项目地图。"
            ),
        ),
        expect_skills=("architecture",),
        expect_authorized_retrieval=True,
    ),
    "multi_turn_followup": HarnessReplaySet(
        name="multi_turn_followup",
        description="故障多轮追问 — session + FollowupRewriter + compression",
        steps=(
            ReplayStep(question=TRACEBACK),
            ReplayStep(question="那是谁改的？"),
            ReplayStep(question="影响哪里？"),
        ),
        expect_skills=("incident_diagnosis",),
        expect_followup_rewrite=True,
        require_compression_cycle=True,
    ),
}


def list_phase_d_replay_sets() -> tuple[HarnessReplaySet, ...]:
    return tuple(PHASE_D_REPLAY_SETS[name] for name in sorted(PHASE_D_REPLAY_SETS))


def get_replay_set(name: str) -> HarnessReplaySet:
    try:
        return PHASE_D_REPLAY_SETS[name]
    except KeyError as exc:
        raise KeyError(f"unknown harness replay set: {name}") from exc
