#!/usr/bin/env python3
"""Inject one Feishu grey-test event into the live local server."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def _sign(*, body: bytes, timestamp: str, nonce: str, signing_secret: str) -> str:
    content = f"{timestamp}{nonce}{signing_secret}".encode() + body
    return hashlib.sha256(content).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--question",
        default="这个项目里哪个文件最像订单创建入口？",
        help="Free question to inject into the bound Feishu chat",
    )
    args = parser.parse_args()

    env = _load_env(ENV_PATH)
    token = env["PROJECT_LENS_FEISHU_VERIFICATION_TOKEN"]
    signing_secret = env.get("PROJECT_LENS_FEISHU_SIGNING_SECRET") or ""
    bindings = json.loads(env["PROJECT_LENS_FEISHU_PROJECT_BINDINGS"])
    binding = bindings["bindings"][0]
    event_id = f"grey-hermes-{uuid.uuid4().hex[:12]}"
    text = args.question
    payload = {
        "schema": "2.0",
        "token": token,
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "tenant_key": binding["tenant_key"],
        },
        "event": {
            # Current Feishu ingress requires the trusted open_id field.
            "sender": {"sender_id": {"open_id": "u1"}},
            "message": {
                "message_id": f"msg-{event_id}",
                "chat_id": binding["chat_id"],
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex[:16]
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if signing_secret:
        headers["X-Lark-Request-Timestamp"] = timestamp
        headers["X-Lark-Request-Nonce"] = nonce
        headers["X-Lark-Signature"] = _sign(
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            signing_secret=signing_secret,
        )
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/feishu/events",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        accepted = json.loads(resp.read().decode("utf-8"))
    print("accepted_status=", accepted.get("status"))
    print("run_id=", accepted.get("run_id"))
    run_id = accepted.get("run_id")
    if not run_id:
        raise SystemExit(1)

    run: dict = {}
    for _ in range(90):
        with urllib.request.urlopen(
            f"http://127.0.0.1:8000/api/v1/runs/{run_id}", timeout=15
        ) as resp:
            run = json.loads(resp.read().decode("utf-8"))
        if run.get("status") in {"completed", "failed", "canceled"}:
            break
        time.sleep(0.5)

    answer = run.get("answer") or {}
    print("run_status=", run.get("status"))
    print("skill=", answer.get("skill"))
    print("evidence_count=", len(answer.get("evidence") or []))
    print("summary_prefix=", (answer.get("business_summary") or "")[:100])

    with urllib.request.urlopen(
        f"http://127.0.0.1:8000/api/v1/runs/{run_id}/events", timeout=15
    ) as resp:
        events = json.loads(resp.read().decode("utf-8"))
    agent_mode = None
    provider = None
    fallback_reason = None
    tools: set[str] = set()
    for ev in events:
        payload_ev = ev.get("payload") or {}
        if payload_ev.get("agent_mode"):
            agent_mode = payload_ev["agent_mode"]
        if isinstance(payload_ev.get("tool_names"), list):
            tools.update(str(item) for item in payload_ev["tool_names"])
        if payload_ev.get("tool"):
            tools.add(str(payload_ev["tool"]))
        model_adapter = payload_ev.get("model_adapter") or {}
        if model_adapter.get("provider"):
            provider = model_adapter["provider"]
        provider_meta = payload_ev.get("provider") or {}
        if isinstance(provider_meta, dict) and provider_meta.get(
            "investigation_provider"
        ):
            provider = provider_meta["investigation_provider"]
            if provider_meta.get("fallback_reason"):
                fallback_reason = str(provider_meta["fallback_reason"])[:200]
        if payload_ev.get("lifecycle") == "model.provider_failed":
            fallback_reason = str(payload_ev.get("error") or "")[:200]
    print("agent_mode=", agent_mode)
    print("provider=", provider)
    if fallback_reason:
        print("fallback_reason=", fallback_reason)
    print("tools=", ",".join(sorted(tools)))
    print("feishu_outbound=attempted")


if __name__ == "__main__":
    main()
