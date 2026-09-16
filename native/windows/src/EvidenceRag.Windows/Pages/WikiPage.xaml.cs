using EvidenceRag.Windows.Core.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace EvidenceRag.Windows.Pages;
public sealed partial class WikiPage : Page
{
    public WikiViewModel ViewModel { get; } = new(App.Services.Api, App.Services.ProjectSelection);
    public WikiPage() { InitializeComponent(); DataContext = ViewModel; Loaded += OnLoaded; Unloaded += (_, _) => ViewModel.Cancel(); }
    private async void OnLoaded(object sender, RoutedEventArgs e) { Loaded -= OnLoaded; await ViewModel.LoadAsync(); }
    private async void Search_Click(object sender, RoutedEventArgs e) => await ViewModel.SearchAsync();
    private async void Previous_Click(object sender, RoutedEventArgs e) => await ViewModel.PreviousAsync();
    private async void Next_Click(object sender, RoutedEventArgs e) => await ViewModel.NextAsync();
}
