import SwiftUI

struct StateBanner: View {
  let state: AppStore.LoadState

  var body: some View {
    switch state {
    case .idle, .loaded:
      EmptyView()
    case .loading:
      HStack(spacing: 8) {
        ProgressView().controlSize(.small)
        Text("正在读取可信数据…")
      }
      .font(.callout)
      .foregroundStyle(.secondary)
    case .failed(let message):
      Label(message, systemImage: "exclamationmark.triangle.fill")
        .font(.callout)
        .foregroundStyle(.red)
        .textSelection(.enabled)
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
  }
}

struct EmptyContentView: View {
  let title: String
  let message: String
  let systemImage: String

  var body: some View {
    ContentUnavailableView(title, systemImage: systemImage, description: Text(message))
  }
}

struct MetricBadge: View {
  let title: String
  let value: String

  var body: some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(value)
        .font(.title3.weight(.semibold))
        .monospacedDigit()
      Text(title)
        .font(.caption)
        .foregroundStyle(.secondary)
    }
    .padding(10)
    .frame(minWidth: 95, alignment: .leading)
    .background(.quaternary, in: RoundedRectangle(cornerRadius: 9))
  }
}

struct StatusPill: View {
  let text: String
  var positive = false

  var body: some View {
    Text(text)
      .font(.caption.weight(.medium))
      .padding(.horizontal, 8)
      .padding(.vertical, 4)
      .foregroundStyle(positive ? .green : .secondary)
      .background((positive ? Color.green : Color.secondary).opacity(0.1), in: Capsule())
  }
}

extension String {
  var compactDigest: String {
    count > 20 ? "\(prefix(10))…\(suffix(6))" : self
  }
}
