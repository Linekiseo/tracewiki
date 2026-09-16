using EvidenceRag.Windows.Core.Mvvm;

namespace EvidenceRag.Windows.Core.Services;

public interface ICredentialVault
{
    Task SaveAsync(string projectId, string secret);
    Task<string?> ReadAsync(string projectId);
    Task DeleteAsync(string projectId);
}

public sealed class ProjectSelection : ObservableObject
{
    private string _projectId = "project-rag";
    public string ProjectId
    {
        get => _projectId;
        set
        {
            var normalized = string.IsNullOrWhiteSpace(value) ? "project-rag" : value.Trim();
            SetProperty(ref _projectId, normalized);
        }
    }
}
