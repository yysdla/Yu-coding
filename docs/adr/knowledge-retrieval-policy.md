# ADR: Knowledge Retrieval Policy

日期：2026-09-18  
状态：accepted

## Context

ProjectLens currently uses exact traceback matching and BM25 for Evidence
retrieval. Semantic memory retrieval already has optional embeddings, but
enabling that capability must not silently change the authoritative Evidence
path. The vectorized knowledge plan also needs a stable rollback point while
the index and evaluation set are still being built.

## Decision

1. The public retrieval modes are `lexical`, `vector`, and `hybrid`.
2. `lexical` is the production default. It preserves exact matching plus BM25.
3. Vector retrieval is effective only when it is explicitly enabled and the
   provider, model, model version, chunker version, and provider credentials
   are configured.
4. Incomplete vector configuration or vector failure falls back to lexical
   retrieval when `knowledge_vector_fallback=true`; otherwise the request
   fails closed.
5. Candidate and result limits are bounded by service-side hard caps.
6. The configuration factory is side-effect free. Provider construction and
   network policy remain owned by the existing embedding provider layer.

## Consequences

- Existing BM25 behavior remains the rollback target.
- A configured embedding provider does not change Evidence retrieval until the
  knowledge vector flag and index are deliberately enabled.
- The retrieval mode and version metadata can be recorded in later retrieval
  traces without exposing credentials.

## Rejected alternatives

- Automatically reusing the memory embedding switch for Evidence retrieval.
- Allowing arbitrary per-request modes to bypass server-side configuration.
- Treating vector similarity as a fact or authority signal.
