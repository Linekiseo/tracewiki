import Foundation

@MainActor
final class AppStore: ObservableObject {
  enum LoadState: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)

    var isLoading: Bool { self == .loading }
  }

  @Published var selection: AppSection = .projects
  @Published private(set) var projects: [Project] = []
  @Published private(set) var repositories: [RepositorySummary] = []
  @Published var selectedProjectID: String?
  @Published var selectedRepositoryID: String?
  @Published private(set) var providerStatus: ProviderStatus?
  @Published private(set) var queryResponse: QueryResponse?
  @Published private(set) var queryHistory: [QueryHistoryItem] = []
  @Published private(set) var wikiPages: [WikiPage] = []
  @Published var selectedWikiPage: WikiPage?
  @Published private(set) var graphSnapshot: GraphSnapshot?
  @Published private(set) var sessions: [CodexSession] = []
  @Published private(set) var agents: [AgentSummary] = []
  @Published private(set) var state: LoadState = .idle
  @Published private(set) var queryState: LoadState = .idle
  @Published private(set) var providerState: LoadState = .idle
  @Published var serverURLText: String
  @Published var providerBaseURL: String
  @Published var providerModel: String

  private var backend: BackendServicing
  private let keychain: SecretStoring
  private let defaults: UserDefaults

  static func live() -> AppStore {
    let defaults = UserDefaults.standard
    let rawURL = defaults.string(forKey: "native.serverURL") ?? "http://127.0.0.1:8765"
    let url = URL(string: rawURL) ?? URL(string: "http://127.0.0.1:8765")!
    return AppStore(
      backend: APIClient(baseURL: url),
      keychain: KeychainStore(),
      defaults: defaults
    )
  }

  init(backend: BackendServicing, keychain: SecretStoring, defaults: UserDefaults = .standard) {
    self.backend = backend
    self.keychain = keychain
    self.defaults = defaults
    serverURLText = defaults.string(forKey: "native.serverURL") ?? "http://127.0.0.1:8765"
    providerBaseURL =
      defaults.string(forKey: "native.providerBaseURL") ?? "https://api.deepseek.com"
    providerModel = defaults.string(forKey: "native.providerModel") ?? "deepseek-v4-flash"
  }

  var selectedProject: Project? {
    projects.first { $0.id == selectedProjectID }
  }

  func start() async {
    guard projects.isEmpty, !state.isLoading else { return }
    await loadProjects()
  }

  func loadProjects() async {
    state = .loading
    do {
      projects = try await backend.projects()
      if selectedProjectID == nil || !projects.contains(where: { $0.id == selectedProjectID }) {
        selectedProjectID =
          projects.first(where: { $0.status != "archived" })?.id ?? projects.first?.id
      }
      state = .loaded
      await loadProjectContext()
    } catch {
      state = .failed(error.localizedDescription)
    }
  }

  func loadProjectContext() async {
    guard let projectID = selectedProjectID else { return }
    await loadRepositories(projectID: projectID)
    await loadProviderStatus(projectID: projectID)
    if providerStatus?.configured != true {
      await restoreStoredProviderIfAvailable(projectID: projectID)
    }
    await loadQueryHistory(projectID: projectID)
  }

  func selectProject(_ projectID: String) async {
    selectedProjectID = projectID
    repositories = []
    selectedRepositoryID = nil
    queryResponse = nil
    selectedWikiPage = nil
    await loadProjectContext()
    await refreshCurrentSection()
  }

  func refreshCurrentSection() async {
    guard let projectID = selectedProjectID else {
      await loadProjects()
      return
    }
    switch selection {
    case .projects: await loadProjects()
    case .trustedQuery:
      await loadProviderStatus(projectID: projectID)
      await loadQueryHistory(projectID: projectID)
    case .wiki: await loadWiki(projectID: projectID, query: "")
    case .graph: await loadGraph(projectID: projectID)
    case .sessions: await loadSessions(projectID: projectID)
    case .agents: await loadAgents(projectID: projectID)
    case .settings: await loadProviderStatus(projectID: projectID)
    }
  }

  func runQuery(question: String, sources: Set<String>, allowGeneration: Bool) async {
    let normalized = question.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty else {
      queryState = .failed("请输入要核验的问题。")
      return
    }
    guard let projectID = selectedProjectID else {
      queryState = .failed("请先选择项目。")
      return
    }
    if sources.contains("code"), selectedRepositoryID == nil {
      queryState = .failed("代码检索需要选择一个已发布的仓库。")
      return
    }
    queryState = .loading
    do {
      queryResponse = try await backend.runQuery(
        QueryRequest(
          question: normalized,
          scope: QueryScope(
            projectID: projectID,
            repositoryIDs: selectedRepositoryID.map { [$0] } ?? [],
            branch: selectedRepository?.defaultBranch,
            commit: selectedRepository?.headCommit,
            sourceTypes: sources.sorted()
          ),
          mode: allowGeneration ? "answer" : "evidence",
          maxEvidence: 16,
          include: sources.sorted(),
          deadlineMilliseconds: 60_000,
          answerFormat: allowGeneration ? "detailed" : "evidence_only"
        ))
      queryState = .loaded
      await loadQueryHistory(projectID: projectID)
    } catch {
      queryState = .failed(error.localizedDescription)
    }
  }

  func loadWiki(projectID: String? = nil, query: String) async {
    guard let projectID = projectID ?? selectedProjectID else { return }
    state = .loading
    do {
      let response = try await backend.wikiPages(
        projectID: projectID, query: query, offset: 0, limit: 80)
      wikiPages = response.pages
      if let selectedWikiPage,
        wikiPages.contains(where: { $0.id == selectedWikiPage.id })
      {
        self.selectedWikiPage = selectedWikiPage
      } else {
        selectedWikiPage = wikiPages.first
      }
      state = .loaded
    } catch {
      state = .failed(error.localizedDescription)
    }
  }

  func readWikiPage(_ page: WikiPage) async {
    guard let projectID = selectedProjectID else { return }
    state = .loading
    do {
      selectedWikiPage = try await backend.wikiPage(
        projectID: projectID,
        path: page.logicalPath,
        generationID: nil
      )
      state = .loaded
    } catch {
      state = .failed(error.localizedDescription)
    }
  }

  func loadGraph(projectID: String? = nil) async {
    guard let projectID = projectID ?? selectedProjectID else { return }
    state = .loading
    do {
      graphSnapshot = try await backend.graph(projectID: projectID, limit: 180)
      state = .loaded
    } catch {
      state = .failed(error.localizedDescription)
    }
  }

  func loadSessions(projectID: String? = nil) async {
    guard let projectID = projectID ?? selectedProjectID else { return }
    state = .loading
    do {
      sessions = try await backend.sessions(projectID: projectID, limit: 200)
      state = .loaded
    } catch {
      state = .failed(error.localizedDescription)
    }
  }

  func loadAgents(projectID: String? = nil) async {
    guard let projectID = projectID ?? selectedProjectID else { return }
    state = .loading
    do {
      agents = try await backend.agents(projectID: projectID)
      state = .loaded
    } catch {
      state = .failed(error.localizedDescription)
    }
  }

  func saveServerURL() async {
    let normalized = serverURLText.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let url = URL(string: normalized), let scheme = url.scheme,
      ["http", "https"].contains(scheme), url.host != nil
    else {
      state = .failed("请输入有效的 HTTP 或 HTTPS 服务地址。")
      return
    }
    defaults.set(normalized, forKey: "native.serverURL")
    backend = APIClient(baseURL: url)
    projects = []
    selectedProjectID = nil
    await loadProjects()
  }

  func configureProvider(apiKey: String) async {
    guard let projectID = selectedProjectID else {
      providerState = .failed("请先选择项目。")
      return
    }
    let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !key.isEmpty else {
      providerState = .failed("请输入模型 API 密钥。")
      return
    }
    providerState = .loading
    do {
      try keychain.save(key, account: providerAccount(projectID))
      providerStatus = try await backend.configureProvider(
        ProviderConfiguration(
          projectID: projectID,
          baseURL: providerBaseURL,
          model: providerModel,
          apiKey: key
        ))
      defaults.set(providerBaseURL, forKey: "native.providerBaseURL")
      defaults.set(providerModel, forKey: "native.providerModel")
      providerState = .loaded
    } catch {
      providerState = .failed(error.localizedDescription)
    }
  }

  func registerStoredProvider() async {
    guard let projectID = selectedProjectID else { return }
    providerState = .loading
    do {
      guard let key = try keychain.read(account: providerAccount(projectID)), !key.isEmpty else {
        providerState = .failed("系统钥匙串中尚未保存该项目的模型密钥。")
        return
      }
      providerStatus = try await backend.configureProvider(
        ProviderConfiguration(
          projectID: projectID,
          baseURL: providerBaseURL,
          model: providerModel,
          apiKey: key
        ))
      providerState = .loaded
    } catch {
      providerState = .failed(error.localizedDescription)
    }
  }

  func testProvider() async {
    guard let projectID = selectedProjectID else { return }
    providerState = .loading
    do {
      providerStatus = try await backend.testProvider(projectID: projectID)
      providerState = .loaded
    } catch {
      providerState = .failed(error.localizedDescription)
    }
  }

  private func loadProviderStatus(projectID: String) async {
    do { providerStatus = try await backend.providerStatus(projectID: projectID) } catch {
      providerStatus = nil
    }
  }

  private var selectedRepository: RepositorySummary? {
    repositories.first { $0.id == selectedRepositoryID }
  }

  private func loadRepositories(projectID: String) async {
    do {
      repositories = try await backend.repositories(projectID: projectID)
        .filter { $0.status == "ready" && $0.activeGenerationID != nil }
      if repositories.count == 1 {
        selectedRepositoryID = repositories[0].id
      } else if !repositories.contains(where: { $0.id == selectedRepositoryID }) {
        selectedRepositoryID = nil
      }
    } catch {
      repositories = []
      selectedRepositoryID = nil
    }
  }

  private func restoreStoredProviderIfAvailable(projectID: String) async {
    guard let key = try? keychain.read(account: providerAccount(projectID)), !key.isEmpty
    else { return }
    providerStatus = try? await backend.configureProvider(
      ProviderConfiguration(
        projectID: projectID,
        baseURL: providerBaseURL,
        model: providerModel,
        apiKey: key
      ))
  }

  private func loadQueryHistory(projectID: String) async {
    do {
      queryHistory = try await backend.queryHistory(projectID: projectID, limit: 40).items
    } catch { queryHistory = [] }
  }

  private func providerAccount(_ projectID: String) -> String {
    "project:\(projectID):llm"
  }
}
