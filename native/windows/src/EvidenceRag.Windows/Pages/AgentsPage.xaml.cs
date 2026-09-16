using EvidenceRag.Windows.Core.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace EvidenceRag.Windows.Pages;
public sealed partial class AgentsPage : Page
{
    public AgentsViewModel ViewModel { get; } = new(App.Services.Api, App.Services.ProjectSelection);
    public AgentsPage() { InitializeComponent(); DataContext = ViewModel; Loaded += OnLoaded; Unloaded += (_, _) => ViewModel.Cancel(); }
    private async void OnLoaded(object sender, RoutedEventArgs e) { Loaded -= OnLoaded; await ViewModel.LoadAsync(); }
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await ViewModel.LoadAsync();
}
