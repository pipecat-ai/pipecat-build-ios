import SwiftUI

@main
struct PipecatVoiceApp: App {
    @State private var model = ConversationModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ConversationView(model: model)
                .preferredColorScheme(.light)
                .onChange(of: scenePhase) { _, phase in
                    if phase == .background { model.stop() }
                    if phase == .active { model.availability = AppleLanguageModel.unavailableReason }
                }
        }
    }
}
