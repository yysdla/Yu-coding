# Hermes `projectlens-safe` profile samples

These files live in the **ProjectLens** repo as copy-paste samples.
They do **not** modify Hermes core and are **not** auto-written to `~/.hermes`.

Related plugin doc: [`docs/hermes-projectlens-plugin.md`](../../docs/hermes-projectlens-plugin.md)

## Files

| File | Purpose |
|------|---------|
| `projectlens-safe.config.yaml` | MCP server, Feishu tool whitelist, disabled toolsets, profile route stub |
| `SOUL.projectlens-safe.md` | Outer Hermes persona: must call ProjectLens MCP; no fact invention |

## Prerequisites

```powershell
cd C:\Users\Administrator\Desktop\project-lens
pip install -e ".[mcp]"
# Optional: also run the HTTP API for debugging
python -m uvicorn project_lens.main:app --reload --port 8000
```

Verify MCP stdio starts (will wait on stdin — Ctrl+C to stop):

```powershell
python -m project_lens.integrations.mcp.server
```

## Copy into Hermes (manual)

1. Create profile directory if needed:
   `~/.hermes/profiles/projectlens-safe/`
2. Merge keys from `projectlens-safe.config.yaml` into that profile’s `config.yaml`
   (or your active `~/.hermes/config.yaml`).
3. Copy `SOUL.projectlens-safe.md` content into the profile `SOUL.md` (or equivalent
   persona file your Hermes version uses).
4. Set `cwd` / `command` to your real ProjectLens path and Python executable.
5. Replace `oc_REPLACE_WITH_FEISHU_CHAT_ID` with the Feishu test group chat id.
6. Restart Hermes gateway / chat so MCP discovery runs.

## Why not default `hermes-feishu`?

`hermes-feishu` includes full core tools (terminal/file/…). A project group using that
preset can bypass ProjectLens Gateway. `projectlens-safe` whitelists only:

- `mcp-projectlens` (tool: `mcp__projectlens__projectlens_ask_project`)
- `clarify`

plus `agent.disabled_toolsets` defense-in-depth.

## Acceptance (manual Hermes)

1. Tool list shows `mcp__projectlens__projectlens_ask_project`.
2. Free project questions trigger that tool (not terminal/file).
3. ProjectLens run events show tool audit; `allow_apply=false`.
4. Write/deploy/restart asks are refused.

Automated ProjectLens-side checks: `pytest tests/test_hermes_phase2_profile.py -q`
