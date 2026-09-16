using System.Net;
using System.Text;
using System.Text.Json;
using EvidenceRag.Windows.Core.Api;
using EvidenceRag.Windows.Core.Models;

namespace EvidenceRag.Windows.Tests;

[TestClass]
public sealed class ApiClientTests
{
    [TestMethod]
    public async Task Projects_UsesLoopbackContract_AndDecodesTypedItems()
    {
        var handler = new RecordingHandler(request => Json("[{\"id\":\"project-rag\",\"name\":\"RAG\",\"status\":\"active\"}]"));
        var client = Create(handler);
        var projects = await client.GetProjectsAsync(CancellationToken.None);
        Assert.AreEqual("/v1/projects", handler.LastRequest!.RequestUri!.PathAndQuery);
        Assert.AreEqual("project-rag", projects.Single().Id);
        Assert.AreEqual("RAG", projects.Single().Name);
    }

    [TestMethod]
    public async Task Repositories_UsesGovernedProjectEndpoint_AndDecodesVersion()
    {
        var handler = new RecordingHandler(request => Json("[{\"id\":\"repo://local/rag\",\"project_id\":\"project-rag\",\"name\":\"rag\",\"default_branch\":\"main\",\"head_commit\":\"abcdef\",\"status\":\"ready\",\"active_generation_id\":\"gen-one\"}]"));
        var repositories = await Create(handler).GetRepositoriesAsync("project-rag", CancellationToken.None);
        Assert.AreEqual("/v1/repositories?project_id=project-rag", handler.LastRequest!.RequestUri!.PathAndQuery);
        Assert.AreEqual("abcdef", repositories.Single().HeadCommit);
    }

    [TestMethod]
    public async Task TrustedQuery_SendsFullMultiSourceScope_WithoutSecretHeaders()
    {
        var handler = new RecordingHandler(request => Json("{\"answer\":{\"text\":\"grounded\",\"citations\":[\"E1\"]},\"answer_mode\":\"GENERATED\",\"interaction_id\":\"i-1\"}"));
        var client = Create(handler);
        var sources = new[] { "code", "codex", "workspace", "experiment", "notebook", "document" };
        await client.QueryAsync(new QueryRequestDto
        {
            Question = "What changed?",
            Scope = new QueryScopeDto
            {
                ProjectId = "project-rag",
                RepositoryIds = ["repo://local/rag"],
                Branch = "main",
                Commit = "abcdef",
                SourceTypes = sources,
            },
            Include = sources,
        }, CancellationToken.None);

        Assert.AreEqual(HttpMethod.Post, handler.LastRequest!.Method);
        Assert.AreEqual("/v1/query", handler.LastRequest.RequestUri!.AbsolutePath);
        Assert.IsFalse(handler.LastRequest.Headers.Contains("Authorization"));
        using var document = JsonDocument.Parse(handler.LastBody!);
        Assert.AreEqual("project-rag", document.RootElement.GetProperty("scope").GetProperty("project_id").GetString());
        Assert.AreEqual("repo://local/rag", document.RootElement.GetProperty("scope").GetProperty("repository_ids")[0].GetString());
        Assert.AreEqual("abcdef", document.RootElement.GetProperty("scope").GetProperty("commit").GetString());
        Assert.AreEqual(6, document.RootElement.GetProperty("include").GetArrayLength());
        Assert.AreEqual(60_000, document.RootElement.GetProperty("deadline_ms").GetInt32());
    }

    [TestMethod]
    public async Task HistoryDecoder_AcceptsEnvelopeAndArrayContracts()
    {
        foreach (var payload in new[]
        {
            "[{\"interaction_id\":\"i-1\",\"question\":\"q\"}]",
            "{\"items\":[{\"interaction_id\":\"i-2\",\"question\":\"q\"}]}"
        })
        {
            var client = Create(new RecordingHandler(_ => Json(payload)));
            var history = await client.GetQueryHistoryAsync("project-rag", 20, CancellationToken.None);
            Assert.AreEqual(1, history.Count);
        }
    }

    [TestMethod]
    public async Task Decoder_DoesNotLeakErrorResponseBody()
    {
        using var response = new HttpResponseMessage(HttpStatusCode.BadGateway)
        {
            Content = new StringContent("secret-key-and-private-prompt"),
        };
        var failure = await Assert.ThrowsExceptionAsync<ApiFailureException>(() =>
            EvidenceRagApiClient.DecodeAsync<ProjectDto>(response, "projects", CancellationToken.None));
        Assert.IsFalse(failure.Message.Contains("secret", StringComparison.OrdinalIgnoreCase));
        Assert.AreEqual(HttpStatusCode.BadGateway, failure.StatusCode);
    }

    [TestMethod]
    public void NonLoopbackBaseAddress_IsRejected()
    {
        Assert.ThrowsException<ArgumentException>(() =>
            new EvidenceRagApiClient(new HttpClient(new RecordingHandler(_ => Json("{}"))), new ApiClientOptions(new Uri("https://example.com"))));
    }

    private static EvidenceRagApiClient Create(HttpMessageHandler handler) =>
        new(new HttpClient(handler), ApiClientOptions.LocalDefault);

    private static HttpResponseMessage Json(string json) => new(HttpStatusCode.OK)
    {
        Content = new StringContent(json, Encoding.UTF8, "application/json"),
    };

    private sealed class RecordingHandler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
    {
        public HttpRequestMessage? LastRequest { get; private set; }
        public string? LastBody { get; private set; }
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            LastRequest = request;
            LastBody = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken);
            return response(request);
        }
    }
}
