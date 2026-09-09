"""Unified access-denied responses — safe messages without leaking forbidden sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AccessDeniedReason = Literal[
    "unknown_actor",
    "chat_not_bound",
    "source_forbidden",
    "tool_not_allowed",
    "access_denied",
]


@dataclass(frozen=True)
class AccessDeniedAnswer:
    reason_code: AccessDeniedReason
    safe_message: str
    next_step: str

    def to_dict(self) -> dict[str, str]:
        return {
            "reason_code": self.reason_code,
            "safe_message": self.safe_message,
            "next_step": self.next_step,
        }


def access_denied_for_permission_error(message: str) -> AccessDeniedAnswer:
    lowered = message.casefold()
    if "unknown actor" in lowered or "public project sources" in lowered:
        return AccessDeniedAnswer(
            reason_code="unknown_actor",
            safe_message="当前身份未识别或未加入该项目，无法读取项目资料。",
            next_step="请联系项目负责人申请成员权限，或在已绑定的项目群/私聊中提问。",
        )
    if "not allowed in chat" in lowered or "chat" in lowered and "bound" in lowered:
        return AccessDeniedAnswer(
            reason_code="chat_not_bound",
            safe_message="当前聊天未绑定该项目，或您的角色不允许在此聊天中提问。",
            next_step="请在已绑定的项目群中提问，或私聊机器人并选择正确项目。",
        )
    if "forbidden" in lowered or "outside readable" in lowered:
        return AccessDeniedAnswer(
            reason_code="source_forbidden",
            safe_message="请求的资料不在当前有效访问范围内。",
            next_step="请缩小问题范围，或向项目负责人申请更高权限。",
        )
    if "tool" in lowered and "not allowed" in lowered:
        return AccessDeniedAnswer(
            reason_code="tool_not_allowed",
            safe_message="当前身份或聊天环境不允许使用该读取工具。",
            next_step="请改用允许的只读工具，或在私聊中提问以获取更完整资料。",
        )
    return AccessDeniedAnswer(
        reason_code="access_denied",
        safe_message="当前无法访问所请求的项目资料。",
        next_step="请确认项目绑定与成员权限后重试。",
    )
