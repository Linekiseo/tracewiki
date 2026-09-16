from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .bindings.service import BindingService
from .bindings.store import BindingStore
from .code_history.service import CodeHistoryService
from .code_history.store import CodeHistoryStore
from .codex_bridge.service import CodexBridgeService
from .codex_bridge.store import CodexBridgeStore
from .codex_comparisons.service import CodexComparisonService
from .codex_comparisons.store import CodexComparisonStore
from .codex_discovery import (
    CompleteCodexIngestionService,
    CompleteCodexSessionAdapter,
    ProjectBindingRepositoryResolver,
)
from .codex_ingestion import CodexIngestionService
from .codex_retrieval import CodexHybridRetriever
from .config import Settings
from .documents.service import DocumentService
from .documents.store import DocumentStore
from .embeddings import LocalHashEmbedding
from .evaluation.service import EvaluationService
from .evaluation.store import EvaluationStore
from .experiments.service import ExperimentService
from .experiments.store import ExperimentStore
from .ingestion import IngestionService
from .notebooks.service import NotebookService
from .notebooks.store import NotebookStore
from .parser import CodeParser
from .platform.service import PlatformService
from .platform.store import PlatformStore
from .query.provider import RuntimeLLMProviderRegistry
from .query.service import UnifiedQueryService
from .rag.codex_v2 import CodexTemporalRetrieverV2
from .rag.document_v2 import DocumentStructuredRetrieverV2
from .rag.experiment_v2 import ExperimentStructuredRetrieverV2
from .rag.notebook_v2 import NotebookStructuredRetrieverV2
from .rag.sources.code import (
    CodeDenseProfilePublisher,
    CodeDenseRetriever,
    CodeDualWriteCoordinator,
    CodeExactSparseRetriever,
    CodeGraphProfilePublisher,
    CodeHistoryDiffExpansionHook,
    CodeHybridV2Retriever,
    CodePlatformIntegration,
    CodeShadowRunner,
    CodeSourceFusionPipeline,
    CodeSourceRetrieverV1Adapter,
    CodeTestValidationExpansionHookV2,
    CodeTypedGraphRetriever,
    DeterministicCodeReranker,
    ScipConsumer,
    SQLiteGraphAdjacencyProvider,
    resolve_code_embedding_profile,
)
from .rag.workspace_v2 import WorkspaceStructuredRetrieverV2
from .repository import RepositoryResolver
from .retrieval import HybridRetriever
from .sources.service import RawSourceService
from .sources.store import RawSourceStore
from .sqlite_lifecycle import serialize_sqlite_connection_lifecycle
from .storage import SQLiteStore
from .workspace.intelligence import ResearchIntelligenceService
from .workspace.service import WorkspaceService
from .workspace.store import WorkspaceStore

if TYPE_CHECKING:
    from .rag.multisource_runtime_v2 import MultiSourceRuntimeV2
    from .rag.performance_runtime_v2 import RuntimePerformanceV2
    from .rag.production_sources_v2 import ProductionSourceRuntimeRegistryV2
    from .rag.release_control_plane_v2 import (
        CanonicalReleaseControlPlaneV2,
        RuntimeReleaseStatusV2,
    )
    from .rag.wiki.query_v1 import WikiIntelligentQueryV1
    from .rag.wiki.runtime_v1 import WikiRuntimeV1


@dataclass(slots=True)
class Runtime:
    _release_control_plane: CanonicalReleaseControlPlaneV2
    settings: Settings
    store: SQLiteStore
    ingestion: IngestionService
    retriever: HybridRetriever
    code_v1_adapter: CodeSourceRetrieverV1Adapter
    code_integration: CodePlatformIntegration
    code_shadow: CodeShadowRunner | None
    experiment_v2: ExperimentStructuredRetrieverV2
    notebook_v2: NotebookStructuredRetrieverV2
    document_v2: DocumentStructuredRetrieverV2
    workspace_v2: WorkspaceStructuredRetrieverV2
    source_runtime_v2: ProductionSourceRuntimeRegistryV2
    global_v2: MultiSourceRuntimeV2
    performance_v2: RuntimePerformanceV2
    llm_providers: RuntimeLLMProviderRegistry
    answers: UnifiedQueryService
    codex_ingestion: CodexIngestionService
    codex_retriever: CodexHybridRetriever
    codex_v2: CodexTemporalRetrieverV2
    codex_comparisons: CodexComparisonService
    workspace: WorkspaceService
    intelligence: ResearchIntelligenceService
    bindings: BindingService
    experiments: ExperimentService
    documents: DocumentService
    platform: PlatformService
    evaluation: EvaluationService
    sources: RawSourceService
    code_history: CodeHistoryService
    notebooks: NotebookService
    codex_bridge: CodexBridgeService
    wiki: WikiRuntimeV1
    wiki_query: WikiIntelligentQueryV1

    @property
    def release_control_plane(self) -> CanonicalReleaseControlPlaneV2:
        """Expose the process singleton without a public replacement slot."""

        return self._release_control_plane

    def rag_status(self) -> RuntimeReleaseStatusV2:
        """Merge bounded in-memory observations under canonical release truth."""

        try:
            operational_snapshot = self.global_v2.operational_snapshot()
        except Exception:
            # Status must remain available and sanitized when an optional
            # in-memory observer is unavailable or malformed.
            operational_snapshot = None
        return self._release_control_plane.runtime_status(operational_snapshot)

    def close(self) -> None:
        """Release disposable V2 derived indexes owned by this runtime."""

        self.source_runtime_v2.close()


def create_runtime(settings: Settings | None = None) -> Runtime:
    from .rag.global_switches_v2 import GlobalComponentSwitchesV2
    from .rag.multisource_runtime_v2 import (
        MultiSourceRuntimeV2,
        load_reviewed_calibration_bundle_v2,
    )
    from .rag.performance_runtime_v2 import RuntimePerformanceV2
    from .rag.production_sources_v2 import ProductionSourceRuntimeRegistryV2
    from .rag.release_control_plane_v2 import canonical_release_control_plane_v2
    from .rag.wiki.providers_v1 import (
        HttpWikiRerankerV1,
        OpenAICompatibleWikiEmbeddingProviderV1,
    )
    from .rag.wiki.query_v1 import WikiIntelligentQueryV1
    from .rag.wiki.runtime_v1 import WikiRuntimeV1
    from .rag.wiki.store_v1 import WikiStoreV1

    release_control_plane = canonical_release_control_plane_v2()
    resolved = settings or Settings.from_env()
    if (
        resolved.deployment_mode == "production"
        and release_control_plane.current_status().quality_hold
    ):
        resolved = resolved.with_canonical_release_hold()
    resolved.prepare()
    store = SQLiteStore(resolved.database_path)
    serialize_sqlite_connection_lifecycle(store)
    store.initialize()
    workspace_store = WorkspaceStore(store)
    workspace_store.initialize()
    workspace = WorkspaceService(workspace_store)
    workspace.ensure_default_project()
    raw_store = RawSourceStore(store)
    raw_store.initialize()
    sources = RawSourceService(raw_store, resolved.raw_object_dir)
    binding_store = BindingStore(store)
    binding_store.initialize()
    bindings = BindingService(binding_store, workspace)
    experiment_store = ExperimentStore(store)
    experiment_store.initialize()
    experiments = ExperimentService(
        experiment_store,
        workspace,
        sources,
        mlflow_allowed_local_roots=resolved.allowed_local_roots,
    )
    experiment_v2 = ExperimentStructuredRetrieverV2(experiment_store)
    document_store = DocumentStore(store)
    document_store.initialize()
    documents = DocumentService(document_store, experiment_store, workspace, resolved, sources)
    document_v2 = DocumentStructuredRetrieverV2(document_store)
    notebook_store = NotebookStore(store)
    notebook_store.initialize()
    notebooks = NotebookService(notebook_store, workspace, sources, resolved)
    notebook_v2 = NotebookStructuredRetrieverV2(notebook_store)
    workspace_v2 = WorkspaceStructuredRetrieverV2(workspace_store)
    source_runtime_v2 = ProductionSourceRuntimeRegistryV2(
        formal_store=store,
        experiments=experiments,
        notebooks=notebook_store,
        documents=document_store,
        workspace_service=workspace,
        workspace_store=workspace_store,
        raw_sources=sources,
    )
    platform_store = PlatformStore(store)
    platform_store.initialize()
    embedder = LocalHashEmbedding(resolved.embedding_dimensions)
    retriever = HybridRetriever(store, embedder)
    code_v1_adapter = CodeSourceRetrieverV1Adapter(retriever)
    code_exact_sparse = CodeExactSparseRetriever(store)
    code_embedding_profile, code_embedding_provider = resolve_code_embedding_profile(
        resolved.rag_code_embedding_profile,
        dimension=resolved.embedding_dimensions,
    )
    code_hybrid = CodeHybridV2Retriever(
        store,
        dense_retriever=CodeDenseRetriever(
            store,
            embedding_profile=code_embedding_profile,
            provider=code_embedding_provider,
        ),
        exact_sparse_retriever=code_exact_sparse,
    )
    code_graph_retriever = (
        CodeTypedGraphRetriever(SQLiteGraphAdjacencyProvider(resolved.database_path))
        if resolved.rag_code_graph
        else None
    )
    code_v2_pipeline = CodeSourceFusionPipeline(
        code_exact_sparse,
        hybrid_retriever=code_hybrid,
        graph_retriever=code_graph_retriever,
        reranker=(DeterministicCodeReranker() if resolved.rag_code_reranker == "profile" else None),
        history_hook=(
            CodeHistoryDiffExpansionHook(store)
            if resolved.rag_code_unit_builder == "ast-v2"
            else None
        ),
        test_hook=(
            CodeTestValidationExpansionHookV2(store)
            if resolved.rag_code_unit_builder == "ast-v2"
            else None
        ),
    )
    code_integration = CodePlatformIntegration(
        settings=resolved,
        store=store,
        legacy=retriever,
        v1_adapter=code_v1_adapter,
        v2_pipeline=code_v2_pipeline,
    )
    shadow_v2 = (
        code_integration.shadow_v2
        if resolved.rag_code_unit_builder == "ast-v2" or resolved.rag_code_engine == "v2"
        else None
    )
    code_shadow = CodeShadowRunner(code_v1_adapter, shadow_v2) if resolved.rag_code_shadow else None
    codex_retriever = CodexHybridRetriever(store, embedder)
    codex_v2 = CodexTemporalRetrieverV2(codex_retriever)
    codex_comparison_store = CodexComparisonStore(store)
    codex_comparison_store.initialize()
    codex_comparisons = CodexComparisonService(codex_comparison_store, store, workspace)
    evaluation_store = EvaluationStore(store)
    evaluation_store.initialize()
    code_history_store = CodeHistoryStore(store)
    code_history_store.initialize()
    code_history = CodeHistoryService(code_history_store, sources)
    platform = PlatformService(
        platform_store,
        retriever,
        codex_retriever,
        event_store=evaluation_store,
        code_v1_adapter=code_v1_adapter,
        code_integration=code_integration,
        code_shadow=code_shadow,
        codex_v2=codex_v2,
        experiment_v2=experiment_v2,
        notebook_v2=notebook_v2,
        document_v2=document_v2,
        workspace_v2=workspace_v2,
        source_runtime_v2=source_runtime_v2,
    )
    calibration_artifacts = (
        load_reviewed_calibration_bundle_v2(
            resolved.rag_multisource_calibration_bundle,
            expected_sha256=resolved.rag_multisource_calibration_sha256,
        )
        if resolved.rag_multisource_calibration_bundle is not None
        and resolved.rag_multisource_calibration_sha256 is not None
        else {}
    )
    performance_v2 = RuntimePerformanceV2()
    global_v2 = MultiSourceRuntimeV2(
        platform=platform,
        calibration_artifacts=calibration_artifacts,
        performance_runtime=performance_v2,
        component_switches=GlobalComponentSwitchesV2(
            generator_prompt=resolved.rag_global_generator_prompt_v2,
            context_packer=resolved.rag_global_context_packer_v2,
            fusion=resolved.rag_global_fusion_v2,
            reranker=resolved.rag_global_reranker_v2,
            embedding_generation=resolved.rag_global_embedding_generation_v2,
            source_retrievers=resolved.rag_global_source_retrievers_v2,
            planner=resolved.rag_global_planner_v2,
        ),
    )
    intelligence = ResearchIntelligenceService(store, workspace_store)
    wiki_dense_provider = (
        OpenAICompatibleWikiEmbeddingProviderV1(
            base_url=resolved.wiki_embedding_base_url,
            api_key=resolved.wiki_embedding_api_key,
            model_id=resolved.wiki_embedding_model,
            dimension=resolved.wiki_embedding_dimension,
            timeout_seconds=resolved.wiki_embedding_timeout_seconds,
        )
        if resolved.wiki_embedding_base_url is not None
        and resolved.wiki_embedding_model is not None
        else None
    )
    wiki_reranker = (
        HttpWikiRerankerV1(
            base_url=resolved.wiki_reranker_base_url,
            api_key=resolved.wiki_reranker_api_key,
            model_id=resolved.wiki_reranker_model,
            timeout_seconds=resolved.wiki_reranker_timeout_seconds,
        )
        if resolved.wiki_reranker_base_url is not None and resolved.wiki_reranker_model is not None
        else None
    )
    wiki = WikiRuntimeV1(
        store=WikiStoreV1(
            resolved.wiki_database_path,
            isolated_root=resolved.resolved_wiki_data_dir,
        ),
        dense_provider=wiki_dense_provider,
        reranker=wiki_reranker,
    )
    llm_providers = RuntimeLLMProviderRegistry(resolved)
    wiki_query = WikiIntelligentQueryV1(
        settings=resolved,
        wiki=wiki,
        providers=llm_providers,
    )
    codex_bridge_store = CodexBridgeStore(store)
    codex_bridge_store.initialize()
    codex_bridge = CodexBridgeService(
        codex_bridge_store,
        workspace,
        platform,
        wiki=wiki,
        intelligence=intelligence,
        project_root=resolved.project_root or resolved.allowed_local_roots[0],
        artifact_dir=resolved.data_dir / "codex-executions",
        plugin_dir=(resolved.project_root or resolved.allowed_local_roots[0])
        / "plugins"
        / "research-project-bridge",
        token_configured=bool(resolved.mcp_token),
        configured_authority_id=(
            "configured-token://sha256:" + codex_bridge_store.hash_token(resolved.mcp_token)
            if resolved.mcp_token
            else None
        ),
    )
    evaluation = EvaluationService(evaluation_store, platform, workspace)
    code_dual_writer = None
    if resolved.rag_code_unit_builder == "ast-v2":
        code_dual_writer = CodeDualWriteCoordinator(
            store,
            dense_publisher=(
                CodeDenseProfilePublisher(
                    store,
                    provider=code_embedding_provider,
                )
                if resolved.rag_code_dense_index
                else None
            ),
            dense_profile=(code_embedding_profile if resolved.rag_code_dense_index else None),
            graph_publisher=(CodeGraphProfilePublisher(store) if resolved.rag_code_graph else None),
        )
    ingestion = IngestionService(
        store=store,
        resolver=ProjectBindingRepositoryResolver(RepositoryResolver(resolved), store),
        parser=CodeParser(),
        embedder=embedder,
        sources=sources,
        history=code_history,
        code_dual_writer=code_dual_writer,
        code_semantic_consumer=(
            ScipConsumer() if resolved.rag_code_semantic_resolver == "scip-python" else None
        ),
    )
    codex_ingestion = CompleteCodexIngestionService(
        store=store,
        adapter=CompleteCodexSessionAdapter(resolved),
        embedder=embedder,
        sources=sources,
    )
    return Runtime(
        _release_control_plane=release_control_plane,
        settings=resolved,
        store=store,
        ingestion=ingestion,
        retriever=retriever,
        code_v1_adapter=code_v1_adapter,
        code_integration=code_integration,
        code_shadow=code_shadow,
        experiment_v2=experiment_v2,
        notebook_v2=notebook_v2,
        document_v2=document_v2,
        workspace_v2=workspace_v2,
        source_runtime_v2=source_runtime_v2,
        global_v2=global_v2,
        performance_v2=performance_v2,
        llm_providers=llm_providers,
        answers=UnifiedQueryService(
            resolved,
            platform,
            global_v2=global_v2,
            workspace=workspace,
            providers=llm_providers,
        ),
        codex_ingestion=codex_ingestion,
        codex_retriever=codex_retriever,
        codex_v2=codex_v2,
        codex_comparisons=codex_comparisons,
        workspace=workspace,
        intelligence=intelligence,
        bindings=bindings,
        experiments=experiments,
        documents=documents,
        platform=platform,
        evaluation=evaluation,
        sources=sources,
        code_history=code_history,
        notebooks=notebooks,
        codex_bridge=codex_bridge,
        wiki=wiki,
        wiki_query=wiki_query,
    )
