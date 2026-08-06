# ProjectLens

ProjectLens is an evidence-grounded project collaboration agent for Feishu. It combines a
Claude Code-style agent runtime with hybrid RAG so teams can ask project questions and receive
answers that separate verified facts, inferences, unknowns, and recommended actions.

## MVP

The first release supports one Python project and three question types:

1. Explain a project error from a pasted traceback.
2. Explain the impact of a pull request or release.
3. Report the current owner and handling status of an issue.

All integrations are read-only by default. Any write operation must create an approval request.

## Architecture

```text
Feishu / HTTP API
        |
Application service and run state
        |
Agent runtime: understand -> retrieve -> analyze -> verify -> compose
        |
Project context engine and immutable evidence
        |
GitHub, Feishu documents, logs, vector search, project graph
```

Canonical plan: `docs/project-agent-final-plan.md` (plain-language companion:
`docs/projectlens-next-plan-simple.md`). Design constraints: `docs/architecture.md`.
For pilot / Feishu launch env profiles (Stub, Safe Live LLM, test-group grey), see
`docs/pilot-launch-config.md` and copy `.env.example` to `.env`.
For Feishu test-group grey release steps and pinned-message text, see
`docs/feishu-grey-release.md`.
For Hermes plugin / `projectlens-safe` profile samples, see
`docs/hermes-projectlens-plugin.md` and `config/hermes/`.

## Development

```powershell
py -3.12 -m pip install -e ".[dev]"
py -3.12 -m pytest -q
py -3.12 -m uvicorn project_lens.main:app --reload

# Run the deterministic local context benchmark
py -3.12 -m project_lens.evaluation.run_context `
  --project-root examples/payment_service `
  --dataset evaluation/context_dataset.json
```

Open `http://127.0.0.1:8000/docs` for the API documentation.

## Project registration

ProjectLens indexes one or more projects from `PROJECT_LENS_LOCAL_PROJECT_REGISTRY`. Each entry
contains its project identity, local repository/documents/incidents paths, and its retrieval ACL.
Feishu bindings must refer to exactly one registered project, so a chat cannot accidentally query
another project's knowledge. The full JSON configuration is shown in `.env.example`.

For example, register a second project by adding another object under `projects`, then bind its
Feishu chat with that project's `project_id` and, when needed, `service` or `environment`. No
application-code changes are required. `allowed_users` may be added to a binding as a JSON array
of Feishu user IDs for a chat-level allowlist.

## Error-analysis workflow

The local application registers `examples/payment_service` as the first project. A client creates
a run, explicitly executes it, and then reads the result or its audit events:

```text
POST /api/v1/runs
POST /api/v1/runs/{run_id}/execute
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events
```

Execution follows `resolve -> retrieve -> analyze -> verify -> compose`. The analyzer can only
propose candidate claims. The verifier checks project ownership, retrieved evidence IDs, evidence
grades, and supporting terms before claims are allowed into `ProjectAnswer`. Business and
technical summaries are rendered from that same verified claim set.

The current workflow is deterministic and model-independent so local tests do not require network
access. Model-backed analysis adapters and Feishu transport belong to later phases; evidence,
verification, permissions, and answer contracts remain unchanged when those adapters are added.

## Feishu integration

Phase 5 adds a Feishu callback entry point:

```text
POST /api/v1/feishu/events
```

The integration supports URL verification, verification-token checks, optional HMAC request
signature checks, durable event-id deduplication, configured chat-to-project identity mapping, async workflow
execution, progress text, and final interactive-card rendering. The local app uses a recording
messenger adapter so tests can verify outbound Feishu messages without real Feishu credentials.

Useful local settings:

```text
PROJECT_LENS_FEISHU_VERIFICATION_TOKEN=project-lens-local-token
PROJECT_LENS_FEISHU_SIGNING_SECRET=
```

The Feishu layer creates an `AgentRun` from an `im.message.receive_v1` group message, executes the
same verified workflow used by the HTTP run API, and renders business summary, technical details,
unknowns, evidence, and approval-required actions into one card.
