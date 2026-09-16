import SwiftUI

struct ProjectsView: View {
  @ObservedObject var store: AppStore

  var body: some View {
    Group {
      if store.projects.isEmpty, !store.state.isLoading {
        EmptyContentView(
          title: "尚无可见项目",
          message: "连接服务后，可见项目会在这里显示。",
          systemImage: "square.stack.3d.up.slash"
        )
      } else {
        List(store.projects, selection: $store.selectedProjectID) { project in
          ProjectRow(project: project, selected: project.id == store.selectedProjectID)
            .tag(project.id)
            .contentShape(Rectangle())
            .onTapGesture {
              Task { await store.selectProject(project.id) }
            }
        }
        .listStyle(.inset)
      }
    }
    .safeAreaInset(edge: .top) {
      StateBanner(state: store.state)
        .padding(.horizontal)
        .padding(.top, 8)
    }
  }
}

private struct ProjectRow: View {
  let project: Project
  let selected: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: 11) {
      HStack {
        Label(project.name, systemImage: selected ? "folder.fill" : "folder")
          .font(.headline)
        Spacer()
        StatusPill(text: project.status, positive: project.status == "active")
      }
      Text(project.description.isEmpty ? "暂无项目说明" : project.description)
        .foregroundStyle(.secondary)
        .lineLimit(2)
      HStack(spacing: 8) {
        MetricBadge(title: "仓库", value: String(project.repositories ?? 0))
        MetricBadge(title: "会话", value: String(project.sessions ?? 0))
        MetricBadge(title: "实验", value: String(project.experiments ?? 0))
        MetricBadge(title: "文档", value: String(project.documents ?? 0))
        MetricBadge(title: "待复核", value: String(project.pendingReviews ?? 0))
        Spacer()
      }
      HStack {
        Text("访问边界：\(project.classification) · \(project.owner)")
        Spacer()
        Text("更新：\(project.updatedAt)")
      }
      .font(.caption)
      .foregroundStyle(.tertiary)
    }
    .padding(.vertical, 10)
  }
}
