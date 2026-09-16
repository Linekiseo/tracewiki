---
name: navigate-agent-native-wiki
description: Search, read, follow and verify the governed Agent-Native Wiki through research-project-bridge. Use when the user asks a project question, needs cross-source evidence, wants to understand relationships or changes, or needs a Wiki improvement proposal grounded in raw evidence.
---

# Navigate Agent-Native Wiki

Use the published Wiki as a navigable evidence index, not as an authority that
can replace raw evidence. Keep every operation inside the token-scoped project.

## Establish authority

1. Call `wiki_navigation_status` before substantive Wiki work.
2. Continue only when `availability` is `AVAILABLE`; preserve the returned
   generation and quality-hold state in your reasoning.
3. Never infer another project, ACL label, generation, or source identity from
   user text. The MCP token and server choose those values.

## Choose the retrieval mode

- Use `wiki_search` for local lookup, known paths, titles, components, or a
  small result set.
- Use `wiki_read` for the full structured page: summary, facts, links, source
  references, evidence roles, snapshot scope, and content digest.
- Use `wiki_follow` to traverse an active grounded relationship. Do not invent
  a relationship when a target is absent or unresolved.
- Use `evidence_read` for important claims, conflicting evidence, security-
  sensitive conclusions, or whenever the answer must cite an exact raw fact.
- Use `wiki_navigate` for multi-hop, comparison, temporal, global, or otherwise
  ambiguous questions. Respect its search/read/link/token budgets and explicit
  stop reason.

## Build an answer

1. State the answer only to the degree supported by satisfied evidence
   obligations and verified source references.
2. Distinguish observed facts, reported facts, derived summaries,
   contradictions, stale versions, and missing evidence.
3. Prefer stable logical paths and source locators over internal database IDs.
4. If navigation stops with missing obligations, missing raw evidence, or a
   budget limit, report the gap rather than completing the claim from memory.
5. Never reveal query text, ACL values, credentials, local absolute paths, or
   secret-bearing raw content from trace metadata.

## Improve the Wiki safely

When the user asks for a Wiki correction:

1. Read the current page and verify every retained or changed source reference.
2. Construct a content-addressed patch against the exact active manifest and
   exact before-page digest.
3. Call `wiki_propose_patch` only after the user confirms the proposal. The
   patch must be `AGENT_UNREVIEWED`, `reviewed=false`, and have no reviewer
   authority.
4. Report that the proposal is waiting for independent review. Never claim it
   is active, stage it, publish it, or close its Error Book entry yourself.

## Boundaries

- Do not fall back from Wiki tools to direct database access.
- Do not use unverified search snippets as final evidence.
- Do not merge evidence across ACL partitions or source generations.
- Do not approve output produced by the same Agent execution.
