import SwiftUI

struct SessionsView: View {
  @ObservedObject var store: AppStore
  @State private var searchText = ""

  private var filteredSessions: [CodexSession] {
    let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    guard !query.isEmpty else { return store.sessions }
    return store.sessions.filter {
      $0.title.lowercased().contains(query) || $0.threadID.lowercased().contains(query)
    }
  }

  var body: some View {
    Group {
      if filteredSessions.isEmpty, !store.state.isLoading {
        EmptyContentView(
          title: "暂无研发会话",
          message: "同步到当前项目的 Codex 会话会在这里显示。",
          systemImage: "bubble.left.and.text.bubble.right"
        )
      } else {
        Table(filteredSessions) {
          TableColumn("会话") { session in
            VStack(alignment: .leading, spacing: 3) {
              Text(session.title).lineLimit(1)
              Text(session.threadID)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
            }
          }
          TableColumn("状态") { session in
            StatusPill(text: session.status, positive: session.status == "completed")
          }
          .width(100)
          TableColumn("轮次") { session in Text(String(session.turnCount)).monospacedDigit() }.width(
            55)
          TableColumn("变更") { session in Text(String(session.fileChangeCount)).monospacedDigit() }
            .width(55)
          TableColumn("命令") { session in Text(String(session.commandCount)).monospacedDigit() }
            .width(55)
          TableColumn("最近更新") { session in Text(session.updatedAt).font(.caption) }.width(
            min: 150, ideal: 190)
        }
      }
    }
    .searchable(text: $searchText, prompt: "搜索会话标题或 ID")
    .overlay(alignment: .top) { StateBanner(state: store.state).padding() }
  }
}
