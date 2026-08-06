# Architecture

## Product boundary

ProjectLens answers questions about a software project. It is not a general enterprise assistant
and it does not perform autonomous production changes.

The source of truth is a structured project report. Business, technical, and management views are
rendered from the same report so they cannot silently disagree.

Feishu is a transport adapter. Callback parsing, signature checks, deduplication, identity mapping,
and card rendering live outside the workflow so the agent core can also be used from HTTP APIs,
CLIs, future Slack or enterprise WeChat adapters, and evaluation harnesses.

## Core planes

### Context plane

- Ingest project documents, repository artifacts, commits, pull requests, tasks, and incidents.
- Retrieve evidence using metadata filters, BM25, dense retrieval, reranking, and project relations.
- Preserve source identity, timestamps, access scope, and content hashes.

### Action plane

- Resolve the user's goal and missing context.
- Collect evidence through read-only tools.
- Produce claims and verify that each claim cites evidence.
- Create explicit approval requests before any write action.

### Integration plane

- Verify external callbacks before creating runs.
- Deduplicate external events before executing workflows.
- Map external tenant, user, and chat identities to project permissions.
- Execute workflows outside callback request handling.
- Render progress updates and final answers from AgentRun and ProjectAnswer.

### Graph and evaluation plane

- Build a deterministic relationship graph from immutable evidence.
- Keep graph edges explainable and traceable to source evidence.
- Replay fixed incidents to compare workflow completion and answer quality over time.
- Measure citation coverage, evidence precision, unknown reporting, and approval safety.

### Durable operations plane

- Persist runs and audit events behind the existing repository and event-sink interfaces.
- Persist approval requests and enforce one-way pending -> approved/rejected decisions.
- Keep database paths configurable so local tests can use `:memory:` and deployments can use a
  mounted SQLite file or a future PostgreSQL adapter.

## Non-negotiable invariants

1. A factual claim must cite at least one evidence record.
2. Evidence access is checked before retrieval, not only before rendering.
3. Model-generated confidence is not trusted; evidence coverage determines the evidence grade.
4. Unverified inference cannot be promoted to project memory.
5. Production-changing tools remain unavailable in the MVP.
