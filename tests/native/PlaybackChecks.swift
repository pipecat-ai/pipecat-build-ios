import AVFoundation
import Foundation

struct VoiceError: Error { let message: String }

// Exercise the real PCMPlayer against an offline engine, without microphone
// permission, speakers, or the app's voice-processing audio session.
@MainActor final class VoiceAudioEngine {
    let engine = AVAudioEngine()
    let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!

    func attach(_ player: AVAudioPlayerNode, format: AVAudioFormat) {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func start() throws {
        guard !engine.isRunning else { return }
        if !engine.isInManualRenderingMode {
            try engine.enableManualRenderingMode(.offline, format: format, maximumFrameCount: 480)
        }
        try engine.start()
    }

    func render(frames: Int) async throws -> [Float] {
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
        var output: [Float] = []
        while output.count < frames {
            let count = AVAudioFrameCount(min(480, frames - output.count))
            let status = try engine.renderOffline(count, to: buffer)
            precondition(status == .success, "Offline audio render failed")
            output.append(contentsOf: UnsafeBufferPointer(start: buffer.floatChannelData![0], count: Int(buffer.frameLength)))
            // Completion handlers dispatch onto the main actor, like in the app.
            try await Task.sleep(for: .milliseconds(1))
        }
        return output
    }
}

@main struct PlaybackChecks {
    @MainActor static func main() async throws {
        let audio = VoiceAudioEngine()
        let player = PCMPlayer(audio: audio)
        var levels: [Double] = []
        var speaking: [Bool] = []
        player.onLevel = { levels.append($0) }
        player.onSpeakingChanged = { speaking.append($0) }

        func tone(_ gain: Float, frames: Int = 4_800) -> [Float] {
            (0..<frames).map { gain * sin(Float($0) * 2 * .pi * 200 / 24_000) }
        }

        // One large synthesis chunk contains quiet speech, silence, loud speech,
        // and a tail shorter than a meter window. Queueing must not light the orb.
        let input = tone(0.015) + tone(0) + tone(0.32) + tone(0.015, frames: 1_083)
        try player.enqueue(input)
        precondition(levels.isEmpty, "Queued audio must not appear as played audio")
        let output = try await audio.render(frames: input.count + 1_920)
        try await player.finish()
        precondition(zip(input, output).allSatisfy { abs($0 - $1) < 0.00001 }, "Meter windows changed the PCM stream")
        precondition(levels.count == 34, "Expected one level per 20 ms window, including the short tail, plus reset")
        let quiet = Array(levels[0..<10])
        let silence = Array(levels[10..<20])
        let loud = Array(levels[20..<30])
        precondition(quiet.allSatisfy { $0 > 0.1 && $0 < 0.35 })
        precondition(silence.allSatisfy { $0 == 0 }, "Silence must stay silent")
        precondition(loud.allSatisfy { $0 > 0.7 && $0 <= 1 }, "Gain changes were flattened")
        precondition(levels.last == 0 && speaking == [true, false])

        // Old completion callbacks must not relight or drain a replacement reply.
        try player.enqueue(tone(0.4, frames: 24_000))
        _ = try await audio.render(frames: 960)
        player.stop()
        levels.removeAll()
        speaking.removeAll()
        try player.enqueue(tone(0.015))
        _ = try await audio.render(frames: 6_720)
        try await player.finish()
        precondition(levels.count == 11 && levels.dropLast().allSatisfy { $0 > 0 && $0 < 0.35 })
        precondition(levels.last == 0 && speaking == [true, false])

        try player.enqueue(tone(0.3, frames: 24_000))
        let finish = Task { try await player.finish() }
        await Task.yield()
        finish.cancel()
        do { try await finish.value; preconditionFailure("Cancelled finish succeeded") }
        catch is CancellationError { }
        precondition(levels.last == 0 && speaking.last == false)
        audio.engine.stop()
        print("Native playback checks passed: PCM continuity, playback-only levels, gain, silence, interruption, restart, cancellation")
    }
}
