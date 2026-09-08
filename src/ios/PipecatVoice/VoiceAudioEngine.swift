import AVFoundation

/// Capture and agent playback share VoiceProcessingIO's echo-cancellation reference.
@MainActor
final class VoiceAudioEngine {
    private let engine = AVAudioEngine()
    private var hasCaptureTap = false

    func attach(_ player: AVAudioPlayerNode, format: AVAudioFormat) {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func microphoneFormat() throws -> AVAudioFormat {
        if !engine.inputNode.isVoiceProcessingEnabled {
            guard !engine.isRunning else {
                throw VoiceError(message: "Stop audio before enabling microphone echo cancellation.")
            }
            try engine.inputNode.setVoiceProcessingEnabled(true)
        }
        let format = engine.inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw VoiceError(message: "The microphone audio format is unavailable.")
        }
        return format
    }

    func startCapture(_ handler: @escaping AVAudioNodeTapBlock) throws {
        stopCapture()
        let format = try microphoneFormat()
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: format, block: handler)
        hasCaptureTap = true
        do { try start() }
        catch { stopCapture(); throw error }
    }

    func start() throws {
        _ = try microphoneFormat()
        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }
    }

    /// Removing capture leaves queued agent audio and its engine running.
    func stopCapture() {
        if hasCaptureTap {
            engine.inputNode.removeTap(onBus: 0)
            hasCaptureTap = false
        }
    }

    func stop() {
        stopCapture()
        engine.stop()
    }
}
