import Foundation

enum AppSection: String, CaseIterable, Identifiable {
  case projects
  case trustedQuery
  case wiki
  case graph
  case sessions
  case agents
  case settings

  var id: String { rawValue }

  var title: String {
    switch self {
    case .projects: "项目"
    case .trustedQuery: "可信查询"
    case .wiki: "Wiki"
    case .graph: "关系图谱"
    case .sessions: "研发会话"
    case .agents: "智能体"
    case .settings: "设置"
    }
  }

  var systemImage: String {
    switch self {
    case .projects: "square.stack.3d.up"
    case .trustedQuery: "sparkle.magnifyingglass"
    case .wiki: "books.vertical"
    case .graph: "point.3.connected.trianglepath.dotted"
    case .sessions: "bubble.left.and.text.bubble.right"
    case .agents: "cpu"
    case .settings: "gearshape"
    }
  }

  var detail: String {
    switch self {
    case .projects: "项目与数据边界"
    case .trustedQuery: "规划、检索与证据回答"
    case .wiki: "可追溯知识页面"
    case .graph: "跨来源关系网络"
    case .sessions: "会话与代码变动"
    case .agents: "Codex 接入与能力"
    case .settings: "服务与模型配置"
    }
  }
}
