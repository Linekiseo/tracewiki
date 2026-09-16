import SwiftUI

struct WikiView: View {
  @ObservedObject var store: AppStore
  @State private var searchText = ""

  var body: some View {
    HSplitView {
      VStack(spacing: 0) {
        HStack {
          TextField("筛选知识页面", text: $searchText)
            .textFieldStyle(.roundedBorder)
            .onSubmit { Task { await store.loadWiki(query: searchText) } }
          Button("搜索") { Task { await store.loadWiki(query: searchText) } }
        }
        .padding()
        Divider()
        if store.wikiPages.isEmpty, !store.state.isLoading {
          EmptyContentView(
            title: "暂无 Wiki 页面",
            message: "当前项目还没有可见的已发布知识页面。",
            systemImage: "books.vertical"
          )
        } else {
          List(store.wikiPages) { page in
            VStack(alignment: .leading, spacing: 3) {
              Text(page.title).lineLimit(1)
              Text(page.logicalPath)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            .padding(.vertical, 3)
            .contentShape(Rectangle())
            .onTapGesture { Task { await store.readWikiPage(page) } }
          }
          .listStyle(.inset)
        }
      }
      .frame(minWidth: 280, idealWidth: 340, maxWidth: 430)

      ScrollView {
        if let page = store.selectedWikiPage {
          WikiPageDetail(page: page)
            .padding(20)
        } else {
          EmptyContentView(
            title: "选择知识页面",
            message: "页面正文、事实、关系和原始证据定位会在这里展开。",
            systemImage: "doc.text.magnifyingglass"
          )
          .frame(maxWidth: .infinity, minHeight: 520)
        }
      }
      .frame(minWidth: 560)
    }
    .safeAreaInset(edge: .top) {
      StateBanner(state: store.state).padding(.horizontal)
    }
  }
}

private struct WikiPageDetail: View {
  let page: WikiPage

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      VStack(alignment: .leading, spacing: 7) {
        Text(page.title).font(.largeTitle.weight(.semibold))
        Text(page.logicalPath)
          .font(.callout.monospaced())
          .foregroundStyle(.secondary)
          .textSelection(.enabled)
        HStack {
          StatusPill(text: page.pageType)
          ForEach(page.evidenceRoles, id: \.self) { StatusPill(text: $0) }
        }
        Text(page.summary)
          .font(.title3)
          .foregroundStyle(.secondary)
          .textSelection(.enabled)
      }

      if !page.facts.isEmpty {
        GroupBox("已核验事实") {
          VStack(alignment: .leading, spacing: 0) {
            ForEach(page.facts) { fact in
              HStack(alignment: .top) {
                Text(fact.predicate).font(.headline).frame(width: 150, alignment: .leading)
                Text(fact.objectText ?? fact.objectPath ?? "—").textSelection(.enabled)
                Spacer()
                Text(fact.confidence.formatted(.percent.precision(.fractionLength(0))))
                  .foregroundStyle(.secondary)
              }
              .padding(.vertical, 7)
              Divider()
            }
          }
          .padding(6)
        }
      }

      if !page.links.isEmpty {
        GroupBox("关系导航") {
          VStack(alignment: .leading, spacing: 8) {
            ForEach(page.links) { link in
              Label("\(link.relation)  →  \(link.targetPath)", systemImage: "arrow.triangle.branch")
                .textSelection(.enabled)
            }
          }
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(6)
        }
      }

      GroupBox("原始证据定位") {
        VStack(alignment: .leading, spacing: 9) {
          ForEach(page.sourceRefs) { ref in
            VStack(alignment: .leading, spacing: 3) {
              HStack {
                StatusPill(text: ref.source)
                Text(ref.entityType).font(.caption)
              }
              Text(ref.locator)
                .font(.caption.monospaced())
                .textSelection(.enabled)
            }
          }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(6)
      }

      Text("内容指纹：\(page.contentSHA256)")
        .font(.caption.monospaced())
        .foregroundStyle(.tertiary)
        .textSelection(.enabled)
    }
  }
}
