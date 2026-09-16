import Foundation

enum APIClientError: LocalizedError, Equatable {
  case invalidBaseURL
  case invalidResponse
  case server(status: Int, message: String)
  case decoding(String)

  var errorDescription: String? {
    switch self {
    case .invalidBaseURL: "服务地址无效。"
    case .invalidResponse: "服务返回了无法识别的响应。"
    case .server(let status, let message): "服务请求失败（\(status)）：\(message)"
    case .decoding(let message): "响应格式与客户端合同不一致：\(message)"
    }
  }
}

protocol BackendServicing {
  func projects() async throws -> [Project]
  func repositories(projectID: String) async throws -> [RepositorySummary]
  func providerStatus(projectID: String) async throws -> ProviderStatus
  func configureProvider(_ configuration: ProviderConfiguration) async throws -> ProviderStatus
  func testProvider(projectID: String) async throws -> ProviderStatus
  func runQuery(_ request: QueryRequest) async throws -> QueryResponse
  func queryHistory(projectID: String, limit: Int) async throws -> QueryHistoryResponse
  func wikiPages(projectID: String, query: String, offset: Int, limit: Int) async throws
    -> WikiPageListResponse
  func wikiPage(projectID: String, path: String, generationID: String?) async throws -> WikiPage
  func graph(projectID: String, limit: Int) async throws -> GraphSnapshot
  func sessions(projectID: String, limit: Int) async throws -> [CodexSession]
  func agents(projectID: String) async throws -> [AgentSummary]
}

final class APIClient: BackendServicing, @unchecked Sendable {
  private let baseURL: URL
  private let session: URLSession
  private let decoder: JSONDecoder
  private let encoder: JSONEncoder

  init(baseURL: URL, session: URLSession = .shared) {
    self.baseURL = baseURL
    self.session = session
    decoder = JSONDecoder()
    encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
  }

  func projects() async throws -> [Project] {
    try await get("/v1/projects")
  }

  func repositories(projectID: String) async throws -> [RepositorySummary] {
    try await get("/v1/repositories", query: ["project_id": projectID])
  }

  func providerStatus(projectID: String) async throws -> ProviderStatus {
    try await get("/v1/ai/provider", query: ["project_id": projectID])
  }

  func configureProvider(_ configuration: ProviderConfiguration) async throws -> ProviderStatus {
    try await send("/v1/ai/provider", method: "PUT", body: configuration)
  }

  func testProvider(projectID: String) async throws -> ProviderStatus {
    try await send("/v1/ai/provider/test", method: "POST", body: ["project_id": projectID])
  }

  func runQuery(_ request: QueryRequest) async throws -> QueryResponse {
    try await send("/v1/query", method: "POST", body: request)
  }

  func queryHistory(projectID: String, limit: Int) async throws -> QueryHistoryResponse {
    try await get("/v1/query/history", query: ["project_id": projectID, "limit": String(limit)])
  }

  func wikiPages(projectID: String, query: String, offset: Int, limit: Int) async throws
    -> WikiPageListResponse
  {
    var values = [
      "project_id": projectID,
      "offset": String(offset),
      "limit": String(limit),
    ]
    if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      values["q"] = query
    }
    return try await get("/v1/wiki/pages", query: values)
  }

  func wikiPage(projectID: String, path: String, generationID: String?) async throws -> WikiPage {
    var values = ["project_id": projectID, "path": path]
    if let generationID { values["generation_id"] = generationID }
    return try await get("/v1/wiki/read", query: values)
  }

  func graph(projectID: String, limit: Int) async throws -> GraphSnapshot {
    try await get(
      "/v1/graph",
      query: [
        "project_id": projectID,
        "domain": "overview",
        "mode": "relations",
        "query": "",
        "limit": String(limit),
      ])
  }

  func sessions(projectID: String, limit: Int) async throws -> [CodexSession] {
    try await get(
      "/v1/codex/sessions",
      query: [
        "project_id": projectID,
        "include_subagents": "true",
        "limit": String(limit),
        "offset": "0",
      ])
  }

  func agents(projectID: String) async throws -> [AgentSummary] {
    try await get("/v1/codex-bridge/agents", query: ["project_id": projectID])
  }

  private func get<Response: Decodable>(
    _ path: String,
    query: [String: String] = [:]
  ) async throws -> Response {
    var request = try makeRequest(path: path, query: query)
    request.httpMethod = "GET"
    return try await execute(request)
  }

  private func send<Body: Encodable, Response: Decodable>(
    _ path: String,
    method: String,
    body: Body
  ) async throws -> Response {
    var request = try makeRequest(path: path)
    request.httpMethod = method
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try encoder.encode(body)
    return try await execute(request)
  }

  private func makeRequest(path: String, query: [String: String] = [:]) throws -> URLRequest {
    guard
      var components = URLComponents(
        url: baseURL.appendingPathComponent(
          path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))),
        resolvingAgainstBaseURL: false
      )
    else { throw APIClientError.invalidBaseURL }
    if !query.isEmpty {
      components.queryItems = query.sorted(by: { $0.key < $1.key }).map(URLQueryItem.init)
    }
    guard let url = components.url else { throw APIClientError.invalidBaseURL }
    var request = URLRequest(url: url)
    request.timeoutInterval = 65
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    return request
  }

  private func execute<Response: Decodable>(_ request: URLRequest) async throws -> Response {
    let (data, response) = try await session.data(for: request)
    guard let http = response as? HTTPURLResponse else {
      throw APIClientError.invalidResponse
    }
    guard 200...299 ~= http.statusCode else {
      let detail = (try? decoder.decode(ServerError.self, from: data).detail) ?? "请求未成功"
      throw APIClientError.server(status: http.statusCode, message: detail)
    }
    do {
      return try decoder.decode(Response.self, from: data)
    } catch {
      throw APIClientError.decoding(String(describing: error))
    }
  }
}

private struct ServerError: Decodable {
  let detail: String
}
