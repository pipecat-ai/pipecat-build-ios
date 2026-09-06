import Foundation
import FoundationModels

struct HistoryMessage: Decodable {
    let role: String
    let content: String
}

@MainActor
enum AppleLanguageModel {
    static var unavailableReason: String? {
        switch SystemLanguageModel.default.availability {
        case .available: return nil
        case .unavailable(.deviceNotEligible):
            return "This device does not support Apple Intelligence. Use a supported iPhone for voice conversations."
        case .unavailable(.appleIntelligenceNotEnabled):
            return "Enable Apple Intelligence in Settings to use the language model."
        case .unavailable(.modelNotReady):
            return "Apple Intelligence is downloading its model. Try again once it is ready."
        @unknown default: return "Apple’s language model is currently unavailable."
        }
    }

    static func generate(prompt: String, instructions: String, history: [HistoryMessage],
                         onDelta: (String) -> Void) async throws {
        if let reason = unavailableReason { throw VoiceError(message: reason) }
        var entries: [Transcript.Entry] = [
            .instructions(.init(segments: [.text(.init(content: instructions))], toolDefinitions: []))
        ]
        for message in history {
            let segments: [Transcript.Segment] = [.text(.init(content: message.content))]
            entries.append(message.role == "user"
                ? .prompt(.init(segments: segments))
                : .response(.init(assetIDs: [], segments: segments)))
        }
        let session = LanguageModelSession(transcript: Transcript(entries: entries))
        let stream = session.streamResponse(to: prompt, options: GenerationOptions(maximumResponseTokens: 160))
        var previous = ""
        for try await snapshot in stream {
            try Task.checkCancellation()
            // Foundation Models emits cumulative snapshots, not token deltas.
            guard snapshot.content.hasPrefix(previous) else {
                throw VoiceError(message: "The language model revised an already spoken response. Please try again.")
            }
            let delta = String(snapshot.content.dropFirst(previous.count))
            previous = snapshot.content
            if !delta.isEmpty { onDelta(delta) }
        }
    }
}
