import AVFoundation

/// Capture and agent playback share an engine and an explicitly verified AEC path.
@MainActor
final class VoiceAudioEngine {
    private enum EchoCancellation: String {
        case nativeInput = "native-input"
        case voiceProcessing = "voice-processing"
    }

    private let engine = AVAudioEngine()
    private var hasCaptureTap = false
    private var captureFormat: AVAudioFormat?
    private var echoCancellation: EchoCancellation?

    /// Native input AEC preserves the default playback mode on supported iPhones.
    /// Older devices and unsupported routes retain VoiceProcessingIO's voice AEC.
    func configureSession() throws {
        guard !engine.isRunning, !hasCaptureTap else {
            throw VoiceError(message: "Stop the conversation before configuring audio.")
        }
        echoCancellation = nil
        let session = AVAudioSession.sharedInstance()
        do {
            // VoiceProcessingIO implicitly enables a chat mode. Disable it before
            // selecting default mode, including when restarting after a fallback.
            if engine.inputNode.isVoiceProcessingEnabled {
                try engine.inputNode.setVoiceProcessingEnabled(false)
            }
            try session.setCategory(.playAndRecord, mode: .default,
                                    options: [.defaultToSpeaker, .allowBluetoothHFP])
            if session.isEchoCancelledInputAvailable {
                do {
                    try session.setPrefersEchoCancelledInput(true)
                    try session.setActive(true)
                    // A preference is not a guarantee: route and active-session
                    // compatibility decide whether the system actually honors it.
                    if session.isEchoCancelledInputEnabled {
                        echoCancellation = .nativeInput
                        logConfiguration("configured")
                        return
                    }
                } catch {
                    #if DEBUG
                    print("AUDIO_SESSION: native input AEC unavailable: \(error.localizedDescription)")
                    #endif
                }
            }

            // Never capture speaker output without AEC if the native path fails.
            if session.prefersEchoCancelledInput {
                try session.setPrefersEchoCancelledInput(false)
            }
            try session.setCategory(.playAndRecord, mode: .voiceChat,
                                    options: [.defaultToSpeaker, .allowBluetoothHFP])
            try session.setActive(true)
            try engine.inputNode.setVoiceProcessingEnabled(true)
            guard engine.inputNode.isVoiceProcessingEnabled else {
                throw VoiceError(message: "Microphone echo cancellation could not be enabled.")
            }
            echoCancellation = .voiceProcessing
            logConfiguration("configured")
        } catch {
            stop()
            throw error
        }
    }

    func attach(_ player: AVAudioPlayerNode, format: AVAudioFormat) {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func microphoneFormat() throws -> AVAudioFormat {
        try validateConfiguration()
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
        captureFormat = format
        do { try start() }
        catch { stopCapture(); throw error }
    }

    func start() throws {
        _ = try microphoneFormat()
        if !engine.isRunning {
            engine.prepare()
            do {
                try engine.start()
                try validateConfiguration()
            } catch {
                engine.stop()
                throw error
            }
        }
    }

    /// Reconfiguring a live engine invalidates ASR's input format. End the session
    /// instead; its next start can safely choose the route's supported AEC path.
    func routeChangeError() -> String? {
        guard echoCancellation != nil else { return nil }
        logConfiguration("route changed")
        do {
            try validateConfiguration()
            if let captureFormat, !captureFormat.isEqual(engine.inputNode.outputFormat(forBus: 0)) {
                return "The microphone audio format changed. Restart the conversation to use the new route."
            }
            return nil
        }
        catch { return error.localizedDescription }
    }

    private func validateConfiguration() throws {
        switch echoCancellation {
        case .nativeInput:
            let session = AVAudioSession.sharedInstance()
            guard session.isEchoCancelledInputEnabled, session.mode == .default,
                  !engine.inputNode.isVoiceProcessingEnabled else {
                throw VoiceError(message: "The audio route no longer supports the current echo cancellation. Restart the conversation to use the new route.")
            }
        case .voiceProcessing:
            guard engine.inputNode.isVoiceProcessingEnabled else {
                throw VoiceError(message: "Microphone echo cancellation stopped. Restart the conversation.")
            }
        case nil:
            throw VoiceError(message: "The conversation audio session is not configured.")
        }
    }

    private func logConfiguration(_ context: String) {
        #if DEBUG
        let session = AVAudioSession.sharedInstance()
        let inputs = session.currentRoute.inputs.map { $0.portType.rawValue }.joined(separator: ",")
        let outputs = session.currentRoute.outputs.map { $0.portType.rawValue }.joined(separator: ",")
        print("AUDIO_SESSION: \(context), path=\(echoCancellation?.rawValue ?? "none"), mode=\(session.mode.rawValue), native_available=\(session.isEchoCancelledInputAvailable), native_enabled=\(session.isEchoCancelledInputEnabled), input=\(inputs), output=\(outputs), rate=\(session.sampleRate)")
        #endif
    }

    /// Removing capture leaves queued agent audio and its engine running.
    func stopCapture() {
        if hasCaptureTap {
            engine.inputNode.removeTap(onBus: 0)
            hasCaptureTap = false
        }
        captureFormat = nil
    }

    func stop() {
        stopCapture()
        engine.stop()
        echoCancellation = nil
        let session = AVAudioSession.sharedInstance()
        if session.prefersEchoCancelledInput { try? session.setPrefersEchoCancelledInput(false) }
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
    }
}
