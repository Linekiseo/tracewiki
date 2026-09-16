using System.Text.Json;
using System.Text.Json.Serialization;

namespace EvidenceRag.Windows.Core.Models;

public sealed record ProjectDto
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("description")] public string Description { get; init; } = "";
    [JsonPropertyName("owner")] public string Owner { get; init; } = "";
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonPropertyName("classification")] public string Classification { get; init; } = "";
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record RepositoryDto
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("project_id")] public string ProjectId { get; init; } = "";
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("default_branch")] public string? DefaultBranch { get; init; }
    [JsonPropertyName("head_commit")] public string? HeadCommit { get; init; }
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonPropertyName("active_generation_id")] public string? ActiveGenerationId { get; init; }
    [JsonIgnore] public string DisplayName => $"{Name} · {DefaultBranch ?? "未命名分支"}";
}

public sealed record ProviderStatusDto
{
    [JsonPropertyName("contract_version")] public string ContractVersion { get; init; } = "";
    [JsonPropertyName("provider")] public string Provider { get; init; } = "";
    [JsonPropertyName("base_url")] public string BaseUrl { get; init; } = "";
    [JsonPropertyName("model")] public string Model { get; init; } = "";
    [JsonPropertyName("configured")] public bool Configured { get; init; }
    [JsonPropertyName("api_key_present")] public bool ApiKeyPresent { get; init; }
    [JsonPropertyName("source")] public string Source { get; init; } = "";
    [JsonPropertyName("capabilities")] public IReadOnlyList<string> Capabilities { get; init; } = [];
}

public sealed record QueryScopeDto
{
    [JsonPropertyName("project_id")] public string ProjectId { get; init; } = "project-rag";
    [JsonPropertyName("repository_ids")] public IReadOnlyList<string> RepositoryIds { get; init; } = [];
    [JsonPropertyName("branch")] public string? Branch { get; init; }
    [JsonPropertyName("commit")] public string? Commit { get; init; }
    [JsonPropertyName("source_types")] public IReadOnlyList<string> SourceTypes { get; init; } = [];
}

public sealed record QueryRequestDto
{
    [JsonPropertyName("question")] public string Question { get; init; } = "";
    [JsonPropertyName("scope")] public QueryScopeDto Scope { get; init; } = new();
    [JsonPropertyName("mode")] public string Mode { get; init; } = "answer";
    [JsonPropertyName("max_evidence")] public int MaxEvidence { get; init; } = 12;
    [JsonPropertyName("include")] public IReadOnlyList<string> Include { get; init; } = [];
    [JsonPropertyName("answer_format")] public string AnswerFormat { get; init; } = "detailed";
    [JsonPropertyName("deadline_ms")] public int DeadlineMs { get; init; } = 60_000;
}

public sealed record QueryResultDto
{
    [JsonPropertyName("answer")] public QueryAnswerDto Answer { get; init; } = new();
    [JsonPropertyName("mode")] public string Mode { get; init; } = "";
    [JsonPropertyName("answer_mode")] public string AnswerMode { get; init; } = "";
    [JsonPropertyName("interaction_id")] public string InteractionId { get; init; } = "";
    [JsonPropertyName("evidence")] public JsonElement? Evidence { get; init; }
    [JsonPropertyName("citations")] public JsonElement? Citations { get; init; }
    [JsonPropertyName("trace")] public JsonElement? Trace { get; init; }
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record QueryAnswerDto
{
    [JsonPropertyName("text")] public string Text { get; init; } = "";
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonPropertyName("answer_mode")] public string AnswerMode { get; init; } = "";
    [JsonPropertyName("citations")] public IReadOnlyList<string> Citations { get; init; } = [];
    [JsonPropertyName("refusal")] public bool Refusal { get; init; }
    [JsonPropertyName("refusal_reason")] public string? RefusalReason { get; init; }
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record QueryHistoryEnvelope
{
    [JsonPropertyName("items")] public IReadOnlyList<QueryHistoryItemDto> Items { get; init; } = [];
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record QueryHistoryItemDto
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("query_id")] public string QueryId { get; init; } = "";
    [JsonPropertyName("trace_id")] public string TraceId { get; init; } = "";
    [JsonPropertyName("interaction_id")] public string InteractionId { get; init; } = "";
    [JsonPropertyName("question")] public string Question { get; init; } = "";
    [JsonPropertyName("question_digest")] public string QuestionDigest { get; init; } = "";
    [JsonPropertyName("answer")] public string Answer { get; init; } = "";
    [JsonPropertyName("answer_mode")] public string AnswerMode { get; init; } = "";
    [JsonPropertyName("reason_code")] public string ReasonCode { get; init; } = "";
    [JsonPropertyName("evidence")] public QueryHistoryEvidenceDto Evidence { get; init; } = new();
    [JsonPropertyName("created_at")] public string CreatedAt { get; init; } = "";
    [JsonIgnore]
    public string DisplayTitle => !string.IsNullOrWhiteSpace(Question)
        ? Question
        : (!string.IsNullOrWhiteSpace(QuestionDigest) ? $"已保护问题 · {QuestionDigest[..Math.Min(23, QuestionDigest.Length)]}" : QueryId);
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record QueryHistoryEvidenceDto
{
    [JsonPropertyName("count")] public int Count { get; init; }
    [JsonPropertyName("sources")] public IReadOnlyList<string> Sources { get; init; } = [];
    [JsonPropertyName("citation_ids")] public IReadOnlyList<string> CitationIds { get; init; } = [];
}

public sealed record WikiPageEnvelope
{
    [JsonPropertyName("project_id")] public string ProjectId { get; init; } = "";
    [JsonPropertyName("generation_id")] public string GenerationId { get; init; } = "";
    [JsonPropertyName("total")] public int Total { get; init; }
    [JsonPropertyName("offset")] public int Offset { get; init; }
    [JsonPropertyName("limit")] public int Limit { get; init; }
    [JsonPropertyName("next_offset")] public int? NextOffset { get; init; }
    [JsonPropertyName("pages")] public IReadOnlyList<WikiPageDto> Pages { get; init; } = [];
}

public sealed record WikiPageDto
{
    [JsonPropertyName("logical_path")] public string LogicalPath { get; init; } = "";
    [JsonPropertyName("title")] public string Title { get; init; } = "";
    [JsonPropertyName("summary")] public string Summary { get; init; } = "";
    [JsonPropertyName("page_type")] public string PageType { get; init; } = "";
    [JsonPropertyName("source")] public string Source { get; init; } = "";
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record GraphEnvelope
{
    [JsonPropertyName("nodes")] public IReadOnlyList<GraphNodeDto> Nodes { get; init; } = [];
    [JsonPropertyName("edges")] public IReadOnlyList<GraphEdgeDto> Edges { get; init; } = [];
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record GraphNodeDto
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("label")] public string Label { get; init; } = "";
    [JsonPropertyName("type")] public string Type { get; init; } = "";
    [JsonPropertyName("source_type")] public string SourceType { get; init; } = "";
    [JsonPropertyName("domain")] public string Domain { get; init; } = "";
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("title")] public string Title { get; init; } = "";
    [JsonIgnore] public string DisplayLabel => First(Label, Name, Title, Id);
    [JsonIgnore] public string DisplayType => First(Type, SourceType, Domain);
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
    private static string First(params string[] values) => values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? "";
}

public sealed record GraphEdgeDto
{
    [JsonPropertyName("source")] public string Source { get; init; } = "";
    [JsonPropertyName("target")] public string Target { get; init; } = "";
    [JsonPropertyName("type")] public string Type { get; init; } = "";
    [JsonPropertyName("predicate")] public string Predicate { get; init; } = "";
    [JsonIgnore] public string DisplayType => string.IsNullOrWhiteSpace(Type) ? Predicate : Type;
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record SessionDto
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("thread_id")] public string ThreadId { get; init; } = "";
    [JsonPropertyName("title")] public string Title { get; init; } = "";
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonPropertyName("updated_at")] public string UpdatedAt { get; init; } = "";
    [JsonPropertyName("item_count")] public int ItemCount { get; init; }
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record AgentDto
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("client_id")] public string ClientId { get; init; } = "";
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonPropertyName("availability")] public string Availability { get; init; } = "";
    [JsonIgnore] public string DisplayStatus => string.IsNullOrWhiteSpace(Status) ? Availability : Status;
    [JsonPropertyName("last_seen_at")] public string LastSeenAt { get; init; } = "";
    [JsonExtensionData] public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record ProviderConfigurationDto
{
    [JsonPropertyName("project_id")] public string ProjectId { get; init; } = "project-rag";
    [JsonPropertyName("provider")] public string Provider { get; init; } = "openai_compatible";
    [JsonPropertyName("base_url")] public string BaseUrl { get; init; } = "";
    [JsonPropertyName("model")] public string Model { get; init; } = "";
    [JsonPropertyName("api_key")] public string ApiKey { get; init; } = "";
}
