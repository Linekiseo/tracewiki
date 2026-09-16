using EvidenceRag.Windows.Core.Services;
using Windows.Security.Credentials;

namespace EvidenceRag.Windows.Services;

public sealed class WindowsCredentialVault : ICredentialVault
{
    private const string Resource = "EvidenceRag.LlmProvider";

    public Task SaveAsync(string projectId, string secret)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(projectId);
        ArgumentException.ThrowIfNullOrWhiteSpace(secret);
        var vault = new PasswordVault();
        foreach (var existing in Find(vault, projectId)) vault.Remove(existing);
        vault.Add(new PasswordCredential(Resource, projectId, secret));
        return Task.CompletedTask;
    }

    public Task<string?> ReadAsync(string projectId)
    {
        var vault = new PasswordVault();
        try
        {
            var credential = vault.Retrieve(Resource, projectId);
            credential.RetrievePassword();
            return Task.FromResult<string?>(credential.Password);
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            return Task.FromResult<string?>(null);
        }
    }

    public Task DeleteAsync(string projectId)
    {
        var vault = new PasswordVault();
        foreach (var existing in Find(vault, projectId)) vault.Remove(existing);
        return Task.CompletedTask;
    }

    private static IEnumerable<PasswordCredential> Find(PasswordVault vault, string projectId)
    {
        try { return vault.FindAllByResource(Resource).Where(item => item.UserName == projectId).ToArray(); }
        catch (Exception exception) when (exception is not OutOfMemoryException) { return []; }
    }
}
