import SwiftUI

struct SidebarView: View {
  @Binding var selection: AppSection

  var body: some View {
    List(selection: $selection) {
      Section("研究工作台") {
        ForEach(AppSection.allCases.filter { $0 != .settings }) { section in
          SidebarRow(section: section)
            .tag(section)
        }
      }

      Section {
        SidebarRow(section: .settings)
          .tag(AppSection.settings)
      }
    }
    .listStyle(.sidebar)
    .navigationTitle("Evidence RAG")
  }
}

private struct SidebarRow: View {
  let section: AppSection

  var body: some View {
    HStack(spacing: 10) {
      Image(systemName: section.systemImage)
        .foregroundStyle(.secondary)
        .frame(width: 17)
      VStack(alignment: .leading, spacing: 2) {
        Text(section.title)
          .lineLimit(1)
        Text(section.detail)
          .font(.caption)
          .foregroundStyle(.secondary)
          .lineLimit(1)
      }
    }
    .padding(.vertical, 2)
  }
}
