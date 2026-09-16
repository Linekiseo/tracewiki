import Foundation
import XCTest

@testable import EvidenceRAGNative

final class DecoderTests: XCTestCase {
  private let decoder = JSONDecoder()

  func testProviderHistoryAndWikiContractsDecode() throws {
    let provider = try decoder.decode(
      ProviderStatus.self,
      from: Data(
        """
        {"contract_version":"llm-provider-runtime-v1","project_id":"project-rag","provider":"openai_compatible","base_url":"https://api.deepseek.com","model":"deepseek-v4-flash","configured":true,"api_key_present":true,"secret_storage":"desktop_keychain","source":"desktop_runtime","capabilities":["grounded_generation"],"health":"ready","tested":true}
        """.utf8))
    XCTAssertTrue(provider.configured)
    XCTAssertEqual(provider.health, "ready")

    let history = try decoder.decode(
      QueryHistoryResponse.self,
      from: Data(
        """
        {"contract_version":"query-history-v1","project_id":"project-rag","items":[{"id":"history-1","created_at":"2026-08-04T00:00:00Z","query_id":"query://1","question_digest":"sha256:abc","intent":"rationale","interaction_state":"completed","answer_mode":"GENERATED","reason_code":null,"evidence":{"citation_ids":["E1"],"sources":["code"],"entity_ids":["code://1"],"count":1}}]}
        """.utf8))
    XCTAssertEqual(history.items.first?.evidence?.count, 1)

    let wiki = try decoder.decode(WikiPageListResponse.self, from: Data(Self.wikiJSON.utf8))
    XCTAssertEqual(wiki.total, 1)
    XCTAssertEqual(wiki.pages[0].facts[0].predicate, "implements")
    XCTAssertEqual(wiki.pages[0].sourceRefs[0].source, "code")
  }

  func testQueryDecoderRejectsMissingAuthorityFields() throws {
    let invalid = Data(#"{"answer":{}}"#.utf8)
    XCTAssertThrowsError(try decoder.decode(QueryResponse.self, from: invalid))
  }

  static let wikiJSON = """
    {
      "project_id":"project-rag","generation_id":"wiki-gen-1","total":1,"offset":0,"limit":40,"next_offset":null,
      "pages":[{
        "fragment_id":"wiki://page/1","logical_path":"/architecture/query","page_type":"architecture","title":"Trusted Query","summary":"Evidence-first query flow.","aliases":[],"tags":["rag"],
        "scope":{"project_id":"project-rag","visibility_partition":"project:rag","acl_refs":["project:rag"],"source_generations":[]},
        "source_refs":[{"source_ref_id":"ref-1","source":"code","entity_type":"symbol","entity_id":"code://1","locator":"code://1#L1","generation_id":"g1","watermark":"w1","acl_refs":["project:rag"],"observed_at":"2026-08-04T00:00:00Z","raw_content_sha256":"sha256:raw","content_sha256":"sha256:ref"}],
        "facts":[{"fact_id":"fact-1","subject_path":"/architecture/query","predicate":"implements","object_text":"hybrid planner","object_path":null,"qualifiers":[],"source_ref_ids":["ref-1"],"authority":"observed","status":"verified","confidence":0.98,"content_sha256":"sha256:fact"}],
        "links":[{"link_id":"link-1","source_path":"/architecture/query","target_path":"/architecture/retrieval","relation":"depends_on","inverse_relation":"supports","source_ref_ids":["ref-1"],"status":"verified","confidence":0.9,"content_sha256":"sha256:link"}],
        "evidence_roles":["implementation"],"compiler_version":"wiki-v1","contract_version":"wiki-page-v1","content_sha256":"sha256:page"
      }]
    }
    """
}
