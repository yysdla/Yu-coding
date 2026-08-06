"""SkillGuide: optional thinking checklist for investigation — not a routing bucket."""

from __future__ import annotations

from dataclasses import dataclass

from project_lens.context.knowledge_gaps import is_knowledge_gap_question
from project_lens.workflow.skills import is_owner_lookup_question, is_project_intro_question


@dataclass(frozen=True)
class SkillGuide:
    name: str
    checklist: tuple[str, ...]
    output_hint: str

    def to_prompt_block(self) -> str:
        lines = [f"Optional SkillGuide: {self.name}", "Checklist:"]
        lines.extend(f"- {item}" for item in self.checklist)
        lines.append(f"Output hint: {self.output_hint}")
        return "\n".join(lines)


PROJECT_INTRO_GUIDE = SkillGuide(
    name="project_intro",
    checklist=(
        "search README / project overview docs",
        "list core services and entrypoints",
        "look up owners if available",
        "note recent changes if evidence exists",
        "list knowledge gaps if still unclear",
    ),
    output_hint="Produce a project overview with cited facts and unknowns.",
)

INCIDENT_GUIDE = SkillGuide(
    name="incident",
    checklist=(
        "search error / traceback keywords",
        "read related code files when path is known",
        "query graph for related services / commits if useful",
        "separate facts vs inferences; time correlation is not causation",
    ),
    output_hint="Return facts / inferences / unknowns / next investigation steps.",
)

FREE_QUESTION_GUIDE = SkillGuide(
    name="free_question",
    checklist=(
        "identify the object the user asks about",
        "search_context with key terms",
        "list_project_files / grep_project_code / search_project_code for navigation",
        "read_project_file or read_project_file_range when a path/filename appears",
        "query_graph only if relationships are needed",
        "if evidence is missing, state what was searched and what is still needed",
    ),
    output_hint="Answer freely with citations; never force a fixed skill bucket.",
)

_INCIDENT_MARKERS = (
    "报错",
    "故障",
    "异常",
    "traceback",
    "exception",
    "error",
    "failed",
    "AttributeError",
)


def select_skill_guide(question: str) -> SkillGuide:
    """Suggest a guide for the planner. Never blocks free questions."""

    normalized = question.casefold()
    if is_project_intro_question(question):
        return PROJECT_INTRO_GUIDE
    if is_knowledge_gap_question(question) or is_owner_lookup_question(question):
        return FREE_QUESTION_GUIDE
    if any(marker.casefold() in normalized for marker in _INCIDENT_MARKERS):
        return INCIDENT_GUIDE
    return FREE_QUESTION_GUIDE
