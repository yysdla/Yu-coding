"""HTTP client for the ProjectLens Hermes plugin."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig


class ProjectLensApiClient:
    """Call ProjectLens HTTP APIs over a process boundary.

    Ask / role-view remain debug/smoke surfaces. Formal Agent tool loop uses
    GET /project-agent/tools and POST /project-agent/tools/call only.
    No internal service imports, no DB access, no filesystem reads.
    """

    def __init__(self, config: ProjectLensPluginConfig) -> None:
        self._config = config

    @property
    def config(self) -> ProjectLensPluginConfig:
        return self._config

    def ask_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._config.api_base_url}/project-agent/ask"
        return self._post_json(url, payload)

    def role_view(self, run_id: str, audience: str) -> dict[str, Any]:
        url = f"{self._config.api_base_url}/project-agent/runs/{run_id}/role-view"
        return self._post_json(url, {"audience": audience})

    def list_tools(self) -> dict[str, Any]:
        url = f"{self._config.api_base_url}/project-agent/tools"
        return self._get_json(url)

    def call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._config.api_base_url}/project-agent/tools/call"
        return self._post_json(url, payload)

    def _get_json(self, url: str) -> dict[str, Any]:
        req = request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        return self._request_json(req)

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        return self._request_json(req)

    def _request_json(self, req: request.Request) -> dict[str, Any]:
        raw = ""
        attempts = 2
        for attempt in range(attempts):
            try:
                with request.urlopen(req, timeout=self._config.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                break
            except error.HTTPError as exc:
                return _http_error_envelope(exc)
            except error.URLError as exc:
                if attempt == attempts - 1:
                    return _connection_error_envelope(str(exc.reason))
            except TimeoutError:
                if attempt == attempts - 1:
                    return _connection_error_envelope("ProjectLens API timed out.")
            time.sleep(0.25)

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error_code": "INVALID_PROJECTLENS_RESPONSE",
                "message": "ProjectLens API returned non-JSON content.",
                "retryable": True,
                "agent_recovery_hint": "Check ProjectLens API logs and retry the command.",
                "audit_ref": {"allow_apply": False, "tool_names": []},
            }
        if not isinstance(decoded, dict):
            return {
                "ok": False,
                "error_code": "INVALID_PROJECTLENS_RESPONSE",
                "message": "ProjectLens API returned a JSON value that is not an object.",
                "retryable": True,
                "agent_recovery_hint": "Check ProjectLens API schema compatibility.",
                "audit_ref": {"allow_apply": False, "tool_names": []},
            }
        return decoded


def _http_error_envelope(exc: error.HTTPError) -> dict[str, Any]:
    message = exc.reason or f"HTTP {exc.code}"
    try:
        body = exc.read().decode("utf-8")
        decoded = json.loads(body)
        if isinstance(decoded, dict) and "ok" in decoded:
            return decoded
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return {
        "ok": False,
        "error_code": "PROJECTLENS_HTTP_ERROR",
        "message": str(message),
        "retryable": 500 <= exc.code < 600,
        "agent_recovery_hint": "Check ProjectLens API status, URL, and request schema.",
        "audit_ref": {"allow_apply": False, "tool_names": []},
    }


def _connection_error_envelope(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": "PROJECTLENS_UNAVAILABLE",
        "message": message,
        "retryable": True,
        "agent_recovery_hint": "Start ProjectLens API or update PROJECTLENS_API_BASE_URL.",
        "audit_ref": {"allow_apply": False, "tool_names": []},
    }
