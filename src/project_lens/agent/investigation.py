"""Read-only ProjectInvestigationAgent — LLM/stub chooses tools via AgentLoop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from project_lens.agent.draft import AnswerDraft
from project_lens.agent.openai_loop_provider import InvestigationProviderError
from project_lens.agent.provider_factory import create_investigation_provider
from project_lens.agent.read_tools import InvestigationLedger, build_investigation_tool_registry
from project_lens.agent.session_context import (
    build_investigation_session_brief,
    prior_file_paths,
    prior_search_hints,
)
from project_lens.agent.skill_guides import select_skill_guide
from project_lens.agent.stub_planner import InvestigationStubProvider
from project_lens.agent.verifier import verify_answer_draft
from project_lens.config import Settings, settings
from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext
from project_lens.domain.conversation import ConversationSession
from project_lens.domain.memory import ProjectMemory
from project_lens.domain.models import (
    ActionProposal,
    AgentRun,
    ProjectAnswer,
    RunStatus,
)
from project_lens.project_space.models import ProjectSpace
from project_lens.project_space.registry import ProjectRegistry
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.runtime.loop import AgentLoop
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.runtime.security import RunContext
from project_lens.runtime.tool_gateway import ToolGateway
from project_lens.runtime.tools import ToolRegistry
from project_lens.runtime.types import RuntimeResult
from project_lens.workflow.providers.transport import ChatTransport

TransitionHandler = Callable[[RunStatus, dict[str, object]], Awaitable[None]]


class ProjectInvestigationAgent:
    """Parallel read-agent path. Does not use classify_project_question."""

    def __init__(
        self,
        *,
        context_engine: ContextEngine,
        project_registry: ProjectRegistry,
        read_gateway: ReadContextGateway | None = None,
        lifecycle: LifecycleBus | None = None,
        max_iterations: int = 6,
        app_settings: Settings | None = None,
        chat_transport: ChatTransport | None = None,
    ) -> None:
        self._engine = context_engine
        self._registry = project_registry
        self._read_gateway = read_gateway or ReadContextGateway(context_engine)
        self._lifecycle = lifecycle or LifecycleBus()
        self._max_iterations = max(2, max_iterations)
        self._settings = app_settings or settings
        self._chat_transport = chat_transport
        self.last_tool_names: tuple[str, ...] = ()
        self.last_read_audit: dict[str, object] = {}
        self.last_provider_meta: dict[str, object] = {}
        self.last_skill_guide: str | None = None
        self.last_session_used: bool = False

    async def execute(
        self,
        run: AgentRun,
        transition: TransitionHandler,
        *,
        session: ConversationSession | None = None,
        memories: tuple[ProjectMemory, ...] = (),
    ) -> ProjectAnswer:
        del memories  # reserved for approved ProjectMemory injection
        await transition(RunStatus.RESOLVING, {"agent_mode": "read_agent"})
        space = self._resolve_space(run)
        access = AccessContext(
            tenant_id=run.project.tenant_id,
            user_id=run.user_id,
            permissions=frozenset(
                {space.access_scope or f"project:{run.project.project_id}:read"}
            ),
        )
        await transition(RunStatus.COLLECTING, {"phase": "investigation_loop"})
        session_brief = build_investigation_session_brief(session)
        self.last_session_used = bool(session_brief)
        guide = select_skill_guide(run.question)
        self.last_skill_guide = guide.name
        await self._lifecycle.emit_async(
            LifecycleEventType.CONTEXT_COLLECTED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=run.project,
            payload={
                "agent_mode": "read_agent",
                "project_space": space.project_id,
                "skill_guide": guide.name,
                "session_used": self.last_session_used,
                "prior_file_count": len(prior_file_paths(session)),
            },
        )

        ledger = InvestigationLedger()
        self._read_gateway.reset_audit()
        tool_gateway = _tool_gateway_for_space(space)
        registry = build_investigation_tool_registry(
            project=run.project,
            access=access,
            read_gateway=self._read_gateway,
            tool_gateway=tool_gateway,
            ledger=ledger,
            access_scope=space.access_scope or f"project:{run.project.project_id}:read",
        )
        provider, provider_meta = create_investigation_provider(
            run.question,
            app_settings=self._settings,
            transport=self._chat_transport,
            prior_paths=prior_file_paths(session),
            prior_hints=prior_search_hints(session),
        )
        self.last_provider_meta = dict(provider_meta)
        system_prompt = (
            f"{_SYSTEM_PROMPT}\n\n"
            f"ProjectSpace: {space.tenant_id}/{space.project_id} ({space.display_name})\n"
            f"{guide.to_prompt_block()}"
        )
        if session_brief:
            system_prompt = f"{system_prompt}\n\n{session_brief}"
        context = RunContext(
            run_id=run.id,
            trace_id=run.trace_id,
            project=run.project,
            user_id=run.user_id,
            permissions=frozenset(
                {
                    "project:read",
                    space.access_scope or f"project:{run.project.project_id}:read",
                    f"project:{run.project.project_id}:read",
                }
            ),
        )
        await transition(
            RunStatus.ANALYZING,
            {
                "phase": "agent_loop",
                "agent_mode": "read_agent",
                "skill_guide": guide.name,
                "session_used": self.last_session_used,
                "model_adapter": _adapter_refs(provider_meta),
            },
        )

        result, provider_meta = await self._run_loop_with_fallback(
            run=run,
            question=run.question,
            provider=provider,
            provider_meta=provider_meta,
            registry=registry,
            system_prompt=system_prompt,
            context=context,
            ledger=ledger,
            prior_paths=prior_file_paths(session),
            prior_hints=prior_search_hints(session),
        )
        self.last_provider_meta = dict(provider_meta)
        self.last_tool_names = tuple(ledger.tool_names)
        self.last_read_audit = self._read_gateway.audit_summary()
        await self._emit_tool_audit_events(run, ledger)

        # Refresh analyzing payload so Feishu Audit can read tool trail from events.
        await transition(
            RunStatus.ANALYZING,
            {
                "phase": "agent_loop_complete",
                "agent_mode": "read_agent",
                "skill_guide": guide.name,
                "session_used": self.last_session_used,
                "tool_names": list(self.last_tool_names),
                "read_audit": dict(self.last_read_audit),
                "model_adapter": _adapter_refs(provider_meta),
            },
        )
        await self._lifecycle.emit_async(
            LifecycleEventType.ANALYSIS_COMPLETED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=run.project,
            payload={
                "agent_mode": "read_agent",
                "finish_reason": (
                    result.finish_reason.value if result is not None else "provider_failed"
                ),
                "tool_names": list(self.last_tool_names),
                "iterations": result.iterations if result is not None else 0,
                "provider": provider_meta,
                "skill_guide": guide.name,
                "read_audit": dict(self.last_read_audit),
            },
        )

        await transition(RunStatus.VERIFYING, {"phase": "draft_verify"})
        if result is None:
            answer = _provider_failure_answer(run, provider_meta)
        else:
            draft = _draft_from_result(
                result.final_text, ledger=ledger, question=run.question
            )
            answer = verify_answer_draft(draft, project=run.project, ledger=ledger)
        await self._lifecycle.emit_async(
            LifecycleEventType.VERIFICATION_COMPLETED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=run.project,
            payload={
                "agent_mode": "read_agent",
                "claim_count": len(answer.claims),
                "evidence_count": len(answer.evidence),
                "read_audit": self.last_read_audit,
            },
        )
        await self._lifecycle.emit_async(
            LifecycleEventType.ANSWER_COMPOSED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=run.project,
            payload={"skill": answer.skill, "agent_mode": "read_agent"},
        )
        return answer

    async def _run_loop_with_fallback(
        self,
        *,
        run: AgentRun,
        question: str,
        provider: object,
        provider_meta: dict[str, Any],
        registry: ToolRegistry,
        system_prompt: str,
        context: RunContext,
        ledger: InvestigationLedger,
        prior_paths: tuple[str, ...] = (),
        prior_hints: tuple[str, ...] = (),
    ) -> tuple[RuntimeResult | None, dict[str, Any]]:
        try:
            loop = AgentLoop(
                provider,  # type: ignore[arg-type]
                registry,
                system_prompt=system_prompt,
                max_iterations=self._max_iterations,
                max_total_tokens=50_000,
                max_runtime_seconds=90.0,
            )
            return await loop.run(question, context), provider_meta
        except InvestigationProviderError as exc:
            await self._lifecycle.emit_async(
                LifecycleEventType.MODEL_PROVIDER_FAILED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=run.project,
                payload={
                    "agent_mode": "read_agent",
                    "provider": provider_meta.get("investigation_provider"),
                    "error": str(exc)[:300],
                    "fallback_to_stub": bool(self._settings.model_fallback_to_stub),
                },
            )
            if (
                self._settings.model_fallback_to_stub
                and provider_meta.get("investigation_provider") == "openai_loop"
            ):
                stub = InvestigationStubProvider(
                    question=question,
                    prior_paths=prior_paths,
                    prior_hints=prior_hints,
                )
                stub_meta = {
                    **provider_meta,
                    "investigation_provider": "stub_planner",
                    "live_effective": False,
                    "fallback_from": "openai_loop",
                    "fallback_reason": str(exc)[:300],
                }
                # Clear partial live tool state before stub re-run.
                ledger.tool_names.clear()
                ledger.observations.clear()
                ledger.evidence.clear()
                self._read_gateway.reset_audit()
                loop = AgentLoop(
                    stub,
                    registry,
                    system_prompt=system_prompt,
                    max_iterations=self._max_iterations,
                    max_total_tokens=50_000,
                    max_runtime_seconds=90.0,
                )
                result = await loop.run(question, context)
                return result, stub_meta
            return None, {
                **provider_meta,
                "live_effective": False,
                "error": str(exc)[:300],
            }

    async def _emit_tool_audit_events(
        self,
        run: AgentRun,
        ledger: InvestigationLedger,
    ) -> None:
        for index, observation in enumerate(ledger.observations):
            tool_name = str(observation.get("tool") or "unknown")
            await self._lifecycle.emit_async(
                LifecycleEventType.TOOL_REQUESTED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=run.project,
                payload={
                    "agent_mode": "read_agent",
                    "tool": tool_name,
                    "index": index,
                    "arguments_summary": {
                        key: value
                        for key, value in observation.items()
                        if key != "tool"
                    },
                },
            )
            await self._lifecycle.emit_async(
                LifecycleEventType.TOOL_COMPLETED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=run.project,
                payload={
                    "agent_mode": "read_agent",
                    "tool": tool_name,
                    "index": index,
                    "ok": True,
                    "result_summary": {
                        key: value
                        for key, value in observation.items()
                        if key != "tool"
                    },
                },
            )

    def _resolve_space(self, run: AgentRun) -> ProjectSpace:
        space = self._registry.get(run.project.tenant_id, run.project.project_id)
        if space is not None:
            return space
        return ProjectSpace(
            tenant_id=run.project.tenant_id,
            project_id=run.project.project_id,
            display_name=run.project.project_id,
            repositories=(),
            services=(run.project.service,) if run.project.service else (),
            environments=(run.project.environment,) if run.project.environment else (),
            access_scope=f"project:{run.project.project_id}:read",
        )


def _tool_gateway_for_space(space: ProjectSpace) -> ToolGateway | None:
    root = space.primary_repository_root
    if root is None or not root.exists():
        return None
    return ToolGateway(
        EngineeringPolicy(
            project_root=root,
            allowed_path_prefixes=space.file_allowlist or ("src/", "tests/", "knowledge/"),
            allow_apply=False,
        )
    )


def _adapter_refs(provider_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": provider_meta.get("investigation_provider")
        or provider_meta.get("model_provider")
        or "unknown",
        "model_name": provider_meta.get("model_name") or "stub-v1",
        "live": bool(provider_meta.get("live_effective")),
        "live_effective": bool(provider_meta.get("live_effective")),
        "allow_apply": False,
        "retries": 0,
        "timed_out": False,
        "usage": {},
        "notes": [
            "agent_mode=read_agent",
            f"investigation_provider={provider_meta.get('investigation_provider')}",
            *(
                [f"fallback_from={provider_meta['fallback_from']}"]
                if provider_meta.get("fallback_from")
                else []
            ),
            *(
                [f"fallback_reason={provider_meta['fallback_reason']}"]
                if provider_meta.get("fallback_reason")
                else []
            ),
            *(
                [f"error={provider_meta['error']}"]
                if provider_meta.get("error")
                else []
            ),
        ],
    }


def _provider_failure_answer(run: AgentRun, provider_meta: dict[str, Any]) -> ProjectAnswer:
    reason = str(provider_meta.get("error") or "live investigation provider failed")
    return ProjectAnswer(
        project=run.project,
        status="unknown",
        skill="project_investigation",
        confidence=0.0,
        business_summary=(
            "只读调查未能完成：表达层 / 工具循环 Provider 调用失败。"
            "没有生成带引用的项目结论。"
        ),
        technical_summary=f"provider_error={reason[:200]}",
        claims=(),
        evidence=(),
        unknowns=(
            f"Provider 失败：{reason[:200]}",
            "可重试，或关闭 live 使用 stub planner。",
        ),
        recommended_actions=(
            ActionProposal(
                title="检查 MODEL_LIVE / API Key / 网络后重试",
                description=reason[:300],
                requires_approval=True,
            ),
        ),
    )


def _draft_from_result(
    final_text: str | None,
    *,
    ledger: InvestigationLedger,
    question: str,
) -> AnswerDraft:
    if final_text:
        try:
            return AnswerDraft.from_json_text(final_text)
        except Exception:  # noqa: BLE001 - fall through to empty investigation draft
            pass
    tools = list(ledger.tool_names)
    return AnswerDraft(
        facts=[],
        unknowns=[
            f"调查循环未返回结构化草稿。已调用工具：{', '.join(tools) or '无'}。"
            f"问题：{question[:120]}"
        ],
        next_actions=["补充可检索资料后重试", "指定具体文件路径"],
        business_summary=(
            "只读调查未形成可引用结论；不会用置信度套路掩盖缺失资料。"
        ),
        technical_summary=f"tools={tools}",
        tools_used=tools,
        stop_reason="incomplete",
    )


_SYSTEM_PROMPT = (
    "You are ProjectLens ProjectInvestigationAgent. "
    "Use read-only tools to investigate the project question. "
    "Never invent facts. Cite evidence_ids from tool results. "
    "Apply / PR / deploy / rollback / restart are forbidden. "
    "SkillGuide is optional checklist only — not a forced business bucket. "
    "When done, return a single JSON object AnswerDraft with keys: "
    "facts (array of {text, citations}), inferences, unknowns, next_actions, "
    "business_summary, technical_summary, tools_used, stop_reason."
)
