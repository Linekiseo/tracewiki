using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using EvidenceRag.Windows.Core.Models;

namespace EvidenceRag.Windows.Core.Api;

public sealed record ApiClientOptions(Uri BaseAddress)
{
    public static ApiClientOptions LocalDefault { get; } = new(new Uri("http://127.0.0.1:8765"));
}

public sealed class ApiFailureException(string operation, HttpStatusCode statusCode, string safeMessage)
    : Exception(safeMessage)
{
    public string Operation { get; } = operation;
    public HttpStatusCode StatusCode { get; } = statusCode;
}

public interface IEvidenceRagApiClient
{
    Task<IReadOnlyList<ProjectDto>> GetProjectsAsync(CancellationToken cancellationToken);
    Task<IReadOnlyList<RepositoryDto>> GetRepositoriesAsync(string projectId, CancellationToken cancellationToken);
    Task<ProviderStatusDto> GetProviderStatusAsync(string projectId, CancellationToken cancellationToken);
    Task<ProviderStatusDto> ConfigureProviderAsync(ProviderConfigurationDto configuration, CancellationToken cancellationToken);
    Task<ProviderStatusDto> TestProviderAsync(string projectId, CancellationToken cancellationToken);
    Task<QueryResultDto> QueryAsync(QueryRequestDto request, CancellationToken cancellationToken);
    Task<IReadOnlyList<QueryHistoryItemDto>> GetQueryHistoryAsync(string projectId, int limit, CancellationToken cancellationToken);
    Task<WikiPageEnvelope> GetWikiPagesAsync(string projectId, string query, int offset, int limit, CancellationToken cancellationToken);
    Task<GraphEnvelope> GetGraphAsync(string projectId, string domain, int limit, CancellationToken cancellationToken);
    Task<IReadOnlyList<SessionDto>> GetSessionsAsync(string projectId, int offset, int limit, CancellationToken cancellationToken);
    Task<IReadOnlyList<AgentDto>> GetAgentsAsync(string projectId, CancellationToken cancellationToken);
}

public sealed class EvidenceRagApiClient : IEvidenceRagApiClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
    };
    private readonly HttpClient _http;

    public EvidenceRagApiClient(HttpClient http, ApiClientOptions options)
    {
        ArgumentNullException.ThrowIfNull(http);
        ArgumentNullException.ThrowIfNull(options);
        if (!options.BaseAddress.IsLoopback || options.BaseAddress.Scheme != Uri.UriSchemeHttp)
        {
            throw new ArgumentException("The native client only accepts the local loopback API endpoint.", nameof(options));
        }

        _http = http;
        _http.BaseAddress = options.BaseAddress;
        _http.Timeout = Timeout.InfiniteTimeSpan;
    }

    public Task<IReadOnlyList<ProjectDto>> GetProjectsAsync(CancellationToken cancellationToken) =>
        GetAsync<IReadOnlyList<ProjectDto>>("/v1/projects", "projects", cancellationToken);

    public Task<IReadOnlyList<RepositoryDto>> GetRepositoriesAsync(string projectId, CancellationToken cancellationToken) =>
        GetAsync<IReadOnlyList<RepositoryDto>>($"/v1/repositories?project_id={Encode(projectId)}", "repositories", cancellationToken);

    public Task<ProviderStatusDto> GetProviderStatusAsync(string projectId, CancellationToken cancellationToken) =>
        GetAsync<ProviderStatusDto>($"/v1/ai/provider?project_id={Encode(projectId)}", "provider-status", cancellationToken);

    public Task<ProviderStatusDto> ConfigureProviderAsync(ProviderConfigurationDto configuration, CancellationToken cancellationToken) =>
        SendAsync<ProviderStatusDto>(HttpMethod.Put, "/v1/ai/provider", configuration, "provider-configure", cancellationToken);

    public Task<ProviderStatusDto> TestProviderAsync(string projectId, CancellationToken cancellationToken) =>
        SendAsync<ProviderStatusDto>(HttpMethod.Post, "/v1/ai/provider/test", new { project_id = projectId }, "provider-test", cancellationToken);

    public Task<QueryResultDto> QueryAsync(QueryRequestDto request, CancellationToken cancellationToken) =>
        SendAsync<QueryResultDto>(HttpMethod.Post, "/v1/query", request, "trusted-query", cancellationToken);

    public async Task<IReadOnlyList<QueryHistoryItemDto>> GetQueryHistoryAsync(string projectId, int limit, CancellationToken cancellationToken)
    {
        using var json = await GetDocumentAsync($"/v1/query/history?project_id={Encode(projectId)}&limit={Clamp(limit, 1, 500)}", "query-history", cancellationToken);
        var root = json.RootElement;
        if (root.ValueKind == JsonValueKind.Array)
        {
            return root.Deserialize<IReadOnlyList<QueryHistoryItemDto>>(JsonOptions) ?? [];
        }

        foreach (var name in new[] { "items", "history", "interactions" })
        {
            if (root.TryGetProperty(name, out var items) && items.ValueKind == JsonValueKind.Array)
            {
                return items.Deserialize<IReadOnlyList<QueryHistoryItemDto>>(JsonOptions) ?? [];
            }
        }

        throw new JsonException("Query history response has no supported item collection.");
    }

    public Task<WikiPageEnvelope> GetWikiPagesAsync(string projectId, string query, int offset, int limit, CancellationToken cancellationToken) =>
        GetAsync<WikiPageEnvelope>($"/v1/wiki/pages?project_id={Encode(projectId)}&q={Encode(query)}&offset={Math.Max(0, offset)}&limit={Clamp(limit, 1, 200)}", "wiki-pages", cancellationToken);

    public Task<GraphEnvelope> GetGraphAsync(string projectId, string domain, int limit, CancellationToken cancellationToken) =>
        GetAsync<GraphEnvelope>($"/v1/graph?project_id={Encode(projectId)}&domain={Encode(domain)}&mode=relations&limit={Clamp(limit, 1, 500)}", "relation-graph", cancellationToken);

    public Task<IReadOnlyList<SessionDto>> GetSessionsAsync(string projectId, int offset, int limit, CancellationToken cancellationToken) =>
        GetAsync<IReadOnlyList<SessionDto>>($"/v1/codex/sessions?project_id={Encode(projectId)}&offset={Math.Max(0, offset)}&limit={Clamp(limit, 1, 500)}", "sessions", cancellationToken);

    public Task<IReadOnlyList<AgentDto>> GetAgentsAsync(string projectId, CancellationToken cancellationToken) =>
        GetAsync<IReadOnlyList<AgentDto>>($"/v1/codex-bridge/agents?project_id={Encode(projectId)}", "agents", cancellationToken);

    private async Task<T> GetAsync<T>(string path, string operation, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync(path, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        return await DecodeAsync<T>(response, operation, cancellationToken).ConfigureAwait(false);
    }

    private async Task<JsonDocument> GetDocumentAsync(string path, string operation, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync(path, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new ApiFailureException(operation, response.StatusCode, SafeFailure(operation, response.StatusCode));
        }
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        return await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken).ConfigureAwait(false);
    }

    private async Task<T> SendAsync<T>(HttpMethod method, string path, object body, string operation, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(method, path) { Content = JsonContent.Create(body, options: JsonOptions) };
        using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        return await DecodeAsync<T>(response, operation, cancellationToken).ConfigureAwait(false);
    }

    public static async Task<T> DecodeAsync<T>(HttpResponseMessage response, string operation, CancellationToken cancellationToken)
    {
        if (!response.IsSuccessStatusCode)
        {
            // Deliberately do not include the response body: upstream errors can contain prompts or secrets.
            throw new ApiFailureException(operation, response.StatusCode, SafeFailure(operation, response.StatusCode));
        }

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var result = await JsonSerializer.DeserializeAsync<T>(stream, JsonOptions, cancellationToken).ConfigureAwait(false);
        return result ?? throw new JsonException($"The {operation} response was empty.");
    }

    private static string SafeFailure(string operation, HttpStatusCode statusCode) =>
        $"{operation} failed with HTTP {(int)statusCode}. No response details were retained.";

    private static string Encode(string value) => Uri.EscapeDataString(value ?? "");
    private static int Clamp(int value, int minimum, int maximum) => Math.Min(maximum, Math.Max(minimum, value));
}
