"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from project_lens import __version__
from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.api.memory_routes import router as memory_router
from project_lens.api.project_agent_routes import router as project_agent_router
from project_lens.api.routes import router
from project_lens.application.conversation_service import ConversationService
from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_scheduler import (
    FeishuDocSyncScheduler,
    attach_scheduler_to_app,
)
from project_lens.application.feishu_doc_sync_status import SQLiteFeishuDocSyncStatusStore
from project_lens.application.project_agent_ask import ProjectAgentAskService
from project_lens.application.project_agent_role_view import ProjectAgentRoleViewService
from project_lens.application.project_agent_tools import ProjectAgentToolService
from project_lens.application.run_service import RunService
from project_lens.config import settings
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    parse_local_project_registrations,
    to_project_registrations,
)
from project_lens.context.conversation_store import (
    ConversationStore,
    InMemoryConversationStore,
    SQLiteConversationStore,
)
from project_lens.context.memory_store import SQLiteMemoryStore
from project_lens.project_space.policies import ProjectRuntimeContextResolver
from project_lens.project_space.registry import (
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.memory_approval_gateway import MemoryApprovalGateway
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.approval_routes import router as approval_router
from project_lens.integrations.feishu.docs_client import FeishuDocClient
from project_lens.integrations.feishu.http_adapter import (
    FeishuTenantTokenProvider,
    HttpFeishuMessenger,
)
from project_lens.integrations.feishu.hermes_tool_loop import FeishuHermesToolLoopBridge
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.integrations.feishu.routes import router as feishu_router
from project_lens.integrations.feishu.security import FeishuRequestVerifier
from project_lens.integrations.feishu.service import FeishuEventService
from project_lens.integrations.feishu.status_routes import router as feishu_status_router
from project_lens.persistence.sqlite import (
    SQLiteApprovalStore,
    SQLiteDatabase,
    SQLiteEventDeduplicator,
    SQLiteEventSink,
    SQLiteRunRepository,
)
from project_lens.runtime.lifecycle import LifecycleBus
from project_lens.workflow.engineering_skill import ProjectEngineeringSkill
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.providers.factory import create_model_adapter_from_settings
from project_lens.workflow.resolver import ProjectResolver


def create_app(database_path: str = ":memory:") -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        scheduler: FeishuDocSyncScheduler | None = getattr(
            application.state, "feishu_doc_sync_scheduler", None
        )
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()

    application = FastAPI(
        title="ProjectLens API",
        description="Evidence-grounded enterprise project collaboration agent",
        version=__version__,
        lifespan=lifespan,
    )
    base_dir = Path(__file__).resolve().parents[2]
    project_registrations = parse_local_project_registrations(
        settings.local_project_registry,
        base_dir=base_dir,
    )
    context_engine, evidence_index = build_registered_context_engine(project_registrations)
    application.state.context_engine = context_engine
    application.state.evidence_index = evidence_index
    application.state.local_project_registrations = project_registrations
    resolver_registrations = to_project_registrations(project_registrations)
    default_project = resolver_registrations[0].project
    engineering_root = _default_engineering_root(project_registrations)
    engineering_skill = (
        ProjectEngineeringSkill.for_project_root(engineering_root, allow_apply=False)
        if engineering_root is not None
        else None
    )
    database = SQLiteDatabase(database_path)
    event_sink = SQLiteEventSink(database)
    lifecycle = LifecycleBus(event_sink=event_sink)
    application.state.lifecycle_bus = lifecycle
    model_adapter = create_model_adapter_from_settings(settings)
    workflow = ProjectWorkflow(
        ProjectResolver(resolver_registrations),
        context_engine,
        engineering_skill=engineering_skill,
        lifecycle=lifecycle,
        model_adapter=model_adapter,
    )
    project_registry = registry_from_local_registrations(project_registrations)
    spaces_dir = base_dir / settings.project_spaces_dir
    for space in load_project_spaces_from_dir(spaces_dir, base_dir=base_dir):
        # JSON ProjectSpace overlays local registrations so file_allowlist /
        # role_policies / chat_visibility_policies from config/projects win.
        project_registry.upsert(space)
    runtime_context_resolver = ProjectRuntimeContextResolver(
        project_registry=project_registry,
    )
    investigation_agent = ProjectInvestigationAgent(
        context_engine=context_engine,
        project_registry=project_registry,
        lifecycle=lifecycle,
    )
    run_repository = SQLiteRunRepository(database)
    run_service = RunService(
        run_repository,
        workflow,
        event_sink,
        lifecycle=lifecycle,
        investigation_agent=investigation_agent,
        agent_mode=settings.agent_mode,
    )
    # Hermes/MCP one-shot path always uses read_agent + ProjectInvestigationAgent.
    ask_run_service = RunService(
        run_repository,
        workflow,
        event_sink,
        lifecycle=lifecycle,
        investigation_agent=investigation_agent,
        agent_mode="read_agent",
    )
    project_agent_ask_service = ProjectAgentAskService(
        run_service=ask_run_service,
        project_registry=project_registry,
    )
    project_agent_role_view_service = ProjectAgentRoleViewService(
        run_service=ask_run_service,
    )
    project_agent_tool_service = ProjectAgentToolService(
        context_engine=context_engine,
        project_registry=project_registry,
    )
    hermes_tool_loop_bridge = (
        FeishuHermesToolLoopBridge(
            tool_service=project_agent_tool_service,
            config=ProjectLensPluginConfig.from_env(),
            hermes_repo=settings.hermes_repo,
            hermes_provider=settings.feishu_hermes_provider,
            hermes_model=settings.feishu_hermes_model,
        )
        if settings.feishu_use_hermes_tool_loop
        else None
    )
    application.state.model_adapter = model_adapter
    application.state.project_registry = project_registry
    application.state.project_runtime_context_resolver = runtime_context_resolver
    application.state.investigation_agent = investigation_agent
    application.state.run_service = run_service
    application.state.ask_run_service = ask_run_service
    application.state.project_agent_ask_service = project_agent_ask_service
    application.state.project_agent_role_view_service = project_agent_role_view_service
    application.state.project_agent_tool_service = project_agent_tool_service
    application.state.feishu_hermes_tool_loop_bridge = hermes_tool_loop_bridge
    application.state.approval_store = SQLiteApprovalStore(database)
    application.state.memory_store = SQLiteMemoryStore(database)
    memory_approval_gateway = MemoryApprovalGateway(application.state.memory_store)
    application.state.memory_approval_gateway = memory_approval_gateway
    sync_status_store = SQLiteFeishuDocSyncStatusStore(database)
    application.state.feishu_doc_sync_status_store = sync_status_store
    identity_mapper = parse_project_bindings(
        settings.feishu_project_bindings,
        default_project=default_project,
        registered_projects=tuple(space.project for space in project_registry.list()),
    )
    token_provider = None
    if settings.feishu_app_id and settings.feishu_app_secret:
        token_provider = FeishuTenantTokenProvider(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            base_url=settings.feishu_api_base_url,
        )
        messenger = HttpFeishuMessenger(
            token_provider=token_provider,
            base_url=settings.feishu_api_base_url,
        )
    else:
        messenger = RecordingFeishuMessenger()
    application.state.feishu_credentials_configured = bool(
        settings.feishu_app_id and settings.feishu_app_secret
    )
    application.state.feishu_outbound_mode = (
        "http" if application.state.feishu_credentials_configured else "recording"
    )
    application.state.feishu_binding_count = identity_mapper.binding_count
    application.state.feishu_messenger = messenger
    doc_client = (
        FeishuDocClient(
            token_provider=token_provider,
            base_url=settings.feishu_api_base_url,
        )
        if token_provider is not None
        else None
    )
    sync_service = FeishuDocumentSyncService(
        client=doc_client,
        index=evidence_index,
        status_store=sync_status_store,
    )
    application.state.feishu_doc_sync_service = sync_service
    attach_scheduler_to_app(
        application,
        FeishuDocSyncScheduler(
            service=sync_service,
            registrations=project_registrations,
            on_startup=settings.feishu_doc_sync_on_startup,
            interval_seconds=settings.feishu_doc_sync_interval_seconds,
            lifecycle=lifecycle,
        ),
    )
    conversation_store = _build_conversation_store(database)
    conversation_service = ConversationService(
        store=conversation_store,
        lifecycle=lifecycle,
    )
    application.state.conversation_store = conversation_store
    application.state.conversation_service = conversation_service
    application.state.feishu_event_service = FeishuEventService(
        run_service=run_service,
        verifier=FeishuRequestVerifier(
            verification_token=settings.feishu_verification_token,
            signing_secret=settings.feishu_signing_secret,
        ),
        deduplicator=SQLiteEventDeduplicator(database),
        identity_mapper=identity_mapper,
        messenger=application.state.feishu_messenger,
        memory_store=application.state.memory_store,
        memory_approval_gateway=memory_approval_gateway,
        sync_status_store=sync_status_store,
        conversation_service=conversation_service,
        lifecycle=lifecycle,
        runtime_context_resolver=runtime_context_resolver,
        hermes_tool_loop_bridge=hermes_tool_loop_bridge,
    )
    application.include_router(router, prefix=settings.api_prefix)
    application.include_router(project_agent_router, prefix=settings.api_prefix)
    application.include_router(feishu_router, prefix=settings.api_prefix)
    application.include_router(feishu_status_router, prefix=settings.api_prefix)
    application.include_router(approval_router, prefix=settings.api_prefix)
    application.include_router(memory_router, prefix=settings.api_prefix)
    return application


def _build_conversation_store(database: SQLiteDatabase) -> ConversationStore:
    backend = settings.conversation_store.strip().lower()
    if backend in {"memory", "inmemory", "in_memory"}:
        return InMemoryConversationStore()
    return SQLiteConversationStore(database)


def _default_engineering_root(registrations: tuple) -> Path | None:
    if not registrations:
        return None
    repository_root: Path = registrations[0].sources.repository_root
    if repository_root.name == "src":
        return repository_root.parent
    return repository_root


app = create_app(settings.database_path)
