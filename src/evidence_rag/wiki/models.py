from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..rag.wiki.builder_v1 import WikiBuilderPatchV1, WikiBuilderTrialV1
from ..rag.wiki.compiler_v1 import WikiCompilationRequestV1
from ..rag.wiki.contracts_v1 import WikiSourceDomainV1
from ..rag.wiki.search_v1 import WikiQueryClassV1


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WikiSearchApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    query: str = Field(min_length=1, max_length=4_000)
    query_class: WikiQueryClassV1 | None = None
    required_sources: list[WikiSourceDomainV1] = Field(default_factory=list, max_length=6)
    required_roles: list[str] = Field(default_factory=list, max_length=32)
    generation_id: str | None = Field(default=None, max_length=240)
    top_k: int = Field(default=12, ge=1, le=50)
    candidate_limit: int = Field(default=2_000, ge=1, le=20_000)


class WikiNavigateApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    query: str = Field(min_length=1, max_length=4_000)
    query_class: WikiQueryClassV1 | None = None
    required_sources: list[WikiSourceDomainV1] = Field(default_factory=list, max_length=6)
    required_roles: list[str] = Field(default_factory=list, max_length=32)
    as_of: str | None = None
    max_searches: int = Field(default=8, ge=1, le=32)
    max_page_reads: int = Field(default=48, ge=1, le=256)
    max_link_hops: int = Field(default=3, ge=0, le=6)
    max_raw_reads: int = Field(default=16, ge=1, le=64)
    empty_search_patience: int = Field(default=2, ge=1, le=8)
    retrieval_deadline_ms: int = Field(default=4_000, ge=50, le=120_000)
    top_k_per_search: int = Field(default=8, ge=1, le=25)
    token_budget: int = Field(default=8_000, ge=256, le=64_000)
    require_raw_evidence: bool = True


class WikiPublishApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    generation_id: str = Field(min_length=1, max_length=240)
    expected_manifest_sha256: str = Field(pattern=r"sha256:[0-9a-f]{64}")
    reviewer_authority_sha256: str = Field(pattern=r"sha256:[0-9a-f]{64}")


class WikiOrganizationStageApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)


class WikiRollbackApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    target_generation_id: str = Field(min_length=1, max_length=240)
    expected_target_manifest_sha256: str = Field(pattern=r"sha256:[0-9a-f]{64}")
    expected_active_generation_id: str = Field(min_length=1, max_length=240)
    expected_active_manifest_sha256: str = Field(pattern=r"sha256:[0-9a-f]{64}")
    reviewer_authority_sha256: str = Field(pattern=r"sha256:[0-9a-f]{64}")


class WikiPatchProposalApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    patch: WikiBuilderPatchV1


class WikiPatchEvaluationApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    trial: WikiBuilderTrialV1


class WikiPatchStageApiRequest(_Request):
    project_id: str = Field(min_length=1, max_length=240)
    generation_id: str = Field(min_length=1, max_length=240)


WikiCompileApiRequest = WikiCompilationRequestV1
