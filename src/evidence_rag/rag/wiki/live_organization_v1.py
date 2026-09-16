"""Governed project inventory and live Wiki organization compiler.

The core Wiki compiler intentionally accepts only already-governed candidates.  This
module is the product-side bridge that inventories the *current project* from the six
production source stores, turns that inventory into bounded semantic candidates, and
then delegates every page/fact/link decision to the reviewed compiler.

It never publishes implicitly.  Preview is read-only; staging and publication remain
separate mutations in :mod:`evidence_rag.wiki.router`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..multisource_foundation_v2 import MULTISOURCE_DOMAINS, MultiSourceCandidateV2
from .compiler_v1 import (
    WikiCompilationRequestV1,
    WikiCompilationResultV1,
    build_wiki_compilation_request_v1,
    compile_wiki_v1,
)
from .contracts_v1 import (
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    canonical_sha256_v1,
)

if TYPE_CHECKING:
    from ...runtime import Runtime


WIKI_LIVE_ORGANIZER_VERSION = "project-live-wiki-organizer-v1"
WIKI_LIVE_DERIVATION = "live_project_inventory_v1"
_MAX_CODE_GROUPS = 160
_MAX_CODE_FILES_PER_GROUP = 16
_MAX_CODEX_THREADS = 240
_MAX_DOMAIN_ITEMS = 240


class WikiLiveOrganizationError(ValueError):
    """Raised when a current-project inventory cannot be compiled safely."""


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class WikiOrganizationSourceSummaryV1(_Frozen):
    source: WikiSourceDomainV1
    availability: Literal["AVAILABLE", "EMPTY", "UNAVAILABLE"]
    raw_entity_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    generation_id: str
    roles: tuple[str, ...]
    diagnostic: str | None = None


class WikiOrganizationPreviewV1(_Frozen):
    project_id: str
    organization_kind: Literal["live_project_inventory"] = "live_project_inventory"
    generation_id: str
    request_sha256: str
    active_generation_id: str | None
    active_snapshot_kind: Literal["none", "engineering_fixture", "live_project"]
    source_summaries: tuple[WikiOrganizationSourceSummaryV1, ...]
    candidate_count: int = Field(ge=0)
    compiled_candidate_count: int = Field(ge=0)
    quarantined_candidate_count: int = Field(ge=0)
    page_count: int = Field(ge=0)
    page_type_counts: tuple[tuple[str, int], ...]
    knowledge_role_counts: tuple[tuple[str, int], ...]
    added_page_count: int = Field(ge=0)
    retained_page_count: int = Field(ge=0)
    removed_page_count: int = Field(ge=0)
    warnings: tuple[str, ...]
    requires_explicit_publish: Literal[True] = True
    quality_state: Literal["QUALITY_HOLD"] = "QUALITY_HOLD"
    organizer_version: Literal[WIKI_LIVE_ORGANIZER_VERSION] = WIKI_LIVE_ORGANIZER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiOrganizationPreviewV1:
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiLiveOrganizationError("live Wiki preview digest mismatch")
        if self.candidate_count != sum(item.candidate_count for item in self.source_summaries):
            raise WikiLiveOrganizationError("live Wiki preview candidate denominator mismatch")
        return self


class WikiOrganizationStageV1(_Frozen):
    project_id: str
    generation_id: str
    manifest_sha256: str
    request_sha256: str
    candidate_count: int = Field(ge=0)
    page_count: int = Field(ge=0)
    quarantined_candidate_count: int = Field(ge=0)
    reviewer_authority_sha256: str
    state: Literal["STAGED_FOR_REVIEW"] = "STAGED_FOR_REVIEW"
    published: Literal[False] = False
    organizer_version: Literal[WIKI_LIVE_ORGANIZER_VERSION] = WIKI_LIVE_ORGANIZER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiOrganizationStageV1:
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiLiveOrganizationError("live Wiki staging digest mismatch")
        return self


@dataclass(frozen=True, slots=True)
class _InventoryItem:
    source: str
    entity_id: str
    entity_type: str
    title: str
    snippet: str
    locator: str
    stable_version: str
    task: str
    roles: tuple[str, ...]
    fact_status: str
    timestamp: str | None = None
    counter_evidence: bool = False


@dataclass(frozen=True, slots=True)
class _SourceInventory:
    source: str
    items: tuple[_InventoryItem, ...]
    raw_entity_count: int
    availability: Literal["AVAILABLE", "EMPTY", "UNAVAILABLE"]
    diagnostic: str | None = None


def _safe_time(*values: Any) -> str | None:
    parsed: list[datetime] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            candidate = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=UTC)
        parsed.append(candidate.astimezone(UTC))
    if not parsed:
        return None
    return max(parsed).isoformat().replace("+00:00", "Z")


def _compact_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _stable_token(value: Any, *, width: int = 24) -> str:
    return canonical_sha256_v1(value)[7 : 7 + width]


def _typed_locator(source: str, entity_type: str, entity_id: str) -> str:
    return f"{source}://project-inventory/{quote(entity_type, safe='._-')}/{quote(entity_id, safe='._-')}"


_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wiki-knowledge-organization", ("wiki", "knowledge", "知识", "atlas", "navigator")),
    ("intelligent-retrieval", ("query", "retriev", "search", "检索", "answer", "rag")),
    ("agent-integration", ("agent", "codex", "mcp", "plugin", "智能体")),
    ("evidence-governance", ("acl", "govern", "authority", "review", "evidence", "证据")),
    ("evaluation-quality", ("test", "eval", "quality", "gate", "validation", "测试")),
    ("source-connectors", ("source", "adapter", "experiment", "notebook", "document")),
    ("runtime-api", ("runtime", "router", "api", "service", "endpoint")),
    ("product-experience", ("frontend", "workbench", "ui", "interaction", "界面", "交互")),
    ("knowledge-graph", ("graph", "edge", "relation", "lineage", "关系", "图谱")),
    ("release-operations", ("release", "publish", "rollback", "deploy", "发布")),
)


def _roles(*values: Any) -> tuple[str, ...]:
    haystack = " ".join(str(value or "") for value in values).casefold()
    selected = [role for role, terms in _ROLE_RULES if any(term in haystack for term in terms)]
    return tuple(selected[:4] or ("project-knowledge",))


def _code_group(path: str) -> str:
    normalized = path.strip().replace("\\", "/").lstrip("./")
    parts = PurePosixPath(normalized).parts
    if not parts:
        return "project-root"
    if parts[0] == "frontend" and len(parts) >= 4 and parts[1:3] == ("src", "features"):
        return "/".join(parts[:4])
    if len(parts) >= 5 and parts[:3] == ("src", "evidence_rag", "rag"):
        return "/".join(parts[:5] if parts[3] == "sources" else parts[:4])
    if len(parts) >= 3 and parts[:2] == ("src", "evidence_rag"):
        return "/".join(parts[:3])
    if parts[0] == "tests":
        filename = parts[-1].casefold()
        family = next(
            (
                name
                for name in (
                    "wiki",
                    "codex",
                    "experiment",
                    "notebook",
                    "document",
                    "workspace",
                    "code",
                    "multisource",
                )
                if name in filename
            ),
            "general",
        )
        return f"tests/{family}"
    if parts[0] in {"docs", "plugins", "artifacts", "evals"} and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _code_inventory(runtime: Runtime, project_id: str, acl_ref: str) -> _SourceInventory:
    repositories = [
        item
        for item in runtime.store.list_repositories()
        if item.get("project_id") == project_id
        and item.get("status") == "ready"
        and item.get("acl_ref") in {acl_ref, "public"}
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    raw_count = 0
    for repository in repositories:
        files = runtime.store.list_files(str(repository["id"]))
        raw_count += len(files)
        for file in files:
            grouped[(str(repository["id"]), _code_group(str(file.get("path") or "")))].append(file)
    items: list[_InventoryItem] = []
    repo_by_id = {str(item["id"]): item for item in repositories}
    for (repository_id, group), files in sorted(grouped.items())[:_MAX_CODE_GROUPS]:
        repository = repo_by_id[repository_id]
        paths = tuple(sorted(str(item.get("path") or "") for item in files if item.get("path")))
        languages = tuple(sorted({str(item.get("language") or "unknown") for item in files}))
        version = canonical_sha256_v1(
            tuple(
                (item.get("path"), item.get("content_hash"), item.get("commit_sha"))
                for item in sorted(files, key=lambda row: str(row.get("path") or ""))
            )
        )
        sample = "、".join(paths[:_MAX_CODE_FILES_PER_GROUP])
        snippet = (
            f"{repository.get('name') or repository_id} 的代码子系统 {group}；"
            f"包含 {len(files)} 个当前文件，语言 {', '.join(languages)}。"
            f"关键文件：{sample or '无可见文件'}。"
        )
        roles = _roles(group, sample)
        task = (
            "architecture"
            if any(token in group for token in ("src/", "frontend/", "plugins/"))
            else "procedure"
        )
        items.append(
            _InventoryItem(
                source="code",
                entity_id=f"code-subsystem-{_stable_token((repository_id, group), width=32)}",
                entity_type=f"CodeSubsystem.{group.replace('/', '.')}"[:180],
                title=f"代码子系统 · {group}",
                snippet=snippet,
                locator=_typed_locator("code", "subsystem", f"{repository_id}:{group}"),
                stable_version=version,
                task=task,
                roles=roles,
                fact_status="active",
                timestamp=_safe_time(repository.get("updated_at")),
            )
        )
    return _SourceInventory(
        source="code",
        items=tuple(items),
        raw_entity_count=raw_count,
        availability="AVAILABLE" if items else "EMPTY",
    )


def _codex_inventory(runtime: Runtime, project_id: str, acl_ref: str) -> _SourceInventory:
    threads = runtime.store.list_codex_threads(project_id=project_id, limit=_MAX_CODEX_THREADS)
    items: list[_InventoryItem] = []
    for thread in threads:
        if thread.get("acl_ref") not in {acl_ref, "public"}:
            continue
        title = _compact_text(thread.get("title") or "未命名开发会话", limit=800)
        counts = {
            "turns": int(thread.get("turn_count") or 0),
            "items": int(thread.get("item_count") or 0),
            "changes": int(thread.get("file_change_count") or 0),
            "commands": int(thread.get("command_count") or 0),
        }
        snippet = (
            f"开发会话“{title}”；{counts['turns']} 个回合、{counts['changes']} 个文件变更、"
            f"{counts['commands']} 个命令、{counts['items']} 个可见事件。状态 {thread.get('status') or 'unknown'}。"
        )
        roles = _roles(title, "change validation" if counts["changes"] else "decision goal")
        task = "change_trace" if counts["changes"] else "rationale"
        items.append(
            _InventoryItem(
                source="codex",
                entity_id=str(thread.get("thread_id") or thread.get("id")),
                entity_type="DevelopmentSession",
                title=title,
                snippet=snippet,
                locator=_typed_locator(
                    "codex", "thread", str(thread.get("thread_id") or thread.get("id"))
                ),
                stable_version=str(thread.get("source_hash") or canonical_sha256_v1(thread)),
                task=task,
                roles=roles,
                fact_status=str(thread.get("status") or "observed"),
                timestamp=_safe_time(thread.get("updated_at"), thread.get("started_at")),
                counter_evidence=str(thread.get("status") or "").casefold()
                in {"failed", "blocked"},
            )
        )
    raw_count = sum(
        int(source.get("stats", {}).get("sessions") or 0)
        for source in runtime.store.list_codex_sources()
        if source.get("project_id") == project_id
    )
    return _SourceInventory(
        source="codex",
        items=tuple(items),
        raw_entity_count=max(raw_count, len(threads)),
        availability="AVAILABLE" if items else "EMPTY",
        diagnostic=("bounded_to_recent_threads" if raw_count > len(items) else None),
    )


def _experiment_inventory(runtime: Runtime, project_id: str, acl_ref: str) -> _SourceInventory:
    experiments = runtime.experiments.store.list_experiments(project_id)
    runs = runtime.experiments.store.list_runs(project_id)
    items: list[_InventoryItem] = []
    for experiment in experiments[:_MAX_DOMAIN_ITEMS]:
        title = _compact_text(
            experiment.get("title") or experiment.get("display_key") or experiment.get("id"),
            limit=800,
        )
        snippet = _compact_text(
            f"实验 {title}。目标：{experiment.get('objective') or '未记录'}。假设：{experiment.get('hypothesis') or '未记录'}。"
            f"状态 {experiment.get('status') or 'unknown'}，运行 {experiment.get('run_count') or 0} 次。",
            limit=4_000,
        )
        items.append(
            _InventoryItem(
                "experiment",
                str(experiment["id"]),
                "Experiment",
                title,
                snippet,
                _typed_locator("experiment", "experiment", str(experiment["id"])),
                canonical_sha256_v1(experiment),
                "experiment_validation",
                _roles(title, snippet),
                str(experiment.get("status") or "observed"),
                _safe_time(experiment.get("updated_at"), experiment.get("created_at")),
            )
        )
    for run in runs[:_MAX_DOMAIN_ITEMS]:
        title = _compact_text(run.get("name") or run.get("display_key") or run.get("id"), limit=800)
        snippet = _compact_text(
            f"实验运行 {title}；状态 {run.get('status') or 'unknown'}，数据集 {run.get('dataset_id') or '未记录'}@{run.get('dataset_version') or '未记录'}，"
            f"指标 {run.get('metric_count') or 0}，工件 {run.get('artifact_count') or 0}。",
            limit=4_000,
        )
        status = str(run.get("status") or "observed")
        items.append(
            _InventoryItem(
                "experiment",
                str(run["id"]),
                "ExperimentRun",
                title,
                snippet,
                _typed_locator("experiment", "run", str(run["id"])),
                canonical_sha256_v1(run),
                "experiment_validation",
                _roles(title, snippet),
                status,
                _safe_time(run.get("updated_at"), run.get("completed_at"), run.get("created_at")),
                status.casefold() in {"failed", "cancelled"},
            )
        )
    return _SourceInventory(
        "experiment", tuple(items), len(experiments) + len(runs), "AVAILABLE" if items else "EMPTY"
    )


def _notebook_inventory(runtime: Runtime, project_id: str, acl_ref: str) -> _SourceInventory:
    runs = runtime.notebooks.store.list_runs(
        project_id, allowed_acl_refs=(acl_ref,), enforce_acl=True
    )
    items: list[_InventoryItem] = []
    for run in runs[:_MAX_DOMAIN_ITEMS]:
        title = _compact_text(run.get("template_name") or run.get("id"), limit=800)
        snippet = _compact_text(
            f"Notebook {title}；版本 {run.get('version') or 'unknown'}，状态 {run.get('status') or 'unknown'}，"
            f"{run.get('cell_count') or 0} 个单元、{run.get('output_count') or 0} 个输出。",
            limit=4_000,
        )
        status = str(run.get("status") or "observed")
        items.append(
            _InventoryItem(
                "notebook",
                str(run["id"]),
                "NotebookRun",
                title,
                snippet,
                _typed_locator("notebook", "run", str(run["id"])),
                str(run.get("content_hash") or canonical_sha256_v1(run)),
                "reproduction",
                _roles(title, snippet),
                status,
                _safe_time(run.get("completed_at"), run.get("created_at")),
                status.casefold() in {"failed", "error"},
            )
        )
    return _SourceInventory("notebook", tuple(items), len(runs), "AVAILABLE" if items else "EMPTY")


def _document_inventory(runtime: Runtime, project_id: str, acl_ref: str) -> _SourceInventory:
    documents = runtime.documents.store.list_documents(project_id)
    claims = runtime.documents.store.list_claims(project_id)
    items: list[_InventoryItem] = []
    for document in documents[:_MAX_DOMAIN_ITEMS]:
        title = _compact_text(document.get("title") or document.get("id"), limit=800)
        snippet = _compact_text(
            f"文档 {title}；版本 {document.get('version') or 'unknown'}，{document.get('section_count') or 0} 节，"
            f"{document.get('claim_count') or 0} 条声明，其中 {document.get('verified_claims') or 0} 条已核验。",
            limit=4_000,
        )
        items.append(
            _InventoryItem(
                "document",
                str(document["id"]),
                "Document",
                title,
                snippet,
                _typed_locator("document", "document", str(document["id"])),
                str(document.get("content_hash") or canonical_sha256_v1(document)),
                "claim_verification",
                _roles(title, snippet),
                str(document.get("status") or "observed"),
                _safe_time(document.get("updated_at"), document.get("created_at")),
            )
        )
    for claim in claims[:_MAX_DOMAIN_ITEMS]:
        title = _compact_text(
            claim.get("content") or claim.get("display_key") or claim.get("id"), limit=800
        )
        snippet = _compact_text(
            f"文档声明：{title}。状态 {claim.get('status') or 'reported'}，证据 {claim.get('evidence_count') or 0} 条。",
            limit=4_000,
        )
        status = str(claim.get("status") or "reported")
        items.append(
            _InventoryItem(
                "document",
                str(claim["id"]),
                "DocumentClaim",
                title,
                snippet,
                _typed_locator("document", "claim", str(claim["id"])),
                canonical_sha256_v1(claim),
                "claim_verification",
                _roles(title, snippet),
                status,
                _safe_time(claim.get("updated_at"), claim.get("created_at")),
                status.casefold() in {"contradicted", "rejected"},
            )
        )
    return _SourceInventory(
        "document", tuple(items), len(documents) + len(claims), "AVAILABLE" if items else "EMPTY"
    )


def _workspace_inventory(runtime: Runtime, project_id: str, acl_ref: str) -> _SourceInventory:
    project = runtime.workspace.store.get_project(project_id)
    topics = runtime.workspace.store.list_topics(project_id)
    iterations = runtime.workspace.store.list_iterations(project_id)
    work_items = runtime.workspace.store.list_work_items(project_id, limit=_MAX_DOMAIN_ITEMS)
    records: list[tuple[str, dict[str, Any], str, str]] = []
    if project:
        records.append(("Project", project, "architecture", "project"))
    records.extend(("ResearchTopic", item, "requirement", "topic") for item in topics)
    records.extend(("ResearchIteration", item, "procedure", "iteration") for item in iterations)
    records.extend(("WorkItem", item, "requirement", "work-item") for item in work_items)
    items: list[_InventoryItem] = []
    for entity_type, record, task, locator_type in records[:_MAX_DOMAIN_ITEMS]:
        entity_id = str(record.get("id") or record.get("display_key"))
        title = _compact_text(
            record.get("title") or record.get("name") or record.get("display_key") or entity_id,
            limit=800,
        )
        body = (
            record.get("description")
            or record.get("objective")
            or record.get("goal")
            or record.get("problem_statement")
            or record.get("summary")
            or ""
        )
        snippet = _compact_text(
            f"{entity_type} {title}。{body} 状态 {record.get('status') or 'active'}。", limit=4_000
        )
        status = str(record.get("status") or "active")
        items.append(
            _InventoryItem(
                "workspace",
                entity_id,
                entity_type,
                title,
                snippet,
                _typed_locator("workspace", locator_type, entity_id),
                canonical_sha256_v1(record),
                "issue" if status.casefold() == "blocked" else task,
                _roles(title, snippet),
                status,
                _safe_time(record.get("updated_at"), record.get("created_at")),
                status.casefold() == "blocked",
            )
        )
    return _SourceInventory(
        "workspace", tuple(items), len(records), "AVAILABLE" if items else "EMPTY"
    )


_COLLECTORS = {
    "code": _code_inventory,
    "codex": _codex_inventory,
    "experiment": _experiment_inventory,
    "notebook": _notebook_inventory,
    "document": _document_inventory,
    "workspace": _workspace_inventory,
}


def _collect(runtime: Runtime, project_id: str, acl_ref: str) -> tuple[_SourceInventory, ...]:
    inventories: list[_SourceInventory] = []
    for source in MULTISOURCE_DOMAINS:
        try:
            inventory = _COLLECTORS[source](runtime, project_id, acl_ref)
        except Exception as error:  # fail one source closed without losing other domains
            inventory = _SourceInventory(
                source=source,
                items=(),
                raw_entity_count=0,
                availability="UNAVAILABLE",
                diagnostic=f"inventory_{type(error).__name__}",
            )
        inventories.append(inventory)
    return tuple(inventories)


def assemble_live_wiki_request_v1(
    runtime: Runtime,
    *,
    project_id: str,
    acl_ref: str,
) -> tuple[WikiCompilationRequestV1, tuple[WikiOrganizationSourceSummaryV1, ...]]:
    """Build one deterministic six-source request from current project-owned records."""

    project = runtime.workspace.store.get_project(project_id)
    if project is None or str(project.get("acl_ref")) != acl_ref:
        raise WikiLiveOrganizationError("project authority is unavailable for live Wiki build")
    inventories = _collect(runtime, project_id, acl_ref)
    source_generations = []
    candidates: list[MultiSourceCandidateV2] = []
    summaries: list[WikiOrganizationSourceSummaryV1] = []
    timestamps: list[str] = [str(project.get("updated_at") or project.get("created_at") or "")]
    for inventory in inventories:
        inventory_digest = canonical_sha256_v1(
            {
                "availability": inventory.availability,
                "items": [asdict(item) for item in inventory.items],
                "raw_entity_count": inventory.raw_entity_count,
                "source": inventory.source,
                "version": WIKI_LIVE_ORGANIZER_VERSION,
            }
        )
        generation = build_source_generation_v1(
            source=WikiSourceDomainV1(inventory.source),
            generation_id=f"live-{inventory.source}-{inventory_digest[7:31]}",
            watermark=f"wm-{inventory.source}-{inventory_digest[7:31]}",
        )
        source_generations.append(generation)
        for item in inventory.items:
            timestamps.append(item.timestamp or "")
            identity = canonical_sha256_v1(
                {
                    "entity_id": item.entity_id,
                    "source": item.source,
                    "stable_version": item.stable_version,
                    "version": WIKI_LIVE_ORGANIZER_VERSION,
                }
            )
            candidates.append(
                MultiSourceCandidateV2(
                    candidate_id="candidate-" + identity[7:],
                    entity_id=item.entity_id,
                    retrieval_unit_id=f"inventory-unit-{identity[7:39]}",
                    parent_entity_id=None,
                    source_instance=f"{project_id}:{item.source}",
                    retrieval_domain=item.source,
                    fact_type=f"{item.source}.{item.entity_type.casefold().replace('.', '_')}",
                    entity_type=item.entity_type,
                    task=item.task,
                    title=item.title,
                    snippet=item.snippet,
                    locator=item.locator,
                    stable_version=item.stable_version,
                    source_generation=generation.generation_id,
                    raw_or_derived="derived_fact",
                    derivation=WIKI_LIVE_DERIVATION,
                    review_status="deterministic_derived",
                    fact_status=item.fact_status,
                    channel_scores=(("inventory_authority", 1.0),),
                    calibrated_relevance=1.0,
                    calibration_version=WIKI_LIVE_ORGANIZER_VERSION,
                    matched_roles=item.roles,
                    authority=0.96,
                    version_alignment="exact",
                    acl_ref=acl_ref,
                    token_estimate=max(
                        1, min(2_000, (len(item.title) + len(item.snippet)) // 4 + 1)
                    ),
                    root_provenance=item.stable_version,
                    counter_evidence=item.counter_evidence,
                )
            )
        summaries.append(
            WikiOrganizationSourceSummaryV1(
                source=WikiSourceDomainV1(inventory.source),
                availability=inventory.availability,
                raw_entity_count=inventory.raw_entity_count,
                candidate_count=len(inventory.items),
                generation_id=generation.generation_id,
                roles=tuple(sorted({role for item in inventory.items for role in item.roles})),
                diagnostic=inventory.diagnostic,
            )
        )
    candidates.sort(key=lambda item: item.candidate_id)
    observed_at = _safe_time(*timestamps) or "1970-01-01T00:00:00Z"
    build_identity = canonical_sha256_v1(
        {
            "candidates": [item.model_dump(mode="json") for item in candidates],
            "project_id": project_id,
            "source_generations": [item.model_dump(mode="json") for item in source_generations],
            "version": WIKI_LIVE_ORGANIZER_VERSION,
        }
    )
    request = build_wiki_compilation_request_v1(
        request_id=f"wiki-live-request-{build_identity[7:39]}",
        project_id=project_id,
        generation_id=f"wiki-live-{build_identity[7:39]}",
        source_generations=tuple(source_generations),
        candidates=tuple(candidates),
        observed_at=observed_at,
    )
    return request, tuple(summaries)


def preview_live_wiki_organization_v1(
    runtime: Runtime,
    *,
    project_id: str,
    acl_ref: str,
) -> tuple[WikiOrganizationPreviewV1, WikiCompilationRequestV1, WikiCompilationResultV1]:
    request, source_summaries = assemble_live_wiki_request_v1(
        runtime,
        project_id=project_id,
        acl_ref=acl_ref,
    )
    result = compile_wiki_v1(
        request,
        previous_error_book=runtime.wiki.store.list_error_book(project_id=project_id),
    )
    pages = tuple(item for item in result.records if isinstance(item, WikiPageFragmentV1))
    page_paths = {item.logical_path for item in pages}
    active = runtime.wiki.store.active_manifest(project_id)
    active_pages = (
        runtime.wiki.store.list_pages(
            project_id=project_id,
            requester_acl_refs=(acl_ref,),
            generation_id=active.generation_id,
        )
        if active is not None
        else ()
    )
    active_paths = {item.logical_path for item in active_pages}
    active_fixture = bool(
        active
        and any(
            token in generation.generation_id.casefold()
            for generation in active.source_generations
            for token in ("quality", "golden", "fixture", "synthetic")
        )
    )
    page_types = Counter(item.page_type.value for item in pages)
    roles = Counter(role for item in pages for role in item.evidence_roles)
    warnings = [
        f"{item.source.value}:{item.diagnostic}" for item in source_summaries if item.diagnostic
    ]
    warnings.extend(
        f"{item.source.value}:source_unavailable"
        for item in source_summaries
        if item.availability == "UNAVAILABLE"
    )
    payload = {
        "project_id": project_id,
        "generation_id": request.generation_id,
        "request_sha256": request.content_sha256,
        "active_generation_id": active.generation_id if active else None,
        "active_snapshot_kind": (
            "none"
            if active is None
            else "engineering_fixture"
            if active_fixture
            else "live_project"
        ),
        "source_summaries": source_summaries,
        "candidate_count": len(request.candidates),
        "compiled_candidate_count": result.compiled_candidate_count,
        "quarantined_candidate_count": result.quarantined_candidate_count,
        "page_count": len(pages),
        "page_type_counts": tuple(sorted(page_types.items())),
        "knowledge_role_counts": tuple(sorted(roles.items())),
        "added_page_count": len(page_paths - active_paths),
        "retained_page_count": len(page_paths & active_paths),
        "removed_page_count": len(active_paths - page_paths),
        "warnings": tuple(sorted(set(warnings))),
    }
    preview = WikiOrganizationPreviewV1(
        **payload,
        content_sha256=canonical_sha256_v1(
            WikiOrganizationPreviewV1.model_construct(
                **payload,
                organization_kind="live_project_inventory",
                requires_explicit_publish=True,
                quality_state="QUALITY_HOLD",
                organizer_version=WIKI_LIVE_ORGANIZER_VERSION,
                content_sha256="pending",
            ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
        ),
    )
    return preview, request, result


def stage_live_wiki_organization_v1(
    runtime: Runtime,
    *,
    project_id: str,
    acl_ref: str,
) -> WikiOrganizationStageV1:
    preview, request, _ = preview_live_wiki_organization_v1(
        runtime,
        project_id=project_id,
        acl_ref=acl_ref,
    )
    result = runtime.wiki.compile_to_staging(request)
    reviewer_authority = canonical_sha256_v1(
        {
            "action": "explicit_workbench_review_required",
            "manifest_sha256": result.manifest.content_sha256,
            "organizer_version": WIKI_LIVE_ORGANIZER_VERSION,
            "project_id": project_id,
        }
    )
    payload = {
        "project_id": project_id,
        "generation_id": result.manifest.generation_id,
        "manifest_sha256": result.manifest.content_sha256,
        "request_sha256": request.content_sha256,
        "candidate_count": preview.candidate_count,
        "page_count": preview.page_count,
        "quarantined_candidate_count": result.quarantined_candidate_count,
        "reviewer_authority_sha256": reviewer_authority,
    }
    return WikiOrganizationStageV1(
        **payload,
        content_sha256=canonical_sha256_v1(
            WikiOrganizationStageV1.model_construct(
                **payload,
                state="STAGED_FOR_REVIEW",
                published=False,
                organizer_version=WIKI_LIVE_ORGANIZER_VERSION,
                content_sha256="pending",
            ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
        ),
    )


__all__ = [
    "WIKI_LIVE_ORGANIZER_VERSION",
    "WikiLiveOrganizationError",
    "WikiOrganizationPreviewV1",
    "WikiOrganizationSourceSummaryV1",
    "WikiOrganizationStageV1",
    "assemble_live_wiki_request_v1",
    "preview_live_wiki_organization_v1",
    "stage_live_wiki_organization_v1",
]
