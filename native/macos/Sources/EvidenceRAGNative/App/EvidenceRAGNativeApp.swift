import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
  func applicationDidFinishLaunching(_ notification: Notification) {
    NSApp.setActivationPolicy(.regular)
    NSApp.activate(ignoringOtherApps: true)
  }
}

@main
struct EvidenceRAGNativeApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var store = AppStore.live()

  var body: some Scene {
    WindowGroup("Evidence Wiki + RAG", id: "workspace") {
      RootView(store: store)
        .frame(minWidth: 1_080, minHeight: 700)
    }
    .defaultSize(width: 1_360, height: 860)
    .commands {
      CommandGroup(after: .sidebar) {
        Button("刷新当前模块") {
          Task { await store.refreshCurrentSection() }
        }
        .keyboardShortcut("r", modifiers: .command)
      }
    }

    Settings {
      SettingsView(store: store)
    }
  }
}
