import SwiftUI

struct AgentsView: View {
  @ObservedObject var store: AppStore

  var body: some View {
    Group {
      if store.agents.isEmpty, !store.state.isLoading {
        EmptyContentView(
          title: "暂无已接入智能体",
          message: "配置 Codex Bridge 后，可信身份、能力和同步状态会在这里显示。",
          systemImage: "cpu"
        )
      } else {
        List(store.agents) { agent in
          VStack(alignment: .leading, spacing: 12) {
            HStack {
              Label(agent.name, systemImage: "cpu")
                .font(.headline)
              Spacer()
              StatusPill(text: agent.availability, positive: agent.availability == "ready")
            }
            Text(agent.reason)
              .foregroundStyle(.secondary)
            HStack(spacing: 8) {
              MetricBadge(title: "会话", value: String(agent.sessionCount))
              MetricBadge(title: "执行中", value: String(agent.activeExecutionCount))
              MetricBadge(title: "能力", value: String(agent.capabilities.count))
              Spacer()
            }
            if !agent.capabilities.isEmpty {
              Text(agent.capabilities.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            }
            HStack {
              Text(agent.connector)
              if let version = agent.version { Text("v\(version)") }
              Spacer()
              if let lastSeenAt = agent.lastSeenAt { Text("最近连接：\(lastSeenAt)") }
            }
            .font(.caption)
            .foregroundStyle(.tertiary)
          }
          .padding(.vertical, 10)
        }
        .listStyle(.inset)
      }
    }
    .overlay(alignment: .top) { StateBanner(state: store.state).padding() }
  }
}
