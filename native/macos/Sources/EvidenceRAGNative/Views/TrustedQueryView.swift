import SwiftUI

struct TrustedQueryView: View {
  @ObservedObject var store: AppStore
  @State private var question = ""
  @State private var selectedSources: Set<String> = Set(Self.sources)
  @State private var allowGeneration = true

  static let sources = ["code", "codex", "experiment", "notebook", "document", "workspace"]

  var body: some View {
    HSplitView {
      ScrollView {
        VStack(alignment: .leading, spacing: 18) {
          queryComposer
          StateBanner(state: store.queryState)
          if let response = store.queryResponse {
            QueryResultView(response: response)
          } else if !store.queryState.isLoading {
            EmptyContentView(
              title: "从证据问题开始",
              message: "系统会先理解问题与证据义务，再执行跨来源检索并核验回答。",
              systemImage: "sparkle.magnifyingglass"
            )
            .frame(minHeight: 300)
          }
        }
        .padding(20)
      }
      .frame(minWidth: 620)

      QueryHistoryView(items: store.queryHistory)
        .frame(minWidth: 260, idealWidth: 310, maxWidth: 380)
    }
  }

  private var queryComposer: some View {
    VStack(alignment: .leading, spacing: 13) {
      HStack(alignment: .firstTextBaseline) {
        VStack(alignment: .leading, spacing: 4) {
          Text("可信查询")
            .font(.title2.weight(.semibold))
          Text("先规划、再检索、沿关系导航，最后核验原始证据。")
            .foregroundStyle(.secondary)
        }
        Spacer()
        if let provider = store.providerStatus {
          StatusPill(
            text: provider.configured ? (provider.model ?? "模型已配置") : "本地算法兜底",
            positive: provider.configured
          )
        }
      }

      TextEditor(text: $question)
        .font(.body)
        .frame(minHeight: 88, maxHeight: 150)
        .padding(6)
        .background(.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(.separator))

      HStack(spacing: 12) {
        ForEach(Self.sources, id: \.self) { source in
          Toggle(
            source,
            isOn: Binding(
              get: { selectedSources.contains(source) },
              set: { enabled in
                if enabled {
                  selectedSources.insert(source)
                } else {
                  selectedSources.remove(source)
                }
              }
            )
          )
          .toggleStyle(.checkbox)
        }
      }

      if selectedSources.contains("code") {
        HStack {
          Text("代码仓库")
            .foregroundStyle(.secondary)
          Picker("代码仓库", selection: $store.selectedRepositoryID) {
            Text("请选择仓库").tag(String?.none)
            ForEach(store.repositories) { repository in
              Text("\(repository.name) · \(repository.defaultBranch ?? "未命名分支")")
                .tag(Optional(repository.id))
            }
          }
          .labelsHidden()
          Spacer()
          if store.repositories.count > 1 {
            Text("每次查询只允许一个明确仓库，避免跨仓误用证据。")
              .font(.caption)
              .foregroundStyle(.secondary)
          }
        }
      }

      HStack {
        Toggle("生成有引用的回答", isOn: $allowGeneration)
        Spacer()
        Button("执行查询") {
          Task {
            await store.runQuery(
              question: question,
              sources: selectedSources,
              allowGeneration: allowGeneration
            )
          }
        }
        .keyboardShortcut(.return, modifiers: [.command])
        .buttonStyle(.borderedProminent)
        .disabled(
          question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || store.queryState.isLoading)
      }
    }
    .padding(16)
    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
  }
}

private struct QueryResultView: View {
  let response: QueryResponse

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      GroupBox("核验结论") {
        VStack(alignment: .leading, spacing: 10) {
          HStack {
            StatusPill(text: response.answer.answerMode ?? response.answerMode ?? "unknown")
            StatusPill(
              text: response.answer.decisionStatus ?? response.answer.status ?? "unknown",
              positive: response.answer.refusal != true
            )
            Spacer()
            Text(response.queryID.compactDigest)
              .font(.caption.monospaced())
              .foregroundStyle(.tertiary)
          }
          Text(response.answer.text ?? response.answer.refusalReason ?? "服务未返回可展示的回答。")
            .textSelection(.enabled)
          if let citations = response.answer.citations, !citations.isEmpty {
            Text("引用：\(citations.joined(separator: " · "))")
              .font(.caption)
              .foregroundStyle(.secondary)
              .textSelection(.enabled)
          }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(6)
      }

      if let understanding = response.queryUnderstanding {
        GroupBox("查询理解与检索规划") {
          VStack(alignment: .leading, spacing: 8) {
            LabeledContent("策略", value: understanding.strategy)
            LabeledContent("意图", value: understanding.intent)
            LabeledContent(
              "置信度",
              value: understanding.intentConfidence.formatted(
                .percent.precision(.fractionLength(1))))
            LabeledContent("算法", value: understanding.algorithm.encoder)
            LabeledContent("分类器", value: understanding.algorithm.classifier)
            LabeledContent("分解", value: understanding.algorithm.decomposition)
            if !understanding.subqueries.isEmpty {
              Divider()
              ForEach(Array(understanding.subqueries.enumerated()), id: \.offset) { index, item in
                Text("Q\(index + 1)  \(item)")
                  .font(.callout)
                  .textSelection(.enabled)
              }
            }
          }
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(6)
        }
      }

      if let evidence = response.evidencePack?.allEvidence, !evidence.isEmpty {
        GroupBox("证据（\(evidence.count)）") {
          LazyVStack(alignment: .leading, spacing: 0) {
            ForEach(evidence) { item in
              VStack(alignment: .leading, spacing: 5) {
                HStack {
                  Text(item.title).font(.headline)
                  Spacer()
                  StatusPill(text: item.source)
                }
                Text(item.snippet)
                  .lineLimit(4)
                  .foregroundStyle(.secondary)
                  .textSelection(.enabled)
                Text(item.locator)
                  .font(.caption.monospaced())
                  .foregroundStyle(.tertiary)
                  .textSelection(.enabled)
              }
              .padding(.vertical, 10)
              Divider()
            }
          }
          .padding(6)
        }
      }
    }
  }
}

private struct QueryHistoryView: View {
  let items: [QueryHistoryItem]

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Text("查询历史")
        .font(.headline)
        .padding()
      Divider()
      if items.isEmpty {
        EmptyContentView(
          title: "暂无历史",
          message: "成功提交的查询会出现在这里。",
          systemImage: "clock"
        )
      } else {
        List(items) { item in
          VStack(alignment: .leading, spacing: 5) {
            Text(item.intent ?? "未分类查询")
              .font(.headline)
            Text(item.questionDigest.compactDigest)
              .font(.caption.monospaced())
            HStack {
              Text(item.answerMode ?? "—")
              Spacer()
              Text("证据 \(item.evidence?.count ?? 0)")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
          }
          .padding(.vertical, 4)
        }
        .listStyle(.inset)
      }
    }
  }
}
