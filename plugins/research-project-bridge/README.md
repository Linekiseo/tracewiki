# Research Wiki + RAG Bridge

Plugin version: `0.2.0+wiki.20260803`  
MCP server capability version: `0.2.0`

This Codex plugin connects to the local Evidence RAG MCP endpoint at
`http://127.0.0.1:8000/mcp`. It exposes governed research-work tools together
with the Agent-Native Wiki read, relationship traversal, raw-evidence
verification, and bounded navigation tools.

## Trust model

- The bearer token fixes the project scope and read/write permissions.
- Wiki reads remain pinned to one published generation and its ACL partition.
- `wiki_propose_patch` accepts only content-addressed `AGENT_UNREVIEWED`
  proposals. Agents cannot review, stage, publish, or release Wiki generations.
- `evidence_read` resolves a page source reference through the exact candidate
  ledger used by the Compiler; it does not perform a second ungoverned search.
- Default product retrieval remains V1 while Wiki quality is on hold.

Set `EVIDENCE_RAG_MCP_TOKEN`, start the local Evidence RAG service, and install
the plugin through a local Codex marketplace or a Codex-managed plugin path.
The plugin never stores the token in its manifest; Codex resolves it from the
named environment variable when connecting. A successful MCP handshake must
report server capability version `0.2.0` before Wiki tools are treated as
available.
