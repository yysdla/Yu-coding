"""Project introduction skill routing (Hermes-compatible helpers)."""

from project_lens.workflow.skills import (
    ProjectSkill,
    classify_project_question,
    is_project_intro_question,
)


def test_is_project_intro_question_detects_common_asks() -> None:
    assert is_project_intro_question("介绍一下这个项目")
    assert is_project_intro_question("这个项目是做什么的")
    assert is_project_intro_question("这个项目有哪些核心模块")
    assert is_project_intro_question(
        "请介绍一下这个项目：项目定位、核心服务、关键入口、主要文档、"
        "负责人、最近变更、风险和知识缺口。"
    )
    assert not is_project_intro_question("项目地图")
    assert not is_project_intro_question("知识库缺什么")
    assert not is_project_intro_question("负责人是谁")


def test_intro_questions_route_to_project_knowledge_not_architecture() -> None:
    assert classify_project_question("介绍一下这个项目") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("这个项目有哪些核心模块") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("这个项目是做什么的") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("请解释这个项目的架构和服务依赖") == ProjectSkill.ARCHITECTURE
