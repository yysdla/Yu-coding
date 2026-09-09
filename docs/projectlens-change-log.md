# ProjectLens Change Log

## Phase 0 — Baseline freeze & test isolation (2026-08-25)

### Baseline record


| Item     | Value                                                   |
| -------- | ------------------------------------------------------- |
| Artifact | `docs/_baseline_pytest.txt`, `docs/_baseline_junit.xml` |
| Captured | 2026-08-24T15:18:20+08:00 (junit timestamp)             |
| Result   | 50 failed, 455 passed (505 total), ~2058s               |
| Python   | 3.12.10                                                 |
| pytest   | 8.4.2                                                   |
| FastAPI  | 0.140.0                                                 |
| Pydantic | 2.13.4                                                  |


### First failure & skill compatibility strategy

First failure in the baseline run:

- `tests/test_api.py::test_execute_error_analysis_and_query_events`
- Expected skill `incident_diagnosis`, observed `project_investigation`

Root-cause priority: developer `.env` leaking into the global `Settings` instance (especially `PROJECT_LENS_AGENT_MODE=read_agent`), not a requirement to rewrite all paths back to the old Skill name.

**Fixed entry layering (do not reverse for green tests):**

```text
Natural-language Project Agent / ask / Feishu NL (agent_mode=read_agent)
  -> skill = project_investigation

/api/v1/runs + workflow default path, /project debug
  -> may keep incident_diagnosis (and other legacy Skill fields)

Forbidden: force-write incident_diagnosis on every path to satisfy old assertions
```

Phase 0 does **not** mass-update Feishu/harness tests still asserting `incident_diagnosis`. Remaining baseline failures are known debt for later entry-layer cleanup / Phase 1+.

### Behavior & config changes in this phase

- Added `test_mode` and `allow_external_calls` to `Settings`.
- Runtime guards: under `test_mode` or `PYTEST_CURRENT_TEST`, force `model_live=false` and block external Feishu HTTP / Hermes live / GitHub outbound helpers.
- `tests/conftest.py` isolates global settings from developer `.env` (`test_mode=True`, `model_live=False`, `allow_external_calls=False`, `agent_mode=workflow`).
- Empty `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` no longer invents `demo`/`chat-1`. Demo fallback only when `allow_demo_fallback=True` and `env=development` outside test mode.
- `.env.example` documents `development` / `test` / `pilot` / `production` plus isolation knobs.

## Phase 1 — Trusted identity, members & deny-by-default (2026-08-25)

### Identity & ingress

- Added immutable `ActorContext` in `src/project_lens/domain/identity.py` (`tenant_key`, `actor_id`, `chat_id`, `chat_type`, `source`, `authenticated`).
- `FeishuRunContext` now carries `actor: ActorContext`; Feishu ingress requires `open_id`, non-empty `tenant_key`, `chat_id`, and `chat_type in {p2p, group}`.
- Removed default `tenant_key="demo"` on Feishu event headers.

### Members & deny-by-default

- Added `members` + `public_sources` on `ProjectSpace`; loader expands members into role templates via `member_directory.py`.
- Unknown actors resolve to `guest` with **public sources only**; empty public scope raises `PermissionError`.
- Updated `config/projects/{payment,crm,projectlens}.json` with `public_sources`, `members`, and cross-project role fixture `u-shared-1`.

### API trust boundary

- Project-agent ask/tools/scope routes resolve identity from `request.state.actor_context` under test/production isolation; body `user_id` cannot elevate privileges.
- `TrustedActorTestMiddleware` + `X-ProjectLens-*` headers inject test fixture actors in pytest.
- `ProjectAgentAskService` resolves `EffectiveAccessScope` before creating runs.

### Tests

- Added `tests/test_feishu_identity_context.py`, `tests/test_project_member_roles.py`.
## Phase 2 — Private/group EffectiveAccessScope (2026-08-26)

### Scope model

- Extended `ChatVisibilityPolicy` with `chat_type`, `allow_private_details`, `public_sources`, `policy_version`.
- Extended `EffectiveAccessScope` with `identity_source`, `policy_version`, `chat_type`, `allow_private_details`.
- `ProjectRuntimeContextResolver.resolve()` accepts `chat_type` and `identity_source`; p2p uses full role scope, group intersects with chat policy and caps private visibility.

### Gateway & tools

- `ReadContextGateway` enforces `EffectiveAccessScope` when provided (tool allowlist + audit fields).
- `build_investigation_tool_registry` passes scope to read tools; file reads check `source_path_allowed` before execution.
- `AccessDeniedAnswer` unified safe deny structure for tool API errors.

### Feishu per-message context

- `MessageAccessContext` resolves fresh scope per message; `FeishuEventService` no longer reuses session actor/scope across speakers.
- `conversation_service.record_turn` preserves current message `runtime_access`, not stale scratchpad copies.

### Tests

- Added `tests/test_effective_access_scope.py`, `tests/test_group_visibility_intersection.py`.
- Updated policy/read-gateway/tool API tests for p2p vs group and audit fields.

### Phase 2 completion hardening (2026-08-26)

- Persisted the audit-safe per-message scope on `AgentRun`; read-agent execution no longer loses the Feishu/Ask access decision after run creation.
- Added validated scope restoration bound to the current tenant, project, actor and chat; missing or mismatched scope fails closed before tool registration.
- Added strict `ReadContextGateway` mode for Project Agent paths while retaining legacy workflow compatibility.
- Filtered search, authorized evidence, knowledge-gap inputs and graph paths before they reach the model.
- Enforced effective tool/source scope across file read, list, grep/search and ranged-read tools.
- Added role/chat/identity/policy fields to read-agent and Project Agent tool audits.
- Verification: Phase 2 focused suite `40 passed`; related identity/role/MCP/multi-turn/read-agent suite `68 passed`.
- Apply / PR / deploy / rollback / restart remain disabled.

## Phase 3 — Role-aware answering vertical slice (2026-08-26)

### Verified fact core

- Added compatibility-safe `ProjectAnswer` fields for conclusion, fact/inference partitions, impact, next actions, citation refs and policy identity.
- Added `EvidenceRef`; audience projections do not receive source bodies.
- Verifier derives conclusion and impact from citation-verified FACT claims and accepts citation IDs only from the InvestigationLedger.

### Shared audience projection

- Added `application/audience_views.py` with a pure `render_audience_view()` projection.
- Added distinct developer, ops, QA, product, manager, onboarding and team ordering.
- Kept actor role separate from a manually selected presentation role so view switching cannot appear as an authorization change.
- Project Agent JSON, RoleView Markdown and Feishu cards now consume the same `AudienceAnswer`.
- Feishu automatically chooses the current message actor's role from the persisted effective scope.

### Verification

- Added `tests/test_role_aware_answering.py` and `tests/test_same_facts_different_audience.py`.
- Phase 3 acceptance suite: 34 passed.
- Full related regression suite: 153 passed.
- Ruff: all checks passed.
- Apply / PR / deploy / rollback / restart remain disabled.

## Phase 4 — Deterministic MVP risk engine (2026-08-26)

### Risk domain and rules

- Added immutable risk contracts, stable SHA-256 fingerprints, severity/state enums and durable state events.
- Implemented six deterministic rules for overdue tasks, blocked dependencies, unsynced requirements, code/task mismatches, PR review or CI failures, and explicit chat blockers.
- Rules only consume authorized Evidence plus an injected UTC clock; every finding cites one or more input Evidence UUIDs.
- Added risk metadata support to task records and indexing without breaking existing task fixtures.

### Lifecycle and persistence

- Added `RiskEngine` deduplication: unchanged evidence only refreshes `last_seen_at`; material changes and severity upgrades produce events and notification candidates.
- Added automatic resolution, reopening when evidence reappears, and actor-attributed transitions to acknowledged/snoozed/dismissed/resolved.
- Unowned findings route to `project_owners` instead of guessing an assignee.
- Added in-memory and SQLite stores for findings and ordered state events.

### ACL boundary

- Added `ContextEngine.scan_risks()` so Evidence access filtering occurs before any rule evaluates data.
- Limited-visibility scans cannot resolve risks backed by hidden Evidence; authoritative background scans retain disappearance-based resolution.

### Verification

- Phase 4 acceptance suite: 29 passed.
- Task indexing and Context regression set: 50 passed.
- Phase 2/3 ACL, investigation, role-view and Feishu regression set: 58 passed.
- Ruff: all checks passed.
- The full repository suite still has documented historical baseline failures; its first failure remains an unrelated legacy Feishu/context-pack 400 response.
- Apply / PR / deploy / rollback / restart remain disabled.

## Phase 5 — Proactive risk notifications and Feishu feedback (2026-08-26)

### Feedback and audit

- Added risk feedback contracts for acknowledge, dismiss, snooze, progress update and help request.
- Button callbacks and structured natural-language feedback normalize to the same state machine.
- Production authorization permits structured risk owners and ProjectSpace managers; unauthorized actors fail before audit mutation.
- Added durable SQLite feedback and notification audit tables with idempotency keys.

### Notification routing

- High/open findings are sent immediately by Feishu `open_id` private message; medium/low findings remain digest-only.
- Unowned findings route to declared project managers and never guess or publicly mention an owner.
- Notification claims are inserted before send to prevent concurrent duplicate delivery; failed sends release claims for retry.
- Snooze expiry reopens and reminds once, with retry-safe restoration to snoozed after transport failure.
- Help requests resolve the bound project group and recompute group visibility policy before posting a citation-only group card.

### Cards and daily digest

- Added private risk cards with severity, affected refs, Evidence UUID citations, freshness and five feedback actions.
- Added a group-safe risk card without mutation buttons or source bodies.
- Added per-project/per-owner daily digests covering new high, unacknowledged, acknowledged-open, due-today, resolved and dismissed counts.
- Digest rendering excludes risk summaries, Evidence bodies, code/log bodies and private chat text.

### Verification

- Phase 4+5 risk suite: 56 passed.
- Ruff: all checks passed.
- Focused diff check: passed, with line-ending warnings only.
- Remaining related legacy callback failures use old fixtures without trusted `open_id`; the Phase 1 identity boundary was intentionally retained.
- Apply / PR / deploy / rollback / restart remain disabled.

## Phase 6 — Read-only connectors, sync state & freshness (2026-09-01)

### Connector contracts

- Unified `Connector` / `SyncBatch` / `SyncCursor` / `ConnectorHealth` in `context/connectors/base.py`.
- Fixture-backed read-only connectors: `feishu_project`, `feishu_bitable`, `feishu_minutes`, `github` (cursor resume + `deleted` → revoke ids; GitHub write methods raise).
- Evidence keeps `revoked` / `revoked_at`; search uses `index.all()` which excludes revoked by default.

### Sync orchestration

- `ConnectorSyncService` applies batches into `EvidenceIndex`, persists cursors via SQLite or in-memory `ConnectorSyncStateStore`.
- Tracks `last_attempt_at`, `last_success_at`, `error_count`, `last_error`; sync failures keep the prior cursor for retry.
- Freshness helpers emit “资料可能过期” when `last_success_at` exceeds `connector_freshness_max_age_seconds` (default 24h).

### Verification

- Phase 6 acceptance suite: 13 passed (`test_connector_contracts`, `test_feishu_project_connector`, `test_feishu_bitable_connector`, `test_github_connector`, `test_sync_freshness`).
- Real Feishu/GitHub HTTP transport remains out of scope for CI; connectors accept injected records for local `payment` fixtures.
- Apply / PR / deploy / rollback / restart remain disabled.
# 2026-09-01

## Hermes 完整体 Batch 1 / 阶段 1：上下文接入

- 新增 `hermes_context.py`，将已授权的 `ConversationSession`、项目长期记忆和运行权限摘要渲染为 Hermes 请求上下文。
- Hermes Runner 支持可选 `context`，并注入真实 `AIAgent` 的临时 system prompt。
- 飞书 Hermes 路径在调用前读取当前会话和有效项目记忆，调用后继续保存会话与工具循环审计。
- 保持 `skip_memory=True`、`skip_context_files=True`，不让 Hermes 绕过 ProjectLens 的记忆和文件权限边界。
- Apply、PR、deploy、rollback、restart 仍关闭。
- 新增 Hermes 最终答案解析器：结构化 JSON 进入共享 `AnswerDraft`，非结构化文本保留为未验证 unknown。
- 新增 Hermes 引用验证适配：工具 `citations/evidence_refs` 转为 `citation_only` Evidence，复用 `verify_answer_draft()` 生成 `ProjectAnswer`；未知引用不会成为 FACT。
- Hermes 飞书路径开始使用正式 AgentRun：请求创建 run，完成后由 `RunService.complete_external()` 回写标准答案、状态和 AgentEvent。
- Hermes 成功答案复用 Feishu `AudienceView` / `EffectiveAccessScope` 投影，按当前角色生成卡片并保持群聊可见边界。
- Hermes 已验证 FACT 接入项目记忆提案和审批事件；未审批前不会写入正式 ProjectMemory。
- 新增 Hermes Propose/Validate 基础服务：patch plan、test plan、风险升级为审批所需草案；路径和命令 allowlist 校验默认拒绝不安全操作。
## Hermes advanced proposal/validation tools

- Added explicit opt-in flag `PROJECT_LENS_FEISHU_HERMES_ADVANCED_TOOLS` for the
  server and `PROJECTLENS_HERMES_ADVANCED_TOOLS` for the Hermes plugin process.
- Default tool catalog remains the original five read-only tools.
- Opt-in catalog adds approval-gated draft/report tools for patch plans, test
  plans, risk escalation, and patch-plan validation.
- These tools are still constrained by the resolved ProjectSpace ACL and never
  execute tests, write files, create PRs, deploy, rollback, or restart services.

## Hermes controlled execution foundation

- Added `ExecutionGuard` with fail-closed checks for feature flag, engineering
  policy, matching approved proposal, project scope, and approval expiry.
- Added `HermesExecutionService` for disposable isolated-worktree patch/test
  execution and bounded diff/output reporting.
- Main workspace remains unchanged; production deploy, merge, rollback, and
  restart are still unavailable.

## Hermes risk collaboration foundation

- Added `HermesRiskAssistant` for evidence-linked risk explanations, impact
  summaries, owner actions, and 24-hour review plans.
- Optional escalation output is an approval-required proposal only.
- Risk state remains owned by `RiskEngine` and `RiskFeedbackService`; Hermes
  cannot dismiss, resolve, or mutate a finding.
- Added `HermesRiskReviewScheduler` with evidence-signature-based idempotency
  keys for background reviews. Repeated runs over the same snapshot are
  skipped, while changed evidence produces a new review opportunity.
- Added optional `HermesRiskBackgroundScheduler` lifecycle wiring. It is
  disabled by default, uses project-scoped service access, and reports recent
  review results through the Feishu status endpoint.

## Production readiness gates

- Added `/ready` alongside `/health`.
- `/health` remains a lightweight liveness response.
- `/ready` checks the database, registry, context engine, run service, risk
  engine, and enabled Hermes bridge without exposing secrets or prompt content.
- Readiness returns `not_ready` when Hermes is enabled but its bridge is absent.
- Added production/pilot service-token middleware for internal Project Agent and
  Hermes APIs; pilot/production requests without a valid Bearer token return 401.
- Added `/release-gate`, which evaluates measured citation, leakage, write-safety,
  success, fallback, notification, replay-sample, and pilot-duration thresholds.
  Missing measurements keep the deployment classified as an internal technical
  pilot.

## Hermes completion status (2026-09-02)

- Phase 5 tool coverage now includes project todo proposals, diff-scope checks,
  and evidence-link validation.
- Phase 6 engineering approvals persist in SQLite and isolated execution is
  available only through the explicit execution flag plus matching approval.
- Phase 7 review claims persist in SQLite to prevent duplicate background reviews
  across process restarts.
