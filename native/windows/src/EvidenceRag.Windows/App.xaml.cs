using EvidenceRag.Windows.Core.Api;
using EvidenceRag.Windows.Core.Services;
using EvidenceRag.Windows.Services;
using Microsoft.UI.Xaml;

namespace EvidenceRag.Windows;

public partial class App : Application
{
    private Window? _window;
    public static NativeAppServices Services { get; private set; } = null!;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, args) =>
        {
            // Keep exception text out of logs: provider errors may contain sensitive material.
            args.Handled = false;
        };
        var http = new HttpClient();
        Services = new NativeAppServices(
            new EvidenceRagApiClient(http, ApiClientOptions.LocalDefault),
            new ProjectSelection(),
            new WindowsCredentialVault());
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        _window.Activate();
    }
}

public sealed record NativeAppServices(
    IEvidenceRagApiClient Api,
    ProjectSelection ProjectSelection,
    ICredentialVault CredentialVault);
