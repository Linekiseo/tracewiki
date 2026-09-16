using EvidenceRag.Windows.Core.Api;
using EvidenceRag.Windows.Core.Models;
using EvidenceRag.Windows.Core.Services;
using EvidenceRag.Windows.Core.ViewModels;

namespace EvidenceRag.Windows.Tests;

[TestClass]
public sealed class ViewModelTests
{
    [TestMethod]
    public async Task ProjectSelection_FollowsSelectedRealProject()
    {
        var selection = new ProjectSelection();
        var api = new StubApi { Projects = [new ProjectDto { Id = "p-1", Name = "One" }, new ProjectDto { Id = "p-2", Name = "Two" }] };
        var viewModel = new ProjectsViewModel(api, selection);
        await viewModel.LoadAsync();
        viewModel.SelectedProject = api.Projects[1];
        Assert.AreEqual("p-2", selection.ProjectId);
    }

    [TestMethod]
    public async Task WikiPagination_UsesServerNextOffset_AndResetsOnSearch()
    {
        var api = new StubApi
        {
            WikiFactory = offset => new WikiPageEnvelope
            {
                Offset = offset,
                Limit = 30,
                Total = 61,
                NextOffset = offset < 60 ? offset + 30 : null,
                Pages = [new WikiPageDto { LogicalPath = $"/page-{offset}", Title = $"Page {offset}" }],
            },
        };
        var viewModel = new WikiViewModel(api, new ProjectSelection());
        await viewModel.LoadAsync();
        await viewModel.NextAsync();
        Assert.AreEqual(30, viewModel.Offset);
        viewModel.SearchText = "architecture";
        await viewModel.SearchAsync();
        Assert.AreEqual(0, viewModel.Offset);
        CollectionAssert.Contains(api.WikiOffsets, 30);
    }

    [TestMethod]
    public async Task QueryViewModel_UsesFixedDenominatorSources_AndLoadsHistory()
    {
        var api = new StubApi
        {
            Repositories = [new RepositoryDto { Id = "repo://local/rag", ProjectId = "project-rag", Name = "rag", DefaultBranch = "main", HeadCommit = "abcdef", Status = "ready", ActiveGenerationId = "gen-one" }],
            QueryResult = new QueryResultDto { Answer = new QueryAnswerDto { Text = "answer [E1]", Citations = ["E1"] }, AnswerMode = "GENERATED", InteractionId = "i-1" },
            History = [new QueryHistoryItemDto { InteractionId = "i-1", Question = "why" }],
        };
        var viewModel = new TrustedQueryViewModel(api, new ProjectSelection());
        await viewModel.LoadAsync();
        viewModel.Question = "why";
        await viewModel.SubmitAsync();
        Assert.AreEqual("answer [E1]", viewModel.Answer);
        Assert.AreEqual(6, api.LastQuery!.Include.Count);
        Assert.AreEqual("repo://local/rag", api.LastQuery.Scope.RepositoryIds.Single());
        Assert.AreEqual("abcdef", api.LastQuery.Scope.Commit);
        Assert.AreEqual(1, viewModel.History.Count);
    }

    [TestMethod]
    public async Task QueryViewModel_RejectsRepositoriesWithoutExactGovernedVersion()
    {
        var api = new StubApi
        {
            Repositories =
            [
                new RepositoryDto { Id = "repo://local/no-branch", ProjectId = "project-rag", Name = "no branch", HeadCommit = "abcdef", Status = "ready", ActiveGenerationId = "gen-one" },
                new RepositoryDto { Id = "repo://local/no-commit", ProjectId = "project-rag", Name = "no commit", DefaultBranch = "main", Status = "ready", ActiveGenerationId = "gen-one" },
                new RepositoryDto { Id = "repo://local/cross-project", ProjectId = "another-project", Name = "cross project", DefaultBranch = "main", HeadCommit = "abcdef", Status = "ready", ActiveGenerationId = "gen-one" },
            ],
        };
        var viewModel = new TrustedQueryViewModel(api, new ProjectSelection());

        await viewModel.LoadAsync();
        viewModel.Question = "why";
        viewModel.SelectedRepository = api.Repositories[0];
        await viewModel.SubmitAsync();

        Assert.AreEqual(0, viewModel.Repositories.Count);
        Assert.IsNull(api.LastQuery);
        Assert.IsTrue(viewModel.HasError);
    }

    [TestMethod]
    public async Task LatestLoad_CancelsThePreviousOperation()
    {
        var api = new StubApi { BlockProjects = true };
        var viewModel = new ProjectsViewModel(api, new ProjectSelection());
        var first = viewModel.LoadAsync();
        await api.FirstProjectCallStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
        api.BlockProjects = false;
        var second = viewModel.LoadAsync();
        await Task.WhenAll(first, second);
        Assert.IsTrue(api.FirstProjectCallCancelled);
    }

    private sealed class StubApi : IEvidenceRagApiClient
    {
        private int _projectCalls;
        public IReadOnlyList<ProjectDto> Projects { get; set; } = [];
        public IReadOnlyList<RepositoryDto> Repositories { get; set; } = [];
        public QueryResultDto QueryResult { get; set; } = new();
        public IReadOnlyList<QueryHistoryItemDto> History { get; set; } = [];
        public Func<int, WikiPageEnvelope> WikiFactory { get; set; } = _ => new();
        public List<int> WikiOffsets { get; } = [];
        public QueryRequestDto? LastQuery { get; private set; }
        public bool BlockProjects { get; set; }
        public TaskCompletionSource<bool> FirstProjectCallStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public bool FirstProjectCallCancelled { get; private set; }

        public async Task<IReadOnlyList<ProjectDto>> GetProjectsAsync(CancellationToken cancellationToken)
        {
            _projectCalls++;
            if (_projectCalls == 1 && BlockProjects)
            {
                FirstProjectCallStarted.SetResult(true);
                try { await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken); }
                catch (OperationCanceledException) { FirstProjectCallCancelled = true; throw; }
            }
            return Projects;
        }
        public Task<IReadOnlyList<RepositoryDto>> GetRepositoriesAsync(string projectId, CancellationToken cancellationToken) => Task.FromResult(Repositories);
        public Task<ProviderStatusDto> GetProviderStatusAsync(string projectId, CancellationToken cancellationToken) => Task.FromResult(new ProviderStatusDto());
        public Task<ProviderStatusDto> ConfigureProviderAsync(ProviderConfigurationDto configuration, CancellationToken cancellationToken) => Task.FromResult(new ProviderStatusDto { Configured = true });
        public Task<ProviderStatusDto> TestProviderAsync(string projectId, CancellationToken cancellationToken) => Task.FromResult(new ProviderStatusDto { Configured = true });
        public Task<QueryResultDto> QueryAsync(QueryRequestDto request, CancellationToken cancellationToken) { LastQuery = request; return Task.FromResult(QueryResult); }
        public Task<IReadOnlyList<QueryHistoryItemDto>> GetQueryHistoryAsync(string projectId, int limit, CancellationToken cancellationToken) => Task.FromResult(History);
        public Task<WikiPageEnvelope> GetWikiPagesAsync(string projectId, string query, int offset, int limit, CancellationToken cancellationToken) { WikiOffsets.Add(offset); return Task.FromResult(WikiFactory(offset)); }
        public Task<GraphEnvelope> GetGraphAsync(string projectId, string domain, int limit, CancellationToken cancellationToken) => Task.FromResult(new GraphEnvelope());
        public Task<IReadOnlyList<SessionDto>> GetSessionsAsync(string projectId, int offset, int limit, CancellationToken cancellationToken) => Task.FromResult<IReadOnlyList<SessionDto>>([]);
        public Task<IReadOnlyList<AgentDto>> GetAgentsAsync(string projectId, CancellationToken cancellationToken) => Task.FromResult<IReadOnlyList<AgentDto>>([]);
    }
}
