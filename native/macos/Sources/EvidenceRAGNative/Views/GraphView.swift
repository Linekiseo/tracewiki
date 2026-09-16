import SwiftUI

struct GraphView: View {
  @ObservedObject var store: AppStore
  @State private var selectedNodeID: String?

  var body: some View {
    VStack(spacing: 0) {
      if let snapshot = store.graphSnapshot {
        HStack(spacing: 10) {
          MetricBadge(title: "可见节点", value: String(snapshot.metadata.visibleNodes))
          MetricBadge(title: "可见关系", value: String(snapshot.metadata.visibleEdges))
          MetricBadge(title: "全部节点", value: String(snapshot.metadata.totalNodes))
          MetricBadge(title: "全部关系", value: String(snapshot.metadata.totalEdges))
          Spacer()
        }
        .padding()
        Divider()
        HSplitView {
          NetworkCanvas(
            nodes: snapshot.nodes,
            edges: snapshot.edges,
            selectedNodeID: selectedNodeID
          )
          .frame(minWidth: 650, minHeight: 560)

          VStack(alignment: .leading, spacing: 0) {
            Text("节点索引")
              .font(.headline)
              .padding()
            Divider()
            List(snapshot.nodes, selection: $selectedNodeID) { node in
              VStack(alignment: .leading, spacing: 3) {
                Text(node.label).lineLimit(1)
                Text("\(node.domain) · \(node.type)")
                  .font(.caption)
                  .foregroundStyle(.secondary)
              }
              .tag(node.id)
            }
            .listStyle(.inset)
          }
          .frame(minWidth: 260, idealWidth: 320, maxWidth: 400)
        }
      } else if !store.state.isLoading {
        EmptyContentView(
          title: "暂无关系图谱",
          message: "选择项目后刷新，即可读取跨来源节点和关系。",
          systemImage: "point.3.connected.trianglepath.dotted"
        )
      }
    }
    .overlay(alignment: .top) { StateBanner(state: store.state).padding() }
  }
}

private struct NetworkCanvas: View {
  let nodes: [GraphNode]
  let edges: [GraphEdge]
  let selectedNodeID: String?

  var body: some View {
    GeometryReader { geometry in
      let positions = nodePositions(in: geometry.size)
      Canvas { context, _ in
        for edge in edges {
          guard let start = positions[edge.source], let end = positions[edge.target] else {
            continue
          }
          var path = Path()
          path.move(to: start)
          path.addLine(to: end)
          context.stroke(
            path,
            with: .color(.secondary.opacity(0.23)),
            lineWidth: edge.id == selectedNodeID ? 2 : 1
          )
        }

        for node in nodes {
          guard let point = positions[node.id] else { continue }
          let selected = node.id == selectedNodeID
          let rect = CGRect(x: point.x - 42, y: point.y - 18, width: 84, height: 36)
          context.fill(
            Path(roundedRect: rect, cornerRadius: 9),
            with: .color(selected ? .accentColor : domainColor(node.domain).opacity(0.82))
          )
          context.draw(
            Text(node.label)
              .font(.caption2.weight(.medium))
              .foregroundStyle(.white),
            in: rect.insetBy(dx: 5, dy: 5)
          )
        }
      }
      .background(.quaternary.opacity(0.25))
    }
  }

  private func nodePositions(in size: CGSize) -> [String: CGPoint] {
    guard !nodes.isEmpty else { return [:] }
    let columns = max(3, Int(sqrt(Double(nodes.count)).rounded(.up)))
    let rows = max(1, Int(ceil(Double(nodes.count) / Double(columns))))
    let cellWidth = max(110, size.width / CGFloat(columns))
    let cellHeight = max(78, size.height / CGFloat(rows))
    return Dictionary(
      uniqueKeysWithValues: nodes.enumerated().map { index, node in
        let column = index % columns
        let row = index / columns
        let stagger = row.isMultiple(of: 2) ? CGFloat.zero : cellWidth * 0.18
        return (
          node.id,
          CGPoint(
            x: min(size.width - 55, cellWidth * (CGFloat(column) + 0.5) + stagger),
            y: min(size.height - 32, cellHeight * (CGFloat(row) + 0.5))
          )
        )
      })
  }

  private func domainColor(_ domain: String) -> Color {
    switch domain {
    case "code": .blue
    case "codex": .purple
    case "experiment": .orange
    case "document": .teal
    case "notebook": .indigo
    default: .gray
    }
  }
}
