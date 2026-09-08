import Foundation

private struct CheckFailure: Error { let message: String }

private func check(_ value: @autoclosure () -> Bool, _ message: String) throws {
    if !value() { throw CheckFailure(message: message) }
}

@main
struct TranscriptChecks {
    static func main() throws {
        let committed = [TranscriptMessage(id: "turn-user", role: "user", text: "Let’s plan", isFinal: true)]
        let live = TranscriptMessage.displaying(committed, partial: "a weekend away", turn: "turn")
        try check(live.count == 1 && live[0].id == committed[0].id, "Partial ASR created a duplicate bubble")
        try check(live[0].text == "Let’s plan a weekend away" && !live[0].isFinal, "Partial ASR was not marked live")
        try check(committed[0].text == "Let’s plan" && committed[0].isFinal, "Display changed committed text")

        let revised = TranscriptMessage.displaying(committed, partial: "a day out", turn: "turn")
        try check(revised[0].text == "Let’s plan a day out", "Revised ASR retained an obsolete hypothesis")
        let final = [TranscriptMessage(id: "turn-user", role: "user", text: "Let’s plan a day out.", isFinal: true)]
        try check(TranscriptMessage.displaying(final, partial: "", turn: "turn") == final, "Final text retained its live state")

        let next = TranscriptMessage.displaying(final, partial: "Somewhere nearby", turn: "next")
        try check(next.count == 2 && next[1].id == "next-user" && !next[1].isFinal, "New turn reused the old bubble")
        try check(TranscriptMessage.displaying(final, partial: "Old audio", turn: nil) == final, "Unscoped partial entered the transcript")
        let spaced = [TranscriptMessage(id: "turn-user", role: "user", text: "Hello ", isFinal: true)]
        try check(TranscriptMessage.displaying(spaced, partial: "there", turn: "turn")[0].text == "Hello there", "Partial duplicated whitespace")

        var interrupted = final + [TranscriptMessage(id: "turn-assistant", role: "assistant", text: "You could take", isFinal: false)]
        TranscriptMessage.interrupt(&interrupted, turn: "turn", reason: .user)
        try check(interrupted.count == 2 && interrupted[1].text == "You could take", "Interruption lost generated text")
        try check(interrupted[1].isFinal && interrupted[1].interruption == .user, "Interrupted reply stayed live")
        TranscriptMessage.interrupt(&interrupted, turn: "turn", reason: .ended)
        try check(interrupted.count == 2 && interrupted[1].interruption == .user, "Stop relabeled an existing interruption")

        var beforeFirstToken = final
        TranscriptMessage.interrupt(&beforeFirstToken, turn: "turn", reason: .user)
        try check(beforeFirstToken.count == 2 && beforeFirstToken[1].text.isEmpty, "Early interruption needs an event without an empty bubble")
        try check(beforeFirstToken[1].interruption == .user && beforeFirstToken[1].isFinal, "Early interruption was lost")
        var ended = final
        TranscriptMessage.interrupt(&ended, turn: "turn", reason: .ended)
        try check(ended[1].interruption == .ended, "Ending a conversation was labeled as a user interruption")
        print("Native transcript checks passed")
    }
}
