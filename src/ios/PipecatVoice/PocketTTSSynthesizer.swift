import FluidAudio
import Foundation

actor PocketTTSSynthesizer: SpeechSynthesizer {
    private let root: URL
    private var manager: PocketTtsManager?
    private var voice = ""
    private var active: SpeechSynthesis?

    init(root: URL) { self.root = root }

    func prepare(voice: String) async throws {
        let assets = try PocketTTSAssets.load(from: root)
        guard assets.voices.contains(voice) else {
            throw VoiceError(message: "The selected PocketTTS voice is not bundled.")
        }
        if manager == nil {
            try assets.verify(in: root)
            let candidate = PocketTtsManager(defaultVoice: voice, language: .english,
                                            directory: root, precision: .int8, placement: .gpu)
            try await candidate.initialize()
            try Task.checkCancellation()
            manager = candidate
        }
        self.voice = voice
    }

    func synthesize(_ text: String) async throws -> SpeechSynthesis {
        try Task.checkCancellation()
        guard let manager else { throw VoiceError(message: "PocketTTS is not loaded.") }
        let previous = active
        let voice = voice
        let request = SpeechSynthesis { continuation in
            await previous?.waitForCompletion()
            try Task.checkCancellation()
            let session = try await manager.makeSession(voice: voice)
            do {
                try await withTaskCancellationHandler {
                    try Task.checkCancellation()
                    session.enqueue(text)
                    session.finish()
                    for try await frame in session.frames {
                        try Task.checkCancellation()
                        guard frame.samples.allSatisfy(\.isFinite) else {
                            throw VoiceError(message: "PocketTTS returned invalid audio samples.")
                        }
                        if case .terminated = continuation.yield(frame.samples) { throw CancellationError() }
                    }
                    try Task.checkCancellation()
                } onCancel: {
                    Task { await session.cancel() }
                }
            } catch {
                await session.cancel()
                throw error
            }
            await session.cancel()
        }
        active = request
        return request
    }

    func unload() async {
        active?.cancel()
        await active?.waitForCompletion()
        active = nil
        await manager?.cleanup()
        manager = nil
        voice = ""
    }
}
