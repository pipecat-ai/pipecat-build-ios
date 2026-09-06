import AVFoundation

@MainActor
final class PCMPlayer {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
    private var pending = 0
    private var generation = 0
    private var drained: CheckedContinuation<Void, Error>?
    var onLevel: ((Double) -> Void)?

    init() {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func enqueue(_ samples: [Float]) throws {
        guard !samples.isEmpty else { return }
        if !engine.isRunning { try engine.start() }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
              let output = buffer.floatChannelData?[0] else {
            throw VoiceError(message: "Could not allocate the playback buffer.")
        }
        buffer.frameLength = buffer.frameCapacity
        samples.withUnsafeBufferPointer { output.update(from: $0.baseAddress!, count: samples.count) }
        pending += 1
        let epoch = generation
        let rms = sqrt(samples.reduce(0.0) { $0 + Double($1 * $1) } / Double(samples.count))
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor in
                guard let self, epoch == self.generation else { return }
                self.pending -= 1
                self.onLevel?(min(1, rms * 6))
                if self.pending == 0 {
                    self.drained?.resume()
                    self.drained = nil
                    self.onLevel?(0)
                }
            }
        }
        if !player.isPlaying { player.play() }
    }

    func finish() async throws {
        try Task.checkCancellation()
        if pending == 0 { return }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { drained = $0 }
        } onCancel: {
            Task { @MainActor [weak self] in self?.stop() }
        }
    }

    func stop() {
        generation += 1
        player.stop()
        engine.stop()
        pending = 0
        drained?.resume(throwing: CancellationError())
        drained = nil
        onLevel?(0)
    }
}
