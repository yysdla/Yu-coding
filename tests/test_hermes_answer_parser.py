from project_lens.agent.hermes_answer_parser import parse_hermes_answer


def test_parse_structured_hermes_answer_keeps_citations_for_later_verification() -> None:
    draft = parse_hermes_answer(
        '{"facts":[{"text":"owner is Ada","citations":["ev-1"]}],'
        '"inferences":["may affect checkout"],"next_actions":["confirm"]}'
    )

    assert draft.facts[0].text == "owner is Ada"
    assert draft.facts[0].citations == ["ev-1"]
    assert draft.inferences == ["may affect checkout"]


def test_parse_plain_text_does_not_promote_text_to_verified_fact() -> None:
    draft = parse_hermes_answer("The owner is probably Ada.")

    assert draft.facts == []
    assert draft.conclusion == "The owner is probably Ada."
    assert draft.unknowns


def test_parse_empty_hermes_answer_is_unknown() -> None:
    draft = parse_hermes_answer(" \n ")

    assert draft.facts == []
    assert draft.conclusion == ""
    assert draft.unknowns == ["Hermes 未返回最终答案。"]
