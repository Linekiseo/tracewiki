using System.Collections.ObjectModel;
using System.Text.Json;
using EvidenceRag.Windows.Core.Api;
using EvidenceRag.Windows.Core.Models;
using EvidenceRag.Windows.Core.Mvvm;
using EvidenceRag.Windows.Core.Services;

namespace EvidenceRag.Windows.Core.ViewModels;

public sealed class ProjectsViewModel(IEvidenceRagApiClient api, ProjectSelection selection) : ViewModelBase
{
    private ProjectDto? _selectedProject;
    public ObservableCollection<ProjectDto> Projects { get; } = [];
    public ProjectDto? SelectedProject
    {
        get => _selectedProject;
        set
        {
            if (SetProperty(ref _selectedProject, value) && value is not null) selection.ProjectId = value.Id;
        }
    }
    public string ActiveProjectId => selection.ProjectId;

    public Task LoadAsync() => RunLatestAsync(async token =>
    {
        var projects = await api.GetProjectsAsync(token);
        Projects.Clear();
        foreach (var project in projects) Projects.Add(project);
        SelectedProject = Projects.FirstOrDefault(item => item.Id == selection.ProjectId) ?? Projects.FirstOrDefault();
        RaisePropertyChanged(nameof(ActiveProjectId));
    });
}

public sealed class TrustedQueryViewModel(IEvidenceRagApiClient api, ProjectSelection selection) : ViewModelBase
{
    private const int HistoryPageSize = 20;
    private string _question = "";
    private string _answer = "";
    private string _answerMode = "";
    private string _interactionId = "";
    private int _evidenceCount;
    private int _historyPage;
    private bool _hasNextHistoryPage;
    private RepositoryDto? _selectedRepository;

    public string Question { get => _question; set => SetProperty(ref _question, value); }
    public string Answer { get => _answer; private set => SetProperty(ref _answer, value); }
    public string AnswerMode { get => _answerMode; private set => SetProperty(ref _answerMode, value); }
    public string InteractionId { get => _interactionId; private set => SetProperty(ref _interactionId, value); }
    public int EvidenceCount { get => _evidenceCount; private set => SetProperty(ref _evidenceCount, value); }
    public int HistoryPage { get => _historyPage; private set { SetProperty(ref _historyPage, value); RaisePropertyChanged(nameof(HistoryPageLabel)); RaisePropertyChanged(nameof(CanPreviousHistoryPage)); } }
    public string HistoryPageLabel => $"第 {HistoryPage + 1} 页";
    public bool CanPreviousHistoryPage => HistoryPage > 0;
    public bool HasNextHistoryPage { get => _hasNextHistoryPage; private set => SetProperty(ref _hasNextHistoryPage, value); }
    public ObservableCollection<QueryHistoryItemDto> History { get; } = [];
    public ObservableCollection<RepositoryDto> Repositories { get; } = [];
    public RepositoryDto? SelectedRepository { get => _selectedRepository; set => SetProperty(ref _selectedRepository, value); }

    public Task LoadAsync() => RunLatestAsync(async token =>
    {
        var repositories = await api.GetRepositoriesAsync(selection.ProjectId, token);
        Repositories.Clear();
        foreach (var repository in repositories.Where(item => IsGovernedRepository(item, selection.ProjectId)))
            Repositories.Add(repository);
        SelectedRepository = Repositories.Count == 1 ? Repositories[0] : null;
        await LoadHistoryCoreAsync(token);
    });

    public Task SubmitAsync() => RunLatestAsync(async token =>
    {
        var question = Question.Trim();
        if (question.Length == 0) throw new ArgumentException("请输入查询问题。");
        if (SelectedRepository is null
            || !Repositories.Any(item => ReferenceEquals(item, SelectedRepository))
            || !IsGovernedRepository(SelectedRepository, selection.ProjectId))
            throw new ArgumentException("请选择一个已发布且具有确定分支和提交的代码仓库，以固定查询版本。");
        var sources = new[] { "code", "codex", "workspace", "experiment", "notebook", "document" };
        var result = await api.QueryAsync(new QueryRequestDto
        {
            Question = question,
            Scope = new QueryScopeDto
            {
                ProjectId = selection.ProjectId,
                RepositoryIds = [SelectedRepository.Id],
                Branch = SelectedRepository.DefaultBranch,
                Commit = SelectedRepository.HeadCommit,
                SourceTypes = sources,
            },
            Include = sources,
        }, token);
        Answer = result.Answer.Text;
        AnswerMode = !string.IsNullOrEmpty(result.AnswerMode)
            ? result.AnswerMode
            : (!string.IsNullOrEmpty(result.Answer.AnswerMode) ? result.Answer.AnswerMode : result.Mode);
        InteractionId = result.InteractionId;
        EvidenceCount = result.Answer.Citations.Count + CountArray(result.Evidence) + CountArray(result.Citations);
        await LoadHistoryCoreAsync(token);
    });

    public Task LoadHistoryAsync() => RunLatestAsync(LoadHistoryCoreAsync);
    public Task NextHistoryPageAsync() => RunLatestAsync(async token => { HistoryPage++; await LoadHistoryCoreAsync(token); });
    public Task PreviousHistoryPageAsync() => RunLatestAsync(async token => { if (HistoryPage > 0) HistoryPage--; await LoadHistoryCoreAsync(token); });

    private async Task LoadHistoryCoreAsync(CancellationToken token)
    {
        var requested = (HistoryPage + 2) * HistoryPageSize;
        var items = await api.GetQueryHistoryAsync(selection.ProjectId, requested, token);
        var page = items.Skip(HistoryPage * HistoryPageSize).Take(HistoryPageSize).ToArray();
        History.Clear();
        foreach (var item in page) History.Add(item);
        HasNextHistoryPage = items.Count > (HistoryPage + 1) * HistoryPageSize;
    }

    private static int CountArray(JsonElement? element) =>
        element is { ValueKind: JsonValueKind.Array } value ? value.GetArrayLength() : 0;

    private static bool IsGovernedRepository(RepositoryDto repository, string projectId) =>
        repository.ProjectId == projectId
        && repository.Status == "ready"
        && !string.IsNullOrWhiteSpace(repository.Id)
        && !string.IsNullOrWhiteSpace(repository.ActiveGenerationId)
        && !string.IsNullOrWhiteSpace(repository.DefaultBranch)
        && !string.IsNullOrWhiteSpace(repository.HeadCommit);
}

public sealed class WikiViewModel(IEvidenceRagApiClient api, ProjectSelection selection) : ViewModelBase
{
    private const int PageSize = 30;
    private string _searchText = "";
    private int _offset;
    private int _total;
    private int? _nextOffset;
    public ObservableCollection<WikiPageDto> Pages { get; } = [];
    public string SearchText { get => _searchText; set => SetProperty(ref _searchText, value); }
    public int Total { get => _total; private set => SetProperty(ref _total, value); }
    public string PageLabel => $"{Offset / PageSize + 1} / {Math.Max(1, (Total + PageSize - 1) / PageSize)}";
    public int Offset { get => _offset; private set { SetProperty(ref _offset, value); RaisePropertyChanged(nameof(PageLabel)); RaisePropertyChanged(nameof(CanPrevious)); } }
    public bool CanPrevious => Offset > 0;
    public bool CanNext => _nextOffset is not null;
    public Task SearchAsync() => RunLatestAsync(async token => { Offset = 0; await LoadCoreAsync(token); });
    public Task LoadAsync() => RunLatestAsync(LoadCoreAsync);
    public Task NextAsync() => RunLatestAsync(async token => { if (_nextOffset is int next) Offset = next; await LoadCoreAsync(token); });
    public Task PreviousAsync() => RunLatestAsync(async token => { Offset = Math.Max(0, Offset - PageSize); await LoadCoreAsync(token); });

    private async Task LoadCoreAsync(CancellationToken token)
    {
        var page = await api.GetWikiPagesAsync(selection.ProjectId, SearchText.Trim(), Offset, PageSize, token);
        Pages.Clear(); foreach (var item in page.Pages) Pages.Add(item);
        Total = page.Total; _nextOffset = page.NextOffset;
        RaisePropertyChanged(nameof(PageLabel)); RaisePropertyChanged(nameof(CanNext));
    }
}

public sealed class GraphViewModel(IEvidenceRagApiClient api, ProjectSelection selection) : ViewModelBase
{
    private string _domain = "overview";
    private int _nodeCount;
    private int _edgeCount;
    public ObservableCollection<GraphNodeDto> Nodes { get; } = [];
    public ObservableCollection<GraphEdgeDto> Edges { get; } = [];
    public string Domain { get => _domain; set => SetProperty(ref _domain, value); }
    public int NodeCount { get => _nodeCount; private set => SetProperty(ref _nodeCount, value); }
    public int EdgeCount { get => _edgeCount; private set => SetProperty(ref _edgeCount, value); }
    public Task LoadAsync() => RunLatestAsync(async token =>
    {
        var graph = await api.GetGraphAsync(selection.ProjectId, Domain, 200, token);
        Nodes.Clear(); foreach (var node in graph.Nodes) Nodes.Add(node);
        Edges.Clear(); foreach (var edge in graph.Edges) Edges.Add(edge);
        NodeCount = Nodes.Count; EdgeCount = Edges.Count;
    });
}

public sealed class SessionsViewModel(IEvidenceRagApiClient api, ProjectSelection selection) : ViewModelBase
{
    private const int PageSize = 50;
    private int _offset;
    private bool _canNext;
    public ObservableCollection<SessionDto> Sessions { get; } = [];
    public int Offset { get => _offset; private set { SetProperty(ref _offset, value); RaisePropertyChanged(nameof(PageLabel)); RaisePropertyChanged(nameof(CanPrevious)); } }
    public string PageLabel => $"第 {Offset / PageSize + 1} 页";
    public bool CanPrevious => Offset > 0;
    public bool CanNext { get => _canNext; private set => SetProperty(ref _canNext, value); }
    public Task LoadAsync() => RunLatestAsync(LoadCoreAsync);
    public Task NextAsync() => RunLatestAsync(async token => { Offset += PageSize; await LoadCoreAsync(token); });
    public Task PreviousAsync() => RunLatestAsync(async token => { Offset = Math.Max(0, Offset - PageSize); await LoadCoreAsync(token); });
    private async Task LoadCoreAsync(CancellationToken token)
    {
        var items = await api.GetSessionsAsync(selection.ProjectId, Offset, PageSize, token);
        Sessions.Clear(); foreach (var item in items) Sessions.Add(item);
        CanNext = items.Count == PageSize;
    }
}

public sealed class AgentsViewModel(IEvidenceRagApiClient api, ProjectSelection selection) : ViewModelBase
{
    public ObservableCollection<AgentDto> Agents { get; } = [];
    public Task LoadAsync() => RunLatestAsync(async token =>
    {
        var agents = await api.GetAgentsAsync(selection.ProjectId, token);
        Agents.Clear(); foreach (var agent in agents) Agents.Add(agent);
    });
}

public sealed class SettingsViewModel(IEvidenceRagApiClient api, ICredentialVault vault, ProjectSelection selection) : ViewModelBase
{
    private string _baseUrl = "https://api.deepseek.com";
    private string _model = "deepseek-v4-flash";
    private ProviderStatusDto? _status;
    private string _notice = "";
    public string BaseUrl { get => _baseUrl; set => SetProperty(ref _baseUrl, value); }
    public string Model { get => _model; set => SetProperty(ref _model, value); }
    public ProviderStatusDto? Status { get => _status; private set => SetProperty(ref _status, value); }
    public string Notice { get => _notice; private set => SetProperty(ref _notice, value); }

    public Task LoadAsync() => RunLatestAsync(async token => Status = await api.GetProviderStatusAsync(selection.ProjectId, token));

    public Task SaveAndRegisterAsync(string apiKey) => RunLatestAsync(async token =>
    {
        var secret = apiKey.Trim();
        if (secret.Length == 0) throw new ArgumentException("API Key 不能为空。");
        await vault.SaveAsync(selection.ProjectId, secret);
        try
        {
            Status = await api.ConfigureProviderAsync(new ProviderConfigurationDto
            {
                ProjectId = selection.ProjectId,
                BaseUrl = BaseUrl.Trim(),
                Model = Model.Trim(),
                ApiKey = secret,
            }, token);
            Notice = "密钥已保存到 Windows Credential Locker，并已注册到本机运行时。";
        }
        finally { secret = string.Empty; }
    });

    public Task RestoreAndRegisterAsync() => RunLatestAsync(async token =>
    {
        var secret = await vault.ReadAsync(selection.ProjectId);
        if (string.IsNullOrEmpty(secret)) { Notice = "Credential Locker 中没有此项目的密钥。"; return; }
        try
        {
            Status = await api.ConfigureProviderAsync(new ProviderConfigurationDto
            {
                ProjectId = selection.ProjectId,
                BaseUrl = BaseUrl.Trim(),
                Model = Model.Trim(),
                ApiKey = secret,
            }, token);
            Notice = "已从 Credential Locker 恢复，并仅注册到本机进程内存。";
        }
        finally { secret = string.Empty; }
    });

    public Task TestAsync() => RunLatestAsync(async token =>
    {
        Status = await api.TestProviderAsync(selection.ProjectId, token);
        Notice = "模型连接测试通过。";
    });
}
