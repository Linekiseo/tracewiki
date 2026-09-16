import subprocess

from evidence_rag.code_history.service import CodeHistoryError
from evidence_rag.models import EvidenceSearchRequest, RepositoryIngestRequest, SearchScope
from evidence_rag.repository import RepositoryResolver
from evidence_rag.runtime import create_runtime


def test_repository_becomes_versioned_searchable_evidence(settings, sample_repository) -> None:
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["status"] == "completed"
    assert workflow["counters"]["files"] == 3
    assert workflow["counters"]["symbols"] >= 5

    repositories = runtime.store.list_repositories()
    assert len(repositories) == 1
    repository = repositories[0]
    assert repository["active_generation_id"].startswith("gen-")
    assert repository["head_commit"].startswith("worktree-")

    result = runtime.retriever.search(
        EvidenceSearchRequest(
            query="normalize_score relevance score",
            scope={"repository_ids": [repository["id"]]},
            limit=5,
        )
    )
    assert result["results"]
    top = result["results"][0]
    assert top["commit"] == repository["head_commit"]
    assert top["evidence_locator"].startswith("sample-code@")
    assert any(
        "normalize_score" in (item["qualified_name"] or item["name"]) for item in result["results"]
    )
    assert any(edge["edge_type"] == "CALLS" for item in result["results"] for edge in item["edges"])
    assert len(runtime.store.dense_candidates(SearchScope())) > len(
        runtime.store.dense_candidates(SearchScope(), limit=1)
    )
    semantic_results = runtime.retriever.search(
        EvidenceSearchRequest(
            query="ranking dense score",
            scope={"repository_ids": [repository["id"]]},
            limit=12,
            include_edges=False,
        )
    )
    semantic_links = runtime.retriever.semantic_links(
        [item["entity_id"] for item in semantic_results["results"]]
    )
    assert semantic_links
    assert {link["predicate"] for link in semantic_links} == {"SEMANTIC_SIMILAR"}
    assert all(link["derivation"] == "vector_cosine" for link in semantic_links)
    assert all(0.16 <= link["confidence"] <= 1 for link in semantic_links)


def test_high_confidence_secret_file_is_quarantined(settings, sample_repository) -> None:
    (sample_repository / "src" / "leaked.py").write_text(
        'TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"\n', encoding="utf-8"
    )
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow["status"] == "completed"
    assert workflow["counters"]["quarantined"] == 1
    repository = runtime.store.list_repositories()[0]
    paths = {item["path"] for item in runtime.store.list_files(repository["id"])}
    assert "src/leaked.py" not in paths


def test_overloaded_symbols_receive_distinct_stable_ids(settings, sample_repository) -> None:
    component = sample_repository / "src" / "model.ts"
    component.write_text(
        """
class Graph {
  get Elements(): string[] {
    return [];
  }
  set Elements(elements: string[]) {
    void elements;
  }
}
""".strip(),
        encoding="utf-8",
    )
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["status"] == "completed"
    repository = runtime.store.list_repositories()[0]
    indexed = runtime.store.get_file(repository["id"], "src/model.ts")
    assert indexed is not None
    elements = [
        symbol for symbol in indexed["symbols"] if symbol["qualified_name"].endswith(".Elements")
    ]
    assert len(elements) == 2
    assert len({symbol["id"] for symbol in elements}) == 2


def test_git_discovery_respects_ignore_rules_for_generated_local_data(settings, tmp_path) -> None:
    repository = tmp_path / "git-project"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / ".gitignore").write_text("var/\n", encoding="utf-8")
    (repository / "main.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    generated = repository / "var" / "cache"
    generated.mkdir(parents=True)
    (generated / "generated.py").write_text("SHOULD_NOT_INDEX = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", ".gitignore", "main.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )

    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow and workflow["status"] == "completed"
    indexed = runtime.store.list_files(runtime.store.list_repositories()[0]["id"])
    assert {item["path"] for item in indexed} == {"main.py"}


def test_git_discovery_deduplicates_paths_reported_by_git(settings, tmp_path, monkeypatch) -> None:
    repository = tmp_path / "git-project"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    source = repository / "main.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    alias = repository / "answer.py"
    alias.symlink_to(source.name)
    subprocess.run(["git", "-C", str(repository), "add", "main.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )

    resolver = RepositoryResolver(settings)
    snapshot = resolver.resolve(str(repository), project_id="test", acl_ref="public")
    completed_process = subprocess.CompletedProcess(
        args=["git", "ls-files"],
        returncode=0,
        stdout=b"main.py\0main.py\0answer.py\0",
        stderr=b"",
    )
    monkeypatch.setattr(
        "evidence_rag.repository.subprocess.run",
        lambda *args, **kwargs: completed_process,
    )

    assert resolver.discover_files(snapshot, []) == [source]


def test_missing_git_history_does_not_block_current_code_publication(
    settings, sample_repository, monkeypatch
) -> None:
    runtime = create_runtime(settings)

    def missing_history(*args, **kwargs):
        assert kwargs["fetch_remote"] is False
        raise CodeHistoryError("missing promisor object")

    monkeypatch.setattr(runtime.ingestion.history, "sync", missing_history)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow and workflow["status"] == "completed"
    assert workflow["counters"]["history_errors"] == 1
    repository = runtime.store.list_repositories()[0]
    assert repository["active_generation_id"]
    assert runtime.store.list_files(repository["id"])


def test_call_graph_does_not_bind_unimported_common_method_names(
    settings, sample_repository
) -> None:
    (sample_repository / "src" / "helper.py").write_text(
        "class Helper:\n    def get(self, key: str):\n        return key\n",
        encoding="utf-8",
    )
    (sample_repository / "src" / "consumer.py").write_text(
        "def lookup(values: dict[str, str]):\n    return values.get('answer')\n",
        encoding="utf-8",
    )
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    repository = runtime.store.list_repositories()[0]
    helper = runtime.store.get_file(repository["id"], "src/helper.py")
    consumer = runtime.store.get_file(repository["id"], "src/consumer.py")
    assert helper and consumer
    helper_get = next(item["id"] for item in helper["symbols"] if item["name"] == "get")
    lookup = next(item["id"] for item in consumer["symbols"] if item["name"] == "lookup")
    edges = runtime.store.edges_for_entities([lookup])
    assert not any(
        edge["source_id"] == lookup
        and edge["target_id"] == helper_get
        and edge["edge_type"] == "CALLS"
        for edge in edges
    )


def test_reference_graph_links_unambiguous_non_call_symbol_use(settings, sample_repository) -> None:
    (sample_repository / "src" / "config.py").write_text(
        "class SearchConfig:\n"
        "    pass\n\n"
        "def describe(config_type=SearchConfig):\n"
        "    return config_type.__name__\n",
        encoding="utf-8",
    )
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    repository = runtime.store.list_repositories()[0]
    indexed = runtime.store.get_file(repository["id"], "src/config.py")
    assert indexed is not None
    config = next(item["id"] for item in indexed["symbols"] if item["name"] == "SearchConfig")
    describe = next(item["id"] for item in indexed["symbols"] if item["name"] == "describe")
    edges = runtime.store.edges_for_entities([describe])
    assert any(
        edge["source_id"] == describe
        and edge["target_id"] == config
        and edge["edge_type"] == "REFERENCES"
        for edge in edges
    )
