using EvidenceRag.Windows.Pages;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace EvidenceRag.Windows;

public sealed partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        SystemBackdrop = new MicaBackdrop();
        Navigation.SelectedItem = Navigation.MenuItems[0];
        ContentFrame.Navigate(typeof(ProjectsPage));
    }

    private void Navigation_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected) { Navigate(typeof(SettingsPage)); return; }
        if (args.SelectedItemContainer?.Tag is not string tag) return;
        var pageType = tag switch
        {
            "projects" => typeof(ProjectsPage),
            "query" => typeof(TrustedQueryPage),
            "wiki" => typeof(WikiPage),
            "graph" => typeof(GraphPage),
            "sessions" => typeof(SessionsPage),
            "agents" => typeof(AgentsPage),
            _ => typeof(ProjectsPage),
        };
        Navigate(pageType);
    }

    private void Navigate(Type pageType)
    {
        if (ContentFrame.CurrentSourcePageType != pageType) ContentFrame.Navigate(pageType);
    }
}
