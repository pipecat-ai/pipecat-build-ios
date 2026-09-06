import Foundation

struct VoiceError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

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

final class PhononSynthesizer: @unchecked Sendable {
    private let queue = DispatchQueue(label: "voice.phonon", qos: .userInitiated)
    // All model access is serialized on queue. The C cancellation flag is atomic.
    private var model: OpaquePointer?
    private var loadedVoice = ""
    private var loadedKey = ""

    func load(root: URL, voice: String, key: String) async throws {
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

    func stream(_ text: String) -> AsyncThrowingStream<[Float], Error> {
        let token = CancellationToken()
        return AsyncThrowingStream { continuation in
            continuation.onTermination = { _ in token.cancel() }
            queue.async { [self, token] in
                guard let model else {
                    continuation.finish(throwing: VoiceError(message: "Phonon is not loaded."))
                    return
                }
                let context = Unmanaged.passRetained(PCMContext(continuation))
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
                case 0: continuation.finish()
                case 1: continuation.finish(throwing: CancellationError())
                default: continuation.finish(throwing: VoiceError(message: String(cString: error)))
                }
            }
        }
    }

    deinit { if let model { phonon_free(model) } }
}
