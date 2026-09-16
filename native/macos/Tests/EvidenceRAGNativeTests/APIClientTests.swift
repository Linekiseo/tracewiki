import Foundation
import XCTest

@testable import EvidenceRAGNative

final class APIClientTests: XCTestCase {
  override func tearDown() {
    URLProtocolStub.handler = nil
    super.tearDown()
  }

  func testProjectsUsesCanonicalEndpointAndDecodes() async throws {
    URLProtocolStub.handler = { request in
      XCTAssertEqual(request.httpMethod, "GET")
      XCTAssertEqual(request.url?.path, "/v1/projects")
      return Self.response(
        for: request,
        json: """
          [{
            "id":"project-rag","name":"RAG","description":"Native client",
            "owner":"team","acl_ref":"project:rag","classification":"internal",
            "status":"active","created_at":"2026-08-04T00:00:00Z",
            "updated_at":"2026-08-04T01:00:00Z","topic_count":3,
            "active_iteration_count":1,"repositories":2,"sessions":9,
            "experiments":4,"documents":5,"pending_reviews":6
          }]
          """)
    }

    let projects = try await makeClient().projects()

    XCTAssertEqual(projects.count, 1)
    XCTAssertEqual(projects[0].id, "project-rag")
    XCTAssertEqual(projects[0].pendingReviews, 6)
  }

  func testRepositoriesUsesGovernedProjectEndpoint() async throws {
    URLProtocolStub.handler = { request in
      XCTAssertEqual(request.httpMethod, "GET")
      XCTAssertEqual(request.url?.path, "/v1/repositories")
      XCTAssertEqual(
        URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false)?
          .queryItems?.first(where: { $0.name == "project_id" })?.value,
        "project-rag"
      )
      return Self.response(
        for: request,
        json:
          #"[{"id":"repo://local/rag","project_id":"project-rag","name":"rag","default_branch":"main","head_commit":"abcdef","status":"ready","active_generation_id":"gen-one"}]"#
      )
    }

    let repositories = try await makeClient().repositories(projectID: "project-rag")
    XCTAssertEqual(repositories.map(\.id), ["repo://local/rag"])
    XCTAssertEqual(repositories[0].activeGenerationID, "gen-one")
  }

  func testQuerySendsStructuredScopeWithoutSecrets() async throws {
    URLProtocolStub.handler = { request in
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertEqual(request.url?.path, "/v1/query")
      XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
      let object = try XCTUnwrap(
        try JSONSerialization.jsonObject(with: try Self.bodyData(request)) as? [String: Any]
      )
      XCTAssertEqual(object["question"] as? String, "How is evidence verified?")
      let scope = try XCTUnwrap(object["scope"] as? [String: Any])
      XCTAssertEqual(scope["project_id"] as? String, "project-rag")
      XCTAssertEqual(scope["source_types"] as? [String], ["code", "codex"])
      XCTAssertEqual(scope["repository_ids"] as? [String], ["repo://local/rag"])
      XCTAssertEqual(scope["branch"] as? String, "main")
      XCTAssertEqual(scope["commit"] as? String, "abcdef")
      XCTAssertNil(object["api_key"])
      return Self.response(for: request, json: Self.queryJSON)
    }

    let response = try await makeClient().runQuery(
      QueryRequest(
        question: "How is evidence verified?",
        scope: QueryScope(
          projectID: "project-rag", repositoryIDs: ["repo://local/rag"], branch: "main",
          commit: "abcdef",
          sourceTypes: ["code", "codex"]),
        mode: "answer",
        maxEvidence: 16,
        include: ["code", "codex"],
        deadlineMilliseconds: 60_000,
        answerFormat: "detailed"
      ))

    XCTAssertEqual(response.answer.text, "Grounded answer [E1]")
    XCTAssertEqual(response.evidencePack?.allEvidence.count, 1)
  }

  func testServerErrorPreservesStatusAndStableDetail() async throws {
    URLProtocolStub.handler = { request in
      let response = HTTPURLResponse(
        url: try XCTUnwrap(request.url),
        statusCode: 422,
        httpVersion: nil,
        headerFields: ["Content-Type": "application/json"]
      )!
      return (response, Data(#"{"detail":"scope_unavailable"}"#.utf8))
    }

    do {
      let _: ProviderStatus = try await makeClient().providerStatus(projectID: "project-rag")
      XCTFail("Expected server error")
    } catch let error as APIClientError {
      XCTAssertEqual(error, .server(status: 422, message: "scope_unavailable"))
    }
  }

  private func makeClient() -> APIClient {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [URLProtocolStub.self]
    return APIClient(
      baseURL: URL(string: "http://127.0.0.1:8765")!,
      session: URLSession(configuration: configuration)
    )
  }

  private static func response(for request: URLRequest, json: String) -> (HTTPURLResponse, Data) {
    let response = HTTPURLResponse(
      url: request.url!,
      statusCode: 200,
      httpVersion: nil,
      headerFields: ["Content-Type": "application/json"]
    )!
    return (response, Data(json.utf8))
  }

  private static func bodyData(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody { return body }
    let stream = try XCTUnwrap(request.httpBodyStream)
    stream.open()
    defer { stream.close() }
    var result = Data()
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4_096)
    defer { buffer.deallocate() }
    while stream.hasBytesAvailable {
      let count = stream.read(buffer, maxLength: 4_096)
      if count < 0 { throw try XCTUnwrap(stream.streamError) }
      if count == 0 { break }
      result.append(buffer, count: count)
    }
    return result
  }

  static let queryJSON = """
    {
      "query_id":"query-interaction://one","trace_id":"trace-one",
      "interaction_state":"completed","answer_mode":"GENERATED",
      "answer":{"status":"complete","answer_mode":"GENERATED","decision_status":"sufficient","text":"Grounded answer [E1]","citations":["E1"],"refusal":false,"partial":false},
      "query_understanding":{"strategy":"llm_structured_hybrid","intent":"current_implementation","intent_confidence":0.82,"source_hints":["code"],"required_roles":["implementation"],"subqueries":["evidence verification"],"ambiguity":[],"fallback_reason":null,"algorithm":{"version":"query-understanding-hybrid-v2","encoder":"structured_llm_plus_tfidf_prior","classifier":"calibrated_centroid_softmax","uncertainty":0.18,"complexity":0.4,"decomposition":"constrained_llm_evidence_obligations","planner_model":"deepseek-v4-flash"}},
      "evidence_pack":{"verified_facts":[{"entity_id":"code://one","source":"code","title":"Verifier","subtitle":"service","locator":"code://one#L1","snippet":"Checks claims","version":"abc","evidence_role":"implementation"}],"supporting_evidence":[],"counter_evidence":[],"related_evidence":[]}
    }
    """
}

final class URLProtocolStub: URLProtocol {
  static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    do {
      let result = try XCTUnwrap(Self.handler)(request)
      client?.urlProtocol(self, didReceive: result.0, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: result.1)
      client?.urlProtocolDidFinishLoading(self)
    } catch {
      client?.urlProtocol(self, didFailWithError: error)
    }
  }

  override func stopLoading() {}
}
