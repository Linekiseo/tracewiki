---
name: review-research-evidence
description: Independently review Wiki, code, Codex session, experiment, notebook, document, and workspace evidence for completeness, contradictions, version drift, provenance and validation risk.
---

# Review Research Evidence

Review source-backed evidence without approving your own output.

## Gather

1. Resolve the project, iteration, work item, and current Wiki generation.
2. Use `wiki_navigate` to derive evidence obligations and discover relevant
   cross-source relationships.
3. Read important pages and call `evidence_read` for every claim that controls a
   decision, quality gate, security conclusion, or release recommendation.
4. Use `evidence_search` only when the Wiki reports a gap or the task explicitly
   requires raw cross-source discovery.
5. Read execution and pending-review state separately from evidence quality.

## Assess

Classify each claim as directly supported, partially supported, contradicted,
stale/version-mismatched, unauthorized, unavailable, or missing. Check snapshot
generation, source locator, raw/derived status, review status, counter-evidence,
and denominator integrity. Distinguish absence of evidence from evidence of
absence.

## Report

Lead with the decision-relevant outcome. Include evidence paths and locators,
contradictions, missing validation, risks, exclusions, and the next smallest
evidence-producing action. Never hide failed tests, null results, version drift,
or negative quality outcomes. Final acceptance remains human-controlled.
