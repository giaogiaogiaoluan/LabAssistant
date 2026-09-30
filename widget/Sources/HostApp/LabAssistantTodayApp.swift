// Hidden WidgetKit host. WidgetKit opens the containing app first, then delivers
// the widget URL here; this host forwards it to the visible LabAssistant app.

import AppKit
import SwiftUI

@MainActor
private enum WidgetLinkForwarder {
    static var lastForwarded = Date.distantPast

    static func forward(_ url: URL) {
        guard url.scheme == "labassistant-widget",
              Date().timeIntervalSince(lastForwarded) > 1,
              let target = URL(string: "labassistant://today") else { return }
        lastForwarded = Date()
        NSWorkspace.shared.open(target)
    }
}

@MainActor
final class WidgetHostDelegate: NSObject, NSApplicationDelegate {
    func application(_ application: NSApplication, open urls: [URL]) {
        urls.forEach { WidgetLinkForwarder.forward($0) }
    }
}

@main
struct LabAssistantTodayApp: App {
    @NSApplicationDelegateAdaptor(WidgetHostDelegate.self) private var appDelegate

    var body: some Scene {
        Settings {
            EmptyView()
                .onOpenURL { WidgetLinkForwarder.forward($0) }
        }
    }
}
