from uuid import uuid4

from project_lens.agent.draft import AnswerDraft, DraftFact
from project_lens.agent.hermes_answer import project_answer_from_hermes
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.hermes_loop_runner import HermesLoopToolCall


def test_hermes_answer_uses_only_returned_citation_ids() -> None:
    evidence_id = uuid4()
    answer = project_answer_from_hermes(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        draft=AnswerDraft(
            facts=[DraftFact(text="owner is Ada", citations=[str(evidence_id)])]
        ),
        tool_calls=(
            HermesLoopToolCall(
                name="projectlens_search_context",
                arguments={},
                envelope={
                    "evidence_refs": [
                        {
                            "id": str(evidence_id),
                            "kind": "code",
                            "source_uri": "github:src/order.py",
                            "summary": "owner assignment",
                        }
                    ]
                },
            ),
        ),
    )

    assert answer.claims[0].evidence_ids == (evidence_id,)
    assert answer.evidence[0].metadata["citation_only"] is True


def test_hermes_answer_demotes_unknown_citation_to_unknown() -> None:
    answer = project_answer_from_hermes(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        draft=AnswerDraft(
            facts=[DraftFact(text="owner is Ada", citations=[str(uuid4())])]
        ),
        tool_calls=(),
    )

    assert answer.facts == ()
    assert answer.unknowns
