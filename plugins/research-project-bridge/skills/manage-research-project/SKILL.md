---
name: manage-research-project
description: Read and organize research projects, topics, iterations, and actionable work through the research-project-bridge MCP server. Use when the user asks to inspect project status, plan the next research step, create or refine work items, prioritize research activity, or transition work through the research lifecycle.
---

# Manage Research Project

Use the structured research model instead of inferring project state from Codex
JSONL, Wiki summaries, or repository prose.

## Resolve context

1. Call `project_get_context` for the token-scoped project.
2. Call `wiki_navigation_status` when the plan depends on current project
   knowledge, and use `wiki_navigate` for cross-source uncertainties.
3. If the project, repository, or iteration is not uniquely resolved, stop and
   ask. Never choose by similarity.
4. Use `research_list_work` to narrow work and `research_get_work` before an
   update so `expected_version` is current.

Organize plans as:

`research question → evidence obligation → iteration → executable work → validation evidence → independent review`

Distinguish scientific judgment, Agent-executable work, system-derived state,
negative evidence, and missing evidence.

## Write safely

All writes require user approval. Before a write, summarize the exact target and
change, obtain confirmation, use a stable `idempotency_key`, and provide
`expected_version` for updates. A transition to `done` creates a human approval;
it is not Agent approval.

Never delete work or evidence, edit databases directly, or change conclusions
to make the project appear complete.
