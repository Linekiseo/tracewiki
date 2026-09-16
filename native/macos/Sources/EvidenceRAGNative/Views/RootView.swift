import SwiftUI

struct RootView: View {
  @ObservedObject var store: AppStore

  var body: some View {
    NavigationSplitView {
      SidebarView(selection: $store.selection)
        .navigationSplitViewColumnWidth(min: 210, ideal: 235, max: 285)
    } detail: {
      detail
        .navigationTitle(store.selection.title)
        .toolbar { ProjectToolbar(store: store) }
    }
    .task { await store.start() }
    .onChange(of: store.selection) {
      Task { await store.refreshCurrentSection() }
    }
  }

  @ViewBuilder
  private var detail: some View {
    switch store.selection {
    case .projects: ProjectsView(store: store)
    case .trustedQuery: TrustedQueryView(store: store)
    case .wiki: WikiView(store: store)
    case .graph: GraphView(store: store)
    case .sessions: SessionsView(store: store)
    case .agents: AgentsView(store: store)
    case .settings: SettingsModuleView(store: store)
    }
  }
}

private struct ProjectToolbar: ToolbarContent {
  @ObservedObject var store: AppStore

  var body: some ToolbarContent {
    ToolbarItem(placement: .automatic) {
      Menu {
        ForEach(store.projects) { project in
          Button {
            Task { await store.selectProject(project.id) }
          } label: {
            if project.id == store.selectedProjectID {
              Label(project.name, systemImage: "checkmark")
            } else {
              Text(project.name)
            }
          }
        }
      } label: {
        Label(store.selectedProject?.name ?? "选择项目", systemImage: "folder")
      }
      .disabled(store.projects.isEmpty)
      .help("切换当前项目")
    }

    ToolbarItem(placement: .primaryAction) {
      Button {
        Task { await store.refreshCurrentSection() }
      } label: {
        Label("刷新", systemImage: "arrow.clockwise")
      }
      .disabled(store.state.isLoading)
      .help("刷新当前模块（⌘R）")
    }
  }
}
