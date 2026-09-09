from project_lens.workflow.skills import is_owner_lookup_question


def test_is_owner_lookup_question_detects_shortcuts_and_rewrites() -> None:
    assert is_owner_lookup_question("负责人是谁")
    assert is_owner_lookup_question("谁负责 order-service")
    assert is_owner_lookup_question(
        "这个项目的负责人是谁？如果证据不足，请说明缺少哪些负责人资料。"
    )
    assert not is_owner_lookup_question("项目地图")
    assert not is_owner_lookup_question("知识库缺什么")
