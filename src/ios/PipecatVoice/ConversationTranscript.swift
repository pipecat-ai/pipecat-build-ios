import Foundation

enum TranscriptInterruption: String {
    case user = "You interrupted"
    case ended = "Conversation ended"
}

struct TranscriptMessage: Identifiable, Equatable {
    let id: String
    let role: String
    var text: String
    var isFinal: Bool
    var interruption: TranscriptInterruption?

    /// ASR can finalize several fragments within one turn. Present its remaining
    /// hypothesis in that same bubble without committing it to the transcript.
    static func displaying(_ messages: [Self], partial: String, turn: String?) -> [Self] {
        guard !partial.isEmpty, let turn else { return messages }
        var result = messages
        let id = turn + "-user"
        if let index = result.firstIndex(where: { $0.id == id }) {
            let separator = result[index].text.isEmpty || result[index].text.hasSuffix(" ") ? "" : " "
            result[index].text += separator + partial
            result[index].isFinal = false
        } else {
            result.append(Self(id: id, role: "user", text: partial, isFinal: false))
        }
        return result
    }

    static func interrupt(_ messages: inout [Self], turn: String, reason: TranscriptInterruption) {
        let id = turn + "-assistant"
        if let index = messages.firstIndex(where: { $0.id == id }) {
            // A later stop must not relabel an earlier spoken interruption.
            guard messages[index].interruption == nil else { return }
            messages[index].interruption = reason
            messages[index].isFinal = true
        } else {
            // Interrupting generation before its first token still has a place
            // in the timeline. The view renders only the event, with no bubble.
            messages.append(Self(id: id, role: "assistant", text: "", isFinal: true,
                                 interruption: reason))
        }
    }
}
