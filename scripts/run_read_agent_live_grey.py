"""Run read_agent Step-2 Safe Live grey acceptance against local API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "feishu-read-agent-grey-test-results-live.json"

QUESTIONS = (
    "order_service.py 里 create_order 为什么要检查 coupon？",
    "这个项目里哪个文件最像订单创建入口？",
    "README/架构文档和代码入口是否对应？",
    "最近谁改过订单创建相关代码？",
)

PROJECT = {
    "tenant_id": "demo",
    "project_id": "payment",
    "service": "order-service",
    "environment": "production",
}


def _request(method: str, url: str, payload: dict | None = None) -> dict | list:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_meta(events: list[dict]) -> tuple[str | None, str | None, list[str], bool]:
    agent_mode = None
    provider = None
    tools: list[str] = []
    fallback = False
    for event in events:
        payload = event.get("payload") or {}
        if payload.get("agent_mode"):
            agent_mode = str(payload["agent_mode"])
        if isinstance(payload.get("tool_names"), list):
            tools.extend(str(item) for item in payload["tool_names"])
        if payload.get("tool"):
            tools.append(str(payload["tool"]))
        model_adapter = payload.get("model_adapter") or {}
        if model_adapter.get("provider"):
            provider = str(model_adapter["provider"])
        provider_meta = payload.get("provider")
        if isinstance(provider_meta, dict):
            if provider_meta.get("investigation_provider"):
                provider = str(provider_meta["investigation_provider"])
            if provider_meta.get("fallback_from"):
                fallback = True
        if payload.get("fallback_to_stub") or payload.get("fallback_from"):
            fallback = True
        lifecycle = str(payload.get("lifecycle") or payload.get("lifecycle_type") or "")
        if "provider_failed" in lifecycle or lifecycle.endswith("model.provider_failed"):
            fallback = True
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for name in tools:
        if name not in seen:
            seen.add(name)
            uniq.append(name)
    return agent_mode, provider, uniq, fallback


def main() -> None:
    cases: list[dict] = []
    for question in QUESTIONS:
        created = _request(
            "POST",
            "http://127.0.0.1:8000/api/v1/runs",
            {
                "project": PROJECT,
                "user_id": "grey-live",
                "channel_id": "oc_2a853a543e7e5b004a3a0bf15be52ff8",
                "question": question,
            },
        )
        run_id = created["run_id"]
        # execute may take longer under live LLM
        run = _request("POST", f"http://127.0.0.1:8000/api/v1/runs/{run_id}/execute")
        # if still accepted somehow, poll
        for _ in range(60):
            if run.get("status") in {"completed", "failed", "canceled"}:
                break
            time.sleep(1)
            run = _request("GET", f"http://127.0.0.1:8000/api/v1/runs/{run_id}")
        events = _request("GET", f"http://127.0.0.1:8000/api/v1/runs/{run_id}/events")
        assert isinstance(events, list)
        agent_mode, provider, tools, fallback = _extract_meta(events)
        answer = run.get("answer") or {}
        facts = [c for c in (answer.get("claims") or []) if c.get("type") == "fact"]
        uncited = sum(1 for c in facts if not c.get("evidence_ids"))
        summary = str(answer.get("business_summary") or "")
        cases.append(
            {
                "question": question,
                "run_id": run_id,
                "status": run.get("status"),
                "skill": answer.get("skill"),
                "agent_mode": agent_mode,
                "provider": provider,
                "fallback": fallback,
                "tools": tools,
                "fact_count": len(facts),
                "uncited_facts": uncited,
                "evidence_count": len(answer.get("evidence") or []),
                "unknowns": answer.get("unknowns") or [],
                "has_zero_conf_boilerplate": "置信度 0%" in summary,
                "summary": summary,
            }
        )
        print(
            f"OK skill={answer.get('skill')} provider={provider} "
            f"fallback={fallback} tools={tools}"
        )

    accept = {
        "all_project_investigation": all(
            c["skill"] == "project_investigation" for c in cases
        ),
        "all_agent_mode_read_agent": all(c["agent_mode"] == "read_agent" for c in cases),
        "at_least_one_search_plus_file": any(
            "search_context" in c["tools"]
            and (
                "read_project_file" in c["tools"]
                or "read_project_file_range" in c["tools"]
                or "grep_project_code" in c["tools"]
                or "search_project_code" in c["tools"]
            )
            for c in cases
        ),
        "no_uncited_facts": all(c["uncited_facts"] == 0 for c in cases),
        "no_zero_conf_boilerplate": all(
            not c["has_zero_conf_boilerplate"] for c in cases
        ),
        "provider_openai_or_stub_fallback": all(
            c["provider"] in {"openai_loop", "stub_planner"} for c in cases
        ),
        "at_least_one_openai_loop": any(c["provider"] == "openai_loop" for c in cases),
    }
    accept["passed"] = all(
        [
            accept["all_project_investigation"],
            accept["all_agent_mode_read_agent"],
            accept["at_least_one_search_plus_file"],
            accept["no_uncited_facts"],
            accept["no_zero_conf_boilerplate"],
            accept["provider_openai_or_stub_fallback"],
        ]
    )
    feishu_status = _request(
        "GET", "http://127.0.0.1:8000/api/v1/integrations/feishu/status"
    )
    payload = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "read_agent+openai_live",
        "feishu_status": feishu_status,
        "acceptance": accept,
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(accept, ensure_ascii=False, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
