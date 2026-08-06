"""Skill-scoped graph queries issued only through ContextEngine."""

from __future__ import annotations

from project_lens.graph.models import GraphNodeKind
from project_lens.graph.query import GraphQuery
from project_lens.workflow.skills import (
    ProjectSkill,
    is_owner_lookup_question,
    is_project_intro_question,
)


def graph_queries_for_question(
    skill: ProjectSkill,
    question: str,
) -> tuple[GraphQuery, ...]:
    """Return read-only graph queries that may enrich the current skill."""

    if (
        is_owner_lookup_question(question)
        or is_project_intro_question(question)
        or skill == ProjectSkill.ARCHITECTURE
    ):
        queries = [
            GraphQuery(
                start_kind=GraphNodeKind.OWNER,
                end_kind=GraphNodeKind.SERVICE,
                relation="responsible_for",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.SERVICE,
                end_kind=GraphNodeKind.MODULE,
                relation="owns_module",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.SERVICE,
                end_kind=GraphNodeKind.SERVICE,
                relation="depends_on",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.SERVICE,
                end_kind=GraphNodeKind.ENDPOINT,
                relation="exposes",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.DOCUMENT,
                end_kind=GraphNodeKind.MODULE,
                relation="describes",
                max_depth=2,
                limit=5,
            ),
        ]
        return tuple(queries)
    if skill == ProjectSkill.INCIDENT_DIAGNOSIS:
        return (
            GraphQuery(
                start_kind=GraphNodeKind.INCIDENT,
                end_kind=GraphNodeKind.SERVICE,
                relation="affects",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.SERVICE,
                end_kind=GraphNodeKind.ENDPOINT,
                relation="exposes",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.ENDPOINT,
                end_kind=GraphNodeKind.MODULE,
                relation="implemented_by",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.ENDPOINT,
                end_kind=GraphNodeKind.SYMBOL,
                relation="implemented_by",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.TASK,
                end_kind=GraphNodeKind.INCIDENT,
                relation="tracks",
                max_depth=2,
                limit=5,
            ),
        )
    if skill == ProjectSkill.VERSION_CHANGE:
        return (
            GraphQuery(
                start_kind=GraphNodeKind.RELEASE,
                end_kind=GraphNodeKind.COMMIT,
                relation="includes",
                max_depth=2,
                limit=5,
            ),
            GraphQuery(
                start_kind=GraphNodeKind.COMMIT,
                end_kind=GraphNodeKind.SERVICE,
                max_depth=3,
                limit=5,
            ),
        )
    return ()
