"""RoleView projection tests — presentation only, no new facts."""

from __future__ import annotations

from project_lens.application.role_views import (
    build_role_view,
    render_role_view_markdown,
)


def _sample_envelope() -> dict:
    return {
        "ok": True,
        "answer_summary": "This project handles order payments.",
        "facts": [
            {
                "text": (
                    "API entrypoint is app.py. "
                    "[11111111-1111-1111-1111-111111111111, "
                    "22222222-2222-2222-2222-222222222222]"
                ),
                "citations": [
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                ],
            },
            {
                "text": "Payment logic is in services/payment.py.",
                "citations": ["33333333-3333-3333-3333-333333333333"],
            },
            {
                "text": "Coupon checks happen before charge.",
                "citations": ["44444444-4444-4444-4444-444444444444"],
            },
        ],
        "inferences": ["Likely a checkout-related service."],
        "unknowns": ["No deployment runbook was found."],
        "next_actions": [{"title": "补充 runbook", "requires_approval": False}],
        "citations": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "kind": "file",
                "source_uri": "code:app.py",
                "summary": "app.py snippet",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "kind": "file",
                "source_uri": "code:services/payment.py",
                "summary": "payment snippet",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "kind": "doc",
                "source_uri": "doc:README.md",
                "summary": "README snippet",
            },
        ],
        "audit_ref": {
            "trace_id": "trace-1",
            "run_id": "run-1",
            "tool_names": ["search_context", "read_project_file"],
            "allow_apply": False,
        },
    }


def test_team_first_screen_omits_citation_uuid_lists() -> None:
    markdown = render_role_view_markdown(_sample_envelope(), audience="team")

    assert "结论" in markdown
    assert "This project handles order payments." in markdown
    assert "API entrypoint is app.py." in markdown
    assert "11111111-1111-1111-1111-111111111111" not in markdown
    assert "code:app.py" not in markdown
    assert "已基于 3 条项目资料" in markdown
    assert "/project-role evidence" in markdown
    assert "allow_apply=false" in markdown
    assert "search_context" not in markdown


def test_evidence_view_includes_sources() -> None:
    markdown = render_role_view_markdown(_sample_envelope(), audience="evidence")

    assert "来源" in markdown
    assert "code:app.py" in markdown
    assert "doc:README.md" in markdown
    assert "search_context" in markdown


def test_audience_switch_changes_text_keeps_fact_set() -> None:
    envelope = _sample_envelope()
    team = build_role_view(envelope, audience="team")
    technical = build_role_view(envelope, audience="technical")
    business = build_role_view(envelope, audience="business")

    team_md = render_role_view_markdown(envelope, audience="team")
    tech_md = render_role_view_markdown(envelope, audience="technical")
    biz_md = render_role_view_markdown(envelope, audience="business")

    assert team.title != technical.title
    assert "团队协作视图" in team_md
    assert "技术视图" in tech_md
    assert "业务/产品视图" in biz_md
    assert team_md != tech_md

    # Same underlying facts; presentation may truncate / soften paths.
    team_core = {item.split(".")[0] for item in team.confirmed_points}
    tech_core = {item.split(".")[0] for item in technical.confirmed_points}
    assert team_core.issubset(tech_core) or tech_core.intersection(team_core)
    assert all("11111111" not in item for item in team.confirmed_points)
    assert business.conclusion
    assert "Likely a checkout" in "\n".join(technical.inference_lines)
