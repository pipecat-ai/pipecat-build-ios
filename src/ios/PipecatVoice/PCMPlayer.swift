import AVFoundation

@MainActor
final class PCMPlayer {
    private let audio: VoiceAudioEngine
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
    private var pending = 0
    private var generation = 0
    private var drained: CheckedContinuation<Void, Error>?
    var onLevel: ((Double) -> Void)?
    var onSpeakingChanged: ((Bool) -> Void)?
    private var speaking = false

    init(audio: VoiceAudioEngine) {
        self.audio = audio
        audio.attach(player, format: format)
    }

    func enqueue(_ samples: [Float]) throws {
        try Task.checkCancellation()
        guard !samples.isEmpty else { return }
        try audio.start()
        // Meter 20 ms of *played* audio at a time. A synthesis chunk can contain
        // several seconds; its average hides syllables and runs ahead of playback.
        // Contiguous buffers preserve the PCM stream and include output latency
        // in the level callback without tapping the microphone or changing AEC.
        let windowFrames = Int(format.sampleRate * 0.02)
        // Offline audio checks have no output device to report dataPlayedBack.
        let callbackType: AVAudioPlayerNodeCompletionCallbackType = player.engine?.isInManualRenderingMode == true
            ? .dataRendered : .dataPlayedBack
        let epoch = generation
        try samples.withUnsafeBufferPointer { input in
            for offset in stride(from: 0, to: samples.count, by: windowFrames) {
                let count = min(windowFrames, samples.count - offset)
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
                      let output = buffer.floatChannelData?[0] else {
                    throw VoiceError(message: "Could not allocate the playback buffer.")
                }
                buffer.frameLength = buffer.frameCapacity
                output.update(from: input.baseAddress! + offset, count: count)
                let level = Self.level(in: UnsafeBufferPointer(start: output, count: count))
                pending += 1
                player.scheduleBuffer(buffer, completionCallbackType: callbackType) { [weak self] _ in
                    Task { @MainActor in
                        guard let self, epoch == self.generation else { return }
                        self.pending -= 1
                        self.onLevel?(level)
                        if self.pending == 0 {
                            self.drained?.resume()
                            self.drained = nil
                            self.onLevel?(0)
                            self.setSpeaking(false)
                        }
                    }
                }
            }
        }
        if !player.isPlaying { player.play() }
        setSpeaking(true)
    }

    private static func level(in samples: UnsafeBufferPointer<Float>) -> Double {
        var sum = 0.0
        var peak = 0.0
        for sample in samples {
            let amplitude = sample.isFinite ? abs(Double(sample)) : 0
            sum += amplitude * amplitude
            peak = max(peak, amplitude)
        }
        let amplitude = sqrt(sum / Double(samples.count)) * 0.85 + peak * 0.15
        // A fixed perceptual range preserves gain differences between phrases.
        // Do not normalize each chunk: quiet speech should visibly stay quieter.
        let decibels = 20 * log10(max(amplitude, 0.000_001))
        return pow(min(1, max(0, (decibels + 54) / 48)), 1.6)
    }

    func finish() async throws {
        try Task.checkCancellation()
        if pending == 0 { return }
        let expectedGeneration = generation
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { drained = $0 }
        } onCancel: {
            Task { @MainActor [weak self] in
                guard let self, self.generation == expectedGeneration else { return }
                self.stop()
            }
        }
    }

    func stop() {
        generation += 1
        player.stop()
        pending = 0
        drained?.resume(throwing: CancellationError())
        drained = nil
        onLevel?(0)
        setSpeaking(false)
    }

    private func setSpeaking(_ speaking: Bool) {
        guard self.speaking != speaking else { return }
        self.speaking = speaking
        onSpeakingChanged?(speaking)
    }
}
