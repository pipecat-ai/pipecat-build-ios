import Foundation

#if ENABLE_PHONON
private final class CancellationToken: @unchecked Sendable {
    let pointer = phonon_cancellation_new()!
    func cancel() { phonon_cancel(pointer) }
    deinit { phonon_cancellation_free(pointer) }
}

private final class PCMContext {
    let continuation: AsyncThrowingStream<[Float], Error>.Continuation
    init(_ continuation: AsyncThrowingStream<[Float], Error>.Continuation) {
        self.continuation = continuation
    }
}

final class PhononSynthesizer: SpeechSynthesizer, @unchecked Sendable {
    private let queue = DispatchQueue(label: "voice.phonon", qos: .userInitiated)
    // All model access is serialized on queue. The C cancellation flag is atomic.
    private var model: OpaquePointer?
    private var loadedVoice = ""
    private var loadedKey = ""

    func prepare(voice: String) async throws {
        let key = VoiceSettings.loadKey()
        guard VoiceSettings.validKey(key) else { throw VoiceError(message: "Enter a valid Gradium key in Voice settings.") }
        guard let root = Bundle.main.url(forResource: "phonon", withExtension: nil) else {
            throw VoiceError(message: "The selected voice model is missing from the app bundle.")
        }
        try Task.checkCancellation()
        try await load(root: root, voice: voice, key: key)
        try Task.checkCancellation()
    }

    func synthesize(_ text: String) async throws -> SpeechSynthesis {
        SpeechSynthesis { [self] continuation in
            let token = CancellationToken()
            try await withTaskCancellationHandler {
                try await speak(text, token: token, output: continuation)
            } onCancel: { token.cancel() }
        }
    }

    func unload() async {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                if let model { phonon_free(model); self.model = nil }
                loadedKey = ""
                loadedVoice = ""
                continuation.resume()
            }
        }
    }

    private func load(root: URL, voice: String, key: String) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async { [self] in
                if model != nil && loadedVoice == voice && loadedKey == key {
                    continuation.resume()
                    return
                }
                if let model { phonon_free(model); self.model = nil }
                var error = [CChar](repeating: 0, count: 1024)
                model = phonon_load(root.path, voice, key, &error, error.count)
                if model == nil {
                    continuation.resume(throwing: VoiceError(message: String(cString: error)))
                } else {
                    loadedVoice = voice
                    loadedKey = key
                    continuation.resume()
                }
            }
        }
    }

    private func speak(_ text: String, token: CancellationToken,
                       output: AsyncThrowingStream<[Float], Error>.Continuation) async throws {
        // Completion waits for the Rust call itself, including cancelled inference.
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async { [self, token] in
                guard let model else {
                    continuation.resume(throwing: VoiceError(message: "Phonon is not loaded."))
                    return
                }
                let context = Unmanaged.passRetained(PCMContext(output))
                defer { context.release() }
                var error = [CChar](repeating: 0, count: 1024)
                let status = phonon_speak(model, text, token.pointer, { samples, count, opaque in
                    guard let samples, let opaque else { return false }
                    let target = Unmanaged<PCMContext>.fromOpaque(opaque).takeUnretainedValue()
                    let chunk = Array(UnsafeBufferPointer(start: samples, count: count))
                    if case .terminated = target.continuation.yield(chunk) { return false }
                    return true
                }, context.toOpaque(), &error, error.count)
                switch status {
                case 0: continuation.resume()
                case 1: continuation.resume(throwing: CancellationError())
                default: continuation.resume(throwing: VoiceError(message: String(cString: error)))
                }
            }
        }
    }

    deinit { if let model { phonon_free(model) } }
}
#endif
