using EvidenceRag.Windows.Core.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace EvidenceRag.Windows.Pages;
public sealed partial class SettingsPage : Page
{
    public SettingsViewModel ViewModel { get; } = new(App.Services.Api, App.Services.CredentialVault, App.Services.ProjectSelection);
    public SettingsPage() { InitializeComponent(); DataContext = ViewModel; Loaded += OnLoaded; Unloaded += (_, _) => ViewModel.Cancel(); }
    private async void OnLoaded(object sender, RoutedEventArgs e) { Loaded -= OnLoaded; await ViewModel.LoadAsync(); }
    private async void Save_Click(object sender, RoutedEventArgs e) { var secret = ApiKeyBox.Password; ApiKeyBox.Password = ""; await ViewModel.SaveAndRegisterAsync(secret); secret = string.Empty; }
    private async void Restore_Click(object sender, RoutedEventArgs e) => await ViewModel.RestoreAndRegisterAsync();
    private async void Test_Click(object sender, RoutedEventArgs e) => await ViewModel.TestAsync();
    private void Cancel_Click(object sender, RoutedEventArgs e) => ViewModel.Cancel();
}
