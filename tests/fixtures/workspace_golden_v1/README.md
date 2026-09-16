# workspace-golden-v1

`release.json` is the committed, canonical descriptor for the programmatically
rebuilt Workspace Golden. The executable authority remains the frozen builder
and evaluator in `evidence_rag.rag.sources.workspace`; tests require the
descriptor, package digest, authority digest, publication digest, slice
membership, and entity counts to agree exactly.

The fixture is synthetic and contains no production service data. It is safe
for isolated SQLite and in-memory evaluation only.
