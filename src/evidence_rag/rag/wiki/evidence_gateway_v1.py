"""Generation-pinned raw evidence gateway backed by compiled source authority."""

from __future__ import annotations

from ..answer_v2 import EvidenceFactV2
from .contracts_v1 import WikiSourceRefV1, canonical_sha256_v1
from .navigator_v1 import CandidateRawEvidenceReaderV1
from .store_v1 import WikiStoreV1

WIKI_EVIDENCE_GATEWAY_VERSION = "wiki-source-candidate-evidence-gateway-v1"


class StoreRawEvidenceGatewayV1:
    """Resolve source refs only inside one project, generation, and ACL scope.

    The compiler stores the byte-canonical ``MultiSourceCandidateV2`` envelope that
    produced every source ref.  Online navigation reads that exact envelope instead
    of re-running a mutable source query or trusting page text as raw evidence.
    """

    def __init__(
        self,
        *,
        store: WikiStoreV1,
        project_id: str,
        wiki_generation_id: str,
        requester_acl_refs: tuple[str, ...],
    ) -> None:
        if requester_acl_refs != tuple(sorted(set(requester_acl_refs))):
            raise ValueError("raw evidence gateway ACL refs must be sorted and unique")
        self.store = store
        self.project_id = project_id
        self.wiki_generation_id = wiki_generation_id
        self.requester_acl_refs = requester_acl_refs
        self.authority_sha256 = canonical_sha256_v1(
            {
                "project_id": project_id,
                "requester_visibility": canonical_sha256_v1(requester_acl_refs),
                "version": WIKI_EVIDENCE_GATEWAY_VERSION,
                "wiki_generation_id": wiki_generation_id,
            }
        )

    def read(self, source_ref: WikiSourceRefV1) -> EvidenceFactV2 | None:
        candidate = self.store.read_source_candidate(
            project_id=self.project_id,
            generation_id=self.wiki_generation_id,
            requester_acl_refs=self.requester_acl_refs,
            candidate_sha256=source_ref.raw_content_sha256,
        )
        if candidate is None:
            return None
        return CandidateRawEvidenceReaderV1((candidate,)).read(source_ref)
