using EvidenceRag.Windows.Core.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace EvidenceRag.Windows.Pages;
public sealed partial class GraphPage : Page
{
    public GraphViewModel ViewModel { get; } = new(App.Services.Api, App.Services.ProjectSelection);
    public GraphPage() { InitializeComponent(); DataContext = ViewModel; Loaded += OnLoaded; Unloaded += (_, _) => ViewModel.Cancel(); }
    private async void OnLoaded(object sender, RoutedEventArgs e) { Loaded -= OnLoaded; await ViewModel.LoadAsync(); }
    private async void Load_Click(object sender, RoutedEventArgs e) => await ViewModel.LoadAsync();
}
