#if DEBUG
import AVFoundation
import Foundation

private final class TTSNetworkBlocker: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    private static var count = 0
    static var attempts: Int { lock.lock(); defer { lock.unlock() }; return count }
    override class func canInit(with request: URLRequest) -> Bool {
        ["http", "https"].contains(request.url?.scheme ?? "")
    }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock(); Self.count += 1; Self.lock.unlock()
        client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
    }
    override func stopLoading() {}
}

private final class CaptureProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    var buffers: Int { lock.lock(); defer { lock.unlock() }; return count }
    func received() { lock.lock(); count += 1; lock.unlock() }
}

@MainActor
enum TTSDiagnostics {
    private static func report(_ message: String) {
        print(message)
        fflush(nil)
    }

    static func run() async {
        URLProtocol.registerClass(TTSNetworkBlocker.self)
        defer { URLProtocol.unregisterClass(TTSNetworkBlocker.self) }
        let audio = VoiceAudioEngine()
        let player = PCMPlayer(audio: audio)
        let synth: PocketTTSSynthesizer
        do { synth = try PocketTTSSynthesizer(root: PocketTTSAssets.bundledRoot()) }
        catch { report("POCKET_TTS_FAILED: \(error.localizedDescription)"); return }
        do {
            let start = Date()
            try await synth.prepare(voice: "alba")
            report("POCKET_TTS_MODEL_LOADED: \(Date().timeIntervalSince(start))s")
            try audio.configureSession()
            let capture = CaptureProbe()
            try audio.startCapture { _, _ in capture.received() }
            let url = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                                   appropriateFor: nil, create: true).appendingPathComponent("pocket-tts-check.wav")
            let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
            let file = try AVAudioFile(forWriting: url, settings: format.settings)
            let request = try await synth.synthesize("Hello there. Pocket TTS is speaking on this device.")
            var count = 0
            var energy = 0.0
            let generationStart = Date()
            for try await chunk in request.samples {
                if count == 0 { report("POCKET_TTS_FIRST_AUDIO: \(Date().timeIntervalSince(generationStart))s") }
                count += chunk.count
                energy += chunk.reduce(0.0) { $0 + Double($1 * $1) }
                let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(chunk.count))!
                buffer.frameLength = buffer.frameCapacity
                chunk.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: chunk.count) }
                try file.write(from: buffer)
                try player.enqueue(chunk)
            }
            await request.waitForCompletion()
            report("POCKET_TTS_SYNTHESIS_TIME: \(Date().timeIntervalSince(generationStart))s")
            try await player.finish()
            guard count > 2400, energy.isFinite, energy > 0.001 else {
                throw VoiceError(message: "Synthesis did not produce finite, non-silent speech.")
            }
            report("POCKET_TTS_PLAYED: \(count) samples, \(Double(count) / 24000)s audio")

            let cancelled = try await synth.synthesize("This is a longer sentence for the interruption test. It should stop when the first audio arrives.")
            for try await chunk in cancelled.samples {
                try player.enqueue(chunk)
                player.stop()
                cancelled.cancel()
                break
            }
            let cancellationStart = Date()
            await cancelled.waitForCompletion()
            report("POCKET_TTS_CANCELLED: inference drained in \(Date().timeIntervalSince(cancellationStart))s")
            let captureBeforeRestart = capture.buffers
            let resumed = try await synth.synthesize("The next reply works after an interruption.")
            cancelled.cancel() // A delayed cancellation must leave the new request intact.
            var resumedCount = 0
            for try await samples in resumed.samples {
                resumedCount += samples.count
                try player.enqueue(samples)
            }
            await resumed.waitForCompletion()
            try await player.finish()
            guard resumedCount > 2400, capture.buffers > captureBeforeRestart,
                  TTSNetworkBlocker.attempts == 0 else {
                throw VoiceError(message: "Restart failed, capture stopped, or the model attempted a network request.")
            }
            report("POCKET_TTS_VERIFIED: offline load, playback, cancellation, restart and continuous capture")
        } catch { report("POCKET_TTS_FAILED: \(error.localizedDescription)") }
        await synth.unload()
        player.stop()
        audio.stop()
    }
}
#endif
