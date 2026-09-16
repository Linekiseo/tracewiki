import Foundation

struct Project: Codable, Identifiable, Hashable {
  let id: String
  let name: String
  let description: String
  let owner: String
  let aclRef: String
  let classification: String
  let status: String
  let createdAt: String
  let updatedAt: String
  let topicCount: Int
  let activeIterationCount: Int
  let repositories: Int?
  let sessions: Int?
  let experiments: Int?
  let documents: Int?
  let pendingReviews: Int?

  enum CodingKeys: String, CodingKey {
    case id, name, description, owner, classification, status, repositories, sessions, experiments,
      documents
    case aclRef = "acl_ref"
    case createdAt = "created_at"
    case updatedAt = "updated_at"
    case topicCount = "topic_count"
    case activeIterationCount = "active_iteration_count"
    case pendingReviews = "pending_reviews"
  }
}

struct RepositorySummary: Decodable, Identifiable, Hashable {
  let id: String
  let projectID: String
  let name: String
  let defaultBranch: String?
  let headCommit: String?
  let status: String
  let activeGenerationID: String?

  enum CodingKeys: String, CodingKey {
    case id, name, status
    case projectID = "project_id"
    case defaultBranch = "default_branch"
    case headCommit = "head_commit"
    case activeGenerationID = "active_generation_id"
  }
}

struct ProviderStatus: Codable, Equatable {
  let contractVersion: String
  let projectID: String
  let provider: String
  let baseURL: String?
  let model: String?
  let configured: Bool
  let apiKeyPresent: Bool
  let secretStorage: String
  let source: String?
  let capabilities: [String]
  let health: String?
  let tested: Bool?

  enum CodingKeys: String, CodingKey {
    case provider, model, configured, source, capabilities, health, tested
    case contractVersion = "contract_version"
    case projectID = "project_id"
    case baseURL = "base_url"
    case apiKeyPresent = "api_key_present"
    case secretStorage = "secret_storage"
  }
}

struct QueryRequest: Encodable, Equatable {
  let question: String
  let scope: QueryScope
  let mode: String
  let maxEvidence: Int
  let include: [String]
  let deadlineMilliseconds: Int
  let answerFormat: String

  enum CodingKeys: String, CodingKey {
    case question, scope, mode, include
    case maxEvidence = "max_evidence"
    case deadlineMilliseconds = "deadline_ms"
    case answerFormat = "answer_format"
  }
}

struct QueryScope: Encodable, Equatable {
  let projectID: String
  let repositoryIDs: [String]
  let branch: String?
  let commit: String?
  let sourceTypes: [String]

  enum CodingKeys: String, CodingKey {
    case branch, commit
    case projectID = "project_id"
    case repositoryIDs = "repository_ids"
    case sourceTypes = "source_types"
  }
}

struct QueryResponse: Decodable, Equatable {
  let queryID: String
  let traceID: String?
  let interactionState: String?
  let answerMode: String?
  let answer: QueryAnswer
  let queryUnderstanding: QueryUnderstanding?
  let evidencePack: EvidencePack?

  enum CodingKeys: String, CodingKey {
    case answer
    case queryID = "query_id"
    case traceID = "trace_id"
    case interactionState = "interaction_state"
    case answerMode = "answer_mode"
    case queryUnderstanding = "query_understanding"
    case evidencePack = "evidence_pack"
  }
}

struct QueryAnswer: Decodable, Equatable {
  let status: String?
  let answerMode: String?
  let decisionStatus: String?
  let text: String?
  let citations: [String]?
  let refusal: Bool?
  let refusalReason: String?
  let fallbackReason: String?
  let partial: Bool?
  let nextActions: [String]?

  enum CodingKeys: String, CodingKey {
    case status, text, citations, refusal, partial
    case answerMode = "answer_mode"
    case decisionStatus = "decision_status"
    case refusalReason = "refusal_reason"
    case fallbackReason = "fallback_reason"
    case nextActions = "next_actions"
  }
}

struct QueryUnderstanding: Decodable, Equatable {
  let strategy: String
  let intent: String
  let intentConfidence: Double
  let sourceHints: [String]
  let requiredRoles: [String]
  let subqueries: [String]
  let ambiguity: [String]
  let fallbackReason: String?
  let algorithm: QueryAlgorithm

  enum CodingKeys: String, CodingKey {
    case strategy, intent, ambiguity, algorithm
    case intentConfidence = "intent_confidence"
    case sourceHints = "source_hints"
    case requiredRoles = "required_roles"
    case subqueries
    case fallbackReason = "fallback_reason"
  }
}

struct QueryAlgorithm: Decodable, Equatable {
  let version: String
  let encoder: String
  let classifier: String
  let uncertainty: Double
  let complexity: Double
  let decomposition: String
  let plannerModel: String?

  enum CodingKeys: String, CodingKey {
    case version, encoder, classifier, uncertainty, complexity, decomposition
    case plannerModel = "planner_model"
  }
}

struct EvidencePack: Decodable, Equatable {
  let verifiedFacts: [EvidenceItem]?
  let supportingEvidence: [EvidenceItem]?
  let counterEvidence: [EvidenceItem]?
  let relatedEvidence: [EvidenceItem]?

  enum CodingKeys: String, CodingKey {
    case verifiedFacts = "verified_facts"
    case supportingEvidence = "supporting_evidence"
    case counterEvidence = "counter_evidence"
    case relatedEvidence = "related_evidence"
  }

  var allEvidence: [EvidenceItem] {
    var seen = Set<String>()
    return [verifiedFacts, supportingEvidence, counterEvidence, relatedEvidence]
      .compactMap { $0 }
      .flatMap { $0 }
      .filter { seen.insert($0.entityID).inserted }
  }
}

struct EvidenceItem: Decodable, Identifiable, Equatable {
  let entityID: String
  let source: String
  let title: String
  let subtitle: String?
  let locator: String
  let snippet: String
  let version: String?
  let evidenceRole: String?

  var id: String { entityID }

  enum CodingKeys: String, CodingKey {
    case source, title, subtitle, locator, snippet, version
    case entityID = "entity_id"
    case evidenceRole = "evidence_role"
  }
}

struct QueryHistoryResponse: Decodable, Equatable {
  let contractVersion: String
  let projectID: String
  let items: [QueryHistoryItem]

  enum CodingKeys: String, CodingKey {
    case items
    case contractVersion = "contract_version"
    case projectID = "project_id"
  }
}

struct QueryHistoryItem: Decodable, Identifiable, Equatable {
  let id: String
  let createdAt: String
  let queryID: String
  let questionDigest: String
  let intent: String?
  let interactionState: String?
  let answerMode: String?
  let reasonCode: String?
  let evidence: HistoryEvidence?

  enum CodingKeys: String, CodingKey {
    case id, intent, evidence
    case createdAt = "created_at"
    case queryID = "query_id"
    case questionDigest = "question_digest"
    case interactionState = "interaction_state"
    case answerMode = "answer_mode"
    case reasonCode = "reason_code"
  }
}

struct HistoryEvidence: Decodable, Equatable {
  let citationIDs: [String]?
  let sources: [String]?
  let entityIDs: [String]?
  let count: Int?

  enum CodingKeys: String, CodingKey {
    case sources, count
    case citationIDs = "citation_ids"
    case entityIDs = "entity_ids"
  }
}

struct WikiPageListResponse: Decodable, Equatable {
  let projectID: String
  let generationID: String?
  let total: Int
  let offset: Int
  let limit: Int
  let nextOffset: Int?
  let pages: [WikiPage]

  enum CodingKeys: String, CodingKey {
    case total, offset, limit, pages
    case projectID = "project_id"
    case generationID = "generation_id"
    case nextOffset = "next_offset"
  }
}

struct WikiPage: Decodable, Identifiable, Equatable {
  let fragmentID: String
  let logicalPath: String
  let pageType: String
  let title: String
  let summary: String
  let aliases: [String]
  let tags: [String]
  let sourceRefs: [WikiSourceRef]
  let facts: [WikiFact]
  let links: [WikiLink]
  let evidenceRoles: [String]
  let contentSHA256: String

  var id: String { fragmentID }

  enum CodingKeys: String, CodingKey {
    case title, summary, aliases, tags, facts, links
    case fragmentID = "fragment_id"
    case logicalPath = "logical_path"
    case pageType = "page_type"
    case sourceRefs = "source_refs"
    case evidenceRoles = "evidence_roles"
    case contentSHA256 = "content_sha256"
  }
}

struct WikiSourceRef: Decodable, Identifiable, Equatable {
  let sourceRefID: String
  let source: String
  let entityType: String
  let entityID: String
  let locator: String
  var id: String { sourceRefID }

  enum CodingKeys: String, CodingKey {
    case source, locator
    case sourceRefID = "source_ref_id"
    case entityType = "entity_type"
    case entityID = "entity_id"
  }
}

struct WikiFact: Decodable, Identifiable, Equatable {
  let factID: String
  let predicate: String
  let objectText: String?
  let objectPath: String?
  let authority: String
  let status: String
  let confidence: Double
  var id: String { factID }

  enum CodingKeys: String, CodingKey {
    case predicate, authority, status, confidence
    case factID = "fact_id"
    case objectText = "object_text"
    case objectPath = "object_path"
  }
}

struct WikiLink: Decodable, Identifiable, Equatable {
  let linkID: String
  let sourcePath: String
  let targetPath: String
  let relation: String
  let status: String
  let confidence: Double
  var id: String { linkID }

  enum CodingKeys: String, CodingKey {
    case relation, status, confidence
    case linkID = "link_id"
    case sourcePath = "source_path"
    case targetPath = "target_path"
  }
}

struct GraphSnapshot: Decodable, Equatable {
  let domain: String
  let mode: String
  let query: String
  let nodes: [GraphNode]
  let edges: [GraphEdge]
  let metadata: GraphMetadata
}

struct GraphNode: Decodable, Identifiable, Equatable {
  let id: String
  let type: String
  let label: String
  let locator: String
  let domain: String
}

struct GraphEdge: Decodable, Identifiable, Equatable {
  let id: String
  let source: String
  let predicate: String
  let target: String
  let confidence: Double?
  let reviewStatus: String?

  enum CodingKeys: String, CodingKey {
    case id, source, predicate, target, confidence
    case reviewStatus = "review_status"
  }
}

struct GraphMetadata: Decodable, Equatable {
  let totalNodes: Int
  let totalEdges: Int
  let visibleNodes: Int
  let visibleEdges: Int

  enum CodingKeys: String, CodingKey {
    case totalNodes = "total_nodes"
    case totalEdges = "total_edges"
    case visibleNodes = "visible_nodes"
    case visibleEdges = "visible_edges"
  }
}

struct CodexSession: Decodable, Identifiable, Equatable {
  let id: String
  let threadID: String
  let projectID: String
  let title: String
  let status: String
  let updatedAt: String
  let turnCount: Int
  let itemCount: Int
  let fileChangeCount: Int
  let commandCount: Int

  enum CodingKeys: String, CodingKey {
    case id, title, status
    case threadID = "thread_id"
    case projectID = "project_id"
    case updatedAt = "updated_at"
    case turnCount = "turn_count"
    case itemCount = "item_count"
    case fileChangeCount = "file_change_count"
    case commandCount = "command_count"
  }
}

struct AgentSummary: Decodable, Identifiable, Equatable {
  let id: String
  let clientID: String?
  let name: String
  let connector: String
  let version: String?
  let availability: String
  let reason: String
  let capabilities: [String]
  let lastSeenAt: String?
  let activeExecutionCount: Int
  let sessionCount: Int

  enum CodingKeys: String, CodingKey {
    case id, name, connector, version, availability, reason, capabilities
    case clientID = "client_id"
    case lastSeenAt = "last_seen_at"
    case activeExecutionCount = "active_execution_count"
    case sessionCount = "session_count"
  }
}

struct ProviderConfiguration: Encodable {
  let projectID: String
  let provider = "openai_compatible"
  let baseURL: String
  let model: String
  let apiKey: String

  enum CodingKeys: String, CodingKey {
    case provider, model
    case projectID = "project_id"
    case baseURL = "base_url"
    case apiKey = "api_key"
  }
}
