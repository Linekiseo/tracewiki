import SwiftUI

struct SettingsView: View {
  @ObservedObject var store: AppStore
  @State private var apiKey = ""

  var body: some View {
    TabView {
      Form {
        Section("本地服务") {
          TextField("服务地址", text: $store.serverURLText)
            .textContentType(.URL)
          HStack {
            Spacer()
            Button("保存并重新连接") { Task { await store.saveServerURL() } }
          }
        }
        StateBanner(state: store.state)
      }
      .formStyle(.grouped)
      .tabItem { Label("连接", systemImage: "network") }

      Form {
        Section("OpenAI 兼容模型") {
          TextField("Base URL", text: $store.providerBaseURL)
            .textContentType(.URL)
          TextField("模型", text: $store.providerModel)
          SecureField("API 密钥", text: $apiKey)
          Text("密钥只保存到 macOS 钥匙串，不写入偏好、日志或项目文件。")
            .font(.caption)
            .foregroundStyle(.secondary)
          HStack {
            Button("载入钥匙串配置") { Task { await store.registerStoredProvider() } }
            Button("测试连接") { Task { await store.testProvider() } }
            Spacer()
            Button("保存并注册") {
              let submittedKey = apiKey
              apiKey = ""
              Task { await store.configureProvider(apiKey: submittedKey) }
            }
            .buttonStyle(.borderedProminent)
          }
        }

        if let status = store.providerStatus {
          Section("运行状态") {
            LabeledContent("项目", value: status.projectID)
            LabeledContent("模型", value: status.model ?? "未配置")
            LabeledContent("密钥", value: status.apiKeyPresent ? "已安全注入" : "缺失")
            LabeledContent("存储", value: status.secretStorage)
            LabeledContent(
              "健康检查", value: status.health ?? (status.tested == true ? "ready" : "未测试"))
          }
        }
        StateBanner(state: store.providerState)
      }
      .formStyle(.grouped)
      .tabItem { Label("模型", systemImage: "brain") }
    }
    .frame(width: 560, height: 430)
    .scenePadding()
  }
}

struct SettingsModuleView: View {
  @ObservedObject var store: AppStore

  var body: some View {
    VStack(spacing: 18) {
      Image(systemName: "gearshape.2")
        .font(.system(size: 46))
        .foregroundStyle(.secondary)
      Text("原生设置窗口")
        .font(.title2.weight(.semibold))
      Text("服务连接、模型与钥匙串配置位于独立的 macOS 设置窗口。")
        .foregroundStyle(.secondary)
      SettingsLink {
        Label("打开设置", systemImage: "gearshape")
      }
      .buttonStyle(.borderedProminent)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }
}
