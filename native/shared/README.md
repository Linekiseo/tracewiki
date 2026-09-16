# Native Desktop Shared API Contract

This directory is the platform-neutral boundary used by the native macOS
(SwiftUI) and Windows (WinUI 3) clients. It does **not** contain a web UI, live
project data, credentials, or a second backend implementation.

The versioned contract is in [`v1/manifest.json`](v1/manifest.json). Its route
and Pydantic-schema digests are computed from `create_app(defer_runtime=True)`
and are checked against FastAPI's OpenAPI document by
`tests/test_native_desktop_contract.py`. A backend change that would silently
break a native client therefore fails the contract test.

## Boundary rules

- The backend remains the authority for RAG, Wiki, project, provider and Codex
  state. Native clients render these responses; they do not reproduce retrieval
  logic locally.
- Fixtures are redacted decoder fixtures derived from actual public endpoint
  shapes and frozen Pydantic models. They are test assets only and must never be
  shown as live records.
- `api_key`, authorization values, prompts, ACL internals and host filesystem
  paths are forbidden in every response fixture.
- Provider credentials are written through the native OS credential vault and
  registered with the process-local backend. Only the public provider status is
  decodable by the app.
- Native clients ignore unknown response fields for forward compatibility, but
  must reject a changed contract version, missing required field, invalid enum,
  or invalid pagination cursor.

## Client flow

1. Load `/v1/projects` and select a project.
2. Load `/v1/ai/provider`; configure/test through the native credential flow.
3. Preview or execute `/v1/query`, preserving conversation identifiers and
   rendering evidence/citation state rather than only answer text.
4. Browse Wiki pages with `offset`, `limit` and `next_offset`, then load a page
   through `/v1/wiki/read`.
5. Load `/v1/codex-bridge/status`, `/agents`, and `/agents/audit` for the native
   agent management surface.

The error fixture defines the user-facing split: validation (`422`), scoped
absence (`404`), and provider failure (`502`). Native clients must never render
raw exception bodies.
