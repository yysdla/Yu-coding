"""Phase 4.5/4.6: Feishu RoleView interactive cards + in-place patch."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from project_lens.application.role_views import build_role_view, render_role_view_markdown
from project_lens.integrations.hermes_plugin.cards import (
    build_role_view_card,
    role_button_value,
)
from project_lens.integrations.hermes_plugin.commands import (
    ProjectLensCommands,
    reset_last_run_id_for_tests,
)
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.feishu_client import FeishuCardClient
from project_lens.integrations.hermes_plugin.gateway_hook import (
    pre_gateway_dispatch_card_hook,
)


class FakeAskClient:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "ok": True,
            "run_id": "run-card-1",
            "answer_summary": "Payments land in order service.",
            "facts": [
                {"text": "Entrypoint is app.py.", "citations": ["ev-1"]},
                {"text": "Core module is services/payment.py.", "citations": ["ev-2"]},
            ],
            "unknowns": ["No runbook."],
            "next_actions": [{"title": "补充 runbook"}],
            "citations": [
                {
                    "id": "ev-1",
                    "kind": "file",
                    "source_uri": "code:app.py",
                    "summary": "app",
                }
            ],
            "audit_ref": {"allow_apply": False, "run_id": "run-card-1"},
        }
        self.requests: list[dict[str, Any]] = []
        self.role_view_calls: list[tuple[str, str]] = []

    def ask_project(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return self.response

    def role_view(self, run_id: str, audience: str) -> dict[str, Any]:
        self.role_view_calls.append((run_id, audience))
        return {
            "ok": True,
            "run_id": run_id,
            "audience": audience,
            "answer_summary": "Payments land in order service.",
            "facts": self.response.get("facts") or [],
            "unknowns": self.response.get("unknowns") or [],
            "citations": self.response.get("citations") or [],
            "markdown": f"### replayed {audience}\n**结论**\nPayments land in order service.",
            "audit_ref": {"allow_apply": False, "run_id": run_id},
        }


class FakeCardPoster:
    def __init__(self, *, fail_post: bool = False, fail_patch: bool = False) -> None:
        self.fail_post = fail_post
        self.fail_patch = fail_patch
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self._seq = 0

    def post_interactive_card(self, *, chat_id: str, card: dict[str, Any]) -> str:
        if self.fail_post:
            raise RuntimeError("feishu down")
        self.posts.append((chat_id, card))
        self._seq += 1
        return f"om_msg_{self._seq}"

    def patch_interactive_card(self, *, message_id: str, card: dict[str, Any]) -> None:
        if self.fail_patch:
            raise RuntimeError("feishu patch down")
        self.patches.append((message_id, card))


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if "tenant_access_token" in url:
            return 200, {"tenant_access_token": "tok-1", "expire": 7200}
        if method == "POST" and "im/v1/messages" in url:
            return 200, {"code": 0, "data": {"message_id": "om_created_1"}}
        return 200, {"code": 0, "msg": "ok"}


def test_role_view_card_buttons_are_tech_evidence_map() -> None:
    markdown = (
        "### 团队协作视图\n"
        "**结论**\nPayments land in order service.\n\n"
        "**已确认**\n- Entrypoint is app.py."
    )
    card = build_role_view_card(
        markdown=markdown,
        run_id="run-42",
        current_audience="team",
        message_id="om_1",
    )

    assert card["config"]["wide_screen_mode"] is True
    assert card["config"]["update_multi"] is True
    assert "Payments land in order service." in card["elements"][0]["content"]
    assert "code:app.py" not in card["elements"][0]["content"]

    action = card["elements"][1]
    values = [item["value"] for item in action["actions"]]
    audiences = {item["audience"] for item in values}
    assert audiences == {"technical", "evidence", "map"}
    assert all(item["action"] == "projectlens_role" for item in values)
    assert all(item["run_id"] == "run-42" for item in values)
    assert all(item["message_id"] == "om_1" for item in values)
    assert role_button_value(audience="map", run_id="run-42", message_id="om_1") in values


def test_map_role_view_prefers_module_facts() -> None:
    envelope = {
        "ok": True,
        "answer_summary": "Order service handles checkout.",
        "facts": [
            {"text": "Entrypoint is src/app.py.", "citations": ["a"]},
            {"text": "Business summary only.", "citations": ["b"]},
        ],
        "unknowns": [],
        "citations": [],
        "audit_ref": {"allow_apply": False},
    }
    view = build_role_view(envelope, audience="map")
    assert view.title == "项目地图"
    assert any("src/app.py" in item or "Entrypoint" in item for item in view.confirmed_points)
    md = render_role_view_markdown(envelope, audience="map")
    assert "项目地图" in md


def test_card_command_strips_button_prefix_and_replays() -> None:
    reset_last_run_id_for_tests()
    client = FakeAskClient()
    commands = ProjectLensCommands(client=client, config=ProjectLensPluginConfig())

    output = commands.card(
        'button {"action":"projectlens_role","audience":"business","run_id":"run-9"}'
    )

    assert client.role_view_calls == [("run-9", "business")]
    assert "replayed business" in output
    assert not client.requests


def test_feishu_card_client_posts_and_patches() -> None:
    transport = FakeTransport()
    client = FeishuCardClient(
        app_id="cli_x",
        app_secret="secret",
        transport=transport,
    )
    card = build_role_view_card(markdown="hello", run_id="run-1")
    message_id = client.post_interactive_card(chat_id="oc_chat", card=card)
    assert message_id == "om_created_1"

    stamped = build_role_view_card(markdown="hello", run_id="run-1", message_id=message_id)
    client.patch_interactive_card(message_id=message_id, card=stamped)

    methods = [call["method"] for call in transport.calls]
    assert methods.count("POST") >= 2  # token + create
    assert "PATCH" in methods
    patch_call = next(call for call in transport.calls if call["method"] == "PATCH")
    assert "om_created_1" in patch_call["url"]


def test_hook_posts_then_patches_message_id_on_outbound() -> None:
    reset_last_run_id_for_tests()
    ask = FakeAskClient()
    commands = ProjectLensCommands(client=ask, config=ProjectLensPluginConfig())
    poster = FakeCardPoster()
    event = SimpleNamespace(
        text="/project 这个项目是做什么的？",
        source=SimpleNamespace(platform=SimpleNamespace(value="feishu"), chat_id="oc_1"),
    )

    result = pre_gateway_dispatch_card_hook(
        event=event,
        commands=commands,
        config=ProjectLensPluginConfig(feishu_cards_enabled=True),
        card_poster=poster,
    )

    assert result == {"action": "skip", "reason": "projectlens_card_sent"}
    assert len(poster.posts) == 1
    assert len(poster.patches) == 1
    message_id, patched = poster.patches[0]
    assert message_id == "om_msg_1"
    values = [item["value"] for item in patched["elements"][1]["actions"]]
    assert all(item.get("message_id") == "om_msg_1" for item in values)
    assert {item["audience"] for item in values} == {"technical", "evidence", "map"}


def test_hook_patches_card_click_and_skips_markdown() -> None:
    reset_last_run_id_for_tests()
    ask = FakeAskClient()
    commands = ProjectLensCommands(client=ask, config=ProjectLensPluginConfig())
    poster = FakeCardPoster()
    event = SimpleNamespace(
        text=(
            '/card button {"action":"projectlens_role","audience":"map",'
            '"run_id":"run-card-1","message_id":"om_keep"}'
        ),
        source=SimpleNamespace(platform="feishu", chat_id="oc_1"),
        raw_message=None,
    )

    result = pre_gateway_dispatch_card_hook(
        event=event,
        commands=commands,
        config=ProjectLensPluginConfig(feishu_cards_enabled=True),
        card_poster=poster,
    )

    assert result == {"action": "skip", "reason": "projectlens_card_patched"}
    assert ask.role_view_calls == [("run-card-1", "map")]
    assert not poster.posts
    assert len(poster.patches) == 1
    mid, card = poster.patches[0]
    assert mid == "om_keep"
    assert "项目地图" in card["header"]["title"]["content"] or "map" in card["elements"][0][
        "content"
    ].lower() or "replayed map" in card["elements"][0]["content"]


def test_hook_card_click_uses_open_message_id_fallback() -> None:
    reset_last_run_id_for_tests()
    ask = FakeAskClient()
    commands = ProjectLensCommands(client=ask, config=ProjectLensPluginConfig())
    poster = FakeCardPoster()
    event = SimpleNamespace(
        text='/card button {"action":"projectlens_role","audience":"evidence","run_id":"run-card-1"}',
        source=SimpleNamespace(platform="feishu", chat_id="oc_1"),
        raw_message={
            "event": {"context": {"open_message_id": "om_from_raw", "open_chat_id": "oc_1"}}
        },
    )

    result = pre_gateway_dispatch_card_hook(
        event=event,
        commands=commands,
        config=ProjectLensPluginConfig(feishu_cards_enabled=True),
        card_poster=poster,
    )

    assert result == {"action": "skip", "reason": "projectlens_card_patched"}
    assert poster.patches[0][0] == "om_from_raw"


def test_hook_allows_when_card_post_fails() -> None:
    reset_last_run_id_for_tests()
    ask = FakeAskClient()
    commands = ProjectLensCommands(client=ask, config=ProjectLensPluginConfig())
    event = SimpleNamespace(
        text="/project-map",
        source=SimpleNamespace(platform="feishu", chat_id="oc_2"),
    )

    result = pre_gateway_dispatch_card_hook(
        event=event,
        commands=commands,
        config=ProjectLensPluginConfig(feishu_cards_enabled=True),
        card_poster=FakeCardPoster(fail_post=True),
    )

    assert result == {"action": "allow"}
    assert ask.requests


def test_hook_allows_card_click_when_patch_fails() -> None:
    reset_last_run_id_for_tests()
    ask = FakeAskClient()
    commands = ProjectLensCommands(client=ask, config=ProjectLensPluginConfig())
    event = SimpleNamespace(
        text=(
            '/card button {"action":"projectlens_role","audience":"technical",'
            '"run_id":"run-card-1","message_id":"om_x"}'
        ),
        source=SimpleNamespace(platform="feishu", chat_id="oc_1"),
    )

    result = pre_gateway_dispatch_card_hook(
        event=event,
        commands=commands,
        config=ProjectLensPluginConfig(feishu_cards_enabled=True),
        card_poster=FakeCardPoster(fail_patch=True),
    )

    assert result == {"action": "allow"}


def test_hook_noop_when_cards_disabled_or_non_feishu() -> None:
    reset_last_run_id_for_tests()
    ask = FakeAskClient()
    commands = ProjectLensCommands(client=ask, config=ProjectLensPluginConfig())
    poster = FakeCardPoster()
    event = SimpleNamespace(
        text="/project hello",
        source=SimpleNamespace(platform="feishu", chat_id="oc_1"),
    )

    assert (
        pre_gateway_dispatch_card_hook(
            event=event,
            commands=commands,
            config=ProjectLensPluginConfig(feishu_cards_enabled=False),
            card_poster=poster,
        )
        is None
    )
    cli_event = SimpleNamespace(
        text="/project hello",
        source=SimpleNamespace(platform="cli", chat_id="local"),
    )
    assert (
        pre_gateway_dispatch_card_hook(
            event=cli_event,
            commands=commands,
            config=ProjectLensPluginConfig(feishu_cards_enabled=True),
            card_poster=poster,
        )
        is None
    )
    assert not poster.posts
    assert not ask.requests
