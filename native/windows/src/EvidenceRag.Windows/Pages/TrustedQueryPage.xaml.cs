using EvidenceRag.Windows.Core.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace EvidenceRag.Windows.Pages;

public sealed partial class TrustedQueryPage : Page
{
    public TrustedQueryViewModel ViewModel { get; } = new(App.Services.Api, App.Services.ProjectSelection);
    public TrustedQueryPage() { InitializeComponent(); DataContext = ViewModel; Loaded += OnLoaded; Unloaded += (_, _) => ViewModel.Cancel(); }
    private async void OnLoaded(object sender, RoutedEventArgs e) { Loaded -= OnLoaded; await ViewModel.LoadAsync(); }
    private async void Submit_Click(object sender, RoutedEventArgs e) => await ViewModel.SubmitAsync();
    private void Cancel_Click(object sender, RoutedEventArgs e) => ViewModel.Cancel();
    private async void Previous_Click(object sender, RoutedEventArgs e) => await ViewModel.PreviousHistoryPageAsync();
    private async void Next_Click(object sender, RoutedEventArgs e) => await ViewModel.NextHistoryPageAsync();
}
