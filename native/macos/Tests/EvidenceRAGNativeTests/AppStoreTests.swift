import Foundation
import XCTest

@testable import EvidenceRAGNative

@MainActor
final class AppStoreTests: XCTestCase {
  func testStartLoadsProjectProviderAndHistory() async {
    let backend = BackendStub()
    let store = AppStore(
      backend: backend,
      keychain: MemorySecretStore(),
      defaults: isolatedDefaults()
    )

    await store.start()

    XCTAssertEqual(store.state, .loaded)
    XCTAssertEqual(store.selectedProjectID, "project-rag")
    XCTAssertEqual(store.selectedRepositoryID, "repo://local/rag")
    XCTAssertEqual(store.providerStatus?.configured, true)
    XCTAssertEqual(store.queryHistory.count, 1)
  }

  func testQueryStateTransitionsAndStructuredSources() async {
    let backend = BackendStub()
    let store = AppStore(
      backend: backend,
      keychain: MemorySecretStore(),
      defaults: isolatedDefaults()
    )
    await store.start()

    await store.runQuery(
      question: "Explain the current implementation",
      sources: ["code", "codex"],
      allowGeneration: true
    )

    XCTAssertEqual(store.queryState, .loaded)
    XCTAssertEqual(store.queryResponse?.answer.text, "Grounded answer [E1]")
    XCTAssertEqual(backend.lastQuery?.scope.sourceTypes, ["code", "codex"])
    XCTAssertEqual(backend.lastQuery?.scope.repositoryIDs, ["repo://local/rag"])
    XCTAssertEqual(backend.lastQuery?.scope.commit, "abcdef")
    XCTAssertEqual(backend.lastQuery?.mode, "answer")
  }

  func testBlankQueryFailsBeforeBackend() async {
    let backend = BackendStub()
    let store = AppStore(
      backend: backend,
      keychain: MemorySecretStore(),
      defaults: isolatedDefaults()
    )
    await store.runQuery(question: "   ", sources: [], allowGeneration: false)
    XCTAssertEqual(store.queryState, .failed("请输入要核验的问题。"))
    XCTAssertNil(backend.lastQuery)
  }

  private func isolatedDefaults() -> UserDefaults {
    let name = "EvidenceRAGNativeTests.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: name)!
    defaults.removePersistentDomain(forName: name)
    return defaults
  }
}

private final class BackendStub: BackendServicing {
  var lastQuery: QueryRequest?

  func projects() async throws -> [Project] { [.fixture] }
  func repositories(projectID: String) async throws -> [RepositorySummary] {
    [
      RepositorySummary(
        id: "repo://local/rag",
        projectID: projectID,
        name: "rag",
        defaultBranch: "main",
        headCommit: "abcdef",
        status: "ready",
        activeGenerationID: "gen-one"
      )
    ]
  }
  func providerStatus(projectID: String) async throws -> ProviderStatus { .fixture }
  func configureProvider(_ configuration: ProviderConfiguration) async throws -> ProviderStatus {
    .fixture
  }
  func testProvider(projectID: String) async throws -> ProviderStatus { .fixture }
  func runQuery(_ request: QueryRequest) async throws -> QueryResponse {
    lastQuery = request
    return try JSONDecoder().decode(QueryResponse.self, from: Data(APIClientTests.queryJSON.utf8))
  }
  func queryHistory(projectID: String, limit: Int) async throws -> QueryHistoryResponse {
    QueryHistoryResponse(
      contractVersion: "query-history-v1",
      projectID: projectID,
      items: [
        QueryHistoryItem(
          id: "history-1",
          createdAt: "2026-08-04T00:00:00Z",
          queryID: "query://1",
          questionDigest: "sha256:abc",
          intent: "current_implementation",
          interactionState: "completed",
          answerMode: "GENERATED",
          reasonCode: nil,
          evidence: HistoryEvidence(
            citationIDs: ["E1"], sources: ["code"], entityIDs: ["code://1"], count: 1)
        )
      ]
    )
  }
  func wikiPages(projectID: String, query: String, offset: Int, limit: Int) async throws
    -> WikiPageListResponse
  {
    try JSONDecoder().decode(WikiPageListResponse.self, from: Data(DecoderTests.wikiJSON.utf8))
  }
  func wikiPage(projectID: String, path: String, generationID: String?) async throws -> WikiPage {
    try await wikiPages(projectID: projectID, query: "", offset: 0, limit: 1).pages[0]
  }
  func graph(projectID: String, limit: Int) async throws -> GraphSnapshot {
    GraphSnapshot(
      domain: "overview",
      mode: "relations",
      query: "",
      nodes: [],
      edges: [],
      metadata: GraphMetadata(totalNodes: 0, totalEdges: 0, visibleNodes: 0, visibleEdges: 0)
    )
  }
  func sessions(projectID: String, limit: Int) async throws -> [CodexSession] { [] }
  func agents(projectID: String) async throws -> [AgentSummary] { [] }
}

private final class MemorySecretStore: SecretStoring {
  private var values: [String: String] = [:]
  func save(_ value: String, account: String) throws { values[account] = value }
  func read(account: String) throws -> String? { values[account] }
  func delete(account: String) throws { values.removeValue(forKey: account) }
}

extension Project {
  fileprivate static let fixture = Project(
    id: "project-rag",
    name: "RAG",
    description: "Native client",
    owner: "team",
    aclRef: "project:rag",
    classification: "internal",
    status: "active",
    createdAt: "2026-08-04T00:00:00Z",
    updatedAt: "2026-08-04T01:00:00Z",
    topicCount: 1,
    activeIterationCount: 1,
    repositories: 2,
    sessions: 3,
    experiments: 4,
    documents: 5,
    pendingReviews: 6
  )
}

extension ProviderStatus {
  fileprivate static let fixture = ProviderStatus(
    contractVersion: "llm-provider-runtime-v1",
    projectID: "project-rag",
    provider: "openai_compatible",
    baseURL: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
    configured: true,
    apiKeyPresent: true,
    secretStorage: "native_keychain",
    source: "desktop_runtime",
    capabilities: ["grounded_generation"],
    health: "ready",
    tested: true
  )
}
